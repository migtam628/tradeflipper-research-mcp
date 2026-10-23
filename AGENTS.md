# Agent instructions

**First read [AGENT_HANDOFF.md](AGENT_HANDOFF.md)**, then README.md, CHANGELOG.md, source and tests. AGENT_HANDOFF.md is the single shared, model-independent handoff and operating contract.

- Confirm repository, current branch, HEAD, working tree, companion TradeFlipper backend version, active MCP launcher, and actual verification status.
- Preserve all four public research tools and their argument compatibility.
- The MCP is strictly **research-only**. Never place orders, modify a broker account, log secrets, or change TradeFlipper trading plans.
- Do not call unknown data live or treat heuristic scores as win probabilities.
- Run offline regression tests and explicitly distinguish local tests from Claude/tool/runtime integration checks.
- Before finishing, update AGENT_HANDOFF.md's outgoing-agent section and release notes with **verified** changes, exact test results, blockers and next tasks.
