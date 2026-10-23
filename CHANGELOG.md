# TradeFlipper Research MCP release notes

## v0.3.0 (draft, branch `feature/v0.3-opportunity-agent`) — 2026-10-23
- Adds a read-only scheduled opportunity pipeline: configurable universe (`symbols`/`watchlist`/`movers`/rotating `universe`), swing and/or day ranking, options qualification, stale-data rejection.
- Keeps stock technical score, confirmed rule triggers and option liquidity in **separate** blocks; no combined score (enforced by `validate_snapshot`).
- Persists structured, atomically written snapshots (0600, outside the repo) with retention, `latest.json` and universe-rotation state.
- Adds the Overview contract `tradeflipper.overview.opportunities/1`: loopback-only GET-only HTTP API and MCP tool `opportunity_snapshot`; expired/missing snapshots return no items.
- Adds `opportunity_agent` CLI (`run|show|serve|prune`), run lock, `deploy/` wrapper, systemd service/timer and example config (nothing is installed automatically).
- Refactors `screen_stocks` universe resolution into `_resolve_pool` (behaviour unchanged) for reuse.
- Adds 33 offline tests (**47 total**). Existing four tools and their arguments are unchanged.

**Not included:** execution, broker writes, frontend Overview integration, earnings/news gates, licensed option quotes/Greeks, authenticated HTTP. Live data from Yahoo; option quote times remain unknown.

## v0.2.0 — 2026-10-23
- Excludes unfinished US-stock daily bars **before** calculating indicators/signals.
- Treats midnight UTC daily bar timestamps as **session-date labels**, not quote timestamps.
- Distinguishes daily session age, intraday candle age and unavailable quote age.
- Replaces saturated option liquidity rankings with spread/volume/open-interest composite.
- Excludes locked/crossed/zero-bid option quotes from screen results.
- Keeps options bid/ask quote timestamps **unknown** rather than inferring from last trade.
- Adds option moneyness, expiry breakeven, reference underlying price and missing-Greeks warnings.
- Marks local portfolio records with active simulator/Alpaca/Webull paper venue (no live account fetch).
- Adds 9 offline regression tests on top of v0.1's 5, total **14 passing locally**.

**Not included:** automated trades, trade-advice model, real-time executable quotes, live broker-account data, earnings/news event guard, scheduling.