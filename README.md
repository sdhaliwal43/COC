# 🌍 Cross-Border WACC Calculator

A Streamlit app that estimates the **cost of equity, cost of debt and WACC** for a project in **USD** and in a **selected foreign currency**. It pulls the latest market inputs from public sources, and you can override any of them by hand.

## Features

- **Live data fetch** with a **🔄 Refresh data** button. Results are cached for 24 hours. Each source shows its name, link, "as of" date, fetch time and status (Fetched / Fallback / Manual).
- **Inputs:** industry, target country, equity risk premium and target debt share. A toggle sets the **project tax rate** to either the U.S. rate or the selected country's rate. The same rate is used for both currencies.
- **Manual overrides** for the unlevered beta, U.S. and foreign corporate tax rates, USD BBB spread, both 10-year government yields, and the foreign BBB spread. When the foreign-spread override is off, the app shows the auto-calculated value.
- **Side-by-side results** (USD vs. foreign currency): risk-free rate, levered beta, cost of equity, BBB spread, pre- and after-tax cost of debt, and WACC.
- **All-industry comparison:** every Damodaran industry under the current settings, in a sortable table with the selected industry highlighted, plus a WACC bar chart.
- **Key assumptions** explained in plain English.
- **Downloads:** results CSV, all-industry CSV, and an Excel workbook with four sheets (Inputs, Results, All Industries, Sources).

## Data sources

| Input | Source |
|---|---|
| U.S. industry unlevered betas | [Damodaran – betas.xls](https://pages.stern.nyu.edu/~adamodar/pc/datasets/betas.xls) |
| Corporate tax rates (combined statutory) | [OECD Corporate Tax Statistics 2024](https://www.oecd.org/en/publications/corporate-tax-statistics-2024_9c27d6e8-en/full-report/statutory-corporate-income-tax-rates_440f9286.html) |
| USD BBB spread (Baa2/BBB, large non-financial firms) | [Damodaran – ratings.html](https://pages.stern.nyu.edu/~adamodar/New_Home_Page/datafile/ratings.html) |
| 10-year government bond yields | [Trading Economics – Bonds](https://tradingeconomics.com/bonds) (scraped, no API key) |

## Methodology

Let *t* = project tax rate, *w<sub>d</sub>* = debt share, *w<sub>e</sub>* = 1 − *w<sub>d</sub>*, D/E = *w<sub>d</sub>* / *w<sub>e</sub>*.

- **Levered beta (Hamada):** β<sub>L</sub> = β<sub>U</sub> × [1 + (1 − t) × D/E]
- **Cost of equity:** k<sub>e</sub> = r<sub>f</sub> + β<sub>L</sub> × ERP
- **Pre-tax cost of debt:** k<sub>d</sub> = r<sub>f</sub> + BBB spread; **after-tax** = k<sub>d</sub> × (1 − t)
- **WACC** = w<sub>e</sub> × k<sub>e</sub> + w<sub>d</sub> × k<sub>d</sub> × (1 − t)
- **Foreign BBB spread (auto):** k<sub>d,FC</sub> = (1 + k<sub>d,USD</sub>) × (1 + r<sub>f,FC</sub>) / (1 + r<sub>f,US</sub>) − 1, and spread<sub>FC</sub> = k<sub>d,FC</sub> − r<sub>f,FC</sub>

The USD leg uses the U.S. 10Y yield. The foreign leg uses the selected country's 10Y yield. Both legs use the same levered beta, ERP, tax rate and weights. When the selected country is the United States, the two legs are identical.

## Default settings

- Industry: **Software (System & Application)**
- Country: **United States**
- Tax rate: **U.S.**
- Equity risk premium: **6.5%**
- Debt share: **30%**

## Run locally

```bash
pip install -r requirements.txt
streamlit run app.py
```

## Deploy on Streamlit Community Cloud

1. Create a public GitHub repository and upload `app.py`, `requirements.txt` and `README.md`.
2. Go to [share.streamlit.io](https://share.streamlit.io), sign in with GitHub and click **Create app**.
3. Select the repository, branch `main` and main file `app.py`, then click **Deploy**.
4. If something fails, open **Manage app → Logs** to see the error.

## Limitations

- **Scraping is fragile.** If a source changes its page layout, the parser can stop working. In that case the app shows a warning and uses **clearly labeled fallback values**. Use **Refresh data** or the manual overrides.
- **Trading Economics loads its tables with JavaScript and may block cloud servers.** The fallback yields are approximate (mid-2026), so override them with current values if the live fetch fails.
- **The OECD page may not expose a plain HTML table.** If it doesn't, the app uses a built-in table of OECD 2024 combined statutory rates.
- **The fallback betas are an approximate subset of industries.** Rely on the live Damodaran file for exact values and the full list of industries.
- **Model simplifications:** no country risk premium or sovereign-default adjustment, U.S. betas applied globally, and constant target weights. This is an educational tool, not investment advice.
