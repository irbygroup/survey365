-- Migration 006: Antenna height configuration
-- Height of antenna reference point above the ground mark, in meters.

INSERT OR IGNORE INTO config (key, value) VALUES ('antenna_height_m', '0.0');
