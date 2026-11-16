"""
Cross-Border WACC Calculator
============================
A Streamlit app that estimates the cost of equity, cost of debt and WACC for a
project in USD and in a selected foreign currency.

Live data sources (with labeled fallbacks if a fetch fails):
  * Damodaran (NYU Stern)  - U.S. industry unlevered betas (betas.xls)
  * Damodaran (NYU Stern)  - Baa2/BBB default spread for large non-financial firms
  * OECD                   - Combined statutory corporate income tax rates
  * Trading Economics      - 10-year government bond yields
"""

import io
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone

import numpy as np
import pandas as pd
import plotly.express as px
import requests
import streamlit as st
from bs4 import BeautifulSoup

# --------------------------------------------------------------------------------------
# Page config & constants
# --------------------------------------------------------------------------------------
st.set_page_config(page_title="Cross-Border WACC Calculator", page_icon="🌍", layout="wide")

BETAS_URL = "https://pages.stern.nyu.edu/~adamodar/pc/datasets/betas.xls"
TAX_URL = (
    "https://www.oecd.org/en/publications/corporate-tax-statistics-2024_9c27d6e8-en/"
    "full-report/statutory-corporate-income-tax-rates_440f9286.html#chapter-d1e2342-08e74a33fb"
)
RATINGS_URLS = [
    "https://pages.stern.nyu.edu/~adamodar/New_Home_Page/datafile/ratings.html",
    "https://pages.stern.nyu.edu/adamodar/New_Home_Page/datafile/ratings.html",
]
BONDS_URL = "https://tradingeconomics.com/bonds"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/126.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}
TIMEOUT = 25

DEFAULT_INDUSTRY = "Software (System & Application)"
DEFAULT_COUNTRY = "United States"
DEFAULT_ERP = 6.5          # %
DEFAULT_DEBT_SHARE = 30    # %

MONTHS = (
    "January|February|March|April|May|June|July|August|September|October|November|December"
)
MONTH_YEAR_RE = re.compile(rf"\b({MONTHS})\s*(?:\d{{1,2}}\s*,?\s*)?,?\s*(20\d{{2}})\b", re.IGNORECASE)
ISO_DATE_RE = re.compile(r"\b(20\d{2})-(\d{2})-(\d{2})\b")

# --------------------------------------------------------------------------------------
# Fallback data (used only when a live fetch fails; always labeled in the app)
# --------------------------------------------------------------------------------------
# Approximate Damodaran U.S. unlevered betas (subset). Refresh or override for exact values.
FALLBACK_BETAS = {
    "Advertising": 0.95, "Aerospace/Defense": 0.85, "Air Transport": 0.62, "Apparel": 0.86,
    "Auto & Truck": 1.05, "Beverage (Soft)": 0.60, "Biotechnology": 1.00,
    "Building Materials": 0.95, "Chemical (Basic)": 0.80, "Computer Services": 0.98,
    "Computers/Peripherals": 1.15, "Drugs (Pharmaceutical)": 0.80,
    "Electrical Equipment": 1.02, "Engineering/Construction": 0.90, "Entertainment": 0.90,
    "Food Processing": 0.50, "Healthcare Products": 0.88, "Hotel/Gaming": 0.80,
    "Information Services": 0.95, "Machinery": 0.90,
    "Oil/Gas (Integrated)": 0.60, "Oil/Gas (Production and Exploration)": 0.75,
    "Real Estate (Operations & Services)": 0.75, "Restaurant/Dining": 0.80,
    "Retail (General)": 0.80, "Retail (Online)": 1.10, "Semiconductor": 1.40,
    "Software (Entertainment)": 1.15, "Software (Internet)": 1.30,
    "Software (System & Application)": 1.20, "Telecom. Services": 0.45,
    "Transportation": 0.80, "Trucking": 0.75, "Utility (General)": 0.30,
}

# OECD Corporate Tax Statistics 2024 - combined statutory CIT rates (%), approximate.
FALLBACK_TAX = {
    "United States": 25.8, "Australia": 30.0, "Austria": 23.0, "Belgium": 25.0,
    "Canada": 26.1, "Chile": 27.0, "Colombia": 35.0, "Costa Rica": 30.0,
    "Czech Republic": 21.0, "Denmark": 22.0, "Estonia": 20.0, "Finland": 20.0,
    "France": 25.8, "Germany": 29.9, "Greece": 22.0, "Hungary": 9.0, "Iceland": 21.0,
    "Ireland": 12.5, "Israel": 23.0, "Italy": 27.8, "Japan": 29.7, "South Korea": 26.4,
    "Latvia": 20.0, "Lithuania": 15.0, "Luxembourg": 24.9, "Mexico": 30.0,
    "Netherlands": 25.8, "New Zealand": 28.0, "Norway": 22.0, "Poland": 19.0,
    "Portugal": 31.5, "Slovakia": 21.0, "Slovenia": 22.0, "Spain": 25.0, "Sweden": 20.6,
    "Switzerland": 19.6, "Turkey": 25.0, "United Kingdom": 25.0,
    "Argentina": 35.0, "Brazil": 34.0, "China": 25.0, "Hong Kong": 16.5, "India": 25.2,
    "Indonesia": 22.0, "Malaysia": 24.0, "Nigeria": 30.0, "Peru": 29.5,
    "Philippines": 25.0, "Singapore": 17.0, "South Africa": 27.0, "Thailand": 20.0,
    "Vietnam": 20.0, "Egypt": 22.5, "Kenya": 30.0,
}

# Approximate 10Y government bond yields (%). Used only if Trading Economics is unreachable.
FALLBACK_YIELDS = {
    "United States": 4.30, "Germany": 2.60, "United Kingdom": 4.60, "France": 3.40,
    "Italy": 3.60, "Spain": 3.20, "Japan": 1.60, "Canada": 3.30, "Australia": 4.30,
    "Mexico": 9.00, "Brazil": 13.50, "India": 6.40, "China": 1.80, "South Korea": 2.90,
    "Switzerland": 0.30, "Netherlands": 2.80, "Ireland": 2.90, "Belgium": 3.20,
    "Austria": 3.00, "Sweden": 2.50, "Norway": 4.00, "Denmark": 2.40, "Finland": 3.00,
    "Portugal": 3.10, "Greece": 3.40, "Poland": 5.50, "Czech Republic": 4.20,
    "Hungary": 6.80, "Turkey": 30.00, "South Africa": 10.00, "Indonesia": 6.80,
    "New Zealand": 4.50, "Singapore": 2.20, "Hong Kong": 3.20, "Chile": 5.60,
    "Colombia": 11.00, "Israel": 4.20, "Philippines": 6.20, "Thailand": 1.60,
    "Malaysia": 3.50, "Vietnam": 3.20, "Peru": 6.30, "Nigeria": 18.00, "Egypt": 22.00,
    "Kenya": 15.00, "Iceland": 6.80, "Slovakia": 3.50, "Slovenia": 3.10,
    "Lithuania": 3.60, "Latvia": 3.50, "Luxembourg": 2.90,
}

FALLBACK_BBB_SPREAD = 1.11  # %

CURRENCIES = {
    "United States": "USD", "United Kingdom": "GBP", "Japan": "JPY", "Canada": "CAD",
    "Australia": "AUD", "Switzerland": "CHF", "China": "CNY", "India": "INR",
    "Brazil": "BRL", "Mexico": "MXN", "South Korea": "KRW", "Sweden": "SEK",
    "Norway": "NOK", "Denmark": "DKK", "Poland": "PLN", "Czech Republic": "CZK",
    "Hungary": "HUF", "Turkey": "TRY", "South Africa": "ZAR", "Indonesia": "IDR",
    "New Zealand": "NZD", "Singapore": "SGD", "Hong Kong": "HKD", "Chile": "CLP",
    "Colombia": "COP", "Israel": "ILS", "Philippines": "PHP", "Thailand": "THB",
    "Malaysia": "MYR", "Vietnam": "VND", "Peru": "PEN", "Nigeria": "NGN", "Egypt": "EGP",
    "Kenya": "KES", "Iceland": "ISK", "Argentina": "ARS",
}
EURO_AREA = {
    "Germany", "France", "Italy", "Spain", "Netherlands", "Ireland", "Belgium", "Austria",
    "Finland", "Portugal", "Greece", "Slovakia", "Slovenia", "Lithuania", "Latvia",
    "Luxembourg", "Estonia",
}

COUNTRY_ALIASES = {
    "USA": "United States", "US": "United States", "U.S.": "United States",
    "United States of America": "United States", "UK": "United Kingdom",
    "Great Britain": "United Kingdom", "Britain": "United Kingdom",
    "Korea": "South Korea", "Republic of Korea": "South Korea",
    "Korea, Republic of": "South Korea", "Türkiye": "Turkey", "Turkiye": "Turkey",
    "Czechia": "Czech Republic", "Slovak Republic": "Slovakia",
    "Hong Kong, China": "Hong Kong", "Hong Kong SAR": "Hong Kong",
    "Hong Kong (China)": "Hong Kong", "China (People's Republic of)": "China",
    "People's Republic of China": "China", "Russian Federation": "Russia",
    "Viet Nam": "Vietnam",
}


# --------------------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------------------
def now_str() -> str:
    """Current UTC timestamp as a readable string."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")


def to_float(x) -> float:
    """Parse numbers such as '4.25%', '1,234', '−0.5' into floats (NaN if not possible)."""
    if x is None:
        return np.nan
    if isinstance(x, (int, float, np.number)):
        return float(x)
    s = str(x).replace(",", "").replace("%", "").replace("−", "-").strip()
    try:
        return float(s)
    except ValueError:
        return np.nan


def normalize_country(name) -> str | None:
    """Map country names from different sources onto one naming convention."""
    if not isinstance(name, str):
        return None
    n = re.sub(r"\s+", " ", name).strip()
    n = re.sub(r"\s*\d+\s*Y$", "", n)          # drop '10Y' suffixes
    n = re.sub(r"[\*†‡\d]+$", "", n).strip()  # drop footnote markers
    if not n or n.lower() == "nan":
        return None
    return COUNTRY_ALIASES.get(n, n)


def find_date(text: str) -> str | None:
    """Return the first 'Month YYYY' (or ISO date) found in text."""
    m = MONTH_YEAR_RE.search(text or "")
    if m:
        return f"{m.group(1).capitalize()} {m.group(2)}"
    m = ISO_DATE_RE.search(text or "")
    if m:
        return datetime(int(m.group(1)), int(m.group(2)), int(m.group(3))).strftime("%B %Y")
    return None


def get(url: str) -> requests.Response:
    r = requests.get(url, headers=HEADERS, timeout=TIMEOUT)
    r.raise_for_status()
    return r


def currency_of(country: str) -> str:
    if country in EURO_AREA:
        return "EUR"
    return CURRENCIES.get(country, "local currency")


def pct(x: float, d: int = 2) -> str:
    return "n/a" if x is None or pd.isna(x) else f"{x * 100:.{d}f}%"


# --------------------------------------------------------------------------------------
# Data fetchers (cached for 24h; the Refresh button clears the cache)
# --------------------------------------------------------------------------------------
def _pick_unlevered_col(columns) -> str:
    cols = list(columns)
    low = {c: c.lower() for c in cols}
    for c in cols:                                   # 1) exactly "Unlevered beta"
        if low[c] == "unlevered beta":
            return c
    for c in cols:                                   # 2) unlevered beta, not cash-corrected
        if "unlevered beta" in low[c] and "cash" not in low[c]:
            return c
    for c in cols:                                   # 3) any unlevered beta column
        if "unlevered beta" in low[c]:
            return c
    raise ValueError("No 'Unlevered beta' column found")


@st.cache_data(ttl=86400, show_spinner="Fetching Damodaran industry betas…")
def fetch_betas() -> dict:
    fetched_at = now_str()
    try:
        r = get(BETAS_URL)
        sheets = pd.read_excel(io.BytesIO(r.content), sheet_name=None, header=None, engine="xlrd")
        raw, hdr = None, None
        for _, df in sheets.items():
            mask = df.apply(
                lambda col: col.map(lambda v: str(v).strip().lower() == "industry name")
            ).any(axis=1)
            if mask.any():
                raw, hdr = df, mask[mask].index[0]
                break
        if raw is None:
            raise ValueError("'Industry Name' header row not found in betas.xls")

        top_text = " ".join(str(v) for v in raw.iloc[:hdr].values.ravel() if pd.notna(v))
        as_of = find_date(top_text)

        df = raw.iloc[hdr + 1:].copy()
        df.columns = [re.sub(r"\s+", " ", str(c)).strip() for c in raw.iloc[hdr]]
        ub_col = _pick_unlevered_col(df.columns)
        out = df[["Industry Name", ub_col]].rename(
            columns={"Industry Name": "Industry", ub_col: "Unlevered beta"}
        )
        out["Industry"] = out["Industry"].fillna("").astype(str).str.strip()
        out["Unlevered beta"] = pd.to_numeric(out["Unlevered beta"], errors="coerce")
        out = out[
            out["Industry"].ne("")
            & out["Industry"].str.lower().ne("nan")
            & out["Unlevered beta"].notna()
            & ~out["Industry"].str.lower().str.startswith("total", na=False)
        ]
        out = out.drop_duplicates("Industry").sort_values("Industry").reset_index(drop=True)
        if len(out) < 20:
            raise ValueError(f"Only {len(out)} industries parsed")
        return {
            "data": out, "as_of": as_of or "Latest file (date not stated)",
            "fetched_at": fetched_at, "status": "Fetched", "error": None,
            "note": f"Column used: '{ub_col}'",
        }
    except Exception as e:  # noqa: BLE001
        out = pd.DataFrame(
            sorted(FALLBACK_BETAS.items()), columns=["Industry", "Unlevered beta"]
        )
        return {
            "data": out, "as_of": "Approximate fallback values",
            "fetched_at": fetched_at, "status": "Fallback", "error": str(e),
            "note": "Built-in approximate subset of Damodaran industries",
        }


OECD_CIT_APIS = [
    # OECD Data API (SDMX) - same Corporate Tax Statistics database behind the report.
    "https://sdmx.oecd.org/public/rest/data/OECD.CTP.TPS,DSD_TAX_CIT@DF_CIT,/all"
    "?startPeriod=2020&dimensionAtObservation=AllDimensions&format=csvfilewithlabels",
    "https://sdmx.oecd.org/public/rest/data/OECD.CTP.TPS,DSD_TAX_CIT@DF_CIT,2.0/all"
    "?startPeriod=2020&dimensionAtObservation=AllDimensions&format=csvfilewithlabels",
]


def _parse_oecd_cit_csv(text: str) -> tuple[dict, int]:
    """Pick the combined (central + sub-central) statutory CIT rate, latest year per country."""
    df = pd.read_csv(io.StringIO(text))
    ccol = "Reference area" if "Reference area" in df.columns else "REF_AREA"
    vcol = "OBS_VALUE" if "OBS_VALUE" in df.columns else df.columns[-1]
    tcol = "TIME_PERIOD" if "TIME_PERIOD" in df.columns else None
    skip = {ccol, "REF_AREA", vcol, tcol, "Time period", "Observation value"}
    label_cols = [c for c in df.columns
                  if c not in skip and not pd.api.types.is_numeric_dtype(df[c])]
    labels = df[label_cols].fillna("").astype(str).agg(" | ".join, axis=1).str.lower()
    mask = labels.str.contains("combined") & ~labels.str.contains(
        "targeted|small business|less deductions|exclusive of surtax", regex=True
    )
    sub = df[mask].copy()
    sub["_v"] = pd.to_numeric(sub[vcol], errors="coerce")
    sub["_t"] = pd.to_numeric(sub[tcol], errors="coerce") if tcol else 0
    sub["_c"] = sub[ccol].map(normalize_country)
    sub = sub.dropna(subset=["_v", "_c"]).sort_values("_t")
    latest = sub.groupby("_c").tail(1)
    rates = {c: v / 100 for c, v in zip(latest["_c"], latest["_v"]) if 0 <= v <= 60}
    year = int(latest["_t"].max()) if len(latest) else 0
    us = rates.get("United States")
    if us is None or not 0.15 <= us <= 0.35 or len(rates) < 20:
        raise ValueError("Combined CIT rate series not found in OECD API response")
    return rates, year


@st.cache_data(ttl=86400, show_spinner="Fetching OECD corporate tax rates…")
def fetch_tax_rates() -> dict:
    fetched_at = now_str()
    errors = []
    # 1) OECD Data API (reliable, machine-readable)
    for api in OECD_CIT_APIS:
        try:
            r = get(api)
            rates, year = _parse_oecd_cit_csv(r.text)
            return {
                "data": rates, "as_of": f"OECD Corporate Tax Statistics – tax year {year}",
                "fetched_at": fetched_at, "status": "Fetched", "error": None,
                "note": "Combined (central + sub-central) statutory CIT rate, via OECD Data API",
            }
        except Exception as e:  # noqa: BLE001
            errors.append(f"OECD API: {e}")
    # 2) The report web page (often 403 / JavaScript-rendered for scripts)
    try:
        r = get(TAX_URL)
        tables = pd.read_html(io.StringIO(r.text))
        rates: dict[str, float] = {}
        for t in tables:
            t = t.copy()
            if isinstance(t.columns, pd.MultiIndex):
                t.columns = [" ".join(map(str, c)) for c in t.columns]
            countries = t[t.columns[0]].map(normalize_country)
            if not countries.eq("United States").any():
                continue
            numeric_cols = [c for c in t.columns[1:] if t[c].map(to_float).notna().mean() > 0.6]
            if not numeric_cols:
                continue
            col = next((c for c in numeric_cols if "combined" in str(c).lower()), numeric_cols[-1])
            vals = t[col].map(to_float)
            if not 15 <= vals[countries.eq("United States")].iloc[0] <= 35:
                continue
            for c, v in zip(countries, vals):
                if c and pd.notna(v) and 0 <= v <= 60:
                    rates.setdefault(c, v / 100)
        if len(rates) < 20:
            raise ValueError("no parseable rate table")
        return {
            "data": rates, "as_of": "OECD Corporate Tax Statistics 2024",
            "fetched_at": fetched_at, "status": "Fetched", "error": None,
            "note": "Combined (central + sub-central) statutory CIT rate",
        }
    except Exception as e:  # noqa: BLE001
        errors.append(f"OECD report page: {e}")
    # 3) Built-in table
    return {
        "data": {k: v / 100 for k, v in FALLBACK_TAX.items()},
        "as_of": "OECD Corporate Tax Statistics 2024 (fallback table)",
        "fetched_at": fetched_at, "status": "Fallback", "error": " | ".join(errors),
        "note": "Built-in combined statutory CIT rates",
    }


@st.cache_data(ttl=86400, show_spinner="Fetching Damodaran BBB spread…")
def fetch_bbb_spread() -> dict:
    fetched_at = now_str()
    errors = []
    for url in RATINGS_URLS:
        try:
            r = get(url)
            text = BeautifulSoup(r.text, "html.parser").get_text(" ")
            as_of = find_date(text)
            tables = pd.read_html(io.StringIO(r.text))
            for t in tables:
                # Include header cells too, in case the rating ended up as a column name
                rows = [list(map(str, t.columns))] + [
                    [str(x) for x in row] for row in t.values.tolist()
                ]
                for cells in rows:
                    idx = next(
                        (i for i, c in enumerate(cells) if re.search(r"Baa2\s*/\s*BBB", c, re.I)),
                        None,
                    )
                    if idx is None:
                        continue
                    # First value after the rating cell = spread for the LARGE-firm table
                    # (when both tables sit side by side, the large-firm table is on the left)
                    for c in cells[idx + 1:]:
                        v = to_float(c)
                        if pd.notna(v):
                            spread = v / 100 if v > 0.2 else v   # '1.11%' or 0.0111
                            if 0 < spread < 0.1:
                                return {
                                    "data": spread, "as_of": as_of or "Latest page",
                                    "fetched_at": fetched_at, "status": "Fetched",
                                    "error": None,
                                    "note": "Baa2/BBB, large non-financial firms (mkt cap > $5bn)",
                                    "url": url,
                                }
                            break
            errors.append(f"{url}: Baa2/BBB row not found")
        except Exception as e:  # noqa: BLE001
            errors.append(f"{url}: {e}")
    return {
        "data": FALLBACK_BBB_SPREAD / 100, "as_of": "Fallback value",
        "fetched_at": fetched_at, "status": "Fallback", "error": " | ".join(errors),
        "note": "Baa2/BBB, large non-financial firms", "url": RATINGS_URLS[0],
    }


# FRED series used when Trading Economics blocks the request. No API key needed.
# U.S.: daily 10Y Treasury (DGS10). Others: OECD long-term (10Y) government bond yields, monthly.
FRED_CSV = "https://fred.stlouisfed.org/graph/fredgraph.csv?id={}"
FRED_SERIES = {"United States": "DGS10"} | {
    country: f"IRLTLT01{code}M156N" for country, code in {
        "Germany": "DE", "United Kingdom": "GB", "France": "FR", "Italy": "IT", "Spain": "ES",
        "Japan": "JP", "Canada": "CA", "Australia": "AU", "Mexico": "MX", "South Korea": "KR",
        "Switzerland": "CH", "Netherlands": "NL", "Ireland": "IE", "Belgium": "BE",
        "Austria": "AT", "Sweden": "SE", "Norway": "NO", "Denmark": "DK", "Finland": "FI",
        "Portugal": "PT", "Greece": "GR", "Poland": "PL", "Czech Republic": "CZ",
        "Hungary": "HU", "Israel": "IL", "New Zealand": "NZ", "South Africa": "ZA",
        "Chile": "CL", "Colombia": "CO", "Iceland": "IS", "Slovakia": "SK", "Slovenia": "SI",
        "Lithuania": "LT", "Latvia": "LV", "Luxembourg": "LU", "India": "IN",
    }.items()
}


def _fred_latest(series_id: str) -> tuple[float, str]:
    r = get(FRED_CSV.format(series_id))
    df = pd.read_csv(io.StringIO(r.text))
    df.columns = ["date", "value"]
    df["value"] = pd.to_numeric(df["value"], errors="coerce")
    df = df.dropna()
    last = df.iloc[-1]
    return float(last["value"]) / 100, str(last["date"])


def _scrape_trading_economics() -> dict:
    r = get(BONDS_URL)
    yields: dict[str, float] = {}
    try:
        tables = pd.read_html(io.StringIO(r.text))
    except ValueError:
        tables = []
    for t in tables:
        if t.shape[1] < 2:
            continue
        ycol = next(
            (c for c in t.columns if str(c).strip().lower() in ("yield", "price", "last")),
            t.columns[1],
        )
        for name, y in zip(t[t.columns[0]], t[ycol]):
            c, v = normalize_country(str(name)), to_float(y)
            if c and pd.notna(v) and -5 < v < 100:
                yields.setdefault(c, v / 100)
    if not tables:  # rows may sit outside <table> tags
        soup = BeautifulSoup(r.text, "html.parser")
        for tr in soup.select("tr"):
            cells = [td.get_text(" ", strip=True) for td in tr.find_all(["td", "th"])]
            if len(cells) >= 2:
                c, v = normalize_country(cells[0]), to_float(cells[1])
                if c and pd.notna(v) and -5 < v < 100:
                    yields.setdefault(c, v / 100)
    if "United States" not in yields or len(yields) < 10:
        raise ValueError("No bond yield table in the page (blocked or JavaScript-rendered)")
    return yields


@st.cache_data(ttl=86400, show_spinner="Fetching 10Y government bond yields…")
def fetch_bond_yields() -> dict:
    """Trading Economics first; if blocked, FRED (U.S. Treasury daily + OECD monthly);
    any country still missing uses the built-in table. Each country records its source."""
    fetched_at = now_str()
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    # 1) Trading Economics
    try:
        te = _scrape_trading_economics()
        return {
            "data": te, "by_country": {c: ("Trading Economics", today) for c in te},
            "as_of": f"{today} (scrape date)", "fetched_at": fetched_at, "status": "Fetched",
            "error": None, "note": "Latest 10-year government bond yields", "source": "Trading Economics",
            "url": BONDS_URL,
        }
    except Exception as e:  # noqa: BLE001
        te_error = f"Trading Economics: {e}"

    # 2) FRED (parallel downloads)
    data, by_country, errs = {}, {}, []
    with ThreadPoolExecutor(max_workers=12) as pool:
        futures = {pool.submit(_fred_latest, sid): c for c, sid in FRED_SERIES.items()}
        for fut in as_completed(futures):
            c = futures[fut]
            try:
                v, d = fut.result()
                data[c], by_country[c] = v, ("FRED", d)
            except Exception as e:  # noqa: BLE001
                errs.append(f"{c}: {e}")
    if "United States" in data and len(data) >= 10:
        for c, v in FALLBACK_YIELDS.items():          # fill countries FRED doesn't cover
            if c not in data:
                data[c], by_country[c] = v / 100, ("Built-in fallback", "approx. mid-2026")
        us_date = by_country["United States"][1]
        return {
            "data": data, "by_country": by_country,
            "as_of": f"U.S. daily as of {us_date}; other countries = latest monthly OECD average",
            "fetched_at": fetched_at, "status": "Fetched (FRED)", "error": te_error,
            "note": "Trading Economics blocked this server, so FRED / OECD 10Y yields are used",
            "source": "FRED (St. Louis Fed) / OECD", "url": "https://fred.stlouisfed.org/series/DGS10",
        }

    # 3) Built-in table
    return {
        "data": {k: v / 100 for k, v in FALLBACK_YIELDS.items()},
        "by_country": {c: ("Built-in fallback", "approx. mid-2026") for c in FALLBACK_YIELDS},
        "as_of": "Approximate fallback values (mid-2026)", "fetched_at": fetched_at,
        "status": "Fallback", "error": te_error + " | FRED: " + "; ".join(errs[:3]),
        "note": "Built-in approximate 10Y yields", "source": "Trading Economics", "url": BONDS_URL,
    }


# --------------------------------------------------------------------------------------
# Finance
# --------------------------------------------------------------------------------------
def levered_beta(beta_u: float, t: float, wd: float) -> float:
    """Hamada: βL = βU × [1 + (1 − t) × D/E]."""
    de = wd / (1 - wd)
    return beta_u * (1 + (1 - t) * de)


def auto_foreign_spread(rf_us: float, spread_us: float, rf_fc: float) -> float:
    """Convert the USD pre-tax cost of debt into foreign currency via the yield
    differential, then subtract the foreign risk-free rate."""
    kd_us = rf_us + spread_us
    kd_fc = (1 + kd_us) * (1 + rf_fc) / (1 + rf_us) - 1
    return kd_fc - rf_fc


def compute_wacc(beta_u, t, wd, erp, rf_us, spread_us, rf_fc, spread_fc) -> dict:
    """Return both legs (USD and foreign currency) of the WACC calculation."""
    we = 1 - wd
    bl = levered_beta(beta_u, t, wd)
    res = {}
    for leg, rf, spread in (("USD", rf_us, spread_us), ("FC", rf_fc, spread_fc)):
        ke = rf + bl * erp
        kd = rf + spread
        kd_at = kd * (1 - t)
        res[leg] = {
            "Risk-free rate (10Y)": rf,
            "Unlevered beta": beta_u,
            "Levered beta": bl,
            "Equity risk premium": erp,
            "Cost of equity": ke,
            "BBB spread": spread,
            "Pre-tax cost of debt": kd,
            "Tax rate": t,
            "After-tax cost of debt": kd_at,
            "Equity weight": we,
            "Debt weight": wd,
            "WACC": we * ke + wd * kd_at,
        }
    return res


PCT_ITEMS = {
    "Risk-free rate (10Y)", "Equity risk premium", "Cost of equity", "BBB spread",
    "Pre-tax cost of debt", "Tax rate", "After-tax cost of debt", "Equity weight",
    "Debt weight", "WACC",
}


# --------------------------------------------------------------------------------------
# Load data
# --------------------------------------------------------------------------------------
betas_src = fetch_betas()
tax_src = fetch_tax_rates()
bbb_src = fetch_bbb_spread()
yld_src = fetch_bond_yields()

betas_df: pd.DataFrame = betas_src["data"]
tax_rates: dict = dict(tax_src["data"])
yields: dict = dict(yld_src["data"])
if "United States" not in yields:                      # safety net
    yields["United States"] = FALLBACK_YIELDS["United States"] / 100
if "United States" not in tax_rates:
    tax_rates["United States"] = FALLBACK_TAX["United States"] / 100

countries = sorted(set(tax_rates) & set(yields))

# --------------------------------------------------------------------------------------
# Sidebar - inputs
# --------------------------------------------------------------------------------------
st.sidebar.header("⚙️ Settings")
if st.sidebar.button("🔄 Refresh data", width="stretch",
                     help="Clear the cache and re-download all four sources"):
    st.cache_data.clear()
    st.rerun()

industries = betas_df["Industry"].tolist()
ind_default = next(
    (i for i, x in enumerate(industries) if x == DEFAULT_INDUSTRY),
    next((i for i, x in enumerate(industries) if x.startswith("Software (System")), 0),
)
industry = st.sidebar.selectbox("Industry (Damodaran, U.S.)", industries, index=ind_default)
country = st.sidebar.selectbox(
    "Target country", countries, index=countries.index(DEFAULT_COUNTRY)
)
erp = st.sidebar.slider("Equity risk premium (%)", 0.0, 15.0, DEFAULT_ERP, 0.1) / 100
wd = st.sidebar.slider("Target debt share D/(D+E) (%)", 0, 90, DEFAULT_DEBT_SHARE, 1) / 100
tax_choice = st.sidebar.radio(
    "Project tax rate (applied to both currencies)",
    ["U.S. tax rate", "Selected-country tax rate"], index=0, horizontal=True,
)

def yld_country_status(c: str) -> str:
    src = yld_src.get("by_country", {}).get(c, ("", ""))[0]
    return "Fallback" if (src == "Built-in fallback" or yld_src["status"] == "Fallback") else "Fetched"


def yld_label(c: str) -> str:
    src, d = yld_src.get("by_country", {}).get(c, (yld_src.get("source", ""), ""))
    return f" ({src}, {d})" if src else ""


fetched_beta = float(betas_df.loc[betas_df["Industry"] == industry, "Unlevered beta"].iloc[0])
fetched = {
    "beta": (fetched_beta, betas_src["status"]),
    "tax_us": (tax_rates["United States"], tax_src["status"]),
    "tax_fc": (tax_rates[country], tax_src["status"]),
    "spread_us": (bbb_src["data"], bbb_src["status"]),
    "rf_us": (yields["United States"], yld_country_status("United States")),
    "rf_fc": (yields[country], yld_country_status(country)),
}


def override_input(label: str, key: str, default: float, is_pct: bool = True) -> tuple[float, bool]:
    """Checkbox + number input. While the override is off the input is disabled and
    tracks the fetched value; when on, the user's manual value is used."""
    on = st.checkbox(f"Override {label}", key=f"{key}_on")
    shown = default * 100 if is_pct else default
    vkey = f"{key}_val"
    if not on or vkey not in st.session_state:
        st.session_state[vkey] = round(float(shown), 4)
    val = st.number_input(
        f"{label}{' (%)' if is_pct else ''}", key=vkey, disabled=not on,
        step=0.05 if is_pct else 0.01, format="%.2f" if is_pct else "%.3f",
        label_visibility="collapsed",
    )
    if on:
        return (val / 100 if is_pct else val), True
    return default, False


with st.sidebar.expander("✏️ Manual overrides", expanded=False):
    st.caption("Tick a box to replace the fetched value with your own input.")
    beta_u, m_beta = override_input("Unlevered beta", "beta", fetched["beta"][0], is_pct=False)
    tax_us, m_tax_us = override_input("U.S. corporate tax rate", "tax_us", fetched["tax_us"][0])
    tax_fc, m_tax_fc = override_input(f"{country} corporate tax rate", "tax_fc", fetched["tax_fc"][0])
    spread_us, m_spread_us = override_input("USD BBB spread", "spread_us", fetched["spread_us"][0])
    rf_us, m_rf_us = override_input("U.S. 10Y government yield", "rf_us", fetched["rf_us"][0])
    rf_fc, m_rf_fc = override_input(f"{country} 10Y government yield", "rf_fc", fetched["rf_fc"][0])
    spread_fc_auto = auto_foreign_spread(rf_us, spread_us, rf_fc)
    spread_fc, m_spread_fc = override_input(
        f"{country} BBB spread", "spread_fc", spread_fc_auto
    )
    if not m_spread_fc:
        st.info(f"Auto-calculated {country} BBB spread: **{pct(spread_fc_auto)}**")

t = tax_us if tax_choice == "U.S. tax rate" else tax_fc
tax_label = "U.S." if tax_choice == "U.S. tax rate" else country

res = compute_wacc(beta_u, t, wd, erp, rf_us, spread_us, rf_fc, spread_fc)
fc_ccy = currency_of(country)


def status(manual: bool, src_status: str) -> str:
    return "✏️ Manual" if manual else ("🟢 Fetched" if src_status.startswith("Fetched") else "🟡 Fallback")


inputs_df = pd.DataFrame([
    ["Industry", industry, "Selected"],
    ["Target country", country, "Selected"],
    ["Unlevered beta", f"{beta_u:.3f}", status(m_beta, fetched["beta"][1])],
    ["U.S. corporate tax rate", pct(tax_us), status(m_tax_us, fetched["tax_us"][1])],
    [f"{country} corporate tax rate", pct(tax_fc), status(m_tax_fc, fetched["tax_fc"][1])],
    ["Project tax rate used", f"{pct(t)} ({tax_label})", "Selected"],
    ["USD BBB spread", pct(spread_us), status(m_spread_us, fetched["spread_us"][1])],
    ["U.S. 10Y yield", pct(rf_us) + ("" if m_rf_us else yld_label("United States")),
     status(m_rf_us, fetched["rf_us"][1])],
    [f"{country} 10Y yield", pct(rf_fc) + ("" if m_rf_fc else yld_label(country)),
     status(m_rf_fc, fetched["rf_fc"][1])],
    [f"{country} BBB spread", pct(spread_fc), "✏️ Manual" if m_spread_fc else "🧮 Auto-calculated"],
    ["Equity risk premium", pct(erp), "Selected"],
    ["Debt share D/(D+E)", pct(wd, 0), "Selected"],
], columns=["Input", "Value used", "Status"])

sources_df = pd.DataFrame([
    ["Industry unlevered betas", "Damodaran (NYU Stern)", betas_src["as_of"],
     betas_src["fetched_at"], betas_src["status"], betas_src["note"], BETAS_URL],
    ["Corporate tax rates", "OECD Corporate Tax Statistics", tax_src["as_of"],
     tax_src["fetched_at"], tax_src["status"], tax_src["note"], TAX_URL],
    ["USD BBB spread", "Damodaran (NYU Stern)", bbb_src["as_of"], bbb_src["fetched_at"],
     bbb_src["status"], f"{bbb_src['note']}: {pct(bbb_src['data'])}", bbb_src["url"]],
    ["10Y government yields", yld_src["source"], yld_src["as_of"], yld_src["fetched_at"],
     yld_src["status"], yld_src["note"], yld_src["url"]],
], columns=["Data", "Source", "As of", "Fetched at", "Status", "Notes", "URL"])

# --------------------------------------------------------------------------------------
# Main page
# --------------------------------------------------------------------------------------
st.title("🌍 Cross-Border WACC Calculator")
st.markdown(
    f"Cost of equity, cost of debt and WACC for a **{industry}** project in "
    f"**USD** and **{country} ({fc_ccy})**, using one project tax rate "
    f"(**{tax_label}: {pct(t)}**), a **{pct(erp, 1)}** equity risk premium and "
    f"**{pct(wd, 0)}** debt financing."
)

for name, src in (("Betas", betas_src), ("Tax rates", tax_src),
                  ("BBB spread", bbb_src), ("Bond yields", yld_src)):
    if src["status"] == "Fetched (FRED)":
        st.info("**Bond yields:** Trading Economics blocked the request from this server, so live "
                "10Y yields come from FRED (U.S. Treasury daily; other countries = latest OECD monthly "
                "average). Countries FRED doesn't cover use labeled fallback values. Override any yield "
                "in the sidebar if needed.")
    elif src["status"] != "Fetched":
        st.warning(f"**{name}:** live fetch failed, so labeled fallback values are in use. "
                   f"Use *Refresh data* or the manual overrides. _(Detail: {src['error']})_")

with st.expander("📚 Data sources & dates", expanded=True):
    st.dataframe(
        sources_df, hide_index=True, width="stretch",
        column_config={"URL": st.column_config.LinkColumn("URL", display_text="Open source")},
    )

tab_res, tab_all, tab_assume, tab_dl = st.tabs(
    ["📊 Results", "🏭 All-industry comparison", "📝 Key assumptions", "⬇️ Downloads"]
)

# ---- Results tab ----------------------------------------------------------------------
with tab_res:
    c1, c2 = st.columns(2)
    for col, leg, title in ((c1, "USD", "🇺🇸 USD (United States)"),
                            (c2, "FC", f"{country} currency ({fc_ccy})")):
        r = res[leg]
        with col:
            st.subheader(title)
            m1, m2 = st.columns(2)
            delta = (lambda k: None) if leg == "USD" else (
                lambda k: f"{(r[k] - res['USD'][k]) * 100:+.2f} pp vs USD"
            )
            m1.metric("Risk-free rate (10Y)", pct(r["Risk-free rate (10Y)"]),
                      delta("Risk-free rate (10Y)"), delta_color="off")
            m2.metric("Levered beta", f"{r['Levered beta']:.3f}")
            m1.metric("Cost of equity", pct(r["Cost of equity"]),
                      delta("Cost of equity"), delta_color="off")
            m2.metric("BBB spread", pct(r["BBB spread"]), delta("BBB spread"), delta_color="off")
            m1.metric("Pre-tax cost of debt", pct(r["Pre-tax cost of debt"]),
                      delta("Pre-tax cost of debt"), delta_color="off")
            m2.metric("After-tax cost of debt", pct(r["After-tax cost of debt"]),
                      delta("After-tax cost of debt"), delta_color="off")
            st.metric("WACC", pct(r["WACC"]), delta("WACC"), delta_color="off")

    st.markdown("#### Side-by-side comparison")
    rows = []
    for k in res["USD"]:
        u, f = res["USD"][k], res["FC"][k]
        fmt = pct if k in PCT_ITEMS else (lambda x: f"{x:.3f}")
        diff = f"{(f - u) * 100:+.2f} pp" if k in PCT_ITEMS else f"{f - u:+.3f}"
        rows.append([k, fmt(u), fmt(f), diff])
    comp_display = pd.DataFrame(rows, columns=["Line item", "USD", f"{country} ({fc_ccy})", "Difference"])
    st.dataframe(comp_display, hide_index=True, width="stretch")

    st.markdown("#### Inputs in use")
    st.dataframe(inputs_df, hide_index=True, width="stretch")

# ---- All-industry tab -------------------------------------------------------------
all_rows = []
for ind, bu in zip(betas_df["Industry"], betas_df["Unlevered beta"]):
    b = beta_u if (ind == industry and m_beta) else bu
    rr = compute_wacc(b, t, wd, erp, rf_us, spread_us, rf_fc, spread_fc)
    all_rows.append({
        "Industry": ind,
        "Unlevered beta": b,
        "Levered beta": rr["USD"]["Levered beta"],
        "Cost of equity (USD)": rr["USD"]["Cost of equity"],
        f"Cost of equity ({fc_ccy})": rr["FC"]["Cost of equity"],
        "WACC (USD)": rr["USD"]["WACC"],
        f"WACC ({fc_ccy})": rr["FC"]["WACC"],
    })
all_df = pd.DataFrame(all_rows)

with tab_all:
    st.caption(
        f"Every Damodaran industry under the current settings: tax {pct(t)} ({tax_label}), "
        f"ERP {pct(erp, 1)}, debt share {pct(wd, 0)}, U.S. 10Y {pct(rf_us)}, "
        f"{country} 10Y {pct(rf_fc)}. Selected industry is highlighted. Click a column header to sort."
    )
    pct_cols = [c for c in all_df.columns if c.startswith(("Cost", "WACC"))]
    styled = (
        all_df.style
        .apply(lambda row: ["background-color: #fff3b0; font-weight: 600"] * len(row)
               if row["Industry"] == industry else [""] * len(row), axis=1)
        .format({c: "{:.2%}" for c in pct_cols} | {"Unlevered beta": "{:.3f}", "Levered beta": "{:.3f}"})
    )
    st.dataframe(styled, hide_index=True, width="stretch", height=420)

    ccy_opts = ["USD"] if fc_ccy == "USD" and country == "United States" else ["USD", fc_ccy]
    ccy = st.radio("Chart currency", ccy_opts, horizontal=True)
    wcol = f"WACC ({ccy})"
    chart_df = all_df.sort_values(wcol).assign(
        Highlight=lambda d: np.where(d["Industry"] == industry, "Selected industry", "Other industries")
    )
    fig = px.bar(
        chart_df, x=wcol, y="Industry", orientation="h", color="Highlight",
        color_discrete_map={"Selected industry": "#d62728", "Other industries": "#4c78a8"},
        title=f"WACC by industry ({ccy})",
    )
    fig.update_layout(
        height=max(500, 18 * len(chart_df)), xaxis_tickformat=".1%",
        yaxis_title=None, legend_title=None, margin=dict(l=10, r=10, t=50, b=10),
        yaxis=dict(categoryorder="array", categoryarray=chart_df["Industry"].tolist()),
    )
    st.plotly_chart(fig, width="stretch")

# ---- Assumptions tab --------------------------------------------------------------
with tab_assume:
    st.markdown(f"""
**Formulas**

- Levered beta (Hamada): β<sub>L</sub> = β<sub>U</sub> × [1 + (1 − t) × D/E], with D/E = w<sub>d</sub> / (1 − w<sub>d</sub>)
- Cost of equity: k<sub>e</sub> = r<sub>f</sub> + β<sub>L</sub> × ERP
- Pre-tax cost of debt: k<sub>d</sub> = r<sub>f</sub> + BBB spread; after-tax = k<sub>d</sub> × (1 − t)
- WACC = w<sub>e</sub> × k<sub>e</sub> + w<sub>d</sub> × k<sub>d</sub> × (1 − t)
- Auto foreign BBB spread: k<sub>d,FC</sub> = (1 + k<sub>d,USD</sub>) × (1 + r<sub>f,FC</sub>) / (1 + r<sub>f,US</sub>) − 1, and spread<sub>FC</sub> = k<sub>d,FC</sub> − r<sub>f,FC</sub>

**Key assumptions**

1. **Hamada relevering.** Damodaran's U.S. industry unlevered beta is relevered at the target debt share ({pct(wd, 0)}) and the project tax rate. Debt is assumed to carry no systematic risk.
2. **U.S. industry betas are applied globally.** The same levered beta is used in both currencies, so business risk is assumed to be the same in {country}.
3. **Same equity risk premium ({pct(erp, 1)}) in both currencies.** No separate country risk premium is added.
4. **One project tax rate ({tax_label}: {pct(t)}) for both legs.** Use the toggle to switch between the U.S. and {country} combined statutory rates (OECD).
5. **The 10-year government yield is the risk-free rate,** with no adjustment for sovereign default risk. For countries with default risk, the foreign rate overstates the true risk-free rate.
6. **BBB rating assumption.** The project borrows at Damodaran's Baa2/BBB spread for large non-financial firms (market cap > $5 bn).
7. **Yield-differential method for the foreign spread.** The USD cost of debt is converted into {fc_ccy} using the ratio of 10Y yields (an interest-rate-parity / Fisher-type adjustment). The difference between the foreign and U.S. cost of equity comes only from the risk-free rate.
8. **Market-value target weights.** w<sub>d</sub> = {pct(wd, 0)} and w<sub>e</sub> = {pct(1 - wd, 0)} are target (not book) weights and are constant over the project's life.
9. When the target country is the United States, the two legs are identical by construction.
""", unsafe_allow_html=True)

# ---- Downloads tab ----------------------------------------------------------------
results_export = pd.DataFrame({
    "Line item": list(res["USD"].keys()),
    "USD": list(res["USD"].values()),
    f"{country} ({fc_ccy})": list(res["FC"].values()),
})
results_export["Difference"] = results_export.iloc[:, 2] - results_export.iloc[:, 1]


def build_excel() -> bytes:
    buf = io.BytesIO()
    with pd.ExcelWriter(buf, engine="openpyxl") as xw:
        inputs_df.to_excel(xw, sheet_name="Inputs", index=False)
        results_export.to_excel(xw, sheet_name="Results", index=False)
        all_df.to_excel(xw, sheet_name="All Industries", index=False)
        sources_df.to_excel(xw, sheet_name="Sources", index=False)
        for ws in xw.book.worksheets:
            for col_cells in ws.columns:
                width = max(len(str(c.value)) if c.value is not None else 0 for c in col_cells)
                ws.column_dimensions[col_cells[0].column_letter].width = min(max(10, width + 2), 60)
    return buf.getvalue()


with tab_dl:
    stamp = datetime.now().strftime("%Y%m%d")
    slug = re.sub(r"[^A-Za-z0-9]+", "_", f"{industry}_{country}").strip("_")
    st.download_button("⬇️ Side-by-side results (CSV)", results_export.to_csv(index=False),
                       f"wacc_results_{slug}_{stamp}.csv", "text/csv", width="stretch")
    st.download_button("⬇️ All-industry comparison (CSV)", all_df.to_csv(index=False),
                       f"wacc_all_industries_{slug}_{stamp}.csv", "text/csv",
                       width="stretch")
    st.download_button(
        "⬇️ Full workbook (Excel: Inputs, Results, All Industries, Sources)", build_excel(),
        f"wacc_{slug}_{stamp}.xlsx",
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        width="stretch",
    )
    st.caption("Rates are exported as decimals (e.g., 0.0950 = 9.50%).")

st.divider()
st.caption(
    "Educational tool. Data © the respective sources: Aswath Damodaran (NYU Stern), OECD, "
    "Trading Economics. Live scraping can break if a site changes its layout. When that "
    "happens, labeled fallback values are used and every input can be overridden manually."
)
