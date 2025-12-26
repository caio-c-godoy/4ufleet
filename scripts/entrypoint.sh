#!/usr/bin/env bash
set -euo pipefail

# Optionally run migrations before starting the app.
if [[ "${RUN_MIGRATIONS:-1}" != "0" ]]; then
  echo "Running migrations..."
  python -m flask --app run.py db upgrade
else
  echo "Skipping migrations (RUN_MIGRATIONS=0)"
fi

exec gunicorn run:app --bind "0.0.0.0:${PORT:-8000}" --workers 2 --timeout 120
