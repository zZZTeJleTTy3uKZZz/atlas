#!/bin/sh
# atlas installer (Linux / macOS). ASCII-only by design: this file is piped into
# `sh`, and non-ASCII can corrupt under some locales.
#
# Usage:
#   curl -fsSL https://raw.githubusercontent.com/zZZTeJleTTy3uKZZz/atlas/master/install/install.sh | sh
#
# What it does: installs uv (Astral) if missing, then `uv tool install atlas-pm`
# (dist name is atlas-pm; the CLI command stays `atlas`).
set -eu

if ! command -v uv >/dev/null 2>&1; then
  echo "[atlas] uv not found - installing it (Astral)..."
  curl -LsSf https://astral.sh/uv/install.sh | sh
  # make uv visible in this shell
  export PATH="$HOME/.local/bin:$HOME/.cargo/bin:$PATH"
fi

echo "[atlas] installing atlas-pm via uv tool ..."
uv tool install --upgrade atlas-pm
uv tool update-shell >/dev/null 2>&1 || true

# make `atlas` visible in this shell (uv tool shim lives in ~/.local/bin)
export PATH="$HOME/.local/bin:$PATH"

# turnkey: write Atlas rules into agent files (CLAUDE.md/AGENTS.md) and install
# the SessionStart triage hook (portfolio state injected at session start).
# Non-fatal (|| ...): older atlas has no `setup` - the CLI is installed anyway.
echo "[atlas] wiring rules + SessionStart hook into your agent (atlas setup) ..."
atlas setup --scope global || echo "[atlas] (atlas setup not in this version - run it after 'atlas update')"

echo ""
echo "[atlas] Done. Verify:  atlas --version"
echo "[atlas] First run:      atlas config set owner <you>"
echo "[atlas] Rules + hook:   atlas setup     (re-run any time; idempotent)"
echo "[atlas] Update later:   atlas update"
