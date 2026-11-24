#!/bin/sh
set -eu

# Render persistent disks can mount as root-owned directories. Set ownership
# before switching to the unprivileged runtime user.
mkdir -p /app/storage/media
chown appuser:appuser /app/storage/media

# One worker only: the application's SSE broadcaster and login rate limiter
# are currently process-local. Threads serve concurrent demo SSE connections.
exec gosu appuser gunicorn \
  --bind "0.0.0.0:${PORT:-10000}" \
  --worker-class gthread \
  --workers 1 \
  --threads 8 \
  --timeout 120 \
  --access-logfile - \
  --error-logfile - \
  wsgi:app
