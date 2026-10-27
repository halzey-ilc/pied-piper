-- Runs once, the first time the postgres volume is initialised.
-- The POSTGRES_USER (crm_app) already exists and owns the control-plane database.
--
-- For local development we grant crm_app CREATEDB so the provisioning flow can
-- create tenant databases. In production, provisioning must use a SEPARATE role
-- with CREATEDB, distinct from the least-privilege application role, and tenant
-- databases should be created by an operator or a controlled job — never by the
-- request-handling role. See docs/architecture/multi-tenancy.md.

DO $$
BEGIN
    EXECUTE format('ALTER ROLE %I CREATEDB', current_setting('POSTGRES_USER', true));
EXCEPTION WHEN OTHERS THEN
    -- current_setting may be unavailable; fall back to the known local role name.
    BEGIN
        ALTER ROLE crm_app CREATEDB;
    EXCEPTION WHEN undefined_object THEN
        RAISE NOTICE 'crm_app role not found; skipping CREATEDB grant';
    END;
END $$;

-- Harden the control-plane database: no implicit object creation by PUBLIC.
REVOKE CREATE ON SCHEMA public FROM PUBLIC;
