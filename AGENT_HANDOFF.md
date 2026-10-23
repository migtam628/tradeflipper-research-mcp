# AGENT_HANDOFF.md — TradeFlipper Research MCP

**Project:** `migtam628/tradeflipper-research-mcp` (private)  
**Release:** v0.2.0  
**Last reviewed:** 2026-10-23  
**Status:** Local offline tests: **14 passed**. Claude Code connected to v0.1 previously; v0.2 standalone installation / live market calls are **not yet independently verified**.

This document is the **persistent handoff contract for any AI agent/model** (Claude Code, Codex, GPT, Gemini, etc.). Read it **before making code changes**, and update it **after each meaningful work session**. Do not treat an older agent's statements or a passing connection check as runtime proof.

## 1. Mission and scope

Build a private **research-only** Model Context Protocol server for the TradeFlipper application. It identifies *candidates worth further investigation* in US equities and long options, provides transparent indicators and warnings, and permits the human to assess next steps. A technical alignment score or option liquidity score is **not** a predicted return, win probability, or instruction to trade.

**Never add order placement, order cancellation/modification, automatic broker execution, credential export, or live broker-account changes** without a new, separately reviewed and explicitly authorized design. This server must not make changes to TradeFlipper trading plans, positions, risk limits, watchlists, or Webull/Alpaca configurations.

## 2. Repository boundaries and source of truth

- **This repo:** MCP interface (`mcp_server/server.py`), deterministic research helpers (`mcp_server/research.py`), tests, launcher, docs, CI.
- **Companion repo:** `migtam628/tradeFlipper` supplies the Python `backend/app` market-data providers, indicators, strategies, local DB and configuration.
- **Do not duplicate TradeFlipper's trading engine here** or silently change the companion repository. If a backend API is incompatible, document the required version and create an explicit separate change.
- Default server workspace: `~/tradeFlipper`. Optional override `TRADEFLIPPER_ROOT`.
- Preferred connection: **local stdio** (`mcp_server/run.sh`); optional loopback-only HTTP must never be exposed externally without an authentication design.
- The clone path may be `~/tradeflipper-research-mcp`; existing Claude config may still target `~/tradeFlipper/mcp_server/run.sh`. Before modifying either, check what executable Claude is actually using.

## 3. Current public MCP tool contracts (names are stable)

| Tool | Purpose | Side effects |
|---|---|---|
| `screen_stocks` | Read-only day/swing scan of explicit symbols, watchlist, movers or bounded universe slice | None |
| `analyze_stock` | Analyze one symbol using completed bar inputs + TradeFlipper's deterministic signals | None |
| `screen_options` | Read long call/put options chains; premium/expiry/volume/OI/spread screens; liquidity + economics | None |
| `paper_portfolio` | Read the **local** TradeFlipper SQLite paper records and report execution venue | None |

Preserve these tool names and argument compatibility across agent migrations. Add new fields with documented semantics; avoid changing output meaning silently. Never log, serialize or commit API keys, account IDs, tokens, personal data, databases or secrets.

## 4. What v0.2 actually changes

- Excludes an active US stock daily candle until after **16:30 ET** before calculating indicators or confirmed rule signals. Midnight-UTC daily stamps are **session date labels**, not precise quote timestamps.
- Returns `bar_quality`, completed session date and partial-candle exclusion counts.
- Excludes locked/crossed, missing, zero-bid and otherwise unusable options quotes from scored candidates. Liquidity scoring combines displayed spread, volume and open interest; it is **not** a probability or a reliable fill forecast.
- Options quote time is **unknown** when using Yahoo. Last-trade time must never be substituted for a quote timestamp. Missing Greeks remain `null`; no invented delta/theta/gamma.
- Long-option cost and expiration break-even use the standard multiplier of 100; moneyness and break-even percentage depend on an **unverified stock reference price** and are labeled accordingly.
- `paper_portfolio` indicates whether local positions correspond to the built-in simulator or a broker paper venue; it **does not retrieve broker-live positions**.

## 5. Safety, correctness and data-quality invariants

1. **Research-only:** no new broker write capability, trades, watchlist mutations, plan updates or background order loops.
2. **Completed candles first:** filter incomplete bars **before** indicators, scores and strategy evaluations. Warn on absent/old data; explain timezone/session-date interpretation.
3. **Quote freshness:** distinguish candle label, last trade, actual bid/ask quote time, and scan time. When the provider cannot prove freshness, say **unknown**, never "live".
4. **Options contracts:** skip suspect locked/crossed markets, negative or zero bids/asks, bad expirations and unusable quotes; no implied guarantees from "low premium" or score 100.
5. **No fabricated risk statistics:** absent Greeks, earnings-event dates or execution probabilities are explicitly missing.
6. **Isolation:** do not print credentials or secrets; do not include `.env`, private keys, SQLite DBs, data caches or venvs in commits.
7. **No implied autonomy:** connection to MCP means a client can call tools; it does **not** mean the agent is scheduled to scan, trained to select winners or authorized to trade.
8. **Backend safety:** do not bypass or dilute protections in `tradeFlipper` while improving discovery.

## 6. Verification commands

```bash
cd ~/tradeflipper-research-mcp
python3 -m venv .venv-mcp
.venv-mcp/bin/python -m pip install \
  -r ~/tradeFlipper/backend/requirements.txt \
  -r mcp_server/requirements.txt pytest
.venv-mcp/bin/python -m pytest -q mcp_server/tests
.venv-mcp/bin/python -m compileall -q mcp_server
claude mcp list
```

For existing *embedded* installs, run the tests from `~/tradeFlipper` and verify the registered `run.sh` path.

**Tests are not sufficient for release:** independently invoke all four tools against an appropriate non-production test configuration. Verify stock data dates, delayed/unknown option quote times, rejected locked quotes, no broker writes, and accurate local/broker-venue labeling.

## 7. Current known limitations / prioritized backlog

1. Verify the standalone `run.sh` and `mcp.server` import/runtime against installed `mcp>=2,<3`, plus Claude Code smoke tests; fix incompatibilities before calling the deployment healthy.
2. Improve option-chain data with licensed timestamped bid/ask and Greeks; Yahoo options are **not executable quotes**.
3. Add earnings/calendar/news-event gates and max-staleness handling for unknown/old data.
4. Add a broad-universe discovery scheduler with human-readable outcomes **without execution permission**, keeping rate limits and per-source licensing in mind.
5. Integrate reviewable candidate lists into TradeFlipper Overview only via an explicit, separately tested integration.
6. Add contract-level payoff/expiry modeling and tests; avoid presenting liquidity scores as expected profit.
7. Ensure all build/test workflow jobs **actually execute**. Earlier GitHub Actions runs in the companion repo sometimes failed before test steps; no green result should be inferred from that.

## 8. Protocol for switching AI agents/models

### Incoming agent — required checklist

1. Read `AGENT_HANDOFF.md`, `README.md`, `CHANGELOG.md` and the relevant source/tests.
2. Get `git status --short`, `git branch --show-current`, and `git log -5 --oneline`; identify the **exact active repo** and branch. Do not assume a past chat reflects the latest GitHub state.
3. Confirm actual Claude MCP registration/launcher and companion TradeFlipper backend revision. Never assume the v0.2 standalone clone is active because v0.1 was connected.
4. Run existing tests **before modifications** and record exact counts, failures and environment.
5. State what is verified, what is unverified and what changes are planned. Keep work on a feature branch if conflicting changes or unreviewed broker-adjacent functionality is involved.
6. Preserve the four MCP tools, read-only safeguards, and explicit unknown-data flags.
7. After changes: add regression tests, run them, review diff for accidental secrets, update README/CHANGELOG/this file, commit and report the exact SHA. Never claim a deployment or connected tool test unless actually observed.

### Outgoing agent — fill out at the end of each session

- **UTC timestamp and agent/model:**
- **Repository / branch / HEAD SHA:**
- **User goal & constraints:**
- **Changes committed (files and commit links):**
- **Tests executed and exact results:**
- **Runtime/Claude MCP connection verification:**
- **Known bugs, risks, blockers, missing data:**
- **Deployment state:** local / GitHub / server (separate facts)
- **Next 1–3 actionable tasks:**
- **Reproduction commands (redacted):**
- **Secrets encountered:** do not write values; note only if user action is needed.

## 9. Current handoff snapshot (2026-10-23)

- A v0.2 ZIP was assembled and its offline regression suite reported **14 passed**.
- User successfully connected the earlier `tradeflipper-research` MCP in Claude Code and obtained stock and AAPL options screens from v0.1.
- Those v0.1 reports showed unfinished daily candles scored as if final, misleading all-100 options liquidity scores, zero-spread quotes and missing Greeks; v0.2 specifically addresses data integrity and honest scoring.
- A standalone private GitHub repo was created to house this version. GitHub push and independent installed-server smoke tests should be verified explicitly.
- **No orders were requested or placed by this research MCP.**

Keep this file concise, factual, and current. If agent notes disagree, prefer checked source + test output over conversational claims.
