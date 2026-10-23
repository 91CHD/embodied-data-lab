#!/usr/bin/env bash
set -u
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
BUNDLED="${HOME}/.cache/codex-runtimes/codex-primary-runtime/dependencies"
export PATH="${BUNDLED}/node/bin:${BUNDLED}/bin/fallback:${PATH}"
cd "$ROOT"
printf 'Project: %s\n' "$ROOT"
uname -sm
for tool in uv node pnpm docker; do
    if command -v "$tool" >/dev/null 2>&1; then
        "$tool" --version 2>&1 | head -n 1
    else
        printf '%s: unavailable\n' "$tool"
    fi
done
PG_BIN="${PG_BIN:-/opt/homebrew/opt/postgresql@16/bin}"
if [ -x "$PG_BIN/psql" ]; then
    "$PG_BIN/psql" --version
    "$PG_BIN/pg_isready" -h 127.0.0.1 -p "${PG_PORT:-55432}" || true
else
    printf 'PostgreSQL: set PG_BIN to a PostgreSQL 16 bin directory.\n'
fi
if [ -x .venv/bin/python ]; then .venv/bin/python --version; fi
if command -v docker >/dev/null 2>&1; then docker compose version 2>&1 || true; fi
