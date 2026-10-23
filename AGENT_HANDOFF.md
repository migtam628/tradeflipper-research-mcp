# AGENT_HANDOFF.md — TradeFlipper Research MCP

**Project:** `migtam628/tradeflipper-research-mcp` (public)  
**Release:** v0.3.0 on `main`; PR #1 (opportunity agent) and PR #2 (launcher fix) are merged  
**Last reviewed:** 2026-10-23  
**Status:** Python 3.12 GitHub Actions verified the combined v0.3 + launcher fix on commit `3c8d64e` (pytest and compileall passed). Server installation, TradeFlipper database initialisation, active Claude Code MCP registration, and systemd timer are **not yet verified**.

This document is the **persistent handoff contract for any AI agent/model** (Claude Code, Codex, GPT, Gemini, etc.). Read it **before making code changes**, and update it **after each meaningful work session**. Do not treat an older agent's statements or a passing connection check as runtime proof.

## 1. Mission and scope

Build a **research-only** Model Context Protocol server for the TradeFlipper application. It identifies *candidates worth further investigation* in US equities and long options, provides transparent indicators and warnings, and permits the human to assess next steps. A technical alignment score or option liquidity score is **not** a predicted return, win probability, or instruction to trade.

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
| `opportunity_snapshot` (v0.3) | Return the latest/named **persisted** opportunity snapshot (Overview payload); no scan | None |

The scheduled scanner is the CLI `python -m mcp_server.opportunity_agent run` (one-shot, for systemd/cron). It writes snapshot files only. See `docs/OPPORTUNITY_AGENT.md`.

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
8. **Separate evidence (v0.3):** `stock_technical` score, `rule_triggers` and option `liquidity_score` are separate blocks; never add a combined/blended score. Expired snapshots must yield no Overview items.
9. **Backend safety:** do not bypass or dilute protections in `tradeFlipper` while improving discovery.

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
4. ~~Add a broad-universe discovery scheduler~~ **Implemented and merged in v0.3 on `main`** (offline-tested + one live smoke run); the timer is **not installed** anywhere and must be reviewed/enabled by the owner. Rate limits/licensing still apply.
5. Integrate reviewable candidate lists into TradeFlipper Overview only via an explicit, separately tested integration. The v0.3 API contract exists; **no frontend/backend integration has been made**.
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
- The full v0.2 source, tests, docs, launcher, GitHub test workflow, and agent entrypoints **are pushed to this private repository's `main` branch**, verified through its Git tree at commit `4e7353f06fbfd985aef08bfe37de97d64d866a1b`.
- Locally reviewed v0.2 package: `python -m pytest -q mcp_server/tests` → **14 passed**. The exact GitHub blobs for `server.py`, `research.py`, and both test files match the local source.
- No GitHub Actions workflow run was returned at this check; do not claim CI passed. Independent v0.2 Claude connection + live tool smoke tests on the owner's Ubuntu server **remain unverified**.
- **No orders were requested or placed by this research MCP.**

Keep this file concise, factual, and current. If agent notes disagree, prefer checked source + test output over conversational claims.

## 10. Outgoing-agent record — v0.3 opportunity agent session

- **UTC timestamp and agent/model:** 2026-10-23 ~19:00Z, Claude Code (Claude Sonnet 5.5).
- **Repository / branch / HEAD SHA:** `migtam628/tradeflipper-research-mcp`, branch `feature/v0.3-opportunity-agent`, based on `main` @ `2e33828`; head SHA = the PR head (a commit cannot contain its own SHA).
- **User goal & constraints:** read-only scheduled opportunity discovery (configurable universe, day/swing ranking, option qualification, stale rejection, persisted snapshots, Overview API contract); keep technical score / rule triggers / option liquidity separate; no orders, no broker or config changes; tests, docs, draft PR.
- **Changes committed:** `mcp_server/opportunity.py` (pipeline, config, stale rejection, validation, Overview payload), `snapshot_store.py`, `overview_api.py`, `opportunity_agent.py`, `server.py` (`_resolve_pool` refactor + `opportunity_snapshot` tool), `deploy/*`, `docs/OPPORTUNITY_AGENT.md`, `tests/test_v03.py`, README/CHANGELOG/this file.
- **Tests executed and exact results:** `pytest -q mcp_server/tests` → **47 passed** both in the full TradeFlipper venv (Python 3.14.4) and in a clean venv with only pandas+pytest (Python 3.14.4, no `mcp`; matches the CI install set). `compileall` OK. GitHub Actions on PR #1 (after the repo was made public; the first run failed to start because of an Actions budget limit) ran `MCP offline tests` on Python 3.12: **47 passed**; GitGuardian passed. Run URL: https://github.com/migtam628/tradeflipper-research-mcp/actions/runs/40006424083
- **Runtime/Claude MCP connection verification:** Over stdio with the `mcp` client, the server lists 5 tools (4 original + `opportunity_snapshot`); `opportunity_snapshot` returned a fresh payload. A live one-shot `run` on 8 explicit symbols (Yahoo, 2026-10-23) produced an `ok` snapshot (8 candidates, 0 confirmed rule triggers, 3 option underlyings qualified, 0 stale) and the loopback API returned 200 for GET and 405 for POST; `0.0.0.0` bind was refused. **Claude Code itself is not registered to this clone** (registration points to the older `~/tradeFlipper/mcp_server/run.sh`, scoped to that project).
- **Known bugs, risks, blockers:** (1) `mcp_server/run.sh` does `cd` into this clone, so the backend's relative `./data/tradeflipper.db` and `.env` resolve against the clone, not TradeFlipper: `paper_portfolio` failed with `no such table: account` (empty DB created in the clone). Not fixed here (launcher change needs review); `deploy/run_opportunity_agent.sh` uses TradeFlipper as CWD. (2) `~/tradeFlipper/data/tradeflipper.db` was a 0-byte file when checked: TradeFlipper DB appears uninitialised, so `watchlist` scope and `paper_portfolio` are unverified. (3) No strategy has produced a confirmed trigger in any live scan so far; trigger paths are covered by fake signals only. (4) Yahoo option quote times/Greeks unavailable; contract freshness relies on last-trade age. (5) Overview HTTP API has no authentication (loopback only). (6) The root disk filled to 100% earlier in the session (freed ~8GB by removing two Rust `target/` dirs outside this repo).
- **Deployment state:** local: branch in working tree and committed; GitHub: draft PR only (not merged); server: **no timer/service installed**, no MCP re-registration.
- **Next 1-3 actionable tasks:** (a) fix/replace `run.sh` working-directory handling and verify `paper_portfolio` against an initialised TradeFlipper DB; (b) owner review, then install the systemd timer and register the v0.3 server for Claude Code; (c) add an authenticated, separately reviewed TradeFlipper Overview integration and earnings/event gates.
- **Reproduction commands (redacted):** `TRADEFLIPPER_MCP_PYTHON=<venv python> deploy/run_opportunity_agent.sh --dir <scratch> run --symbols AAPL,TSLA`, then `show`; tests: `python -m pytest -q mcp_server/tests`.
- **Secrets encountered:** none read or printed.


## 11. Release handoff — merged v0.3 (2026-10-23)

- **Current GitHub repo:** public; `main` includes PR #2 (launcher working directory) at squash commit `f9477308` and PR #1 (v0.3 discovery agent) at squash commit `e400bd4`.
- **Combined integration check:** GitHub Actions Python 3.12 `MCP offline tests`, commit `3c8d64e` (v0.3 branch rebased via merge from updated `main`) completed successfully: pytest and compileall. Run: https://github.com/migtam628/tradeflipper-research-mcp/actions/runs/40033918977
- **Launcher fix:** `mcp_server/run.sh` now changes to `$TRADEFLIPPER_ROOT` / `~/tradeFlipper` and starts with `python -P` while exposing this clone through `PYTHONPATH`. The regression test is merged. Does not by itself create or initialize the backend DB.
- **Functional surface:** read-only `screen_stocks`, `analyze_stock`, `screen_options`, `paper_portfolio`, `opportunity_snapshot`; optional one-shot scheduler and loopback-only GET API for persisted snapshots. No order execution or broker configuration writes added.
- **Server verification outstanding:** TradeFlipper database was reportedly 0 bytes in the last server log; run the normal TradeFlipper application startup/init process and confirm its actual DB path, then re-test `paper_portfolio` and watchlist scope. Do not assume GitHub CI can test this DB.
- **Deployment outstanding:** `~/tradeflipper-research-mcp` must pull `main`, its virtualenv must be installed/tested, and Claude Code MCP registration must point to its `mcp_server/run.sh` rather than the old embedded copy. No evidence a timer is installed; enable it only after successful one-shot snapshot review.
- **Known limits:** Yahoo option bid/ask quote timestamps/Greeks unavailable; options scores are not trading probabilities; no verified live rule-trigger occurrence; Overview frontend integration and authenticated HTTP access remain separate reviewed work.
- **Next steps:** update checkout; verify DB and 5 MCP tools; run and inspect a one-shot snapshot; then optionally enable the timer. Preserve this record when switching Claude/Codex/Gemini/GPT.
