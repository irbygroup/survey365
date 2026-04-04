-- Migration 009: RTKLIB output configuration defaults.

INSERT OR IGNORE INTO config (key, value) VALUES ('rtcm_engine', 'rtklib');
INSERT OR IGNORE INTO config (key, value) VALUES ('rtklib_local_messages', '1004,1005(10),1006,1008(10),1012,1019,1020,1033(10),1042,1045,1046,1077,1087,1097,1107,1127,1230');
INSERT OR IGNORE INTO config (key, value) VALUES ('rtklib_outbound_messages', '1004,1005(10),1006,1008(10),1012,1019,1020,1033(10),1042,1045,1046,1077,1087,1097,1107,1127,1230');
INSERT OR IGNORE INTO config (key, value) VALUES ('antenna_descriptor', 'ADVNULLANTENNA');
