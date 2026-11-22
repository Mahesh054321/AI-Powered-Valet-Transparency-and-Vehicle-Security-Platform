#!/usr/bin/env bash
set -e

# 1. Check if postgres is installed
if ! command -v psql &> /dev/null; then
    echo "[setup] Installing postgresql..."
    sudo apt-get update -qq
    sudo apt-get install -y -qq postgresql postgresql-contrib
fi

# 2. Check if pip packages are installed
if ! python -c "import flask, flask_sqlalchemy, psycopg2, dotenv, jwt" &> /dev/null; then
    echo "[setup] Installing python dependencies..."
    pip install -q -r /home/user/backend/requirements.txt -r /home/user/backend/requirements-dev.txt
fi

# 3. Start PostgreSQL service
sudo service postgresql status &> /dev/null || sudo service postgresql start

# 4. Ensure valet_user and databases exist
sudo -u postgres psql -tAc "SELECT 1 FROM pg_roles WHERE rolname='valet_user'" | grep -q 1 || {
    sudo -u postgres createuser -s valet_user
    sudo -u postgres psql -c "ALTER USER valet_user WITH PASSWORD 'change-me-locally';"
}
sudo -u postgres psql -tAc "SELECT 1 FROM pg_database WHERE datname='valet_db'" | grep -q 1 || {
    sudo -u postgres createdb -O valet_user valet_db
}
sudo -u postgres psql -tAc "SELECT 1 FROM pg_database WHERE datname='valet_db_test'" | grep -q 1 || {
    sudo -u postgres createdb -O valet_user valet_db_test
}
echo "[setup] Environment ready."
