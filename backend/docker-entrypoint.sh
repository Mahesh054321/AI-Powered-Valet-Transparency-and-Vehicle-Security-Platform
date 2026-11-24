#!/bin/sh
set -eu

# Free Render instances cannot attach persistent disks. These uploaded files
# are temporary and will disappear after a restart or redeploy.
mkdir -p /app/storage/media
chown appuser:appuser /app/storage/media

# Render Free does not support preDeployCommand or interactive shell access.
# Apply idempotent Alembic migrations each time the instance starts.
if [ "${SEED_ON_START:-0}" = "1" ]; then
  # Only for a controlled demo: demo users have documented passwords.
  : "${SEED_ADMIN_EMAIL:?Set SEED_ADMIN_EMAIL in Render environment before demo seeding}"
  : "${SEED_ADMIN_PASSWORD:?Set SEED_ADMIN_PASSWORD in Render environment before demo seeding}"
  echo '[startup] Applying migrations and demo seed data...'
  gosu appuser python utils/scripts/init_db.py --seed
else
  echo '[startup] Applying database migrations...'
  gosu appuser python utils/scripts/init_db.py
fi


# One-time secure admin bootstrap. Unlike SEED_ON_START, creates no demo staff
# accounts with publicly documented passwords.
if [ "${BOOTSTRAP_ADMIN_ON_START:-0}" = "1" ]; then
  : "${SEED_ADMIN_EMAIL:?Set SEED_ADMIN_EMAIL in Render environment}"
  : "${SEED_ADMIN_PASSWORD:?Set SEED_ADMIN_PASSWORD in Render environment}"
  echo '[startup] Bootstrapping administrator if absent...'
  gosu appuser python utils/scripts/bootstrap_admin.py
fi

# One worker is intentional: app SSE broadcaster/rate limiting is process-local.
# Limit threads for a small-memory free instance (512 MB).
exec gosu appuser gunicorn \
  --bind "0.0.0.0:${PORT:-10000}" \
  --worker-class gthread \
  --workers 1 \
  --threads 4 \
  --timeout 120 \
  --access-logfile - \
  --error-logfile - \
  wsgi:app
