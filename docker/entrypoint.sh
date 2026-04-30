#!/usr/bin/env bash
set -euo pipefail

# -----------------------------------------------------------------------------
# entrypoint.sh
# Single entry point for every service. Behavior is selected by the first arg.
#
# [NFR2] Gunicorn worker/thread counts come from env (GUNICORN_WORKERS / THREADS).
#        These caps are the OUTER concurrency ceiling that protects memory and
#        the database connection pool from collapsing under load.
# [NFR2] Celery --concurrency caps the INNER pool size of each Celery worker.
# -----------------------------------------------------------------------------

CMD="${1:-gunicorn}"

case "$CMD" in
  gunicorn)
    python manage.py migrate --noinput
    exec gunicorn config.wsgi:application \
        --bind 0.0.0.0:8000 \
        --workers "${GUNICORN_WORKERS:-4}" \
        --threads "${GUNICORN_THREADS:-2}" \
        --worker-class "${GUNICORN_WORKER_CLASS:-sync}" \
        --timeout "${GUNICORN_TIMEOUT:-30}" \
        --access-logfile - \
        --error-logfile -
    ;;

  celery-worker)
    exec celery -A config worker \
        --loglevel=INFO \
        --concurrency="${CELERY_CONCURRENCY:-4}"
    ;;

  celery-beat)
    exec celery -A config beat --loglevel=INFO
    ;;

  flower)
    exec celery -A config flower --port=5555
    ;;

  shell)
    exec python manage.py shell
    ;;

  *)
    exec "$@"
    ;;
esac
