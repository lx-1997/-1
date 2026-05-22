# TradingAgents Runtime

This directory owns the embedded TradingAgents runtime used by DeepFocus Agent Desk.

The Python environment is intentionally isolated from `backend/.venv` because TradingAgents requires Python 3.10+ while the existing backend runtime may be older.

Install or refresh it with:

```bash
npm run tradingagents:install
```

The backend auto-discovers:

```text
modules/tradingagents-runtime/.venv/bin/python
```

Override it with `DEEPFOCUS_TRADINGAGENTS_PYTHON` when needed.

Model provider, model name, API key, and base URL are read from the shared
DeepFocus model config (`Settings -> Model Config`). The `DEEPFOCUS_TRADINGAGENTS_*`
environment variables are only needed for per-runtime overrides.

For full multi-agent runs, start the backend with:

```bash
npm run backend:long
```

That mode runs without `uvicorn --reload`, keeps the parent worker alive for
long external subprocesses, and allows the Agent Desk heartbeat to refresh task
status while TradingAgents is still working.

DeepFocus also injects web research tools into the TradingAgents `news` and
`social` analysts:

- `deepfocus_web_search`: searches the public web for ticker news, filings,
  press releases, regulation, competitors, and macro catalysts.
- `deepfocus_read_url`: reads a public URL and returns extractive text when the
  page is accessible without login or heavy JavaScript.

Search uses provider API keys when present (`TAVILY_API_KEY`, `SERPER_API_KEY`,
`BRAVE_SEARCH_API_KEY`) and falls back to public Bing RSS / DuckDuckGo HTML.
You can disable it per task with `engine_config.web_search_enabled=false`.
