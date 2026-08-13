# start_local.ps1
#
# Launch the WinRM remote PowerShell TUI (winrm_remote.py) via uv.
# Double-click friendly: resolves uv, runs from the script's folder,
# and keeps the window open afterwards.

$ErrorActionPreference = "Stop"

Set-Location $PSScriptRoot

if (-not (Get-Command uv -ErrorAction SilentlyContinue))
{
    Write-Host ""
    Write-Host "'uv' not found on PATH." -ForegroundColor Red
    Write-Host "Install it with:"
    Write-Host '  powershell -c "irm https://astral.sh/uv/install.ps1 | iex"'
    Read-Host "Press Enter to close"
    exit 1
}

uv run winrm_remote.py

Write-Host ""
Read-Host "Press Enter to close"
