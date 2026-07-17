# atlas installer (Windows). ASCII-only by design: `irm ... | iex` decodes as
# Latin-1, so non-ASCII characters here would break the script.
#
# Usage:
#   irm https://raw.githubusercontent.com/zZZTeJleTTy3uKZZz/atlas/master/install/install.ps1 | iex
#
# What it does: installs uv (Astral) if missing, then `uv tool install atlas-pm`
# (dist name is atlas-pm; the CLI command stays `atlas`).
$ErrorActionPreference = "Stop"

if (-not (Get-Command uv -ErrorAction SilentlyContinue)) {
  Write-Host "[atlas] uv not found - installing it (Astral)..."
  powershell -NoProfile -ExecutionPolicy Bypass -Command "irm https://astral.sh/uv/install.ps1 | iex"
  # make uv visible in this session
  $env:Path = "$env:USERPROFILE\.local\bin;$env:Path"
}

Write-Host "[atlas] installing atlas-pm via uv tool ..."
uv tool install --upgrade atlas-pm
try { uv tool update-shell | Out-Null } catch { }

Write-Host ""
Write-Host "[atlas] Done. Verify:  atlas --version"
Write-Host "[atlas] First run:      atlas config set owner <you> ; atlas init"
Write-Host "[atlas] Update later:   atlas update"
