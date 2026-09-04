# setup.ps1 — one-time setup on Windows
$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot/mcp_server
python -m venv .venv
. .venv/Scripts/Activate.ps1
pip install -e ".[dev]"
playwright install chromium
Write-Host ""
Write-Host "Setup complete. Add credentials to keyring, e.g.:"
Write-Host "  keyring set factory:telegram_desktop phone"
Write-Host "  keyring set factory:telegram_desktop login_code   (optional pre-seed)"
Write-Host ""
Write-Host "Then register in Claude Desktop config (see README)."
