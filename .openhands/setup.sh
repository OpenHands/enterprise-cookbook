#!/usr/bin/env bash
#
# .openhands/setup.sh — runs automatically every time OpenHands starts working
# with this repository (see https://docs.openhands.dev/openhands/usage/customization/repository).
#
# It is also what the `clone-and-attach` example executes after it shallow-clones
# this repo into a sandbox, so keep it fast and side-effect free.
set -euo pipefail

echo "[enterprise-cookbook setup.sh] running in $(pwd)"
echo "[enterprise-cookbook setup.sh] python: $(python3 --version 2>&1)"

# Sync dependencies with uv when it is available; harmless to skip otherwise.
if command -v uv >/dev/null 2>&1; then
  echo "[enterprise-cookbook setup.sh] uv detected — running 'uv sync'"
  uv sync --frozen 2>/dev/null || uv sync || echo "[enterprise-cookbook setup.sh] uv sync skipped"
else
  echo "[enterprise-cookbook setup.sh] uv not found — skipping dependency sync"
fi

echo "[enterprise-cookbook setup.sh] done"
