-- Survey365 Phase 1 Schema
-- SQLite + SpatiaLite

-- Saved survey points
CREATE TABLE IF NOT EXISTS sites (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    name            TEXT NOT NULL,
    lat             REAL NOT NULL,
    lon             REAL NOT NULL,
    height          REAL,
    ortho_height    REAL,
    datum           TEXT DEFAULT 'NAD83(2011)',
    epoch           TEXT DEFAULT '2010.0',
    source          TEXT CHECK(source IN ('manual','cors_rtk','opus','averaged','imported')),
    accuracy_h      REAL,
    accuracy_v      REAL,
    established     TEXT,
    last_used       TEXT,
    notes           TEXT,
    photo_path      TEXT,
    opus_job_id     TEXT,
    created_at      TEXT DEFAULT (datetime('now')),
    updated_at      TEXT DEFAULT (datetime('now'))
);

-- Session history (for resume + logging)
CREATE TABLE IF NOT EXISTS sessions (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    mode             TEXT NOT NULL CHECK(mode IN ('known_base','relative_base','cors_establish','opus_establish','rover','idle')),
    site_id          INTEGER REFERENCES sites(id),
    ntrip_profile_id INTEGER,
    started_at       TEXT DEFAULT (datetime('now')),
    ended_at         TEXT,
    rinex_path       TEXT,
    notes            TEXT
);

-- App configuration (key-value store)
CREATE TABLE IF NOT EXISTS config (
    key        TEXT PRIMARY KEY,
    value      TEXT,
    updated_at TEXT DEFAULT (datetime('now'))
);

-- NTRIP profiles (simplified for Phase 1)
CREATE TABLE IF NOT EXISTS ntrip_profiles (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    name        TEXT NOT NULL,
    type        TEXT NOT NULL CHECK(type IN ('outbound_caster','inbound_cors','local_caster')),
    host        TEXT,
    port        INTEGER,
    mountpoint  TEXT,
    username    TEXT,
    password    TEXT,
    is_default  INTEGER DEFAULT 0,
    notes       TEXT,
    created_at  TEXT DEFAULT (datetime('now')),
    updated_at  TEXT DEFAULT (datetime('now'))
);

-- Seed default config values
INSERT OR IGNORE INTO config (key, value) VALUES
    ('web_password_hash', ''),
    ('session_secret', ''),
    ('auto_resume', 'false'),
    ('maptiler_key', ''),
    ('antenna_voltage_on_boot', 'true'),
    ('f9p_update_rate', '1'),
    ('default_lat', '30.69'),
    ('default_lon', '-88.05'),
    ('default_zoom', '11');
