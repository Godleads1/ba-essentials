#!/bin/sh
# Runs once, when the database volume is first created (security checklist 24 and 30).
#
# - banda_owner owns the database and its tables. Only migrations use it
#   (`docker compose run --rm migrate`).
# - banda_app is what the website uses: it can read and write rows, but can't create,
#   change or drop tables, or switch off the audit trigger (it doesn't own them).
# - banda_restore_check is a scratch database the monthly restore check fills and empties.
# The postgres superuser is for emergencies only; nothing in the app uses it.
set -eu

psql -v ON_ERROR_STOP=1 --username postgres \
     -v owner_pw="$BANDA_OWNER_PASSWORD" -v app_pw="$BANDA_APP_PASSWORD" <<'SQL'
CREATE ROLE banda_owner LOGIN PASSWORD :'owner_pw';
CREATE ROLE banda_app LOGIN PASSWORD :'app_pw';
CREATE DATABASE banda OWNER banda_owner;
CREATE DATABASE banda_restore_check OWNER banda_app;
REVOKE ALL ON DATABASE banda FROM PUBLIC;
GRANT CONNECT, TEMPORARY ON DATABASE banda TO banda_app;
\connect banda
ALTER SCHEMA public OWNER TO banda_owner;
REVOKE ALL ON SCHEMA public FROM PUBLIC;
GRANT USAGE ON SCHEMA public TO banda_app;
ALTER DEFAULT PRIVILEGES FOR ROLE banda_owner IN SCHEMA public
    GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO banda_app;
ALTER DEFAULT PRIVILEGES FOR ROLE banda_owner IN SCHEMA public
    GRANT USAGE, SELECT, UPDATE ON SEQUENCES TO banda_app;
SQL
