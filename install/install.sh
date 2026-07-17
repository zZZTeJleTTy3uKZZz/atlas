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

echo ""
echo "[atlas] Done. Verify:  atlas --version"
echo "[atlas] First run:      atlas config set owner <you> && atlas init"
echo "[atlas] Update later:   atlas update"
