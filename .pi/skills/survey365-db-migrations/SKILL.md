---
name: survey365-db-migrations
description: Database migration patterns for Survey365's SQLite + SpatiaLite schema. Use when adding columns, creating tables, writing migrations, changing the database schema, or working with the config key-value store.
---

# Survey365 Database Migrations

## Database Stack

- **SQLite** — single-file database, no server process
- **SpatiaLite** — spatial extension for geometry columns and spatial queries
- **aiosqlite** — async Python wrapper for sqlite3
- **WAL mode** — enabled on every connection for concurrent reads
- **PRAGMA synchronous=FULL** — durability over write speed on field devices
- Database path: `SURVEY365_DB` env var (default: `data/survey365.db`, resilient: `/srv/survey365/survey365.db`)

## Migration System

Migrations live in `migrations/` as numbered SQL files:

```
migrations/
├── 001_initial.sql              # Core schema: sites, sessions, config, ntrip_profiles
├── 002_projects.sql             # Projects table, project_id FK on sites/sessions
├── 003_gnss_config.sql          # GNSS config keys in config table
├── 004_aldot_cors.sql           # Seed ALDOT CORS NTRIP profiles
├── 005_aldot_cors_normalize.sql # Fix seeded profiles on upgraded DBs
├── 006_antenna_height_config.sql # antenna_height_m config key
├── 007_network_device_config.sql # wifi_networks table
├── 008_remove_imei_device_config.sql # Clean up legacy config keys
└── 009_rtklib_config.sql        # RTKLIB engine config defaults
```

## How Migrations Run

Migrations are applied by `app/db.py:init_db()`, which runs at app startup. **There is no migration framework** — each migration has a hand-written guard condition:

```python
# Pattern: check if the migration's artifact already exists
cursor = await db.execute(
    "SELECT name FROM sqlite_master WHERE type='table' AND name='projects'"
)
if await cursor.fetchone() is None:
    # Run migration SQL statements one at a time
    migration_file = MIGRATIONS_DIR / "002_projects.sql"
    sql = migration_file.read_text()
    for stmt in [s.strip() for s in sql.split(";") if s.strip()]:
        try:
            await db.execute(stmt)
        except Exception:
            pass  # Tolerate re-runs (IF NOT EXISTS, OR IGNORE)
```

Each guard checks for different things depending on the migration:
- **001**: checks if `sites` table exists
- **002**: checks if `projects` table exists
- **003**: checks if `gnss_port` config key exists
- **004**: checks if any `ALDOT CORS%` ntrip_profiles exist
- **005**: checks profile normalization state with a count query
- **006**: checks if `antenna_height_m` config key exists
- **007**: checks if `wifi_networks` table exists
- **008**: checks if any legacy IMEI config keys exist
- **009**: checks if `rtcm_engine` config key exists

## Writing a New Migration

### 1. Choose the next number
```
010_your_description.sql
```

### 2. Write idempotent SQL
```sql
-- Migration 010: Description of what this does
CREATE TABLE IF NOT EXISTS new_table (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    created_at TEXT DEFAULT (datetime('now'))
);

-- For new config keys, use INSERT OR IGNORE
INSERT OR IGNORE INTO config (key, value) VALUES ('new_key', 'default_value');
```

### 3. Add the guard condition in `app/db.py:init_db()`
```python
# --- Migration 010: Description ---
cursor = await db.execute(
    "SELECT name FROM sqlite_master WHERE type='table' AND name='new_table'"
)
if await cursor.fetchone() is None:
    migration_file = MIGRATIONS_DIR / "010_your_description.sql"
    if migration_file.exists():
        sql = migration_file.read_text()
        for stmt in [s.strip() for s in sql.split(";") if s.strip()]:
            try:
                await db.execute(stmt)
            except Exception:
                pass
```

### 4. Place the guard BEFORE the final `await db.commit()`

## SQLite Limitations to Know

### No DROP COLUMN (before SQLite 3.35.0)
Debian trixie ships SQLite ≥ 3.40 so `ALTER TABLE DROP COLUMN` works. But if you need to support older versions:
```sql
-- Create new table, copy data, drop old, rename
CREATE TABLE new_table AS SELECT col1, col2 FROM old_table;
DROP TABLE old_table;
ALTER TABLE new_table RENAME TO old_table;
```

### ALTER TABLE ADD COLUMN
- Cannot add `NOT NULL` columns without a default value
- Cannot add `PRIMARY KEY` or `UNIQUE` constraints inline
- Pattern: `ALTER TABLE sites ADD COLUMN new_col TEXT DEFAULT '';`

### No concurrent writers
WAL mode allows concurrent reads with a single writer. Writes are serialized. This is fine for Survey365's single-server architecture.

## Core Tables

### `sites` — Survey control points
| Column | Type | Notes |
|--------|------|-------|
| id | INTEGER PK | Auto-increment |
| name | TEXT NOT NULL | User-visible name |
| lat, lon | REAL NOT NULL | WGS84 decimal degrees |
| height | REAL | Ellipsoidal height (meters) |
| ortho_height | REAL | Orthometric height (NAVD88) |
| datum | TEXT | Default `NAD83(2011)` |
| source | TEXT | `manual`, `cors_rtk`, `opus`, `averaged`, `imported` |
| accuracy_h, accuracy_v | REAL | Meters |
| project_id | INTEGER FK | References `projects(id)` |
| geom | GEOMETRY | SpatiaLite POINT (4326) — may not exist if SpatiaLite unavailable |

### `sessions` — Operating mode history
| Column | Type | Notes |
|--------|------|-------|
| mode | TEXT NOT NULL | `known_base`, `relative_base`, `cors_establish`, `rover`, `idle` |
| site_id | INTEGER FK | References `sites(id)` |
| started_at, ended_at | TEXT | ISO datetime |
| project_id | INTEGER FK | References `projects(id)` |

### `config` — Key-value store
| Column | Type | Notes |
|--------|------|-------|
| key | TEXT PK | Config key name |
| value | TEXT | Config value |
| updated_at | TEXT | Last modified |

Helper functions: `get_config(key)`, `set_config(key, value)`, `get_all_config()`

### `ntrip_profiles` — NTRIP connection profiles
| Column | Type | Notes |
|--------|------|-------|
| type | TEXT NOT NULL | `outbound_caster`, `inbound_cors`, `local_caster` |
| host, port, mountpoint | — | Connection details |
| username, password | TEXT | Credentials |
| is_default | INTEGER | 0 or 1 |

### `wifi_networks` — Wi-Fi connection profiles
| Column | Type | Notes |
|--------|------|-------|
| ssid | TEXT NOT NULL UNIQUE | Network name |
| psk | TEXT | Pre-shared key |
| priority | INTEGER | NetworkManager priority |
| metric | INTEGER | Route metric (lower = preferred) |

### `projects` — Project organization
| Column | Type | Notes |
|--------|------|-------|
| name | TEXT NOT NULL | Project name |
| description, client | TEXT | Optional metadata |
| last_accessed | TEXT | Updated when project is activated |

## SpatiaLite Usage

SpatiaLite is loaded as an extension on every connection:
```python
ext = _get_spatialite_ext()  # "mod_spatialite" or "libspatialite"
await db.enable_load_extension(True)
await db.load_extension(ext)
```

Geometry operations:
```sql
-- Create point geometry
UPDATE sites SET geom = MakePoint(lon, lat, 4326) WHERE id = ?;

-- Spatial query (find nearby sites)
SELECT *, ST_Distance(geom, MakePoint(?, ?, 4326), 1) as dist_m
FROM sites WHERE dist_m < 1000 ORDER BY dist_m;
```

SpatiaLite may not be available in dev environments. The code handles this gracefully — geometry columns and spatial indexes are optional.

## Important Config Keys

| Key | Default | Purpose |
|-----|---------|---------|
| `gnss_port` | `/dev/ttyGNSS` | GNSS serial port |
| `gnss_baud` | `115200` | Serial baud rate |
| `gnss_backend` | `ublox` | Receiver backend |
| `rtcm_engine` | `rtklib` | RTCM encoding engine (`native` or `rtklib`) |
| `rtcm_messages` | `1005,1077,...` | Native-mode RTCM message selection |
| `rtklib_local_messages` | `1004,1005(10),...` | RTKLIB local caster message set |
| `rtklib_outbound_messages` | `1004,1005(10),...` | RTKLIB outbound push message set |
| `antenna_descriptor` | `ADVNULLANTENNA` | RTCM antenna descriptor string |
| `active_project_id` | (empty) | Currently selected project |
| `auto_resume` | `false` | Auto-resume last session on boot |
| `maptiler_key` | (empty) | MapTiler API key for basemap tiles |
| `antenna_height_m` | `0.0` | Antenna height above ground mark |

## Rules

1. **Never modify a deployed migration** — if it's been applied to any database (dev or prod), create a new migration instead
2. **Always use `INSERT OR IGNORE` for config seeding** — prevents overwriting user-set values
3. **Guard conditions must be unique** — each migration needs a distinct check so it only runs once
4. **Split SQL by `;` and execute statement-by-statement** — `executescript()` doesn't work with loaded extensions
5. **Wrap SpatiaLite DDL in try/except** — it's optional and may not be available
6. **Test against a copy of prod DB** — pull from Pi via `scp jaredirby@rtkbase-pi:/srv/survey365/survey365.db /tmp/test.db`
