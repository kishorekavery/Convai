-- Read-only database role for the application
-- ============================================
--
-- WHY
-- The SQL executed against tenant databases is written by a language model.
-- database/sql_safety.py screens it with regular expressions, which cannot
-- reliably parse SQL, and execute_ai_generated_sql wraps each statement in
-- conn.transaction(readonly=True).
--
-- That transaction guard protects *that code path*. It does not protect the
-- credentials: DB_USERNAME is still a read-write role, so anything else using
-- those credentials - a migration script, a debugging session, a future code
-- path that forgets the wrapper - can still write.
--
-- Granting the application role SELECT only makes the guarantee unconditional
-- and independent of application code.
--
-- HOW TO RUN
-- Requires superuser. Run once per tenant database plus the knowledge base.
-- Substitute :app_role for the value of DB_USERNAME.
--
--   psql -h <host> -U postgres -d <database> \
--        -v app_role=<DB_USERNAME> -f 001_readonly_app_role.sql
--
-- BEFORE YOU RUN THIS - check nothing else needs write access with these
-- credentials. Two things in this repo do:
--   * dataprocessing/kbe_table_embedding_generation.py UPDATEs
--     kbe_user_input_embedding on the knowledge base.
--   * The application UPDATEs public.user_ai_quota on every request.
-- Both are handled below: the quota table keeps write access, and the
-- knowledge base should either keep it or use separate credentials.

\set ON_ERROR_STOP on

BEGIN;

-- Revoke everything, then grant back only what is needed. Starting from a
-- clean slate avoids inheriting a permissive grant made earlier.
REVOKE ALL ON ALL TABLES IN SCHEMA ai FROM :app_role;
REVOKE ALL ON ALL SEQUENCES IN SCHEMA ai FROM :app_role;

GRANT USAGE ON SCHEMA ai TO :app_role;
GRANT SELECT ON ALL TABLES IN SCHEMA ai TO :app_role;

-- Tables created after this runs would otherwise be inaccessible.
ALTER DEFAULT PRIVILEGES IN SCHEMA ai GRANT SELECT ON TABLES TO :app_role;

-- public schema: read-only except the quota counter, which the application
-- must increment on every request.
GRANT USAGE ON SCHEMA public TO :app_role;
GRANT SELECT ON ALL TABLES IN SCHEMA public TO :app_role;
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT SELECT ON TABLES TO :app_role;

GRANT SELECT, UPDATE ON public.user_ai_quota TO :app_role;

COMMIT;

-- VERIFY
-- Expect 'SELECT' only for the ai schema, and SELECT+UPDATE for user_ai_quota:
--
--   SELECT table_schema, table_name, string_agg(privilege_type, ',' ORDER BY privilege_type)
--   FROM information_schema.table_privileges
--   WHERE grantee = :'app_role'
--   GROUP BY 1, 2 ORDER BY 1, 2;
--
-- Then confirm a write actually fails:
--   SET ROLE :app_role;
--   CREATE TABLE ai.should_not_work (x int);   -- expect: permission denied
--   RESET ROLE;
