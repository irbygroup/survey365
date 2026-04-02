CREATE TABLE IF NOT EXISTS wifi_networks (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    ssid        TEXT NOT NULL UNIQUE,
    psk         TEXT NOT NULL DEFAULT '',
    priority    INTEGER NOT NULL DEFAULT 0,
    metric      INTEGER NOT NULL DEFAULT 50,
    created_at  TEXT DEFAULT (datetime('now')),
    updated_at  TEXT DEFAULT (datetime('now'))
);
