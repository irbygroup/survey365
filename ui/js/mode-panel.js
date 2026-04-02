/**
 * survey365App -- Alpine.js root component for Survey365.
 *
 * This is the main reactive state for the entire application:
 *   - Map initialization and basemap switching
 *   - GNSS status (updated by WebSocket)
 *   - Mode selection and control (known point, relative base, stop, resume)
 *   - Site list (sorted by proximity)
 *   - Authentication state
 *   - Establish progress tracking
 *   - Toast notifications
 *
 * Mounted on <body x-data="survey365App()" x-init="init()">
 */
function survey365App() {
  return {
    /* ---------------------------------------------------------------
     * Map State
     * --------------------------------------------------------------- */
    map: null,
    mapReady: false,
    basemap: 'street',
    basemapMenuOpen: false,
    maptilerKey: '',
    defaultLat: 30.69,
    defaultLon: -88.05,
    defaultZoom: 12,
    initialCenterResolved: false,
    initialCenterApplied: false,
    initialCenterSource: '',
    initialCenterDeadlineAt: 0,
    initialStatusReady: false,
    browserPositionUnavailable: false,
    _initialCenterRetryTimer: null,
    _reloadPending: false,

    /* ---------------------------------------------------------------
     * Project State
     * --------------------------------------------------------------- */
    activeProject: null,
    projects: [],
    showProjectGate: true,
    showProjectSwitcher: false,
    newProjectName: '',
    projectError: '',

    /* ---------------------------------------------------------------
     * Menu State
     * --------------------------------------------------------------- */
    menuOpen: false,

    /* ---------------------------------------------------------------
     * Auth State
     * --------------------------------------------------------------- */
    authenticated: false,
    passwordSet: false,
    showLogin: false,
    loginPassword: '',
    loginError: '',

    /* ---------------------------------------------------------------
     * Mode State (updated by WebSocket)
     * --------------------------------------------------------------- */
    mode: 'idle',
    modeLabel: 'IDLE',
    modeSite: null,
    sessionId: null,

    /* ---------------------------------------------------------------
     * GNSS State (updated by WebSocket)
     * --------------------------------------------------------------- */
    gnss: {
      fix_type: '--',
      satellites_used: 0,
      satellites_visible: 0,
      latitude: 0,
      longitude: 0,
      height: null,
      height_msl: null,
      height_navd88: null,
      ground_navd88: null,
      elevation: null,
      elevation_accuracy: null,
      elevation_label: 'NAVD88',
      antenna_height_m: 0,
      rtk_quality: 'none',
      accuracy_h: 0,
      accuracy_v: 0,
      pdop: 0
    },

    /* ---------------------------------------------------------------
     * Services State
     * --------------------------------------------------------------- */
    services: {},

    /* ---------------------------------------------------------------
     * WebSocket
     * --------------------------------------------------------------- */
    wsConnected: false,
    backendReachable: true,
    backendFailureSeconds: 0,

    /* ---------------------------------------------------------------
     * Sites
     * --------------------------------------------------------------- */
    sites: [],
    sitesLoading: false,
    showSiteList: false,
    siteSearch: '',

    /* ---------------------------------------------------------------
     * Mode Panel (bottom sheet)
     * --------------------------------------------------------------- */
    showModePanel: false,
    modePanelView: 'main', /* 'main' | 'site_select' | 'confirm' */
    confirmSite: null,

    /* ---------------------------------------------------------------
     * CORS Establish
     * --------------------------------------------------------------- */
    showCorsPanel: false,
    corsProfiles: [],

    /* ---------------------------------------------------------------
     * Establish Progress
     * --------------------------------------------------------------- */
    establishing: false,
    establishElapsed: 0,
    establishTotal: 120,
    establishSamples: 0,
    showEstablishPanel: false,
    establishPhase: '',
    establishRtkQuality: '',
    establishAccuracy: 0,
    establishNtripConnected: false,

    /* ---------------------------------------------------------------
     * Status Detail Panel
     * --------------------------------------------------------------- */
    showStatusDetail: false,
    showStatusLegend: false,
    satellites: [],
    _satPollTimer: null,

    /* ---------------------------------------------------------------
     * Computed: constellation summary from satellite list
     * --------------------------------------------------------------- */
    get constellationSummary() {
      var counts = {};
      this.satellites.forEach(function (s) {
        if (s.cn0 <= 0) return;
        var name = s.constellation || 'Unknown';
        if (!counts[name]) counts[name] = { total: 0, used: 0 };
        counts[name].total++;
        if (s.used) counts[name].used++;
      });
      var clsMap = { 'GPS': 'gps', 'Galileo': 'gal', 'BeiDou': 'bds', 'GLONASS': 'glo', 'SBAS': 'sbas' };
      return Object.keys(counts).map(function (name) {
        return { name: name, used: counts[name].used, total: counts[name].total, cls: clsMap[name] || '' };
      });
    },

    get filteredSites() {
      var query = (this.siteSearch || '').trim().toLowerCase();
      if (!query) return this.sites;

      return this.sites.filter(function (site) {
        return [
          site.name,
          site.notes,
          site.source
        ].some(function (value) {
          return value && String(value).toLowerCase().indexOf(query) !== -1;
        });
      });
    },

    /* ---------------------------------------------------------------
     * Toast
     * --------------------------------------------------------------- */
    toasts: [],

    /* ---------------------------------------------------------------
     * Computed: status indicator CSS class
     * --------------------------------------------------------------- */
    get statusIndicatorColor() {
      if (this.establishing) return 's365-mode-yellow';
      if (this.mode === 'known_base') return 's365-mode-green';
      if (this.mode === 'relative_base') return 's365-mode-orange';
      if (!this.gnss || !this.gnss.rtk_quality) return 's365-mode-gray';
      if (this.gnss.rtk_quality === 'fixed') return 's365-mode-green';
      if (this.gnss.rtk_quality === 'float') return 's365-mode-yellow';
      if (this.gnss.rtk_quality === 'dgps') return 's365-mode-orange';
      if (this.gnss.rtk_quality === 'autonomous') return 's365-mode-red';
      return 's365-mode-gray';
    },

    get gnssQualityLabel() {
      if (!this.gnss || !this.gnss.connected) return 'No receiver';
      var quality = (this.gnss.rtk_quality || 'none').toLowerCase();
      if (quality === 'fixed') return 'RTK Fixed';
      if (quality === 'float') return 'RTK Float';
      if (quality === 'dgps') return 'DGPS';
      if (quality === 'autonomous') return 'Autonomous';
      if (quality === 'unknown') return 'GNSS';
      return 'No Fix';
    },

    /* ---------------------------------------------------------------
     * Computed: establish progress percentage
     * --------------------------------------------------------------- */
    get establishPercent() {
      if (this.establishTotal <= 0) return 0;
      return Math.min(100, Math.round((this.establishElapsed / this.establishTotal) * 100));
    },

    /* ---------------------------------------------------------------
     * init -- called on page load via x-init
     * --------------------------------------------------------------- */
    async init() {
      var self = this;

      /* 1. Check for active project — gate the app if none */
      await this._fetchActiveProject();
      if (!this.activeProject) {
        await this._fetchProjects();
        this.showProjectGate = true;
      } else {
        this.showProjectGate = false;
      }

      /* 2. Fetch config (MapTiler key, defaults) */
      await this._fetchConfig();

      /* 3. Check auth state */
      this._checkAuth();

      /* 4. Initialize map */
      this._initMap();

      /* 5. Listen for WebSocket events */
      document.addEventListener('s365:status', function (e) {
        self._onStatus(e.detail);
      });
      document.addEventListener('s365:mode_change', function (e) {
        self._onModeChange(e.detail);
      });
      document.addEventListener('s365:establish_progress', function (e) {
        self._onEstablishProgress(e.detail);
      });
      document.addEventListener('s365:establish_error', function (e) {
        self.establishing = false;
        self.showEstablishPanel = false;
        self.showToast(e.detail.message || 'Establish failed', 'error');
      });
      document.addEventListener('s365:ws-connection', function (e) {
        self.wsConnected = e.detail.connected;
      });
      document.addEventListener('s365:backend-state', function (e) {
        self._onBackendState(e.detail);
      });
      document.addEventListener('s365:map-ready', function () {
        self.mapReady = true;
        /* Only load sites if project is active */
        if (self.activeProject) {
          self._loadSites();
        }
        self._centerOnBaseIfNeeded();
      });
      document.addEventListener('s365:user-position', function () {
        self._clearInitialCenterRetry();
        self.browserPositionUnavailable = false;
        self.initialCenterResolved = true;
        self.initialCenterApplied = true;
        self.initialCenterSource = 'browser';
        self.initialCenterDeadlineAt = 0;
      });
      document.addEventListener('s365:user-position-unavailable', function () {
        self.browserPositionUnavailable = true;
        self.initialCenterResolved = true;
        if (self.initialStatusReady && !self.initialCenterDeadlineAt) {
          self.initialCenterDeadlineAt = Date.now() + 4000;
        }
        self._centerOnBaseIfNeeded();
      });
      document.addEventListener('s365:start-base-at-site', function (e) {
        var detail = e.detail;
        var site = window.S365MapSites ? window.S365MapSites.getSiteById(detail.id) : null;
        if (site) {
          self.confirmSite = site;
          self.showModePanel = true;
          self.modePanelView = 'confirm';
        }
      });

      /* 6. Connect WebSocket */
      if (window.S365WS) {
        window.S365WS.connect();
      }

      /* 7. Fetch initial status via REST (before WS connects) */
      this._fetchInitialStatus();

      /* 8. Poll satellites when status detail panel is open */
      this.$watch('showStatusDetail', function (open) {
        if (open) {
          self._fetchSatellites();
          self._satPollTimer = setInterval(function () { self._fetchSatellites(); }, 2000);
        } else {
          if (self._satPollTimer) { clearInterval(self._satPollTimer); self._satPollTimer = null; }
        }
      });
    },

    /* ---------------------------------------------------------------
     * Project Management
     * --------------------------------------------------------------- */

    async _fetchActiveProject() {
      try {
        var res = await fetch('/api/projects/active');
        if (res.ok) {
          var data = await res.json();
          this.activeProject = data.project || null;
        }
      } catch (_) {
        this.activeProject = null;
      }
    },

    async _fetchProjects() {
      try {
        var res = await fetch('/api/projects');
        if (res.ok) {
          var data = await res.json();
          this.projects = data.projects || [];
        }
      } catch (_) {
        this.projects = [];
      }
    },

    async activateProject(project) {
      this.projectError = '';
      try {
        var res = await fetch('/api/projects/' + project.id + '/activate', { method: 'POST' });
        if (res.ok) {
          this.activeProject = project;
          this.showProjectGate = false;
          this.showProjectSwitcher = false;
          /* Load sites for the newly active project */
          if (this.mapReady) {
            this._loadSites();
          }
          this.showToast('Project: ' + project.name, 'info');
        } else {
          var err = await res.json().catch(function () { return {}; });
          this.projectError = err.detail || 'Failed to activate project';
        }
      } catch (err) {
        this.projectError = 'Network error: ' + err.message;
      }
    },

    async createAndActivateProject() {
      var name = this.newProjectName.trim();
      if (!name) return;
      this.projectError = '';

      try {
        var res = await fetch('/api/projects', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ name: name })
        });

        if (res.ok) {
          var project = await res.json();
          this.newProjectName = '';
          await this.activateProject(project);
        } else {
          var err = await res.json().catch(function () { return {}; });
          this.projectError = err.detail || 'Failed to create project';
        }
      } catch (err) {
        this.projectError = 'Network error: ' + err.message;
      }
    },

    openProjectSwitcher() {
      this._fetchProjects();
      this.showProjectSwitcher = true;
      this.newProjectName = '';
      this.projectError = '';
    },

    async switchProject(project) {
      if (this.activeProject && this.activeProject.id === project.id) {
        this.showProjectSwitcher = false;
        return;
      }
      /* Stop active mode before switching */
      if (this.mode !== 'idle') {
        await this.stopMode();
      }
      await this.activateProject(project);
    },

    async createAndSwitchProject() {
      var name = this.newProjectName.trim();
      if (!name) return;
      this.projectError = '';

      try {
        var res = await fetch('/api/projects', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ name: name })
        });

        if (res.ok) {
          var project = await res.json();
          this.newProjectName = '';
          /* Stop active mode before switching */
          if (this.mode !== 'idle') {
            await this.stopMode();
          }
          await this.activateProject(project);
        } else {
          var err = await res.json().catch(function () { return {}; });
          this.projectError = err.detail || 'Failed to create project';
        }
      } catch (err) {
        this.projectError = 'Network error: ' + err.message;
      }
    },

    /* ---------------------------------------------------------------
     * _fetchConfig -- load public config from backend
     * --------------------------------------------------------------- */
    async _fetchConfig() {
      try {
        var res = await fetch('/api/config/public');
        if (res.ok) {
          var data = await res.json();
          this.maptilerKey = data.maptiler_key || '';
          this.defaultLat = parseFloat(data.default_lat) || 30.69;
          this.defaultLon = parseFloat(data.default_lon) || -88.05;
          this.defaultZoom = parseInt(data.default_zoom, 10) || 12;
        }
      } catch (err) {
        console.warn('[survey365App] Config fetch failed, using defaults:', err);
      }
    },

    /* ---------------------------------------------------------------
     * _checkAuth -- check if user is authenticated
     * --------------------------------------------------------------- */
    async _checkAuth() {
      try {
        var res = await fetch('/api/auth/check');
        if (res.ok) {
          var data = await res.json();
          this.authenticated = data.authenticated || false;
          this.passwordSet = data.password_set || false;
        }
      } catch (_) {
        /* Not critical -- admin features just won't be accessible */
      }
    },

    /* ---------------------------------------------------------------
     * _initMap -- create MapLibre map
     * --------------------------------------------------------------- */
    _initMap() {
      if (window.S365MapCore) {
        S365MapCore.setMaptilerKey(this.maptilerKey);
        this.map = S365MapCore.createMap('map', {
          center: [this.defaultLon, this.defaultLat],
          zoom: this.defaultZoom,
          maptilerKey: this.maptilerKey,
          basemap: this.basemap
        });
      }
    },

    /* ---------------------------------------------------------------
     * _fetchInitialStatus -- GET /api/status for initial state
     * --------------------------------------------------------------- */
    async _fetchSatellites() {
      try {
        var res = await fetch('/api/satellites');
        if (res.ok) {
          var data = await res.json();
          this.satellites = data.satellites || [];
        }
      } catch (err) { /* ignore */ }
    },

    async _fetchInitialStatus() {
      try {
        var res = await fetch('/api/status');
        if (res.ok) {
          var data = await res.json();
          this.initialStatusReady = true;
          if (data.gnss) {
            this._updateGnss(data.gnss);
          }
          this._syncEstablishState(data);
          if (data.mode) {
            this.mode = data.mode;
            this.modeLabel = data.mode_label || this._computeModeLabel(data.mode);
          }
          if (data.site) {
            this.modeSite = data.site;
          }
          if (data.session) {
            this.sessionId = data.session.id;
          }
          if (this.browserPositionUnavailable && !this.initialCenterDeadlineAt && !this.initialCenterSource) {
            this.initialCenterDeadlineAt = Date.now() + 4000;
          }
          this._centerOnBaseIfNeeded();
        }
      } catch (err) {
        console.warn('[survey365App] Initial status fetch failed:', err);
      }
    },

    /* ---------------------------------------------------------------
     * _loadSites -- fetch sites from API
     * --------------------------------------------------------------- */
    async _loadSites() {
      this.sitesLoading = true;
      var sites;
      if (window.S365MapSites) {
        sites = await S365MapSites.refreshWithUserPosition();
      } else {
        sites = [];
      }
      this.sites = sites;
      this.sitesLoading = false;
    },

    /* ---------------------------------------------------------------
     * WebSocket Event Handlers
     * --------------------------------------------------------------- */

    _onStatus(msg) {
      if (!this.initialStatusReady) {
        this.initialStatusReady = true;
        if (this.browserPositionUnavailable && !this.initialCenterDeadlineAt && !this.initialCenterSource) {
          this.initialCenterDeadlineAt = Date.now() + 4000;
        }
      }

      if (msg.gnss) {
        this._updateGnss(msg.gnss);
      }
      this._syncEstablishState(msg);
      if (msg.mode != null) {
        this.mode = msg.mode;
        this.modeLabel = msg.mode_label || this._computeModeLabel(msg.mode);
      }
      if (msg.services) {
        this.services = msg.services;
      }

      /* Update map marker */
      if (window.S365MapCore && msg.gnss) {
        var displayMode = this.establishing ? 'establishing' : this.mode;
        S365MapCore.updateBaseMarker(msg.gnss.latitude, msg.gnss.longitude, displayMode);
        S365MapCore.updateAccuracyCircle(msg.gnss.latitude, msg.gnss.longitude, msg.gnss.accuracy_h);
      }

      this._centerOnBaseIfNeeded();
    },

    _onBackendState(detail) {
      var wasDown = !this.backendReachable;

      this.backendReachable = !!(detail && detail.reachable);
      this.backendFailureSeconds = detail && !detail.reachable
        ? (detail.duration_seconds || 0)
        : 0;

      if (!wasDown && !this.backendReachable) {
        this.menuOpen = false;
        this.showModePanel = false;
        this.showStatusDetail = false;
      }

      if (wasDown && this.backendReachable) {
        if (!this._reloadPending) {
          this._reloadPending = true;
          setTimeout(function () {
            var url = new URL(window.location.href);
            url.searchParams.set('_reconnected', Date.now().toString());
            window.location.replace(url.toString());
          }, 300);
        }
      }
    },

    _onModeChange(msg) {
      this.mode = msg.mode || 'idle';
      this.modeLabel = msg.mode_label || this._computeModeLabel(this.mode);
      this.modeSite = msg.site || null;
      this.sessionId = msg.session_id || null;

      /* If switching to idle, clear establish state */
      if (this.mode === 'idle') {
        this.establishing = false;
        this.showEstablishPanel = false;
      }

      /* Refresh sites (last_used may have changed) */
      this._loadSites();

      /* Show toast */
      this.showToast('Mode: ' + this.modeLabel, 'info');
    },

    _onEstablishProgress(msg) {
      this.establishing = true;
      this.showEstablishPanel = true;
      this.establishElapsed = msg.elapsed_seconds || 0;
      this.establishTotal = msg.total_seconds || 120;
      this.establishSamples = msg.samples || 0;

      /* CORS-specific fields */
      this.establishPhase = msg.phase || '';
      this.establishRtkQuality = msg.rtk_quality || '';
      this.establishAccuracy = msg.accuracy_h || 0;
      this.establishNtripConnected = msg.ntrip_connected || false;

      /* Check if complete */
      if (this.establishPhase !== 'waiting_fix' && this.establishPhase !== 'averaging' && this.establishElapsed >= this.establishTotal) {
        this.establishing = false;
        this.showEstablishPanel = false;
      }
    },

    _syncEstablishState(msg) {
      if (!msg) return;

      if (!msg.establishing) {
        if (this.establishing && msg.mode === 'idle') {
          this.establishing = false;
          this.showEstablishPanel = false;
        }
        return;
      }

      var progress = msg.establish_progress || {};
      this.establishing = true;
      this.showEstablishPanel = true;
      this.establishElapsed = progress.elapsed_seconds || 0;
      this.establishTotal = progress.total_seconds || this.establishTotal || 120;
      this.establishSamples = progress.samples || 0;
      this.establishPhase = progress.phase || this.establishPhase || '';
      this.establishRtkQuality = progress.rtk_quality || this.establishRtkQuality || '';
      this.establishAccuracy = progress.accuracy_h || 0;
      this.establishNtripConnected = !!progress.ntrip_connected;
    },

    _updateGnss(gnss) {
      this.gnss.fix_type = gnss.fix_type || '--';
      this.gnss.satellites_used = gnss.satellites_used || 0;
      this.gnss.satellites_visible = gnss.satellites_visible || 0;
      this.gnss.latitude = gnss.latitude || 0;
      this.gnss.longitude = gnss.longitude || 0;
      this.gnss.height = gnss.height ?? null;
      this.gnss.height_msl = gnss.height_msl ?? null;
      this.gnss.height_navd88 = gnss.height_navd88 ?? null;
      this.gnss.ground_navd88 = gnss.ground_navd88 ?? null;
      this.gnss.elevation = gnss.elevation ?? null;
      this.gnss.elevation_accuracy = gnss.elevation_accuracy ?? null;
      this.gnss.elevation_label = gnss.elevation_label || 'NAVD88';
      this.gnss.antenna_height_m = gnss.antenna_height_m ?? 0;
      this.gnss.rtk_quality = gnss.rtk_quality || 'none';
      this.gnss.connected = !!gnss.connected;
      this.gnss.accuracy_h = gnss.accuracy_h || 0;
      this.gnss.accuracy_v = gnss.accuracy_v || 0;
      this.gnss.pdop = gnss.pdop || 0;
    },

    _computeModeLabel(mode) {
      var labels = {
        'idle': 'IDLE',
        'known_base': 'Broadcasting',
        'relative_base': 'Relative Base',
        'establishing': 'Establishing...'
      };
      var label = labels[mode] || mode.toUpperCase();
      if (this.modeSite && this.modeSite.name && mode !== 'idle') {
        label += ' - ' + this.modeSite.name;
      }
      return label;
    },

    _clearInitialCenterRetry() {
      if (this._initialCenterRetryTimer) {
        clearTimeout(this._initialCenterRetryTimer);
        this._initialCenterRetryTimer = null;
      }
    },

    _scheduleInitialCenterRetry() {
      var self = this;

      if (this._initialCenterRetryTimer) {
        return;
      }

      this._initialCenterRetryTimer = setTimeout(function () {
        self._initialCenterRetryTimer = null;
        self._centerOnBaseIfNeeded();
      }, 500);
    },

    _centerOnBaseIfNeeded() {
      if (!this.initialCenterResolved || !this.mapReady || !window.S365MapCore) {
        return;
      }

      if (this.browserPositionUnavailable && !this.initialStatusReady) {
        return;
      }

      if (this.initialCenterSource === 'browser' || this.initialCenterSource === 'base') {
        return;
      }

      var lat = Number(this.gnss.latitude);
      var lon = Number(this.gnss.longitude);
      var hasGnssBase = Number.isFinite(lat) && Number.isFinite(lon) && !(lat === 0 && lon === 0);

      if (!hasGnssBase && this.modeSite) {
        lat = Number(this.modeSite.lat);
        lon = Number(this.modeSite.lon);
        hasGnssBase = Number.isFinite(lat) && Number.isFinite(lon) && !(lat === 0 && lon === 0);
      }

      if (hasGnssBase) {
        this._clearInitialCenterRetry();
        S365MapCore.centerOnBase(lat, lon);
        this.initialCenterApplied = true;
        this.initialCenterSource = 'base';
        this.initialCenterDeadlineAt = 0;
        return;
      }

      if (this.initialCenterSource === 'default') {
        return;
      }

      if (this.initialCenterDeadlineAt && Date.now() < this.initialCenterDeadlineAt) {
        this._scheduleInitialCenterRetry();
        return;
      }

      this._clearInitialCenterRetry();
      if (window.S365MapCore && typeof window.S365MapCore.flyTo === 'function') {
        window.S365MapCore.flyTo(this.defaultLat, this.defaultLon, this.defaultZoom);
      }
      this.initialCenterApplied = true;
      this.initialCenterSource = 'default';
      this.initialCenterDeadlineAt = 0;
    },

    /* ---------------------------------------------------------------
     * Basemap Switching
     * --------------------------------------------------------------- */
    switchBasemap() {
      this.basemapMenuOpen = false;
      if (window.S365MapCore) {
        S365MapCore.switchBasemap(this.basemap);
      }
    },

    /* ---------------------------------------------------------------
     * Center on Base
     * --------------------------------------------------------------- */
    centerOnBase() {
      if (window.S365MapCore && this.gnss.latitude && this.gnss.longitude) {
        S365MapCore.centerOnBase(this.gnss.latitude, this.gnss.longitude);
      }
    },

    /* ---------------------------------------------------------------
     * Mode Panel Actions
     * --------------------------------------------------------------- */

    /* ---------------------------------------------------------------
     * CORS Establish Actions
     * --------------------------------------------------------------- */

    async openCorsEstablish() {
      try {
        var res = await fetch('/api/ntrip');
        if (res.ok) {
          var data = await res.json();
          this.corsProfiles = (data.profiles || []).filter(function (p) {
            return p.type === 'inbound_cors';
          });
        }
      } catch (_) {
        this.corsProfiles = [];
      }
      if (this.corsProfiles.length === 0) {
        this.showToast('No CORS profiles configured. Add one in Settings.', 'warning');
        return;
      }
      this.showCorsPanel = true;
    },

    async selectCorsProfile(profile) {
      this.showCorsPanel = false;
      try {
        var res = await fetch('/api/mode/cors-establish', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            profile_id: profile.id,
            averaging_seconds: 60,
            rtk_timeout_seconds: 120
          })
        });

        if (res.ok) {
          this.establishing = true;
          this.showEstablishPanel = true;
          this.establishElapsed = 0;
          this.establishTotal = 120;
          this.establishSamples = 0;
          this.establishPhase = 'connecting';
          this.establishRtkQuality = '';
          this.establishAccuracy = 0;
          this.establishNtripConnected = false;
          this.showToast('Connecting to ' + profile.name + '...', 'info');
        } else {
          var err = await res.json().catch(function () { return {}; });
          this.showToast(err.detail || 'Failed to start CORS establish', 'error');
        }
      } catch (err) {
        this.showToast('Network error: ' + err.message, 'error');
      }
    },

    /* Open mode panel with site list for known-point selection */
    openModePanel() {
      this.showModePanel = true;
      this.modePanelView = 'site_select';
      this.siteSearch = '';
      this._loadSites();
    },

    /* User picked a site from the list */
    selectSite(site) {
      this.confirmSite = site;
      this.modePanelView = 'confirm';
    },

    /* Confirm starting base at selected site */
    async confirmKnownBase() {
      if (!this.confirmSite) return;
      var site = this.confirmSite;

      try {
        var res = await fetch('/api/mode/known-base', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ site_id: site.id })
        });

        if (res.ok) {
          var data = await res.json();
          this.mode = 'known_base';
          this.modeLabel = 'Broadcasting - ' + site.name;
          this.modeSite = site;
          this.sessionId = data.session_id;
          this.showModePanel = false;
          this.confirmSite = null;
          this.showToast('Base started at ' + site.name, 'success');

          /* Fly to the site */
          if (window.S365MapCore) {
            S365MapCore.centerOnBase(site.lat, site.lon);
          }
        } else {
          var err = await res.json().catch(function () { return {}; });
          this.showToast(err.error || 'Failed to start base', 'error');
        }
      } catch (err) {
        this.showToast('Network error: ' + err.message, 'error');
      }
    },

    /* Cancel confirmation, go back to site list */
    cancelConfirm() {
      this.confirmSite = null;
      this.modePanelView = 'site_select';
    },

    /* Start relative base (averaging) */
    async startRelativeBase() {
      try {
        var res = await fetch('/api/mode/relative-base', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ duration_seconds: 120 })
        });

        if (res.ok) {
          var data = await res.json();
          this.establishing = true;
          this.showEstablishPanel = true;
          this.establishElapsed = 0;
          this.establishTotal = 120;
          this.establishSamples = 0;
          this.sessionId = data.session_id;
          this.showModePanel = false;
          this.showToast('Averaging position...', 'info');
        } else {
          var err = await res.json().catch(function () { return {}; });
          this.showToast(err.error || 'Failed to start relative base', 'error');
        }
      } catch (err) {
        this.showToast('Network error: ' + err.message, 'error');
      }
    },

    /* Stop current mode */
    async stopMode() {
      try {
        var res = await fetch('/api/mode/stop', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' }
        });

        if (res.ok) {
          this.mode = 'idle';
          this.modeLabel = 'IDLE';
          this.modeSite = null;
          this.establishing = false;
          this.showEstablishPanel = false;
          this.showModePanel = false;
          this.showToast('Base stopped', 'warning');
        } else {
          var err = await res.json().catch(function () { return {}; });
          this.showToast(err.error || 'Failed to stop', 'error');
        }
      } catch (err) {
        this.showToast('Network error: ' + err.message, 'error');
      }
    },

    /* Resume last session */
    async resumeMode() {
      try {
        var res = await fetch('/api/mode/resume', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' }
        });

        if (res.ok) {
          var data = await res.json();
          if (data.ok) {
            this.sessionId = data.session_id;
            this.showModePanel = false;
            this.showToast('Resumed last session', 'success');
          } else {
            this.showToast(data.error || 'No previous session to resume', 'warning');
          }
        } else {
          var err = await res.json().catch(function () { return {}; });
          this.showToast(err.error || 'No previous session', 'warning');
        }
      } catch (err) {
        this.showToast('Network error: ' + err.message, 'error');
      }
    },

    /* ---------------------------------------------------------------
     * Login
     * --------------------------------------------------------------- */
    async doLogin() {
      this.loginError = '';
      if (!this.loginPassword) {
        this.loginError = 'Enter a password';
        return;
      }

      try {
        var res = await fetch('/api/auth/login', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ password: this.loginPassword })
        });

        if (res.ok) {
          this.authenticated = true;
          this.showLogin = false;
          this.loginPassword = '';
          this.loginError = '';
          this.showToast('Logged in', 'success');
        } else {
          var data = await res.json().catch(function () { return {}; });
          this.loginError = data.error || 'Invalid password';
        }
      } catch (err) {
        this.loginError = 'Network error';
      }
    },

    /* ---------------------------------------------------------------
     * Logout
     * --------------------------------------------------------------- */
    async doLogout() {
      try {
        await fetch('/api/auth/logout', { method: 'POST' });
      } catch (_) {
        /* Ignore errors -- clear local state regardless */
      }
      this.authenticated = false;
      this.showToast('Logged out', 'info');
    },

    /* ---------------------------------------------------------------
     * Toast Notifications
     * --------------------------------------------------------------- */
    showToast(message, type) {
      type = type || 'info';
      var toast = { id: Date.now(), message: message, type: type };
      this.toasts.push(toast);

      var self = this;
      setTimeout(function () {
        self.toasts = self.toasts.filter(function (t) { return t.id !== toast.id; });
      }, 4000);
    },

    get backendReconnectExpired() {
      return this.backendFailureSeconds >= 180;
    },

    /* ---------------------------------------------------------------
     * Utility: format distance for display
     * --------------------------------------------------------------- */
    formatDistance(meters) {
      if (window.S365MapSites) return S365MapSites.formatDistance(meters);
      if (meters == null) return '';
      var m = parseFloat(meters);
      if (isNaN(m)) return '';
      if (m < 1000) return m.toFixed(0) + ' m';
      return (m / 1000).toFixed(1) + ' km';
    }
  };
}
