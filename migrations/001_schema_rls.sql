-- ============================================================
-- APEX Product Intelligence — Database Schema with RLS
-- PostgreSQL / Supabase compatible
-- Run: psql $DATABASE_URL < migrations/001_schema_rls.sql
-- ============================================================

-- ── Extensions ───────────────────────────────────────────────
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";
CREATE EXTENSION IF NOT EXISTS "pgcrypto";

-- ── Roles ────────────────────────────────────────────────────
-- Three roles:
--   admin    → full read/write on all orgs
--   operator → read/write on own org only
--   viewer   → read-only on own org only

CREATE TYPE apex_role AS ENUM ('admin', 'operator', 'viewer');

-- ── Organizations ─────────────────────────────────────────────
CREATE TABLE organizations (
    id          UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    name        TEXT NOT NULL,
    slug        TEXT UNIQUE NOT NULL,
    created_at  TIMESTAMPTZ DEFAULT NOW()
);

-- ── Users ─────────────────────────────────────────────────────
CREATE TABLE users (
    id              UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    org_id          UUID NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
    email           TEXT UNIQUE NOT NULL,
    -- bcrypt hash — never store plaintext
    password_hash   TEXT NOT NULL,
    role            apex_role NOT NULL DEFAULT 'viewer',
    is_active       BOOLEAN DEFAULT TRUE,
    email_verified  BOOLEAN DEFAULT FALSE,
    created_at      TIMESTAMPTZ DEFAULT NOW(),
    updated_at      TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX idx_users_org ON users(org_id);
CREATE INDEX idx_users_email ON users(email);

-- ── Sessions ─────────────────────────────────────────────────
-- Short-lived JWT tracked here for forced invalidation
CREATE TABLE sessions (
    id              UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    user_id         UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    token_hash      TEXT NOT NULL,          -- SHA-256 of JWT
    ip_address      INET,
    user_agent      TEXT,
    expires_at      TIMESTAMPTZ NOT NULL,
    revoked         BOOLEAN DEFAULT FALSE,
    created_at      TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX idx_sessions_user ON sessions(user_id);
CREATE INDEX idx_sessions_token ON sessions(token_hash);

-- ── Login attempts (for rate limiting) ───────────────────────
CREATE TABLE login_attempts (
    id          BIGSERIAL PRIMARY KEY,
    identifier  TEXT NOT NULL,   -- email or IP
    ip_address  INET NOT NULL,
    success     BOOLEAN NOT NULL,
    attempted_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX idx_login_attempts_identifier ON login_attempts(identifier, attempted_at);
CREATE INDEX idx_login_attempts_ip ON login_attempts(ip_address, attempted_at);

-- ── Products ─────────────────────────────────────────────────
CREATE TABLE products (
    id              UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    org_id          UUID NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
    product_type    TEXT NOT NULL,
    name            TEXT,
    manufacturer    TEXT,
    part_number     TEXT,
    -- Attributes stored as validated JSONB
    attributes      JSONB NOT NULL DEFAULT '{}',
    provenance      JSONB NOT NULL DEFAULT '{}',
    validation      JSONB NOT NULL DEFAULT '{}',
    status          TEXT NOT NULL DEFAULT 'pending'
                    CHECK (status IN ('pending','approved','rejected')),
    created_by      UUID REFERENCES users(id),
    approved_by     UUID REFERENCES users(id),
    created_at      TIMESTAMPTZ DEFAULT NOW(),
    updated_at      TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX idx_products_org     ON products(org_id);
CREATE INDEX idx_products_type    ON products(product_type);
CREATE INDEX idx_products_status  ON products(status);
CREATE INDEX idx_products_attrs   ON products USING GIN(attributes);

-- ── Audit log ────────────────────────────────────────────────
-- Immutable append-only log of all data mutations
CREATE TABLE audit_log (
    id          BIGSERIAL PRIMARY KEY,
    org_id      UUID NOT NULL REFERENCES organizations(id),
    user_id     UUID REFERENCES users(id),
    action      TEXT NOT NULL,      -- INSERT, UPDATE, DELETE, LOGIN, EXPORT
    table_name  TEXT,
    record_id   UUID,
    old_values  JSONB,
    new_values  JSONB,
    ip_address  INET,
    user_agent  TEXT,
    created_at  TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX idx_audit_org  ON audit_log(org_id, created_at);
CREATE INDEX idx_audit_user ON audit_log(user_id, created_at);

-- No UPDATE or DELETE on audit_log — enforced by trigger
CREATE OR REPLACE FUNCTION prevent_audit_mutation()
RETURNS TRIGGER AS $$
BEGIN
    RAISE EXCEPTION 'audit_log is immutable — no UPDATE or DELETE allowed';
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER audit_log_immutable
BEFORE UPDATE OR DELETE ON audit_log
FOR EACH ROW EXECUTE FUNCTION prevent_audit_mutation();

-- ── Audit trigger for products ────────────────────────────────
CREATE OR REPLACE FUNCTION audit_products_change()
RETURNS TRIGGER AS $$
BEGIN
    INSERT INTO audit_log(org_id, action, table_name, record_id, old_values, new_values)
    VALUES (
        COALESCE(NEW.org_id, OLD.org_id),
        TG_OP,
        'products',
        COALESCE(NEW.id, OLD.id),
        CASE WHEN TG_OP != 'INSERT' THEN to_jsonb(OLD) ELSE NULL END,
        CASE WHEN TG_OP != 'DELETE' THEN to_jsonb(NEW) ELSE NULL END
    );
    RETURN COALESCE(NEW, OLD);
END;
$$ LANGUAGE plpgsql SECURITY DEFINER;

CREATE TRIGGER products_audit
AFTER INSERT OR UPDATE OR DELETE ON products
FOR EACH ROW EXECUTE FUNCTION audit_products_change();

-- ── updated_at auto-stamp ─────────────────────────────────────
CREATE OR REPLACE FUNCTION set_updated_at()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER products_updated_at
BEFORE UPDATE ON products
FOR EACH ROW EXECUTE FUNCTION set_updated_at();

CREATE TRIGGER users_updated_at
BEFORE UPDATE ON users
FOR EACH ROW EXECUTE FUNCTION set_updated_at();

-- ============================================================
-- ROW-LEVEL SECURITY
-- ============================================================

-- Enable RLS on all tenant tables
ALTER TABLE products    ENABLE ROW LEVEL SECURITY;
ALTER TABLE users       ENABLE ROW LEVEL SECURITY;
ALTER TABLE audit_log   ENABLE ROW LEVEL SECURITY;
ALTER TABLE sessions    ENABLE ROW LEVEL SECURITY;

-- ── Helper: get current user's org_id from JWT claim ─────────
-- The app sets app.current_user_id and app.current_org_id
-- via SET LOCAL before every query.
CREATE OR REPLACE FUNCTION current_org_id() RETURNS UUID AS $$
    SELECT NULLIF(current_setting('app.current_org_id', TRUE), '')::UUID;
$$ LANGUAGE SQL STABLE SECURITY DEFINER;

CREATE OR REPLACE FUNCTION current_user_id() RETURNS UUID AS $$
    SELECT NULLIF(current_setting('app.current_user_id', TRUE), '')::UUID;
$$ LANGUAGE SQL STABLE SECURITY DEFINER;

CREATE OR REPLACE FUNCTION current_user_role() RETURNS apex_role AS $$
    SELECT NULLIF(current_setting('app.current_user_role', TRUE), '')::apex_role;
$$ LANGUAGE SQL STABLE SECURITY DEFINER;

-- ── Products RLS policies ─────────────────────────────────────

-- SELECT: users see only their org's products
CREATE POLICY products_select ON products FOR SELECT
    USING (org_id = current_org_id());

-- INSERT: operators and admins can create products
CREATE POLICY products_insert ON products FOR INSERT
    WITH CHECK (
        org_id = current_org_id()
        AND current_user_role() IN ('operator', 'admin')
    );

-- UPDATE: operators can update pending products; admins can update any
CREATE POLICY products_update ON products FOR UPDATE
    USING (org_id = current_org_id())
    WITH CHECK (
        org_id = current_org_id()
        AND (
            current_user_role() = 'admin'
            OR (current_user_role() = 'operator' AND status = 'pending')
        )
    );

-- DELETE: admin only
CREATE POLICY products_delete ON products FOR DELETE
    USING (
        org_id = current_org_id()
        AND current_user_role() = 'admin'
    );

-- ── Users RLS policies ────────────────────────────────────────

-- Users see only their own org's users
CREATE POLICY users_select ON users FOR SELECT
    USING (org_id = current_org_id());

-- Only admins can create or modify users
CREATE POLICY users_insert ON users FOR INSERT
    WITH CHECK (
        org_id = current_org_id()
        AND current_user_role() = 'admin'
    );

CREATE POLICY users_update ON users FOR UPDATE
    USING (org_id = current_org_id())
    WITH CHECK (
        org_id = current_org_id()
        AND (
            current_user_role() = 'admin'
            OR id = current_user_id()  -- users can update own record
        )
    );

-- ── Audit log RLS ─────────────────────────────────────────────

-- All roles can read their org's audit log; no one can write directly
CREATE POLICY audit_select ON audit_log FOR SELECT
    USING (org_id = current_org_id());

-- ── Sessions RLS ──────────────────────────────────────────────

-- Users can only see and revoke their own sessions
CREATE POLICY sessions_select ON sessions FOR SELECT
    USING (user_id = current_user_id());

CREATE POLICY sessions_update ON sessions FOR UPDATE
    USING (user_id = current_user_id());

-- ============================================================
-- RATE LIMIT HELPER (called from app layer)
-- Returns TRUE if the identifier is rate-limited
-- ============================================================
CREATE OR REPLACE FUNCTION is_rate_limited(
    p_identifier TEXT,
    p_ip         INET,
    p_max_attempts INT DEFAULT 5,
    p_window_minutes INT DEFAULT 15
) RETURNS BOOLEAN AS $$
DECLARE
    attempt_count INT;
BEGIN
    SELECT COUNT(*) INTO attempt_count
    FROM login_attempts
    WHERE (identifier = p_identifier OR ip_address = p_ip)
      AND success = FALSE
      AND attempted_at > NOW() - (p_window_minutes || ' minutes')::INTERVAL;

    RETURN attempt_count >= p_max_attempts;
END;
$$ LANGUAGE plpgsql SECURITY DEFINER;
