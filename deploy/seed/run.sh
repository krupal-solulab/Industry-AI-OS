#!/usr/bin/env bash
# Seed entrypoint: migrate, then load demo data. Idempotent — safe to re-run.
set -euo pipefail

echo "[seed] applying database migrations..."
cd /app/migrations
alembic upgrade head

echo "[seed] registering demo tenant + data..."
python /app/seed/seed.py

echo "[seed] done."
