# AI fund stock factors

The static `/ai-fund` page reads `/api/ai-fund/stock-factors?symbol=300760` for
current market factors. Holding details show both cards; expanded historical
trades open the same cards in a dialog. Historical decision scores retain their
original timestamp and scale. Current scores are separate 0–100 rule indices.

The endpoint only reads fixed Eastmoney hosts. It never runs a strategy, invokes
AI, accesses an account or changes positions. Invalid stock codes return 400.
The strategy tick route remains private.

- Capital requires five dated observations and uses
  `50 + 50 * tanh(net_flow_5d / 200000000)`; a valid zero is 50, missing is null.
- Chips use 201 price bins with daily triangular price mass and historical
  turnover decay. At least 120 valid bars and 80% modeled coverage are required.
  Unknown initial history remains a separate residual. The UI labels chips as
  model estimates and exposes the rule, dates and source. Intraday bars may be
  incomplete as of retrieval.
- Source identity, dates, OHLC, turnover and finite numbers are validated. Data
  older than seven calendar days is not scored. Trading-calendar completeness
  is not independently established.
- Two source reads run concurrently with independent 4.8-second deadlines;
  failure of one preserves the other. There are four stock collection slots,
  eight pending symbols, a 64-symbol cache and single-flight reads. Successful
  results cache for 60 seconds; complete failures for 15 seconds. One client
  cancelling cannot cancel another client's shared read.
- The browser has an 8-second timeout and rejects late responses after a stock,
  strategy or view change. Selected-stock polling refreshes the 60-second cache.

Run offline validation:

```sh
PYTHONPATH=backend python3 -m pytest -q backend/deepfocus_api/tests/test_stock_factor_scoring.py backend/deepfocus_api/tests/test_ai_fund_factors.py
node tools/ai-fund-factors/test-ui.cjs
```

## Production verification, 2026-10-19

47 Python tests passed using production Python 3.11.13 against a frozen copy of
the current production sources with only these exact patches. UI tests passed,
including null/zero handling, all historical score dimensions, cancellation,
cache expiry, timeout and dialog focus. Desktop and 390 px layout checks passed.

Published two new modules, one route block, one exact public-path entry, and the
standalone HTML. Production engine code and CRA assets were not copied from the
working tree. Source and consistent SQLite backups are retained at
`/root/stock-factors-release-20261019` on the server. The restart gate matched
candidate release ID
`e4c0600eae8d2dd61a537b61a9c7fa13fb13a3da51545ac2ea184429db391e31`.
All 24 health probes passed during the switch (maximum 23.8 ms).

Verified anonymous public GET 200, invalid symbol 400, missing symbol 422,
protected tick GET 401, readiness and source hashes. No tick POST was made.
Actual browser verification covered holding-stock selection, historical scores,
the current-factor dialog, focus restoration and no script errors.

At verification time the production server's two upstream reads were disconnected
by the provider. Real production scores therefore remained null, with explicit
missing-data cards. The Mac could retrieve real observations, but its successful
sample and numeric screenshots were not presented as production results. No
manually seeded scores or replacement turnover proxy were published.
