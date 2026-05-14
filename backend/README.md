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

The React app calls `http://localhost:8300` by default.

## Market data mode

The frontend refreshes quotes from `GET /api/market/quotes?symbols=TSLA,NVDA`
after login and then polls every two minutes. Configure free API keys when you
want live quote providers:

```env
FINNHUB_API_KEY=your_finnhub_key
ALPHAVANTAGE_API_KEY=your_alpha_vantage_key
```

Provider order is Finnhub, Alpha Vantage, then a no-key Stooq public snapshot
fallback. If all providers fail, the UI keeps the local sample stock universe
but labels it as sample data instead of presenting it as live market data.

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
MINIMAX_MODEL=MiniMax-M2.7
```

`DEEPFOCUS_LLM_PROVIDER=mock` keeps local development runnable without GPU or API
keys. It is useful for UI work, not production analysis.
