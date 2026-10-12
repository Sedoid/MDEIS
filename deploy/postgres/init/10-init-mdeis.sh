#!/bin/sh
# Runs once, on the first start of an empty data volume (Postgres image convention).
# Creates the databases, the roles, and the vector extension. Table-level grants come with
# the migrations (T-003, T-030, T-046). Everything is named by environment variables from .env.
#
# An existing data volume does not run this again. After changing this file, run `make clean`
# (or `make dev-reset`) and start again. Nothing is lost while there is no real data.
set -e

psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" \
  -v admin_db="$POSTGRES_DB" \
  -v mdeis_db="$MDEIS_DB_NAME" -v app_user="$MDEIS_APP_USER" -v app_pw="$MDEIS_APP_PASSWORD" \
  -v ro_user="$MDEIS_RO_USER" -v ro_pw="$MDEIS_RO_PASSWORD" \
  -v n8n_db="$N8N_DB_NAME" -v n8n_user="$N8N_DB_USER" -v n8n_pw="$N8N_DB_PASSWORD" \
  -v orch_db="$ORCHESTRATOR_DB_NAME" -v orch_user="$ORCHESTRATOR_DB_USER" -v orch_pw="$ORCHESTRATOR_DB_PASSWORD" \
  -v portal_user="$PORTAL_DB_USER" -v portal_pw="$PORTAL_DB_PASSWORD" \
  -v auto_user="$N8N_AUTO_DB_USER" -v auto_pw="$N8N_AUTO_DB_PASSWORD" <<'SQL'

CREATE ROLE :"app_user"    LOGIN PASSWORD :'app_pw';
CREATE ROLE :"ro_user"     LOGIN PASSWORD :'ro_pw';
CREATE ROLE :"n8n_user"    LOGIN PASSWORD :'n8n_pw';
CREATE ROLE :"orch_user"   LOGIN PASSWORD :'orch_pw';
CREATE ROLE :"portal_user" LOGIN PASSWORD :'portal_pw';
CREATE ROLE :"auto_user"   LOGIN PASSWORD :'auto_pw';

-- These roles cannot write even if a grant is added by mistake.
ALTER ROLE :"ro_user"   SET default_transaction_read_only = on;
ALTER ROLE :"auto_user" SET default_transaction_read_only = on;

CREATE DATABASE :"mdeis_db" OWNER :"app_user";
CREATE DATABASE :"n8n_db"   OWNER :"n8n_user";
CREATE DATABASE :"orch_db"  OWNER :"orch_user";

-- Nobody connects by default. Each role gets its own database and nothing else.
REVOKE ALL ON DATABASE :"admin_db" FROM PUBLIC;
REVOKE ALL ON DATABASE :"mdeis_db" FROM PUBLIC;
REVOKE ALL ON DATABASE :"n8n_db"   FROM PUBLIC;
REVOKE ALL ON DATABASE :"orch_db"  FROM PUBLIC;
GRANT CONNECT ON DATABASE :"mdeis_db" TO :"app_user", :"ro_user", :"portal_user", :"auto_user";
GRANT CONNECT ON DATABASE :"n8n_db"   TO :"n8n_user";
GRANT CONNECT ON DATABASE :"orch_db"  TO :"orch_user";

\connect :mdeis_db

-- Created here as superuser. The migration says CREATE EXTENSION IF NOT EXISTS, which then does nothing.
CREATE EXTENSION IF NOT EXISTS vector;

REVOKE ALL ON SCHEMA public FROM PUBLIC;
GRANT ALL ON SCHEMA public TO :"app_user";
-- USAGE lets a role see objects in the schema. It reads nothing until a migration grants SELECT.
GRANT USAGE ON SCHEMA public TO :"ro_user", :"portal_user", :"auto_user";

-- Tables the app role creates later are readable (SELECT only) by the read-only role.
-- portal_app and n8n_auto get no default: the migrations grant them named tables and views only.
ALTER DEFAULT PRIVILEGES FOR ROLE :"app_user" IN SCHEMA public GRANT SELECT ON TABLES TO :"ro_user";
SQL
