#!/bin/sh
# Modos: api | worker | migrate
set -eu

cmd="${1:-api}"
shift || true

run_migrations() {
    if [ "${SKIP_MIGRATIONS:-0}" != "1" ]; then
        echo "[entrypoint] Running alembic upgrade head..."
        alembic upgrade head
    else
        echo "[entrypoint] SKIP_MIGRATIONS=1 — pulando alembic"
    fi
}

case "$cmd" in
    api)
        run_migrations
        exec gunicorn main:app \
            --bind 0.0.0.0:8000 \
            --workers "${GUNICORN_WORKERS:-4}" \
            --worker-class uvicorn.workers.UvicornWorker \
            --timeout "${GUNICORN_TIMEOUT:-60}" \
            --graceful-timeout 30 \
            --access-logfile - \
            --error-logfile - \
            "$@"
        ;;
    worker)
        run_migrations
        exec arq worker.settings.WorkerSettings "$@"
        ;;
    migrate)
        run_migrations
        ;;
    *)
        echo "[entrypoint] modo desconhecido: $cmd (use api|worker|migrate)" >&2
        exit 64
        ;;
esac
