#!/bin/sh
# Runs once, on first initialisation of the data volume.
# Creates the least-privilege application role; table grants are applied by migrations.
set -eu
: "${APP_DB_USER:?}" "${APP_DB_PASSWORD:?}"
psql -v ON_ERROR_STOP=1 -v app_user="$APP_DB_USER" -v app_pw="$APP_DB_PASSWORD" \
     --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" <<'EOSQL'
CREATE ROLE :"app_user" LOGIN PASSWORD :'app_pw' NOSUPERUSER NOCREATEDB NOCREATEROLE;
GRANT CONNECT ON DATABASE :"DBNAME" TO :"app_user";
GRANT USAGE ON SCHEMA public TO :"app_user";
REVOKE CREATE ON SCHEMA public FROM PUBLIC;
EOSQL
