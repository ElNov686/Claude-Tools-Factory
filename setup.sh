#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/mcp_server"
PYTHON_BIN="${PYTHON:-}"
if [ -z "$PYTHON_BIN" ]; then
    for cmd in python3.12 python3.11 python3 python; do
        if command -v "$cmd" >/dev/null 2>&1; then
            if "$cmd" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)' 2>/dev/null; then
                PYTHON_BIN="$cmd"
                break
            fi
        fi
    done
fi

if [ -z "$PYTHON_BIN" ]; then
    echo "Error: Python >= 3.11 is required but was not found on PATH." >&2
    exit 1
fi

"$PYTHON_BIN" -m venv .venv
. .venv/bin/activate
pip install -e ".[dev]"
playwright install chromium
echo
echo "Setup complete. Add credentials to keyring, e.g.:"
echo "  keyring set factory:telegram_desktop phone"
echo
echo "Then register in Claude Desktop config (see README)."
