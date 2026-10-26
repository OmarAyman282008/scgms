#!/bin/sh
# Waits for PostgreSQL, applies migrations, loads the school data on first start.
set -e

if [ -n "$DATABASE_URL" ]; then
  host=$(python -c "import os,urllib.parse as u;p=u.urlparse(os.environ['DATABASE_URL']);print(p.hostname or '')")
  port=$(python -c "import os,urllib.parse as u;p=u.urlparse(os.environ['DATABASE_URL']);print(p.port or 5432)")
  if [ -n "$host" ]; then
    echo "Waiting for database $host:$port ..."
    i=0
    until nc -z "$host" "$port"; do
      i=$((i+1)); [ $i -gt 60 ] && echo "Database not reachable" && exit 1
      sleep 1
    done
  fi
fi

# Only the web container runs migrations and first-start seeding.
if [ "${RUN_BOOTSTRAP:-1}" = "1" ]; then
  if [ "${LOAD_DEMO_DATA:-0}" = "1" ]; then
    python manage.py bootstrap --demo
  else
    python manage.py bootstrap
  fi
fi

exec "$@"
