# TradeFlipper Research MCP release notes

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