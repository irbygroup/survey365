-- Survey365 Migration 004: Seed ALDOT CORS NTRIP profiles
-- Alabama DOT Continuously Operating Reference Stations

INSERT OR IGNORE INTO ntrip_profiles (name, type, host, port, mountpoint, username, password, is_default, notes)
VALUES
    ('ALDOT CORS - Mobile', 'inbound_cors', 'aldotcors.dot.state.al.us', 2101, 'MOBI_RTCM3', '', '', 1, 'Mobile, AL - closest to base station'),
    ('ALDOT CORS - Daphne', 'inbound_cors', 'aldotcors.dot.state.al.us', 2101, 'DAPH_RTCM3', '', '', 0, 'Daphne, AL - Eastern Shore'),
    ('ALDOT CORS - Evergreen', 'inbound_cors', 'aldotcors.dot.state.al.us', 2101, 'EGRN_RTCM3', '', '', 0, 'Evergreen, AL - north of Mobile');
