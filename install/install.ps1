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

# make `atlas` visible in THIS session (uv tool shim lives in ~/.local/bin)
$env:Path = "$env:USERPROFILE\.local\bin;$env:Path"

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
