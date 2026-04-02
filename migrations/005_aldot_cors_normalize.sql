-- Survey365 Migration 005: Normalize ALDOT CORS NTRIP profiles
-- Ensures upgraded databases converge to the intended seeded profile set.

DELETE FROM ntrip_profiles
WHERE type = 'inbound_cors'
  AND name LIKE 'ALDOT CORS%';

INSERT INTO ntrip_profiles (name, type, host, port, mountpoint, username, password, is_default, notes)
VALUES
    ('ALDOT CORS - Mobile', 'inbound_cors', 'aldotcors.dot.state.al.us', 2101, 'MOBI_RTCM3', '', '', 1, 'Mobile, AL - closest to base station'),
    ('ALDOT CORS - Daphne', 'inbound_cors', 'aldotcors.dot.state.al.us', 2101, 'DAPH_RTCM3', '', '', 0, 'Daphne, AL - Eastern Shore'),
    ('ALDOT CORS - Evergreen', 'inbound_cors', 'aldotcors.dot.state.al.us', 2101, 'EGRN_RTCM3', '', '', 0, 'Evergreen, AL - north of Mobile');
