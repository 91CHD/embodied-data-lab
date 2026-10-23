#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
BUNDLED="${HOME}/.cache/codex-runtimes/codex-primary-runtime/dependencies"
export PATH="${BUNDLED}/node/bin:${BUNDLED}/bin/fallback:${PATH}"
PG_BIN="${PG_BIN:-/opt/homebrew/opt/postgresql@16/bin}"
PG_PORT="${PG_PORT:-55432}"
export PYTHONPATH="$ROOT/backend"
cd "$ROOT"
mkdir -p storage/logs storage/run storage/incoming
for tool in uv node pnpm; do
    command -v "$tool" >/dev/null 2>&1 || { printf 'Missing %s; see docs/ENVIRONMENT.md\n' "$tool" >&2; exit 1; }
done
[ -x "$PG_BIN/pg_ctl" ] || { printf 'Set PG_BIN or install PostgreSQL 16 first. See docs/ENVIRONMENT.md\n' >&2; exit 1; }
if [ ! -f .env ]; then cp .env.example .env; fi
uv sync --frozen
(cd frontend && pnpm install --frozen-lockfile)
if [ ! -f storage/pgdata/PG_VERSION ]; then
    "$PG_BIN/initdb" -D "$ROOT/storage/pgdata" --username="$(id -un)" --auth-local=trust --auth-host=trust --locale=C --encoding=UTF8
fi
if ! "$PG_BIN/pg_ctl" -D "$ROOT/storage/pgdata" status >/dev/null 2>&1; then
    "$PG_BIN/pg_ctl" -D "$ROOT/storage/pgdata" -l "$ROOT/storage/logs/postgres.log" -o "-p $PG_PORT -h 127.0.0.1 -k /tmp" -w start
fi
if ! "$PG_BIN/psql" -h 127.0.0.1 -p "$PG_PORT" -U "$(id -un)" -d postgres -Atc "SELECT 1 FROM pg_database WHERE datname='embodied_lab'" | grep -q 1; then
    "$PG_BIN/createdb" -h 127.0.0.1 -p "$PG_PORT" -U "$(id -un)" embodied_lab
fi
.venv/bin/alembic upgrade head

start_process() {
    local name="$1"
    shift
    local pid_file="$ROOT/storage/run/$name.pid"
    if [ -f "$pid_file" ]; then
        local previous_pid
        previous_pid="$(cat "$pid_file")"
        if kill -0 "$previous_pid" 2>/dev/null && [[ "$(ps -p "$previous_pid" -o stat= 2>/dev/null || true)" != *Z* ]] && [ "$(ps -p "$previous_pid" -o lstart=)" = "$(cat "$pid_file.start" 2>/dev/null)" ]; then
            printf '%s already running (PID %s)\n' "$name" "$previous_pid"
            return
        fi
    fi
    # A new process session survives both a terminal exit and tool process-group cleanup.
    local process_pid
    process_pid="$("$ROOT/.venv/bin/python" - "$ROOT/storage/logs/$name.log" "$@" <<'PY'
import subprocess
import sys

with open(sys.argv[1], "ab", buffering=0) as output:
    process = subprocess.Popen(
        sys.argv[2:], stdin=subprocess.DEVNULL, stdout=output,
        stderr=subprocess.STDOUT, start_new_session=True,
    )
print(process.pid)
PY
)"
    printf '%s\n' "$process_pid" > "$pid_file"
    sleep 0.2
    ps -p "$process_pid" -o lstart= > "$pid_file.start" || { tail -n 30 "$ROOT/storage/logs/$name.log"; return 1; }
    printf '%s started (PID %s)\n' "$name" "$process_pid"
}
start_process api "$ROOT/.venv/bin/python" -m uvicorn app.main:app --app-dir "$ROOT/backend" --host 127.0.0.1 --port 8000 --timeout-graceful-shutdown 3
start_process worker "$ROOT/.venv/bin/python" -m app.worker --id local-worker
start_process frontend node "$ROOT/frontend/node_modules/vite/bin/vite.js" "$ROOT/frontend" --config "$ROOT/frontend/vite.config.ts" --host 127.0.0.1 --port 5173 --strictPort
for attempt in $(seq 1 40); do
    if curl -fsS http://127.0.0.1:8000/api/health >/dev/null 2>&1 && curl -fsS http://127.0.0.1:5173/ >/dev/null 2>&1 && "$ROOT/.venv/bin/python" -m app.worker --health >/dev/null 2>&1; then
        printf '\nReady: http://127.0.0.1:5173\nAPI: http://127.0.0.1:8000/docs\nLogs: %s/storage/logs\nStop: bash scripts/stop.sh\n' "$ROOT"
        exit 0
    fi
    sleep 0.5
done
printf 'Startup not healthy; inspect storage/logs/{api,worker,frontend}.log\n' >&2
exit 1
