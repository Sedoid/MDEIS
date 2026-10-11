#!/bin/sh
# Runs once, on the first start of an empty data volume (Postgres image convention).
# Creates the databases, the three roles, and the vector extension. Table-level grants come with
# the migrations (T-003). Everything is named by environment variables from .env.
set -e

psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" \
  -v admin_db="$POSTGRES_DB" \
  -v mdeis_db="$MDEIS_DB_NAME" -v app_user="$MDEIS_APP_USER" -v app_pw="$MDEIS_APP_PASSWORD" \
  -v ro_user="$MDEIS_RO_USER" -v ro_pw="$MDEIS_RO_PASSWORD" \
  -v n8n_db="$N8N_DB_NAME" -v n8n_user="$N8N_DB_USER" -v n8n_pw="$N8N_DB_PASSWORD" <<'SQL'

CREATE ROLE :"app_user" LOGIN PASSWORD :'app_pw';
CREATE ROLE :"ro_user"  LOGIN PASSWORD :'ro_pw';
CREATE ROLE :"n8n_user" LOGIN PASSWORD :'n8n_pw';

-- The read-only role cannot write even if a grant is added by mistake.
ALTER ROLE :"ro_user" SET default_transaction_read_only = on;

CREATE DATABASE :"mdeis_db" OWNER :"app_user";
CREATE DATABASE :"n8n_db"   OWNER :"n8n_user";

-- Nobody connects by default. Each role gets its own database and nothing else.
REVOKE ALL ON DATABASE :"admin_db" FROM PUBLIC;
REVOKE ALL ON DATABASE :"mdeis_db" FROM PUBLIC;
REVOKE ALL ON DATABASE :"n8n_db"   FROM PUBLIC;
GRANT CONNECT ON DATABASE :"mdeis_db" TO :"app_user", :"ro_user";
GRANT CONNECT ON DATABASE :"n8n_db"   TO :"n8n_user";

\connect :mdeis_db

-- Created here as superuser. The migration says CREATE EXTENSION IF NOT EXISTS, which then does nothing.
CREATE EXTENSION IF NOT EXISTS vector;

REVOKE ALL ON SCHEMA public FROM PUBLIC;
GRANT ALL ON SCHEMA public TO :"app_user";
GRANT USAGE ON SCHEMA public TO :"ro_user";

-- Tables the app role creates later are readable (SELECT only) by the read-only role.
ALTER DEFAULT PRIVILEGES FOR ROLE :"app_user" IN SCHEMA public GRANT SELECT ON TABLES TO :"ro_user";
SQL
