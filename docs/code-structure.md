# Code Structure Guide

This guide is the shortest reliable entry point for humans and AI agents that
need to understand the repository before editing it.

## Top-Level Map

| Path | Purpose | Edit Notes |
| --- | --- | --- |
| `src/` | React + TypeScript desktop/web client. | Start at `src/App.tsx`, then follow `components/`, `services/`, `state/`, and `utils/`. |
| `backend/deepfocus_api/` | FastAPI API used by the client. | `main.py` mounts routes; domain modules hold persistence, adapters, and analysis logic. |
| `backend/finogrid/` | Merged Finogrid backend, SDK, compliance, and partner execution code. | Treat as a separate backend subsystem. |
| `modules/research-workbench/` | Standalone Node research-workbench module proxied by FastAPI. | Keep tool UI/server changes inside this module unless the main app integration changes. |
| `docs/` | Architecture notes and implementation guides. | Prefer adding durable orientation here instead of long comments in code. |
| `e2e/` | Playwright smoke and layout tests. | Use for user-facing workflow or viewport regressions. |
| `artifacts/`, `tmp/`, root PDFs/APKs | Generated analysis output, visual evidence, temporary files, or packaged binaries. | Do not use these as source-code entry points. |

## Frontend Reading Order

1. `src/index.tsx`: React root, router/provider bootstrapping.
2. `src/App.tsx`: top-level app state, login/session flow, route-level layout wiring.
3. `src/state/appReducer.ts`: app-state transitions for watchlist, posts, cart, orders, and demo session.
4. `src/config/workspaces.ts`: single source of truth for workspace sections, sidebar menu mapping, and workspace tabs.
5. `src/components/TradingLayout.tsx` and `src/components/MainContent.tsx`: shell navigation and current-view dispatch.
6. `src/services/README.md`: API client/service domain map.
7. `src/types/index.ts`: shared UI/domain types.
8. `src/utils/`: reusable pure helpers such as market segmentation and stock-pool transformations.

## Frontend Service Boundaries

The old one-file-per-screen service pattern has been collapsed into broader
domain services. Prefer adding APIs to the existing matching domain file:

| Service | Owns |
| --- | --- |
| `apiClient.ts` | Axios setup, auth headers, base-URL fallback, typed HTTP helpers. |
| `agentService.ts` | Agent tasks, orchestrator chat, Dulus runtime, research loop streaming. |
| `researchService.ts` | AI research, FinGPT tasks, model config, report upload/RAG/evals, research workbench calls. |
| `marketService.ts` | Quotes, symbol search, market data layers, options signals, premarket opportunities. |
| `earningsService.ts` | US/HK earnings calendar and China earnings skills. |
| `eventService.ts` | Major events, shareholder changes, realtime messages. |
| `infrastructureService.ts` | System readiness, data sources, evidence items, MCP servers/tools. |
| `riskService.ts` | Positions, Greeks, limits, PnL, risk summary. |
| `specializedService.ts` | Supply-chain, customs trade, multi-market decision, payments. |
| `marketDashboardService.ts` | Global and A-share dashboard summary/analysis APIs. |
| `officialNewsService.ts` | CCTV/official-news APIs. |

## Backend Reading Order

1. `backend/deepfocus_api/main.py`: FastAPI app, route surface, cross-module orchestration.
2. `backend/deepfocus_api/schemas.py`: shared request/response contracts.
3. `backend/deepfocus_api/model_config.py` and `llm.py`: model-provider selection and LLM calls.
4. Domain modules by route family:
   - agents: `agent_runtime.py`, `agent_loop.py`, `agent_events.py`, `agent_engines/`
   - research: `professional_research.py`, `research_workbench.py`, `report_url_ingest.py`
   - data: `data_sources.py`, `market_data.py`, `tushare_data.py`, `official_news.py`
   - skills: `cn_earnings_skill.py`, `major_event_skill.py`, `shareholder_change_skill.py`
   - trading/risk: `backtest_*`, `risk_management.py`, `options_signal.py`, `market_dashboard.py`
5. `backend/deepfocus_api/tests/`: focused regression tests for backend behavior.

## Editing Rules For This Repo

- Keep React components focused on rendering and interaction wiring.
- Keep workspace/sidebar/tab membership in `src/config/workspaces.ts`; do not duplicate it in layout or tests.
- Put API contracts and request functions in `src/services/`, grouped by domain.
- Put pure frontend transformations in `src/utils/`; avoid duplicating constants across `App.tsx` and reducers.
- Keep backend route handlers thin when possible; move durable logic into domain modules.
- Treat SQLite databases, caches, screenshots, PDFs, APKs, and generated CSVs as runtime/output data unless a task explicitly asks to edit them.
- Run `npm run lint` after TypeScript/frontend structural changes.
- Run focused `pytest` tests for touched backend modules when Python behavior changes.
