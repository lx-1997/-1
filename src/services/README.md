# Frontend Services

`src/services/` is the client API layer. Components should call these functions
instead of constructing URLs or using `axios` directly.

## Core Pattern

- `apiClient.ts` owns Axios interceptors, auth token injection, error formatting,
  and API base-URL fallback.
- Domain service files export TypeScript response/request types beside the
  functions that use them.
- Components import the narrow service they need, for example
  `../services/marketService` for quotes or symbol search.
- Mocks live in `src/services/__mocks__/` for Jest tests.

## Domain Files

| File | Use For |
| --- | --- |
| `agentService.ts` | Agent runtime health, investment tasks, SSE events, orchestrator/general chat, Dulus tools/memory, research loop streaming. |
| `researchService.ts` | AI stock analysis, FinGPT tasks, model config, file extraction, professional report library, RAG, evals, workbench search/downloads. |
| `marketService.ts` | Market quotes/search, data-layer status, A-share structured data, options signals, premarket opportunities. |
| `earningsService.ts` | Earnings calendar and China earnings scan/diagnosis/detail APIs. |
| `eventService.ts` | Major event scans, shareholder changes, realtime message APIs and streams. |
| `infrastructureService.ts` | Readiness checks, data-source registry, evidence items, keyword crawl, MCP server/tool operations. |
| `riskService.ts` | Portfolio positions, Greeks, limits, PnL, and risk summaries. |
| `specializedService.ts` | AI supply-chain trends, customs trade, multi-market decision, payment flow. |
| `marketDashboardService.ts` | Market dashboard and dashboard AI analysis endpoints. |
| `officialNewsService.ts` | Official CCTV news endpoints. |

## Adding A New API

1. Add the request/response types in the closest existing domain file.
2. Use `apiGet`, `apiPost`, `apiPatch`, or `apiDelete` from `apiClient.ts`.
3. Keep endpoint strings in the service function, not in the component.
4. Add or update a focused test when the API call has parsing, fallback, or
   user-visible error behavior.
