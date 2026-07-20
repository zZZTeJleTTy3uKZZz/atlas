# atlas installer (Windows). ASCII-only by design: `irm ... | iex` decodes as
# Latin-1, so non-ASCII characters here would break the script.
#
# Usage:
#   irm https://raw.githubusercontent.com/zZZTeJleTTy3uKZZz/atlas/master/install/install.ps1 | iex
#
# What it does: installs uv (Astral) if missing, then `uv tool install atlas-pm`
# (dist name is atlas-pm; the CLI command stays `atlas`).
$ErrorActionPreference = "Stop"

# Strip proxy env vars before uv. A user PowerShell profile may inject
# HTTP/HTTPS/ALL_PROXY (e.g. Hiddify 127.0.0.1:12334); uv would then fetch through
# a dead proxy port and fail (os error 10061). A TUN tunnel routes without them.
foreach ($__pv in 'HTTP_PROXY','HTTPS_PROXY','ALL_PROXY','http_proxy','https_proxy','all_proxy') {
  Remove-Item "Env:$__pv" -ErrorAction SilentlyContinue
}

if (-not (Get-Command uv -ErrorAction SilentlyContinue)) {
  Write-Host "[atlas] uv not found - installing it (Astral)..."
  powershell -NoProfile -ExecutionPolicy Bypass -Command "irm https://astral.sh/uv/install.ps1 | iex"
  # make uv visible in this session
  $env:Path = "$env:USERPROFILE\.local\bin;$env:Path"
}

Write-Host "[atlas] installing atlas-pm via uv tool ..."
# --force: overwrite an existing shim (re-install / other PC), otherwise uv fails
# "Executable already exists: atlas.exe" while the script keeps going and lies "Done".
uv tool install --upgrade --force atlas-pm
if ($LASTEXITCODE -ne 0) {
  Write-Error "[atlas] uv tool install failed (exit $LASTEXITCODE). atlas-pm NOT installed."
  exit 1
}
try { uv tool update-shell | Out-Null } catch { }

# make `atlas` visible in THIS session (uv tool shim lives in ~/.local/bin)
$env:Path = "$env:USERPROFILE\.local\bin;$env:Path"

# Real self-check: `which` finding the file != it runs (broken uv trampoline).
$__atlas = Join-Path $env:USERPROFILE ".local\bin\atlas.exe"
if (-not (Test-Path $__atlas)) { $__atlas = "atlas" }
$__runs = $false
try { & $__atlas --version *> $null; if ($LASTEXITCODE -eq 0) { $__runs = $true } } catch { }
if (-not $__runs) {
  Write-Error "[atlas] installed but does NOT run (broken uv trampoline). Fix: uv tool install --reinstall --force atlas-pm"
  exit 2
}

# turnkey: write Atlas rules into agent files (CLAUDE.md/AGENTS.md) and install
# the SessionStart triage hook (portfolio state is injected at session start).
# Non-fatal: older atlas has no `setup` - the CLI is already installed anyway.
Write-Host "[atlas] wiring rules + SessionStart hook into your agent (atlas setup) ..."
try {
  atlas setup --scope global
  if ($LASTEXITCODE -ne 0) { Write-Host "[atlas] (atlas setup not in this version - run it after 'atlas update')" }
} catch { Write-Host "[atlas] (skipped atlas setup: $($_.Exception.Message))" }

Write-Host ""
Write-Host "[atlas] Done. Verify:  atlas --version"
Write-Host "[atlas] First run:      atlas config set owner <you>"
Write-Host "[atlas] Rules + hook:   atlas setup     (re-run any time; idempotent)"
Write-Host "[atlas] Update later:   atlas update"
