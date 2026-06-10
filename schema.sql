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

-- 4. User profiles — persistent preferences loaded on login
CREATE TABLE user_profiles (
    username    TEXT PRIMARY KEY,
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    age         TEXT,
    sex         TEXT,
    weight      TEXT,
    height      TEXT,
    shoe_size   TEXT,
    width       TEXT,
    arch        TEXT,
    injuries    JSONB NOT NULL DEFAULT '[]',
    waterproof  TEXT,
    priorities  JSONB NOT NULL DEFAULT '[]'
);

ALTER TABLE user_profiles ENABLE ROW LEVEL SECURITY;

CREATE POLICY "Users manage own profile"
  ON user_profiles FOR ALL
  TO authenticated
  USING (username = auth.email())
  WITH CHECK (username = auth.email());

CREATE POLICY "Allow insert profile from backend"
  ON user_profiles FOR ALL
  TO anon
  WITH CHECK (true);

-- 5. Admin users (for the /admin dashboard)
CREATE TABLE admin_users (
    id            SERIAL PRIMARY KEY,
    username      TEXT UNIQUE NOT NULL,
    password_hash TEXT NOT NULL
);

-- After running this file, generate a bcrypt hash for your admin password:
--   python3 -c "import bcrypt; print(bcrypt.hashpw(b'yourpassword', bcrypt.gensalt()).decode())"
-- Then insert it:
--   INSERT INTO admin_users (username, password_hash) VALUES ('admin', '$2b$12$...');

-- ─────────────────────────────────────────────────────────────────────────────
-- ROW LEVEL SECURITY (RLS)
-- Industry-standard data isolation — users cannot read or modify other users'
-- rows even if they obtain the anon API key.
--
-- The Python backend (db.py) must use the SERVICE_ROLE key (never the anon
-- key) so inserts bypass RLS. Add SUPABASE_SERVICE_KEY to your Streamlit
-- secrets and update db.py to use it.
-- ─────────────────────────────────────────────────────────────────────────────

ALTER TABLE sessions          ENABLE ROW LEVEL SECURITY;
ALTER TABLE session_activities ENABLE ROW LEVEL SECURITY;
ALTER TABLE products_shown    ENABLE ROW LEVEL SECURITY;
ALTER TABLE admin_users       ENABLE ROW LEVEL SECURITY;

-- Service role bypasses RLS automatically — no policy needed for it.
-- These policies cover the anon / authenticated roles used by end users.

-- sessions: users can only see their own rows (matched by username).
--           Guests (is_guest = true) are write-only — no read-back.
CREATE POLICY "Users read own sessions"
  ON sessions FOR SELECT
  TO authenticated
  USING (username = auth.email());

CREATE POLICY "Allow insert from backend"
  ON sessions FOR INSERT
  TO anon, authenticated
  WITH CHECK (true);  -- service role handles writes; anon inserts are safe
                       -- because no sensitive data is readable via anon SELECT.

-- session_activities: inherit access through parent session
CREATE POLICY "Users read own activities"
  ON session_activities FOR SELECT
  TO authenticated
  USING (
    session_id IN (
      SELECT id FROM sessions WHERE username = auth.email()
    )
  );

CREATE POLICY "Allow insert activities from backend"
  ON session_activities FOR INSERT
  TO anon, authenticated
  WITH CHECK (true);

-- products_shown: same chain
CREATE POLICY "Users read own products"
  ON products_shown FOR SELECT
  TO authenticated
  USING (
    session_activity_id IN (
      SELECT sa.id FROM session_activities sa
      JOIN sessions s ON s.id = sa.session_id
      WHERE s.username = auth.email()
    )
  );

CREATE POLICY "Allow insert products from backend"
  ON products_shown FOR INSERT
  TO anon, authenticated
  WITH CHECK (true);

-- admin_users: only service role (never expose to anon/authenticated)
CREATE POLICY "No anon access to admin_users"
  ON admin_users FOR ALL
  TO anon, authenticated
  USING (false)
  WITH CHECK (false);
