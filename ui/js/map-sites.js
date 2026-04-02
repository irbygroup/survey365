/**
 * S365MapSites -- Saved point markers and site management for Survey365.
 *
 * Fetches sites from GET /api/sites, renders them via S365MapCore,
 * handles proximity sorting, tap-to-popup, and "Start Base Here" actions.
 *
 * Exposes window.S365MapSites for use by the Alpine.js app component.
 */
(function () {
  'use strict';

  var _sites = [];
  var _nearestId = null;
  var _lastUserLat = null;
  var _lastUserLon = null;

  /* -------------------------------------------------------------------
   * fetchSites -- load sites from API, optionally sorted by proximity
   *
   * @param {object} opts  { lat, lon } for proximity sort (optional)
   * @returns {Promise<Array>}  array of site objects
   * ------------------------------------------------------------------- */
  function fetchSites(opts) {
    opts = opts || {};
    var url = '/api/sites?limit=200';

    if (opts.lat != null && opts.lon != null) {
      url += '&near_lat=' + opts.lat + '&near_lon=' + opts.lon;
      _lastUserLat = opts.lat;
      _lastUserLon = opts.lon;
    } else if (_lastUserLat != null && _lastUserLon != null) {
      url += '&near_lat=' + _lastUserLat + '&near_lon=' + _lastUserLon;
    }

    return fetch(url)
      .then(function (res) {
        if (!res.ok) throw new Error('Failed to fetch sites: ' + res.status);
        return res.json();
      })
      .then(function (data) {
        _sites = data.sites || [];

        /* Determine nearest site */
        _nearestId = null;
        if (_sites.length > 0 && _sites[0].distance_m != null) {
          _nearestId = _sites[0].id;
        }

        /* Update map markers */
        if (window.S365MapCore) {
          window.S365MapCore.updateSiteMarkers(_sites, _nearestId);
        }

        return _sites;
      })
      .catch(function (err) {
        console.error('[S365MapSites] fetchSites error:', err);
        return _sites;
      });
  }

  /* -------------------------------------------------------------------
   * getSites -- return cached sites array
   * ------------------------------------------------------------------- */
  function getSites() {
    return _sites;
  }

  /* -------------------------------------------------------------------
   * getNearestId -- return the ID of the nearest site
   * ------------------------------------------------------------------- */
  function getNearestId() {
    return _nearestId;
  }

  /* -------------------------------------------------------------------
   * getSiteById -- lookup a site by ID from cache
   *
   * @param {number} id
   * @returns {object|null}
   * ------------------------------------------------------------------- */
  function getSiteById(id) {
    for (var i = 0; i < _sites.length; i++) {
      if (_sites[i].id === id) return _sites[i];
    }
    return null;
  }

  /* -------------------------------------------------------------------
   * refreshWithUserPosition -- re-fetch sites using current phone GPS
   * ------------------------------------------------------------------- */
  function refreshWithUserPosition() {
    var pos = window.S365MapCore ? window.S365MapCore.getUserPosition() : null;
    if (pos) {
      return fetchSites({ lat: pos.lat, lon: pos.lon });
    }
    return fetchSites();
  }

  /* -------------------------------------------------------------------
   * formatDistance -- human-readable distance string
   *
   * @param {number|null} meters
   * @returns {string}
   * ------------------------------------------------------------------- */
  function formatDistance(meters) {
    if (meters == null || meters === '') return '';
    var m = parseFloat(meters);
    if (isNaN(m)) return '';
    if (m < 1000) return m.toFixed(0) + ' m';
    return (m / 1000).toFixed(1) + ' km';
  }

  /* -------------------------------------------------------------------
   * Listen for user position updates to refresh proximity data
   * ------------------------------------------------------------------- */
  var _positionDebounce = null;
  document.addEventListener('s365:user-position', function (e) {
    var pos = e.detail;
    if (!pos) return;
    _lastUserLat = pos.lat;
    _lastUserLon = pos.lon;

    /* Debounce: only refresh sites at most every 15 seconds */
    if (_positionDebounce) return;
    _positionDebounce = setTimeout(function () {
      _positionDebounce = null;
      fetchSites({ lat: _lastUserLat, lon: _lastUserLon });
    }, 15000);
  });

  /* -------------------------------------------------------------------
   * Public API
   * ------------------------------------------------------------------- */
  window.S365MapSites = {
    fetchSites: fetchSites,
    getSites: getSites,
    getNearestId: getNearestId,
    getSiteById: getSiteById,
    refreshWithUserPosition: refreshWithUserPosition,
    formatDistance: formatDistance
  };
})();
