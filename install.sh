#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BACKEND="${TRADEFLIPPER_ROOT:-$HOME/tradeFlipper}"
if [[ ! -f "$BACKEND/backend/requirements.txt" ]]; then
  printf 'Missing TradeFlipper at %s; set TRADEFLIPPER_ROOT\n' "$BACKEND" >&2
  exit 1
fi
cd "$ROOT"
python3 -m venv .venv-mcp
.venv-mcp/bin/python -m pip install -r "$BACKEND/backend/requirements.txt" -r mcp_server/requirements.txt pytest
.venv-mcp/bin/python -m pytest -q mcp_server/tests
printf 'Add to Claude Code: claude mcp add --transport stdio tradeflipper-research -- %s/mcp_server/run.sh\n' "$ROOT"
