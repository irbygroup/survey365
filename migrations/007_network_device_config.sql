CREATE TABLE IF NOT EXISTS wifi_networks (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    ssid        TEXT NOT NULL UNIQUE,
    psk         TEXT NOT NULL DEFAULT '',
    priority    INTEGER NOT NULL DEFAULT 0,
    metric      INTEGER NOT NULL DEFAULT 50,
    created_at  TEXT DEFAULT (datetime('now')),
    updated_at  TEXT DEFAULT (datetime('now'))
);

INSERT OR IGNORE INTO config (key, value) VALUES
    ('original_imei', ''),
    ('generated_imei', ''),
    ('generated_model', ''),
    ('generated_date', ''),
    ('imei_api_token', ''),
    ('imei_max_retries', '5'),
    ('imei_models', ''),
    ('check_lost_device', 'true'),
    ('check_verizon', 'true'),
    ('check_tmobile', 'true'),
    ('check_blacklist', 'true');
