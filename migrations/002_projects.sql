-- Survey365 Migration 002: Projects
-- Add project-based organization for sites and sessions

-- Projects table
CREATE TABLE IF NOT EXISTS projects (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    name          TEXT NOT NULL,
    description   TEXT,
    client        TEXT,
    created_at    TEXT DEFAULT (datetime('now')),
    updated_at    TEXT DEFAULT (datetime('now')),
    last_accessed TEXT
);

-- Add project_id to sites
ALTER TABLE sites ADD COLUMN project_id INTEGER REFERENCES projects(id);

-- Add project_id to sessions
ALTER TABLE sessions ADD COLUMN project_id INTEGER REFERENCES projects(id);

-- Track active project in config
INSERT OR IGNORE INTO config (key, value) VALUES ('active_project_id', '');
