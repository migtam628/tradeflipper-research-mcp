# Opportunity discovery agent (v0.3) and Overview API contract

Read-only. The agent scans a configurable universe, rejects stale data, ranks candidates, qualifies
long options, and **persists a structured snapshot**. It never places, modifies or cancels orders, never
touches broker settings, TradeFlipper plans, watchlists or the TradeFlipper database (the watchlist scope
reads it, exactly as `screen_stocks` does).

## Three separate evidence blocks (never blended)

| Block | Meaning | Not |
|---|---|---|
| `stock_technical` | EMA/RSI/volume alignment score 0-100, bias, indicators | a probability or forecast |
| `rule_triggers` | Confirmed TradeFlipper strategy matches on **completed** candles (`confirmed`, `signals[]`) | an order or instruction |
| `options.contracts[].liquidity_score` | Displayed spread/volume/open-interest composite | expected return, fill odds, or a verified quote |

There is **no combined score**. Ordering within a mode is: confirmed rule trigger first, then technical
score, then symbol (`rank_basis` in each mode block). `validate_snapshot` rejects any `combined_score`/
`composite_score` key, `score_is_probability: true`, `execution_enabled: true` or credential-like keys.

## Pipeline

1. **Universe** - `scope`: `symbols` (explicit list), `watchlist`, `movers`, or `universe` (S&P slice of
   `batch_size`; the next offset is stored in `state.json` so scheduled runs rotate and wrap).
2. **Scan** - existing `_prepare`/`_evaluate` helpers from `server.py` (completed candles only), in batches of <= 40.
3. **Stale-data rejection** - swing: last completed session older than `stale.max_daily_age_days` (default 4);
   day: last completed 5m candle older than `stale.max_intraday_age_minutes` (default 40); wrong interval, missing
   or future timestamps. Age is recomputed at snapshot time. Rejected symbols are listed in `rejected_stale` and
   never ranked or given option screening. Symbols with no data appear in `errors`.
4. **Options qualification** - calls for long direction, puts for short (direction: best confirmed trigger side,
   else technical bias; `mixed` is skipped). Gate: `options.require_trigger` or `options.min_technical_score`;
   at most `options.max_underlyings` per run. Uses the existing `screen_options`, then rejects contracts whose last
   trade is unknown or older than `options.max_last_trade_age_minutes` (default 60; `0` disables) and any
   non-positive/locked/crossed quote. Greeks stay `null`; `quote_freshness` is always `unknown`.
5. **Persist** - atomic JSON in `TRADEFLIPPER_OPPORTUNITY_DIR` (default `~/.local/share/tradeflipper-research/snapshots`,
   dir 0700 / files 0600, outside the repo), `latest.json`, retention `retain_snapshots` (default 200).

`status`: `ok`, `partial` (some stale/errored symbols), `empty` (no usable candidates).
`expires_at_utc` = generated + TTL (`ttl_minutes_swing` 360, `ttl_minutes_day` 15; shortest TTL of the modes run).

## Running and scheduling

```bash
deploy/run_opportunity_agent.sh run --config ~/.config/tradeflipper-research/opportunity.json   # one-shot
deploy/run_opportunity_agent.sh run --symbols AAPL,TSLA --modes swing,day
deploy/run_opportunity_agent.sh show            # Overview payload of latest snapshot
deploy/run_opportunity_agent.sh serve           # GET-only API on 127.0.0.1:8766
```

Schedule with the systemd units in `deploy/` (`opportunity-agent.service` + `.timer`) or cron. The wrapper runs with
TradeFlipper as the working directory (so its relative `./data` and `.env` resolve as in the app) and uses
`python -P` so an embedded `~/tradeFlipper/mcp_server` cannot shadow this package. Overlapping runs are prevented
with a lock file. Install: `systemctl --user enable --now opportunity-agent.timer` after copying the units to
`~/.config/systemd/user/`. Example config: `deploy/opportunity.example.json`. **No timer is installed by this repo.**

## Overview API contract - `tradeflipper.overview.opportunities/1`

Transport options (both read persisted files only; neither can scan or trade):

* Loopback HTTP, **no authentication** - bind is restricted to 127.0.0.1/::1/localhost; GET only (other verbs 405):
  `GET /healthz`, `GET /v1/opportunities/latest`, `GET /v1/opportunities/snapshots`,
  `GET /v1/opportunities/snapshots/{snapshot_id}` (full snapshot). TradeFlipper's backend should call it server-side;
  no CORS headers are sent.
* MCP tool `opportunity_snapshot(snapshot_id='')`.

`GET /v1/opportunities/latest`:

```json
{
  "contract_version": "tradeflipper.overview.opportunities/1",
  "read_only": true, "execution_enabled": false,
  "freshness": "fresh | expired | missing",
  "snapshot_id": "20261023T184722Z-6a34fd1ae5",
  "generated_at_utc": "...", "expires_at_utc": "...", "age_minutes": 12,
  "status": "ok | partial | empty | missing",
  "summary": {"candidates": 8, "confirmed_rule_triggers": 0, "options_qualified": 3, "rejected_stale": 0, "errors": 0},
  "items": [{
    "symbol": "TSLA", "mode": "swing", "rank": 2, "research_direction": "long | short | null",
    "stock_technical": {"bias": "bullish", "technical_alignment_score": 82, "score_is_probability": false, "close": 374.58, "rsi14": 52.8, "...": "..."},
    "rule_triggers": {"confirmed": false, "signals": []},
    "option_liquidity": {"status": "qualified | none_qualified | skipped | error", "right": "call", "reason": "", "underlying_reference_price": 371.62,
      "contracts": [{"contract": "...", "expiration": "2026-10-30", "strike": 395.0, "bid": 1.0, "ask": 1.05, "spread_pct": 4.9,
        "volume": 900, "open_interest": 2000, "liquidity_score": 94, "expiration_breakeven_underlying_price": 396.05,
        "expiration_breakeven_move_pct": 6.6, "moneyness": "OTM", "greeks_complete": false,
        "quote_freshness": "unknown", "executable_quote_verified": false, "last_trade_age_minutes": 17}]},
    "data_quality": {"last_completed_session_date": "2026-10-22", "quote_freshness": "unknown", "...": "..."},
    "warnings": []
  }],
  "rejections": {"swing": {"stale": 0, "errors": 0}},
  "notices": ["Research only: ..."]
}
```

Consumer rules: render `freshness` and `age_minutes` prominently; when `expired` or `missing`, `items` is `[]` and
`reason` explains why - never show cached items as current. Show the three blocks separately, never sum or average
them. Treat `quote_freshness: "unknown"` as unknown, not live. Additive fields are non-breaking; changing the meaning
of a field requires a new `contract_version`. Not yet integrated into the TradeFlipper frontend (separate reviewed change).
