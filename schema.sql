-- FITFXR Database Schema
-- Run this in the Supabase SQL Editor (supabase.com > your project > SQL Editor)

-- 1. Sessions — one row per completed recommendation flow
CREATE TABLE sessions (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    username    TEXT,
    is_guest    BOOLEAN NOT NULL DEFAULT TRUE,
    age         TEXT,
    sex         TEXT,
    weight      TEXT,
    height      TEXT,
    shoe_size   TEXT,
    width       TEXT,
    arch        TEXT,
    injuries    JSONB NOT NULL DEFAULT '[]',
    waterproof  TEXT,
    priorities  JSONB NOT NULL DEFAULT '[]',
    app_version TEXT DEFAULT '1.0'
);

-- 2. Activities — one row per activity per session
CREATE TABLE session_activities (
    id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    session_id   UUID NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    activity     TEXT NOT NULL,
    is_occasional BOOLEAN NOT NULL DEFAULT FALSE,
    answers      JSONB NOT NULL DEFAULT '{}',
    search_query TEXT
);

CREATE INDEX idx_sa_session_id ON session_activities(session_id);
CREATE INDEX idx_sa_activity   ON session_activities(activity);

-- 3. Products shown — one row per product card displayed to the user
CREATE TABLE products_shown (
    id                   UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    session_activity_id  UUID NOT NULL REFERENCES session_activities(id) ON DELETE CASCADE,
    created_at           TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    rank                 SMALLINT NOT NULL,
    title                TEXT,
    price                TEXT,
    source               TEXT,
    rating               NUMERIC(3,1),
    reviews              INTEGER,
    product_link         TEXT,
    thumbnail_url        TEXT
);

CREATE INDEX idx_products_sa   ON products_shown(session_activity_id);
CREATE INDEX idx_products_src  ON products_shown(source);

-- 4. Admin users (for the /admin dashboard)
CREATE TABLE admin_users (
    id            SERIAL PRIMARY KEY,
    username      TEXT UNIQUE NOT NULL,
    password_hash TEXT NOT NULL
);

-- After running this file, generate a bcrypt hash for your admin password:
--   python3 -c "import bcrypt; print(bcrypt.hashpw(b'yourpassword', bcrypt.gensalt()).decode())"
-- Then insert it:
--   INSERT INTO admin_users (username, password_hash) VALUES ('admin', '$2b$12$...');
