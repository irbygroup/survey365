-- Migration 003: GNSS configuration keys
-- Stores GNSS and RTCM output config in the Survey365 database.

-- GNSS receiver configuration
INSERT OR IGNORE INTO config (key, value) VALUES ('gnss_port', '/dev/ttyGNSS');
INSERT OR IGNORE INTO config (key, value) VALUES ('gnss_baud', '115200');
INSERT OR IGNORE INTO config (key, value) VALUES ('gnss_backend', 'ublox');

-- RTCM output configuration
INSERT OR IGNORE INTO config (key, value) VALUES ('rtcm_messages', '1005,1077,1087,1097,1127,1230(10)');
INSERT OR IGNORE INTO config (key, value) VALUES ('rinex_enabled', 'true');
INSERT OR IGNORE INTO config (key, value) VALUES ('rinex_rotate_hours', '24');
INSERT OR IGNORE INTO config (key, value) VALUES ('rinex_data_dir', 'data/rinex');
INSERT OR IGNORE INTO config (key, value) VALUES ('local_caster_enabled', 'false');
INSERT OR IGNORE INTO config (key, value) VALUES ('local_caster_port', '2101');
INSERT OR IGNORE INTO config (key, value) VALUES ('local_caster_mountpoint', 'SURVEY365');
