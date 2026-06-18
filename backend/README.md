# DeepFocus Backend

This backend keeps the merged Finogrid code under `backend/finogrid` and adds a
lightweight cloud-model API for the React frontend under `backend/deepfocus_api`.

## Run the frontend API

```bash
cd backend
python -m venv .venv
source .venv/bin/activate
pip install -r requirements-cloud.txt
cp .env.example .env
uvicorn deepfocus_api.main:app --host 0.0.0.0 --port 8300 --reload
```

Use the long-running mode for full TradingAgents jobs:

```bash
npm run backend:long
```

This mode disables `uvicorn --reload` and raises the embedded TradingAgents
runner timeout to 3600 seconds. The reload server is still convenient for UI/API
development, but it can interrupt long external-agent subprocesses.

The React app calls `http://localhost:8300` by default.

The research workbench is also served through this backend at
`http://localhost:8300/research-workbench/`. The backend starts the local
workbench module automatically when the submodule dependencies are installed.

## Market data mode

The frontend refreshes quotes from `GET /api/market/quotes?symbols=TSLA,NVDA`
after login and then polls every two minutes. Configure free API keys when you
want live quote providers:

```env
FINNHUB_API_KEY=your_finnhub_key
ALPHAVANTAGE_API_KEY=your_alpha_vantage_key
```

Provider order prioritizes China public sources for China/HK symbols
(Eastmoney, Sina, Tencent, then Stooq fallback) and keeps Finnhub / Alpha
Vantage as optional key-based sources for broader market coverage. If all
providers fail, the UI keeps the local sample stock universe but labels it as
sample data instead of presenting it as live market data.

`GET /api/market/data-layers?symbol=600519.SH&keyword=贵州茅台` reports the
three-layer integration health used by the data-source center: free public
quotes, Tushare Pro structured A-share data, and Xueqiu/WeChat public sentiment.

```env
TUSHARE_TOKEN=optional_tushare_pro_token
# or TUSHARE_PRO_TOKEN / TS_TOKEN
```

`GET /api/market/ashare/structured?symbol=600519.SH&limit=120` uses Tushare Pro
when configured and returns a clear `unconfigured` status when no token is
present, so the UI can show the A-share structured layer as waiting for setup
instead of broken.

`GET /api/options/signals?symbols=AAPL,NVDA&horizon_days=45&max_expirations=3`
drives the Options Radar module. It aggregates free/delayed option-chain
snapshots into put/call ratios, OI walls, max pain, ATM straddle expected move,
IV skew, term structure, pin-risk, and a quality-adjusted directional score.
Provider order is:

```env
MARKETDATA_APP_TOKEN=optional_free_account_token
# or MARKETDATA_APP_API_KEY=optional_free_account_token
TRADIER_ACCESS_TOKEN=reserved_for_full_chain_provider
```

The runtime tries MarketData.app first, then Nasdaq's public option-chain
snapshot, then Yahoo Finance's public chain as a last fallback. Free sources are
delayed and may omit IV/Greeks or bid/ask fields; responses surface these
limitations in `risk_flags` instead of treating them as live order flow.

`GET /api/earnings/calendar?symbols=TSLA,NVDA&horizon=3month` first scans the
Nasdaq public no-key earnings calendar for matching watchlist symbols. If
`ALPHAVANTAGE_API_KEY` is configured, Alpha Vantage is used as an optional
second source for symbols not found in the public calendar window. Remaining
symbols return watchlist templates marked as pending provider sync, so the UI can
still show company-specific research checklists without inventing report dates.
`NASDAQ_EARNINGS_SCAN_DAYS` controls the no-key scan window and defaults to 60
days; responses are cached in-process for 30 minutes by default.

## Xueqiu keyword crawl

The Xueqiu keyword crawler can use the same single-cookie token style as
community wrappers such as `pysnowball` and `snowball-mcp`. Put your own
authorized Xueqiu session cookie in `.env`:

```env
DEEPFOCUS_XUEQIU_COOKIE=xq_a_token=xxxxx;u=xxxx
DEEPFOCUS_XUEQIU_USER_AGENT=Mozilla/5.0 ...
DEEPFOCUS_XUEQIU_REFERER=https://xueqiu.com/k?q=...
DEEPFOCUS_XUEQIU_STATUS_URL=https://xueqiu.com/query/v1/search/status.json?...md5__1038=...
# or:
XUEQIU_TOKEN=xq_a_token=xxxxx;u=xxxx
```

For the best chance of a valid authenticated request, open Xueqiu in your
browser, search for the keyword, then copy the full `Cookie` and `User-Agent`
from that same browser request in DevTools → Network. A short
`xq_a_token=...;u=...` pair may still be rejected by Xueqiu's WAF.
When copying a `query/v1/search/status.json` request, the importer also stores
the signed status URL as `DEEPFOCUS_XUEQIU_STATUS_URL`; that URL is treated as a
session-scoped template and may need to be refreshed when Xueqiu rotates its
temporary `md5__...` parameter.

The backend does not automate token collection, CAPTCHA solving, WAF bypassing,
or token-pool rotation. If Xueqiu still returns a verification or rate-limit
page, the frontend records the reason and falls back to public sources such as
WeChat public search.

Keyword crawl responses include source-strategy metadata:

- `attempted_providers`: providers tried in order.
- `effective_provider`: provider that supplied stored items.
- `fallback_used`: whether the strategy had to downgrade.
- `provider_policy`: auth mode, risk level, health score, rate-limit guidance,
  and configured/fallback status.

## Cloud model mode

For OpenAI:

```env
DEEPFOCUS_LLM_PROVIDER=openai
OPENAI_API_KEY=your_key
OPENAI_MODEL=gpt-4o-mini
```

For MiniMax:

```env
DEEPFOCUS_LLM_PROVIDER=minimax
MINIMAX_API_KEY=your_key
MINIMAX_MODEL=MiniMax-M3
MINIMAX_BASE_URL=https://api.minimaxi.com/v1
```

`DEEPFOCUS_LLM_PROVIDER=mock` keeps local development runnable without GPU or API
keys. It is useful for UI work, not production analysis.

## Professional research MVP

The professional financial-report kernel is mounted under `/api/pro-research`.
It reuses the existing upload extractor and data-source evidence store, then
keeps a separate auditable SQLite database at
`backend/.professional_research.sqlite3`.

Useful endpoints:

- `POST /api/pro-research/reports/upload`: upload a PDF/Word/Excel/text report,
  store the raw evidence, chunk the report, and extract structured metrics.
- `POST /api/pro-research/reports/ingest-item`: promote an existing data-source
  item into the professional report library.
- `GET /api/pro-research/metrics?report_id=...`: inspect extracted metrics.
- `POST /api/pro-research/rag/query`: answer with `[M1]` metric citations and
  `[C1]` source chunk citations, refusing when evidence is missing.
- `POST /api/pro-research/reports/{report_id}/analyze`: run the minimal
  financial-report analysis Agent.
- `POST /api/pro-research/evals/run`: run the built-in citation/refusal
  regression set for a report.

Run the focused backend tests with:

```bash
PYTHONPATH=backend backend/.venv/bin/python -m pytest backend/deepfocus_api/tests/test_professional_research.py -q
```
