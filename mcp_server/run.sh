#!/usr/bin/env bash
# Standalone and embedded TradeFlipper Research MCP launcher (stdio).
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
if [[ -d "$ROOT/backend/app" ]]; then
  TF_HOME="$ROOT"
elif [[ -n "${TRADEFLIPPER_ROOT:-}" && -d "${TRADEFLIPPER_ROOT}/backend/app" ]]; then
  TF_HOME="$(cd "${TRADEFLIPPER_ROOT}" && pwd)"
elif [[ -d "$HOME/tradeFlipper/backend/app" ]]; then
  TF_HOME="$HOME/tradeFlipper"
else
  printf 'TradeFlipper backend not found; set TRADEFLIPPER_ROOT=/path/to/tradeFlipper\n' >&2
  exit 1
fi
export TRADEFLIPPER_ROOT="$TF_HOME"
cd "$ROOT"
PYTHON="${TRADEFLIPPER_MCP_PYTHON:-$ROOT/.venv-mcp/bin/python}"
if [[ ! -x "$PYTHON" ]]; then
  printf 'Missing MCP Python at %s; create the venv and install requirements first.\n' "$PYTHON" >&2
  exit 1
fi
exec "$PYTHON" -m mcp_server.server "$@"
