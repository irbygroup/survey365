/**
 * S365MapCore -- Map initialization and basemap management for Survey365.
 *
 * Creates a full-viewport MapLibre GL JS map with:
 *   - MapTiler basemaps (Street, Satellite, Topo) with public raster fallbacks
 *   - NavigationControl, GeolocateControl, ScaleControl
 *   - Basemap switching that preserves overlay layers
 *   - Base station marker with accuracy circle
 *   - Site markers rendered as a GeoJSON source
 *
 * Exposes window.S365MapCore for use by other modules.
 */
(function () {
  'use strict';

  /* -------------------------------------------------------------------
   * Basemap tile URL templates
   * {key} placeholder is replaced at runtime with the MapTiler API key.
   * ------------------------------------------------------------------- */
  var BASEMAPS = {
    street: {
      label: 'Street',
      tiles: ['https://api.maptiler.com/maps/streets-v2/{z}/{x}/{y}.png?key={key}'],
      tileSize: 512,
      attribution: '<a href="https://www.maptiler.com/" target="_blank">MapTiler</a> <a href="https://www.openstreetmap.org/copyright" target="_blank">OpenStreetMap</a>'
    },
    satellite: {
      label: 'Satellite',
      tiles: ['https://api.maptiler.com/tiles/satellite-v2/{z}/{x}/{y}.jpg?key={key}'],
      tileSize: 512,
      attribution: '<a href="https://www.maptiler.com/" target="_blank">MapTiler</a>'
    },
    topo: {
      label: 'Topo',
      tiles: ['https://api.maptiler.com/maps/outdoor-v2/{z}/{x}/{y}.png?key={key}'],
      tileSize: 512,
      attribution: '<a href="https://www.maptiler.com/" target="_blank">MapTiler</a> <a href="https://www.openstreetmap.org/copyright" target="_blank">OpenStreetMap</a>'
    }
  };

  /* Fallback basemaps when no MapTiler key is available */
  var FALLBACK_BASEMAPS = {
    street: {
      tiles: [
        'https://a.basemaps.cartocdn.com/rastertiles/voyager/{z}/{x}/{y}.png',
        'https://b.basemaps.cartocdn.com/rastertiles/voyager/{z}/{x}/{y}.png',
        'https://c.basemaps.cartocdn.com/rastertiles/voyager/{z}/{x}/{y}.png'
      ],
      tileSize: 256,
      attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> &copy; <a href="https://carto.com/">CARTO</a>'
    },
    satellite: {
      tiles: [
        'https://services.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}'
      ],
      tileSize: 256,
      attribution: 'Tiles &copy; Esri'
    },
    topo: {
      tiles: [
        'https://a.tile.opentopomap.org/{z}/{x}/{y}.png',
        'https://b.tile.opentopomap.org/{z}/{x}/{y}.png',
        'https://c.tile.opentopomap.org/{z}/{x}/{y}.png'
      ],
      tileSize: 256,
      attribution: 'Map data &copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a>, map style &copy; <a href="https://opentopomap.org">OpenTopoMap</a>'
    }
  };

  /* -------------------------------------------------------------------
   * Internal state
   * ------------------------------------------------------------------- */
  var _map = null;
  var _maptilerKey = '';
  var _currentBasemap = 'street';
  var _baseMarker = null;
  var _accuracySource = null;
  var _geolocateCtrl = null;
  var _userPosition = null;
  var _initialLocateResolved = false;

  function _supportsGeolocationControl() {
    if (typeof window === 'undefined' || typeof navigator === 'undefined' || !navigator.geolocation) {
      return false;
    }

    if (window.isSecureContext) {
      return true;
    }

    var hostname = (window.location && window.location.hostname) || '';
    return hostname === 'localhost' ||
      hostname === '127.0.0.1' ||
      hostname === '[::1]';
  }

  /* -------------------------------------------------------------------
   * _buildTileUrls -- replace {key} placeholder in tile URLs
   * ------------------------------------------------------------------- */
  function _buildTileUrls(basemapKey) {
    var def = BASEMAPS[basemapKey];
    if (!def || !_maptilerKey) return null;
    return {
      tiles: def.tiles.map(function (url) {
        return url.replace('{key}', _maptilerKey);
      }),
      tileSize: def.tileSize,
      attribution: def.attribution
    };
  }

  function _resolveBasemapSource(basemapKey) {
    var resolved = _buildTileUrls(basemapKey);

    if (resolved) {
      return {
        type: 'raster',
        tiles: resolved.tiles,
        tileSize: resolved.tileSize,
        attribution: resolved.attribution
      };
    }

    var fallback = FALLBACK_BASEMAPS[basemapKey] || FALLBACK_BASEMAPS.street;
    return {
      type: 'raster',
      tiles: fallback.tiles,
      tileSize: fallback.tileSize,
      attribution: fallback.attribution
    };
  }

  function _setControlTooltip(selector, label) {
    if (!_map) return;

    var button = _map.getContainer().querySelector(selector);
    if (!button) return;

    button.setAttribute('title', label);
    button.setAttribute('aria-label', label);
  }

  function _dispatchUserPositionUnavailable(detail) {
    document.dispatchEvent(new CustomEvent('s365:user-position-unavailable', {
      detail: detail || {}
    }));
  }

  function _attemptInitialUserCenter() {
    if (_initialLocateResolved) return;

    if (!_supportsGeolocationControl()) {
      _initialLocateResolved = true;
      _dispatchUserPositionUnavailable({ reason: 'unsupported' });
      return;
    }

    navigator.geolocation.getCurrentPosition(function (position) {
      if (!_map) return;

      _initialLocateResolved = true;
      _userPosition = {
        lat: position.coords.latitude,
        lon: position.coords.longitude,
        accuracy: position.coords.accuracy
      };

      document.dispatchEvent(new CustomEvent('s365:user-position', { detail: _userPosition }));
      _map.flyTo({
        center: [_userPosition.lon, _userPosition.lat],
        zoom: Math.max(_map.getZoom(), 15),
        duration: 800
      });
    }, function (err) {
      _initialLocateResolved = true;
      _dispatchUserPositionUnavailable({
        reason: 'unavailable',
        code: err && err.code,
        message: err && err.message
      });
    }, {
      enableHighAccuracy: true,
      timeout: 8000,
      maximumAge: 60000
    });
  }

  /* -------------------------------------------------------------------
   * _buildInitialStyle -- construct the initial MapLibre style object
   * ------------------------------------------------------------------- */
  function _buildInitialStyle(basemapKey) {
    return {
      version: 8,
      sources: {
        'basemap': _resolveBasemapSource(basemapKey)
      },
      layers: [
        { id: 'basemap', type: 'raster', source: 'basemap' }
      ],
      glyphs: 'https://fonts.openmaptiles.org/{fontstack}/{range}.pbf'
    };
  }

  /* -------------------------------------------------------------------
   * createMap -- initialize the MapLibre map
   *
   * @param {string} containerId  DOM element ID for the map
   * @param {object} opts         { center, zoom, maptilerKey }
   * @returns {maplibregl.Map}
   * ------------------------------------------------------------------- */
  function createMap(containerId, opts) {
    opts = opts || {};
    _maptilerKey = opts.maptilerKey || '';
    _currentBasemap = opts.basemap || 'street';

    var center = opts.center || [-88.05, 30.69];
    var zoom = opts.zoom || 12;

    _map = new maplibregl.Map({
      container: containerId,
      style: _buildInitialStyle(_currentBasemap),
      center: center,
      zoom: zoom,
      maxZoom: 22,
      minZoom: 3,
      fadeDuration: 100,
      attributionControl: true,
      pitchWithRotate: false
    });

    /* Navigation control -- bottom right */
    _map.addControl(new maplibregl.NavigationControl({ showCompass: false }), 'bottom-right');
    _setControlTooltip('.maplibregl-ctrl-zoom-in', 'Zoom in');
    _setControlTooltip('.maplibregl-ctrl-zoom-out', 'Zoom out');

    /* Scale control -- bottom left */
    _map.addControl(new maplibregl.ScaleControl({ maxWidth: 150, unit: 'imperial' }), 'bottom-left');

    /* Geolocate control -- phone GPS blue dot */
    if (_supportsGeolocationControl()) {
      _geolocateCtrl = new maplibregl.GeolocateControl({
        positionOptions: { enableHighAccuracy: true },
        trackUserLocation: true,
        showUserHeading: true,
        showAccuracyCircle: true
      });
      _map.addControl(_geolocateCtrl, 'bottom-right');
      _setControlTooltip('.maplibregl-ctrl-geolocate', 'Find my location');

      /* Track user position for proximity sorting */
      _geolocateCtrl.on('geolocate', function (e) {
        _userPosition = {
          lat: e.coords.latitude,
          lon: e.coords.longitude,
          accuracy: e.coords.accuracy
        };
        document.dispatchEvent(new CustomEvent('s365:user-position', { detail: _userPosition }));
      });
    }

    /* After map loads, add overlay-related sources */
    _map.on('load', function () {
      _addSiteSource();
      _addBaseMarkerSource();
      _addAccuracySource();
      _attemptInitialUserCenter();
      document.dispatchEvent(new Event('s365:map-ready'));
    });

    return _map;
  }

  /* -------------------------------------------------------------------
   * switchBasemap -- swap the basemap raster tiles
   *
   * @param {string} basemapKey   'street' | 'satellite' | 'topo'
   * ------------------------------------------------------------------- */
  function switchBasemap(basemapKey) {
    if (!_map) return;
    if (!BASEMAPS[basemapKey]) return;
    _currentBasemap = basemapKey;

    var sourceDef = _resolveBasemapSource(basemapKey);

    var source = _map.getSource('basemap');
    if (source) {
      /* Remove and re-add the basemap source to change tile URLs */
      _map.removeLayer('basemap');
      _map.removeSource('basemap');
    }

    _map.addSource('basemap', {
      type: 'raster',
      tiles: sourceDef.tiles,
      tileSize: sourceDef.tileSize,
      attribution: sourceDef.attribution
    });

    /* Insert basemap layer at the bottom, below all overlays */
    var firstOverlay = _getFirstOverlayLayer();
    _map.addLayer({ id: 'basemap', type: 'raster', source: 'basemap' }, firstOverlay);
  }

  /* -------------------------------------------------------------------
   * _getFirstOverlayLayer -- find the first non-basemap layer
   * ------------------------------------------------------------------- */
  function _getFirstOverlayLayer() {
    var layers = _map.getStyle().layers;
    for (var i = 0; i < layers.length; i++) {
      if (layers[i].id !== 'basemap') return layers[i].id;
    }
    return undefined;
  }

  /* -------------------------------------------------------------------
   * Site Markers -- GeoJSON source + circle + text layers
   * ------------------------------------------------------------------- */
  function _addSiteSource() {
    _map.addSource('sites', {
      type: 'geojson',
      data: { type: 'FeatureCollection', features: [] }
    });

    /* Circle markers for sites */
    _map.addLayer({
      id: 'sites-circle',
      type: 'circle',
      source: 'sites',
      paint: {
        'circle-radius': [
          'interpolate', ['linear'], ['zoom'],
          8, 4,
          14, 8,
          18, 12
        ],
        'circle-color': [
          'match', ['get', 'source'],
          'manual', '#2563eb',
          'cors_rtk', '#16a34a',
          'opus', '#7c3aed',
          'averaged', '#ea580c',
          'imported', '#0891b2',
          '#6b7280'
        ],
        'circle-stroke-width': 2,
        'circle-stroke-color': '#ffffff'
      }
    });

    /* Text labels for site names -- visible at zoom >= 13 */
    _map.addLayer({
      id: 'sites-label',
      type: 'symbol',
      source: 'sites',
      minzoom: 13,
      layout: {
        'text-field': ['get', 'name'],
        'text-size': 13,
        'text-offset': [0, 1.6],
        'text-anchor': 'top',
        'text-font': ['Open Sans Regular'],
        'text-max-width': 10
      },
      paint: {
        'text-color': '#1e293b',
        'text-halo-color': '#ffffff',
        'text-halo-width': 2
      }
    });

    /* Nearest-site highlight ring */
    _map.addLayer({
      id: 'sites-nearest-ring',
      type: 'circle',
      source: 'sites',
      filter: ['==', ['get', 'nearest'], true],
      paint: {
        'circle-radius': [
          'interpolate', ['linear'], ['zoom'],
          8, 7,
          14, 12,
          18, 16
        ],
        'circle-color': 'transparent',
        'circle-stroke-width': 3,
        'circle-stroke-color': '#2563eb'
      }
    });

    /* Click handler for site markers */
    _map.on('click', 'sites-circle', function (e) {
      if (!e.features || e.features.length === 0) return;
      var f = e.features[0];
      var p = f.properties;
      var coords = f.geometry.coordinates;

      var distanceText = '';
      if (p.distance_m != null && p.distance_m !== '') {
        var d = parseFloat(p.distance_m);
        distanceText = d < 1000 ? d.toFixed(0) + ' m away' : (d / 1000).toFixed(1) + ' km away';
      }

      var html = '<h4>' + _escapeHtml(p.name) + '</h4>' +
        '<table>' +
        '<tr><td>Lat</td><td>' + parseFloat(p.lat).toFixed(8) + '</td></tr>' +
        '<tr><td>Lon</td><td>' + parseFloat(p.lon).toFixed(8) + '</td></tr>' +
        (p.ortho_height !== '' ? '<tr><td>NAVD88</td><td>' + parseFloat(p.ortho_height).toFixed(3) + ' m</td></tr>' : '') +
        (p.height !== '' ? '<tr><td>Ellipsoid</td><td>' + parseFloat(p.height).toFixed(3) + ' m</td></tr>' : '') +
        (p.source ? '<tr><td>Source</td><td>' + _escapeHtml(p.source) + '</td></tr>' : '') +
        (p.accuracy_h ? '<tr><td>H Acc</td><td>' + parseFloat(p.accuracy_h).toFixed(3) + ' m</td></tr>' : '') +
        (p.last_used ? '<tr><td>Last used</td><td>' + _escapeHtml(p.last_used) + '</td></tr>' : '') +
        (distanceText ? '<tr><td>Distance</td><td>' + distanceText + '</td></tr>' : '') +
        '</table>' +
        '<button class="popup-start-btn" onclick="document.dispatchEvent(new CustomEvent(\'s365:start-base-at-site\', {detail: {id: ' + p.id + ', name: \'' + _escapeHtml(p.name).replace(/'/g, "\\'") + '\'}}))">Start Base Here</button>';

      new maplibregl.Popup({ maxWidth: '300px', offset: 12 })
        .setLngLat(coords)
        .setHTML(html)
        .addTo(_map);
    });

    /* Pointer cursor on site markers */
    _map.on('mouseenter', 'sites-circle', function () {
      _map.getCanvas().style.cursor = 'pointer';
    });
    _map.on('mouseleave', 'sites-circle', function () {
      _map.getCanvas().style.cursor = '';
    });
  }

  /* -------------------------------------------------------------------
   * updateSiteMarkers -- update sites GeoJSON source
   *
   * @param {Array} sites         Array of site objects from API
   * @param {number|null} nearestId  ID of nearest site to highlight
   * ------------------------------------------------------------------- */
  function updateSiteMarkers(sites, nearestId) {
    if (!_map || !_map.getSource('sites')) return;

    var features = sites.map(function (s) {
      return {
        type: 'Feature',
        geometry: { type: 'Point', coordinates: [s.lon, s.lat] },
        properties: {
          id: s.id,
          name: s.name || '',
          lat: s.lat,
          lon: s.lon,
          ortho_height: s.ortho_height ?? '',
          height: s.height ?? '',
          source: s.source || '',
          accuracy_h: s.accuracy_h || '',
          last_used: s.last_used || '',
          distance_m: s.distance_m != null ? s.distance_m : '',
          nearest: s.id === nearestId
        }
      };
    });

    _map.getSource('sites').setData({
      type: 'FeatureCollection',
      features: features
    });
  }

  /* -------------------------------------------------------------------
   * Base Station Marker -- single marker with mode-based coloring
   * ------------------------------------------------------------------- */
  function _addBaseMarkerSource() {
    _map.addSource('base-marker', {
      type: 'geojson',
      data: { type: 'FeatureCollection', features: [] }
    });

    /* Outer glow for visibility */
    _map.addLayer({
      id: 'base-marker-glow',
      type: 'circle',
      source: 'base-marker',
      paint: {
        'circle-radius': 18,
        'circle-color': ['get', 'color'],
        'circle-opacity': 0.25,
        'circle-blur': 0.8
      }
    });

    /* Inner marker */
    _map.addLayer({
      id: 'base-marker-dot',
      type: 'circle',
      source: 'base-marker',
      paint: {
        'circle-radius': 9,
        'circle-color': ['get', 'color'],
        'circle-stroke-width': 3,
        'circle-stroke-color': '#ffffff'
      }
    });
  }

  /* -------------------------------------------------------------------
   * updateBaseMarker -- update base station position and color
   *
   * @param {number} lat
   * @param {number} lon
   * @param {string} mode  'known_base' | 'relative_base' | 'idle' | 'establishing'
   * ------------------------------------------------------------------- */
  function updateBaseMarker(lat, lon, mode) {
    if (!_map || !_map.getSource('base-marker')) return;
    if (!lat || !lon || (lat === 0 && lon === 0)) {
      _map.getSource('base-marker').setData({ type: 'FeatureCollection', features: [] });
      return;
    }

    var color = '#6b7280'; /* gray = idle */
    if (mode === 'known_base') color = '#22c55e';       /* green = broadcasting */
    if (mode === 'relative_base') color = '#f97316';     /* orange = relative */
    if (mode === 'establishing') color = '#eab308';      /* yellow = averaging */

    _map.getSource('base-marker').setData({
      type: 'FeatureCollection',
      features: [{
        type: 'Feature',
        geometry: { type: 'Point', coordinates: [lon, lat] },
        properties: { color: color }
      }]
    });
  }

  /* -------------------------------------------------------------------
   * Accuracy Circle -- translucent circle around base marker
   * ------------------------------------------------------------------- */
  function _addAccuracySource() {
    _map.addSource('accuracy-circle', {
      type: 'geojson',
      data: { type: 'FeatureCollection', features: [] }
    });

    _map.addLayer({
      id: 'accuracy-circle-fill',
      type: 'fill',
      source: 'accuracy-circle',
      paint: {
        'fill-color': '#2563eb',
        'fill-opacity': 0.1
      }
    }, 'base-marker-glow');

    _map.addLayer({
      id: 'accuracy-circle-line',
      type: 'line',
      source: 'accuracy-circle',
      paint: {
        'line-color': '#2563eb',
        'line-width': 1.5,
        'line-opacity': 0.4,
        'line-dasharray': [4, 4]
      }
    }, 'base-marker-glow');
  }

  /* -------------------------------------------------------------------
   * updateAccuracyCircle -- draw a circle sized to horizontal accuracy
   *
   * @param {number} lat
   * @param {number} lon
   * @param {number} accuracy_m  horizontal accuracy in meters
   * ------------------------------------------------------------------- */
  function updateAccuracyCircle(lat, lon, accuracy_m) {
    if (!_map || !_map.getSource('accuracy-circle')) return;

    /* Only show when accuracy is useful (< 50m) */
    if (!accuracy_m || accuracy_m <= 0 || accuracy_m > 50 || !lat || !lon) {
      _map.getSource('accuracy-circle').setData({ type: 'FeatureCollection', features: [] });
      return;
    }

    /* Generate a 64-point circle polygon */
    var points = 64;
    var coords = [];
    var earthRadius = 6371000; /* meters */
    var angularDistance = accuracy_m / earthRadius;
    var latRad = lat * Math.PI / 180;
    var lonRad = lon * Math.PI / 180;

    for (var i = 0; i <= points; i++) {
      var bearing = (2 * Math.PI * i) / points;
      var pLat = Math.asin(
        Math.sin(latRad) * Math.cos(angularDistance) +
        Math.cos(latRad) * Math.sin(angularDistance) * Math.cos(bearing)
      );
      var pLon = lonRad + Math.atan2(
        Math.sin(bearing) * Math.sin(angularDistance) * Math.cos(latRad),
        Math.cos(angularDistance) - Math.sin(latRad) * Math.sin(pLat)
      );
      coords.push([pLon * 180 / Math.PI, pLat * 180 / Math.PI]);
    }

    _map.getSource('accuracy-circle').setData({
      type: 'FeatureCollection',
      features: [{
        type: 'Feature',
        geometry: {
          type: 'Polygon',
          coordinates: [coords]
        },
        properties: {}
      }]
    });
  }

  /* -------------------------------------------------------------------
   * centerOnBase -- fly the map to the base marker position
   *
   * @param {number} lat
   * @param {number} lon
   * ------------------------------------------------------------------- */
  function centerOnBase(lat, lon) {
    if (!_map || !lat || !lon) return;
    _map.flyTo({ center: [lon, lat], zoom: Math.max(_map.getZoom(), 16), duration: 800 });
  }

  /* -------------------------------------------------------------------
   * getUserPosition -- return the latest phone GPS position
   * ------------------------------------------------------------------- */
  function getUserPosition() {
    return _userPosition;
  }

  /* -------------------------------------------------------------------
   * getMap -- return the map instance
   * ------------------------------------------------------------------- */
  function getMap() {
    return _map;
  }

  /* -------------------------------------------------------------------
   * setMaptilerKey -- update the key (e.g. after config fetch)
   * ------------------------------------------------------------------- */
  function setMaptilerKey(key) {
    _maptilerKey = key || '';
  }

  /* -------------------------------------------------------------------
   * _escapeHtml -- simple HTML escaping for popup content
   * ------------------------------------------------------------------- */
  function _escapeHtml(str) {
    if (!str) return '';
    return String(str)
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;');
  }

  /* -------------------------------------------------------------------
   * Public API
   * ------------------------------------------------------------------- */
  window.S365MapCore = {
    createMap: createMap,
    switchBasemap: switchBasemap,
    updateSiteMarkers: updateSiteMarkers,
    updateBaseMarker: updateBaseMarker,
    updateAccuracyCircle: updateAccuracyCircle,
    centerOnBase: centerOnBase,
    getUserPosition: getUserPosition,
    getMap: getMap,
    setMaptilerKey: setMaptilerKey
  };
})();
