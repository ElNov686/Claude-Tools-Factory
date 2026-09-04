#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/mcp_server"
python -m venv .venv
. .venv/bin/activate
pip install -e ".[dev]"
playwright install chromium
echo
echo "Setup complete. Add credentials to keyring, e.g.:"
echo "  keyring set factory:telegram_desktop phone"
echo
echo "Then register in Claude Desktop config (see README)."
