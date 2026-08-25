# China-Stock-Choose

> Daily AI-driven screener for China A-shares. Filters the universe against
> a published rule set, emails picks to your inbox, and commits results to
> this repo. All inputs come from public disclosures -- no third-party
> analyst ratings or opinion articles.

| Item | Value |
| --- | --- |
| **Repository** | https://github.com/pephupephu/China-Stock-Choose |
| **Runtime** | Python 3.10+, AKShare (cninfo / Sina / 10jqka / Shenwan) |
| **Schedule** | Weekdays 18:30 UTC (02:30 CST, after market close + filings) |
| **License** | MIT |

## Features

- Hard-filter + scoring pipeline applied to every A-share ticker.
- Multi-source verification: cninfo dividends, Sina financial statements,
  Tonghuashun (ths) main-business breakdown, Shenwan industry classification.
- HTML + plain-text + JSON output, automatically attached to a daily
  multipart email.
- On-disk parquet cache (default 6 h TTL) keeps re-runs cheap.
- Re-usable: clone, set SMTP secrets, push a tag -- every weekday you
  receive a fresh pick list.

## Quick Start

```bash
git clone https://github.com/pephupephu/China-Stock-Choose
cd China-Stock-Choose
python -m pip install -r requirements.txt
cp .env.example .env       # then edit SMTP credentials
python -m src.main test    # smoke test on 4 well-known names
python -m src.main run     # full pipeline + email + artefacts
```

CLI:

```
python -m src.main run       # full pipeline + email
python -m src.main screen    # screen only, no email
python -m src.main test      # smoke test on a handful of tickers
```

## Rule Set (Merged)

Hard-filter rules -- a stock must satisfy all of them:

| # | Rule | Source |
| --- | --- | --- |
| 1 | Excluded ST / *ST names | name string |
| 2 | Excluded non-standard audit opinions | disclosed in annual report |
| 3 | Dividend yield >= 4% in at least one of the last 2 reported years | cninfo `stock_dividend_cninfo` |
| 4 | TTM PE between 0 and 30 (exclusive) | Sina daily close + income statement |
| 5 | Last 3 years of 扣非净利润 strictly positive | Sina income statement |
| 6 | ROE TTM >= 10% | Sina balance sheet + income statement |
| 7 | Debt-to-asset ratio <= 70% | Sina balance sheet |
| 8 | Payout ratio >= 40% (>= 100 allowed with warning) | cninfo dividend / Sina income statement |
| 9 | Operating cash flow >= total cash dividend for the year | Sina cash-flow statement |
| 10 | Operating cash flow per share > 0 | Sina cash-flow statement |
| 11 | Largest YoY main-business revenue drop <= 20% in last 3 years | Sina / ths |

All thresholds are environment-driven -- see `.env.example`. The repo was
NOT designed for discretionary overrides; tweaks belong in commits so
they survive review.

## Output

Every run writes:

```
output/pick_YYYY-MM-DD.md      # markdown for git
output/pick_YYYY-MM-DD.html    # email body
output/pick_YYYY-MM-DD.json    # full structured result
```

The HTML body is multipart with a plain-text fallback for clients that
strip HTML.

## Scheduling

The repository ships with `.github/workflows/daily.yml` that runs the
screener Monday-Friday after the China A-share market close.

Required GitHub secrets:

| Secret | Notes |
| --- | --- |
| `SMTP_HOST` / `SMTP_PORT` / `SMTP_USE_SSL` | usually `smtp.qq.com:465` |
| `SMTP_USERNAME` / `SMTP_PASSWORD` | account + app-specific password |
| `EMAIL_SENDER` / `EMAIL_RECIPIENTS` | comma-separated |
| `EMAIL_SUBJECT_PREFIX` | optional prefix |
| `AKSHARE_PROXY` | optional HTTP/HTTPS proxy URL |

`workflow_dispatch` lets you re-run manually with a `smoke` checkbox to
test without sending email.

## Data Sources

- 巨潮资讯网 (`cninfo.com.cn`) -- dividend history, annual-report filings.
- 新浪财经 (`finance.sina.com.cn`) -- balance sheet, income statement,
  cash-flow statement, daily K-line.
- 同花顺 (`10jqka.com.cn`) -- main-business composition.
- 申万指数 (Sina mirror) -- industry classification, industry PE / PB.

See `docs/data-sources.md` for endpoints and gotchas.

## Development

```bash
pytest -q
```

Tests cover the metrics parser and rule engine. They do not hit the
network. Integration smoke-test is the `python -m src.main test` command.

## Limitations & Disclaimer

- Sina's free quarterly reports lag filings by hours to days. Screening
  the same stock right after disclosure may give different results than
  re-running in the morning.
- AkShare endpoints occasionally change shape. If a daily run errors,
  check `get_logs` then `git log` for upstream changes.
- **This software is for personal research only. It is not investment
  advice. Always verify against the original disclosures before
  trading.**

## License

MIT. See `LICENSE`.