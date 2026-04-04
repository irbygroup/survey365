---
name: survey365-ui
description: Frontend development patterns for Survey365's Alpine.js + HTMX + MapLibre stack. Use when working on HTML templates, JavaScript modules, CSS, the map interface, WebSocket live updates, or any UI/frontend changes. No build step — all files served directly.
---

# Survey365 Frontend

## Stack — No Build Step

| Technology | Version | Role |
|-----------|---------|------|
| Alpine.js | 3.14.8 | Reactive components via `x-data`, `x-bind`, `x-show`, `x-for` |
| HTMX | 2.0.4 | Server-driven partials (used lightly; most state is Alpine) |
| Pico.css | 2.x | Classless responsive base styling |
| MapLibre GL JS | 4.7.1 | Vector tile map rendering, GeoJSON layers |
| MapTiler | — | Basemap tiles (street, satellite, topo) — requires API key |

All loaded via CDN (`unpkg.com`, `cdn.jsdelivr.net`). **No npm, no bundler, no build step.** Files are served directly by FastAPI's `StaticFiles` mount.

## File Structure

```
ui/
├── index.html          # Main map app (625 lines) — field crew interface
├── admin.html          # Admin settings panel (1331 lines)
├── login.html          # Simple auth form (90 lines)
├── css/
│   └── survey365.css   # All custom styles (2137 lines), s365-* prefix
└── js/
    ├── map-core.js     # MapLibre setup, tile layers, map controls (682 lines)
    ├── map-sites.js    # GeoJSON source/layer for survey sites (143 lines)
    ├── mode-panel.js   # Mode switching UI: base, establish, stop (1063 lines)
    └── status.js       # GNSS status display, satellite bars (320 lines)
```

## Design Principles

### Mobile-first field UI
- **Primary users:** field crews on phones in direct sunlight, possibly wearing gloves
- Big touch targets (minimum 44×44px), high contrast
- Full-screen map — everything else is overlay or slide panel
- `user-scalable=no` on index.html to prevent accidental zoom
- `apple-mobile-web-app-status-bar-style: black-translucent` for PWA feel

### The map is primary
- `index.html` is a full-screen MapLibre GL map
- Sites are GeoJSON points rendered as map layers
- Mode controls float over the map as overlay panels
- Status bar shows GNSS fix, satellites, accuracy at the top

## Alpine.js Patterns

### Component initialization
```html
<body x-data="survey365App()" x-init="init()" x-cloak>
```

The main app function returns an Alpine component object with reactive state and methods. It's defined at the bottom of `index.html` in a `<script>` tag.

### Reactive state → API fetch pattern
```javascript
// In the Alpine component
async loadSites() {
    const resp = await fetch('/api/sites');
    this.sites = await resp.json();
    this.updateMapSites();  // Push to MapLibre GeoJSON source
},
```

### WebSocket live updates
```javascript
init() {
    this.connectWebSocket();
},
connectWebSocket() {
    this.ws = new WebSocket(`ws://${location.host}/ws/live`);
    this.ws.onmessage = (event) => {
        const data = JSON.parse(event.data);
        if (data.type === 'status') {
            this.gnss = data.gnss;
            this.mode = data.mode;
        } else if (data.type === 'mode_change') {
            // ...
        }
    };
}
```

The WebSocket at `/ws/live` sends JSON every ~1 second with:
- `type: "status"` — GNSS state, mode state, services
- `type: "mode_change"` — mode transitions
- `type: "establish_progress"` — averaging/CORS establish progress
- `type: "establish_error"` — establish failure notification

## MapLibre Patterns

### Initialization (in `map-core.js`)
```javascript
const map = new maplibregl.Map({
    container: 'map',
    style: `https://api.maptiler.com/maps/streets-v2/style.json?key=${maptilerKey}`,
    center: [defaultLon, defaultLat],
    zoom: defaultZoom,
});
```

### GeoJSON sites layer (in `map-sites.js`)
```javascript
map.addSource('sites', {
    type: 'geojson',
    data: { type: 'FeatureCollection', features: [] }
});
map.addLayer({
    id: 'sites-points',
    type: 'circle',
    source: 'sites',
    paint: { 'circle-radius': 8, 'circle-color': '#3b82f6' }
});
```

To update sites: `map.getSource('sites').setData(geojsonFeatureCollection);`

## CSS Conventions

- All custom classes use `s365-` prefix (e.g., `s365-admin-header`, `s365-backend-overlay`)
- Pico.css provides the base — avoid fighting its classless styles
- Use CSS custom properties for theme values where possible
- `x-cloak` attribute hides content until Alpine initializes (prevents flash)
- Dark header bar with `background: #0f172a` (slate-900)

## Admin Panel (`admin.html`)

The admin page is a separate Alpine component (`adminPanel()`) with tab-based sections:
- **GNSS** — receiver config (port, baud, backend, RTCM engine)
- **NTRIP** — inbound CORS profiles, outbound caster config
- **Wi-Fi** — network CRUD + apply button
- **System** — update status, reboot, password change

Each section fetches its own API endpoints. Admin requires authentication via `/api/auth/login`.

## API Endpoints the UI Uses

### Field (no auth)
| Method | Path | Purpose |
|--------|------|---------|
| GET | `/api/status` | Full GNSS + mode + service status |
| GET | `/api/sites` | List sites for active project |
| GET | `/api/projects` | List projects |
| POST | `/api/mode/known-base` | Start broadcasting from known point |
| POST | `/api/mode/relative-base` | Start position averaging |
| POST | `/api/mode/cors-establish` | Start CORS RTK establish |
| POST | `/api/mode/stop` | Stop current mode |
| POST | `/api/mode/resume` | Resume last session |
| WS | `/ws/live` | Live status stream |

### Admin (auth required)
| Method | Path | Purpose |
|--------|------|---------|
| GET/PUT | `/api/config` | Read/write config key-values |
| GET/POST/DELETE | `/api/ntrip/profiles` | NTRIP profile CRUD |
| GET/POST/PUT/DELETE | `/api/wifi/networks` | Wi-Fi network CRUD |
| POST | `/api/wifi/apply` | Apply Wi-Fi config to NetworkManager |
| GET | `/api/system/update-status` | Check for available updates |
| POST | `/api/system/update` | Trigger update |
| POST | `/api/system/reboot` | Reboot the Pi |

## Backend Disconnect Overlay

`index.html` includes a full-screen overlay (`s365-backend-overlay`) that appears when the backend is unreachable:
- Shows "Connection Lost" with a retry timer
- After 180 seconds: shows "Unable to reconnect" (hard fail)
- Retries continuously in the background regardless

## Project Gate

Before showing the map, `index.html` shows a project selection overlay if no active project is set. Users must choose or create a project first.

## Key Gotchas

1. **CDN versions are pinned** — don't update Alpine/HTMX/MapLibre/Pico without testing
2. **No module bundler** — use IIFE or global scope patterns; avoid `import`/`export`
3. **Fonts loaded from Google Fonts** — Manrope (UI) and JetBrains Mono (data)
4. **MapTiler API key required** — stored in `config` table as `maptiler_key`
5. **HTMX is present but Alpine does most of the work** — don't mix the two for the same interaction
6. **`x-cloak` + `[x-cloak] { display: none }` in CSS** — prevents flash of unstyled Alpine content
