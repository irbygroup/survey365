-- Migration 003: GNSS configuration keys
-- Stores GNSS and RTCM output config in the Survey365 database.

-- GNSS receiver configuration
INSERT OR IGNORE INTO config (key, value) VALUES ('gnss_port', '/dev/ttyGNSS');
INSERT OR IGNORE INTO config (key, value) VALUES ('gnss_baud', '115200');
INSERT OR IGNORE INTO config (key, value) VALUES ('gnss_backend', 'ublox');
INSERT OR IGNORE INTO config (key, value) VALUES ('rtcm_engine', 'rtklib');
INSERT OR IGNORE INTO config (key, value) VALUES ('rtklib_local_messages', '1004,1005(10),1006,1008(10),1012,1019,1020,1033(10),1042,1045,1046,1077,1087,1097,1107,1127,1230');
INSERT OR IGNORE INTO config (key, value) VALUES ('rtklib_outbound_messages', '1004,1005(10),1006,1008(10),1012,1019,1020,1033(10),1042,1045,1046,1077,1087,1097,1107,1127,1230');
INSERT OR IGNORE INTO config (key, value) VALUES ('antenna_descriptor', 'ADVNULLANTENNA');

-- RTCM output configuration
INSERT OR IGNORE INTO config (key, value) VALUES ('rtcm_messages', '1005,1077,1087,1097,1127,1230(10)');
INSERT OR IGNORE INTO config (key, value) VALUES ('rinex_enabled', 'true');
INSERT OR IGNORE INTO config (key, value) VALUES ('rinex_rotate_hours', '24');
INSERT OR IGNORE INTO config (key, value) VALUES ('rinex_data_dir', 'data/rinex');
INSERT OR IGNORE INTO config (key, value) VALUES ('local_caster_enabled', 'false');
INSERT OR IGNORE INTO config (key, value) VALUES ('local_caster_port', '2101');
INSERT OR IGNORE INTO config (key, value) VALUES ('local_caster_mountpoint', 'SURVEY365');
