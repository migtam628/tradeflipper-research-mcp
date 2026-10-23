#!/usr/bin/env bash
# Scheduled read-only opportunity scan. Runs with the TradeFlipper checkout as CWD so the
# backend's relative ./data/tradeflipper.db and .env resolve exactly as TradeFlipper does.
set -euo pipefail
MCP_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TF_HOME="${TRADEFLIPPER_ROOT:-$HOME/tradeFlipper}"
[[ -d "$TF_HOME/backend/app" ]] || { printf 'TradeFlipper backend not found at %s\n' "$TF_HOME" >&2; exit 1; }
export TRADEFLIPPER_ROOT="$TF_HOME" PYTHONPATH="$MCP_ROOT${PYTHONPATH:+:$PYTHONPATH}"
PYTHON="${TRADEFLIPPER_MCP_PYTHON:-$MCP_ROOT/.venv-mcp/bin/python}"
cd "$TF_HOME"
# -P: do not put CWD on sys.path, or an embedded ~/tradeFlipper/mcp_server (old v0.1) would shadow this repo.
exec "$PYTHON" -P -m mcp_server.opportunity_agent "$@"
