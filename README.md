# China-Stock-Choose

> Daily AI-driven screener for China A-shares. Filters the universe against
> a published rule set, emails picks to your inbox, and commits results to
> this repo. Every input comes from public disclosures (cninfo, Sina,
> Tonghuashun, Shenwan); no third-party analyst ratings, opinion articles
> or paid data feeds are used. The universe is restricted to mainland
> A-shares -- ST, *ST, B-shares and Hong Kong listings are excluded at
> fetch time and never reach the report.

| Item | Value |
| --- | --- |
| **Repository** | https://github.com/pephupephu/China-Stock-Choose |
| **Runtime** | Python 3.10+, AKShare (cninfo / Sina / 10jqka / Shenwan) |
| **Universe** | Shanghai & Shenzhen & Beijing A-shares only (no ST, B-share, HK) |
| **Schedule** | Weekdays 10:30 UTC (18:30 CST, after market close + filings) |
| **License** | MIT |

## Features

- Hard-filter + scoring pipeline applied to every A-share ticker.
- Multi-source verification: cninfo dividends, Sina financial statements,
  Tonghuashun (10jqka) main-business breakdown (including
  in-progress major projects), Shenwan industry classification.
- HTML + plain-text + JSON output, automatically attached to a daily
  multipart email.
- Rejected stocks are NOT shown in the report body (only the picks
  table, or a clear "no picks today" notice); the full pipeline data
  is still preserved in `pick_YYYY-MM-DD.json` for audit.
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
python -m src.main run --limit 200   # fast local test (first 200 symbols)
```

CLI:

```
python -m src.main run       # full pipeline + email
python -m src.main screen    # screen only, no email
python -m src.main weekly    # incremental daily chunk; accumulate across the week, push on Friday (or when fully covered)
python -m src.main test      # smoke test on a handful of tickers
```

## Incremental weekly mode (recommended)

Scanning all ~5000 A-shares in one go hammers the data sources and is easy to
rate-limit. `weekly` instead processes a small batch (`INCREMENTAL_CHUNK`,
default 700) of **not-yet-screened** symbols each run, accumulates the results
in `output/.weekly_<ISO-week>.json`, and only sends the email once coverage is
complete **or** on `WEEKLY_PUSH_WEEKDAY` (default 4 = Friday). Symbols already
screened this week are read from the store, so they are never re-fetched -- the
per-day cost stays small and stable.

```bash
python -m src.main weekly     # run daily (cron / GitHub Actions)
```

Tune via env: `INCREMENTAL_CHUNK` (symbols per run) and `WEEKLY_PUSH_WEEKDAY`
(0=Mon .. 6=Sun). The bundled workflow already calls `weekly` on weekdays and
pushes automatically on Friday or when the week's coverage is full.

## Rule Set (Merged)

Universe filters (applied before the rules fire):

| Stage | Filter | Why |
| --- | --- | --- |
| Universe | drop ST / *ST names | excluded from any consideration |
| Universe | drop 200xxx (Shenzhen B-share) and 9xxxxx (Shanghai B-share) | not A-shares |
| Universe | drop HK and other non-6-digit codes | not A-shares |

Hard-filter rules -- a stock must satisfy all of them:

| #  | Rule | Source |
| --- | --- | --- |
| 1  | Exclude ST / *ST names (handled at universe stage) | name string |
| 2  | Exclude non-standard audit opinions (default `RULE_EXCLUDE_QUALIFIED=true`) | disclosed in annual report |
| 3  | Dividend yield >= 4% in **every** of the last 3 annual cash distributions (per-share cash amount is **not** used; window via `RULE_DIVIDEND_LOOKBACK_YEARS`) | cninfo `stock_dividend_cninfo` |
| 4  | TTM PE in (0, 30) exclusive | Sina daily close + income statement |
| 5  | Last 3 years of 扣非净利润 strictly positive | Sina income statement |
| 6  | ROE > 10% over the recent period (matches `RULE_MIN_ROE_PCT`) | Sina balance sheet + income statement |
| 7  | Debt-to-asset ratio <= 70%; ratios > 60% are warned but allowed | Sina balance sheet |
| 8  | Payout ratio >= 40%; > 100% allowed with "depleted payout" warning | cninfo dividend / Sina income statement |
| 9  | OCF >= total cash dividend for the year is a **warning by default** (set `RULE_REQUIRE_OCF_COVERS_DIVIDEND=true` to enforce) | Sina cash-flow statement |
| 10 | Operating cash flow per share > 0 | Sina cash-flow statement |
| 11 | Largest YoY main-business revenue drop <= 20% in the last 3 years | Sina / 10jqka |

Explicitly **not** filtered: there is **no** market-cap constraint by
design. The latest rule revision removed it.

All thresholds are environment-driven -- see `.env.example`. The repo was
NOT designed for discretionary overrides; tweaks belong in commits so
they survive review.

## Output Fields

The pick report (markdown / html / json) shows the following per ticker
so a human can sanity-check each name against the original disclosures:

- 股票代码 / 股票名称 (`symbol` / `name`)
- 板块 / 行业（申万分类）, 上市时间 (`listing_date`)
- 现价 (`close_price`) + 报价时点 (`close_price_as_of`)
- PE(TTM)、PB、市赚率（PC ratio = 股价 / 每股经营活动现金流）、ROE(TTM)
- 每股 OCF、负债率、分红支付率
- 近 3 年每股分红、近 3 年股息率（年报对应收盘价）
- 近 3 年主营收入 YoY（仅展示最近 3 年，不再列出 1990 起的所有历史）
- 在建工程 / 重大事项（取自同花顺主营业务构成 + 年报附注，便于人工复查）
- 一次性特别分红、审计意见、负债率/分红现金流等警示标签
- `score`（按综合评分降序，只展示前 50 条）

Rejected stocks (failing one or more rules) are **not** rendered into the
markdown / html body. They are kept only in the JSON attachment for
audit purposes -- this keeps the report focused on actionable picks.

## Output

Every run writes:

```
output/pick_YYYY-MM-DD.md      # markdown for git
output/pick_YYYY-MM-DD.html    # email body
output/pick_YYYY-MM-DD.json    # full structured result (incl. rejected)
```

The HTML body is multipart with a plain-text fallback for clients that
strip HTML.

## Scheduling

The repository ships with `.github/workflows/daily.yml` that runs the
screener Monday-Friday at 10:30 UTC (18:30 Asia/Shanghai), after the China
A-share market close and the daily disclosure deadline. The scheduled run
executes `python -m src.main run` (full pipeline + email); configure the
SMTP secrets below and you receive a fresh pick list every weekday.

Required GitHub secrets:

| Secret | Notes |
| --- | --- |
| `SMTP_HOST` / `SMTP_PORT` / `SMTP_USE_SSL` | e.g. `smtp.163.com:465` |
| `SMTP_USERNAME` / `SMTP_PASSWORD` | account + app-specific password |
| `EMAIL_SENDER` / `EMAIL_RECIPIENTS` | comma-separated |
| `EMAIL_SUBJECT_PREFIX` | optional prefix |
| `AKSHARE_PROXY` | optional HTTP/HTTPS proxy URL |

`workflow_dispatch` lets you re-run manually: leave `smoke` unchecked
for the full pipeline with email, or check it for a dry run that only
screens (`python -m src.main screen`) without sending email.

## Data Sources

Strictly public disclosures; no analyst commentary or paid feeds:

- 巨潮资讯网 (`cninfo.com.cn`) -- dividend history, annual-report filings.
- 新浪财经 (`finance.sina.com.cn`) -- balance sheet, income statement,
  cash-flow statement, daily K-line (PE, PB, close price).
- 同花顺 (`10jqka.com.cn`) -- main-business composition, in-progress
  major projects pulled from segment tags and annual-report notes.
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
- Every observation labelled "在建重大工程" comes from the upstream
  10jqka segment text and should be cross-checked against the original
  annual report before any decision is taken.
- **This software is for personal research only. It is not investment
  advice. Always verify against the original disclosures before
  trading.**

## License

MIT. See `LICENSE`.
