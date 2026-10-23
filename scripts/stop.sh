#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
is_alive() {
    kill -0 "$1" 2>/dev/null || return 1
    local process_state
    process_state="$(ps -p "$1" -o stat= 2>/dev/null || true)"
    # A terminated child can remain a zombie until its parent reaps it.
    case "$process_state" in *Z*|"") return 1 ;; esac
    return 0
}
for name in frontend worker api; do
    pid_file="storage/run/$name.pid"
    if [ -f "$pid_file" ]; then
        process_pid="$(cat "$pid_file")"
        if is_alive "$process_pid" && [ "$(ps -p "$process_pid" -o lstart=)" = "$(cat "$pid_file.start" 2>/dev/null)" ]; then
            kill "$process_pid" 2>/dev/null || true
            # Give the worker time to record cooperative cancellation before stopping PostgreSQL.
            for attempt in $(seq 1 50); do
                if ! is_alive "$process_pid"; then break; fi
                sleep 0.1
            done
            if is_alive "$process_pid" && [ "$(ps -p "$process_pid" -o lstart=)" = "$(cat "$pid_file.start" 2>/dev/null)" ]; then
                printf '%s is still stopping; keeping PID and database for a safe retry.\n' "$name" >&2
                exit 1
            fi
            printf 'Stopped %s (PID %s)\n' "$name" "$process_pid"
        fi
        rm -f "$pid_file" "$pid_file.start"
    fi
done
PG_BIN="${PG_BIN:-/opt/homebrew/opt/postgresql@16/bin}"
if [ -x "$PG_BIN/pg_ctl" ] && [ -f storage/pgdata/PG_VERSION ] && "$PG_BIN/pg_ctl" -D "$ROOT/storage/pgdata" status >/dev/null 2>&1; then
    "$PG_BIN/pg_ctl" -D "$ROOT/storage/pgdata" -m fast -w stop
fi
printf 'Local processes stopped. Database and artifacts preserved in storage/.\n'
