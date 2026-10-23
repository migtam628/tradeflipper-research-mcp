# TradeFlipper Research MCP v0.2.0

**Local, private, research-only MCP service for Claude Code and compatible agents.**

This release **upgrades the existing four v0.1 tools** without changing their names or required parameters:

| MCP tool | Purpose |
|---|---|
| `screen_stocks` | Bounded watchlist/movers/universe research (day or swing) |
| `analyze_stock` | One stock, completed candles, technical alignment and deterministic strategy triggers |
| `screen_options` | Long call/put chain filter, liquidity comparison, cost, breakeven and moneyness |
| `paper_portfolio` | Read-only local SQLite positions, signals and closed trades; reports execution venue |

No orders, broker configuration updates, or automatic trading actions are exposed.

## What's fixed in v0.2

**Daily candle integrity.** For US-equity 1-day research, the active session's daily bar is excluded until **16:30 ET**. Indicators and strategy checks run **after** this filter. A midnight UTC Yahoo daily label denotes a **session date**, not a 17-hour-old quote. Responses include `bar_quality.incomplete_daily_bars_excluded`, `last_completed_session_date`, and separate candle-label/quote fields. This is a conservative time-based policy, not an exchange-holiday calendar or a provider publication confirmation.

**Options quote handling.** Bid=ask, crossed, missing, zero-bid and other unusable quotes are excluded. The liquidity ranking is now a 0–100 composite of spread (45%), option volume (30%), and open interest (25%) with explicit logarithmic scaling. It measures **displayed numeric liquidity only**, not expected returns, probability of fill, or profitability. Neither a positive liquidity score nor a last-trade timestamp verifies an executable quote. Yahoo doesn't supply a dependable bid/ask quote timestamp; `quote_timestamp_utc` and `quote_age_minutes` are **null**, `quote_freshness` is **unknown**.

**Contract economics.** The standard 100-share multiplier, premium cost, expiration break-even, estimated percent move to break-even, strike distance and ITM/ATM/OTM classification are returned. The underlying reference is an **unverified Yahoo value**; if it is unavailable, moneyness and percent move are reported as unknown. Greeks remain null unless actually present in data; missing Greeks trigger warnings. Breakeven applies **at expiration** and excludes commissions/fees.

**Portfolio semantics.** `paper_portfolio` reports the locally stored active execution venue. If Alpaca or Webull is selected, recorded positions may reflect broker paper fills and may be stale. This tool does **not** retrieve the live broker account.

## Install as a standalone GitHub repository

The MCP is **read-only**, but it imports the established TradeFlipper research strategies,
market-data provider, and local SQLite models. Clone the separate repository on the **same
trusted server** where TradeFlipper runs; it is not a cloud API or a standalone trading engine.

```bash
cd ~
git clone https://github.com/migtam628/tradeflipper-research-mcp.git
cd tradeflipper-research-mcp
python3 -m venv .venv-mcp
.venv-mcp/bin/python -m pip install \
  -r "$HOME/tradeFlipper/backend/requirements.txt" \
  -r mcp_server/requirements.txt pytest
.venv-mcp/bin/python -m pytest -q mcp_server/tests
claude mcp add --transport stdio tradeflipper-research \
  -- "$HOME/tradeflipper-research-mcp/mcp_server/run.sh"
claude mcp list
```

The launcher searches for TradeFlipper at `$HOME/tradeFlipper` by default. Set
`TRADEFLIPPER_ROOT=/some/other/path` in the Claude MCP process environment if it is
installed elsewhere. Restart Claude Code after changing the MCP registration.

### Existing in-tree setup

For an existing Claude configuration using
`$HOME/tradeFlipper/mcp_server/run.sh`, you can retain that location and install
this release into the main TradeFlipper checkout without changing its Python/Docker
service. The standalone repository is the **source of truth** for the MCP and can
be synchronized explicitly; never overwrite uncommitted modifications.

```bash
cd ~/tradeFlipper
cp -a mcp_server mcp_server-backup-v0.1
cp -a ~/tradeflipper-research-mcp/mcp_server/. mcp_server/
.venv-mcp/bin/python -m pip install \
  -r backend/requirements.txt -r mcp_server/requirements.txt pytest
.venv-mcp/bin/python -m pytest -q mcp_server/tests
# Restart Claude Code; registration path remains unchanged.
```

The repository **does not contain any TradeFlipper environment variables, broker
credentials, database records, or live trading configuration**.

### Test prompts

> Use tradeflipper-research `screen_stocks` in swing mode for SPY,AAPL,NVDA,TSLA. Show the completed session date and how many partial daily candles were excluded. No orders.

> Use tradeflipper-research `screen_options` on AAPL calls expiring in 7–45 days, max premium $500. Compare liquidity scores, breakeven and moneyness; show quote freshness as **unknown** if timestamps aren't available. No orders.

> Run `paper_portfolio` and report the selected execution venue and whether the displayed balances/positions are from a live broker request (they are not).

### Rollback

```bash
cd ~/tradeFlipper
cp -a mcp_server-backup-v0.1/. mcp_server/
# Restart Claude Code
```

## Operating limits and safeguards

- **Data sources:** TradeFlipper's configured stock data provider (Yahoo/Alpaca), Yahoo's potentially delayed options chains.
- **Coverage:** Up to 40 tickers in each market-universe batch. Repeated batches are needed for broad coverage.
- **Time:** 16:30 ET is a conservative regular-session daily finalization rule; it doesn't account for special closes, holidays, or vendor-delayed publication. Always verify the last completed session date.
- **Options quotes:** Yahoo data is not validated executable bid/ask. A quote's *last trade* is not its quote timestamp. Live entitled chain data (with quote timestamps/Greeks) is a future feature.
- **Options events:** Earnings/FOMC/news/event calendars are **not** yet screened automatically. Do not infer an option contract is event-safe.
- **Long option risk:** Premium may be lost entirely; event volatility, early exit, spread, slippage and fees can materially affect returns.
- **Scoring:** Technical score and liquidity score are **not** probabilities, AI predictions, or signals to execute.
- **Connectivity:** Preferred `stdio` via run.sh. Optional loopback HTTP `127.0.0.1:8765/mcp` has **no built-in authentication** and must never be Internet-exposed.
- **Broker privileges:** No placement, cancellation, modification or live account access through these MCP tools.
- **Automation:** MCP provides research tools to an agent; it does not independently schedule tasks or start trades.

## Development checks

```bash
cd ~/tradeflipper-research-mcp
.venv-mcp/bin/python -m pytest -q mcp_server/tests
.venv-mcp/bin/python -m compileall -q mcp_server
```

## Next steps (not included in v0.2)

1. Broker/entitled option-chain adapter with verified quote timestamps and Greeks.
2. Earnings/calendar catalyst flags and explicit max-staleness rejection.
3. Reproducible, paper-only option payoff/backtest analysis and reviewable AI market-wide shortlists.