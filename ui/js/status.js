/**
 * S365Status -- Live status updates for Survey365.
 *
 * Tries WebSocket first (/ws/live). If WebSocket fails 3 times in a row,
 * falls back to HTTP polling (/api/status every 1 second).
 *
 * Independently heartbeats /api/status so the UI can detect backend loss
 * even when the last live payload is still on screen.
 *
 * Dispatches the same custom DOM events regardless of transport:
 *   - "s365:status"             (GNSS, mode, services)
 *   - "s365:mode_change"
 *   - "s365:establish_progress"
 *   - "s365:ws-connection"      (connected: true/false)
 *
 * Exposes window.S365WS for use by other modules.
 */
(function () {
  'use strict';

  var _ws = null;
  var _reconnectTimer = null;
  var _keepaliveTimer = null;
  var _pollTimer = null;
  var _healthTimer = null;
  var _connected = false;
  var _wsFailCount = 0;
  var _maxWsRetries = 3;
  var _usePolling = false;
  var _pollInterval = 1000;
  var _healthInterval = 5000;
  var _reconnectDelay = 2000;
  var _backendReachable = true;
  var _backendDownSince = null;
  var _wsOpenTimer = null;
  var _pollingAnnounced = false;
  var _started = false;
  var _connecting = false;

  /* -------------------------------------------------------------------
   * _dispatch -- send a status event to the DOM
   * ------------------------------------------------------------------- */
  function _dispatch(msg) {
    if (!msg || !msg.type) return;

    switch (msg.type) {
      case 'status':
        document.dispatchEvent(new CustomEvent('s365:status', { detail: msg }));
        break;
      case 'mode_change':
        document.dispatchEvent(new CustomEvent('s365:mode_change', { detail: msg }));
        break;
      case 'establish_progress':
        document.dispatchEvent(new CustomEvent('s365:establish_progress', { detail: msg }));
        break;
      case 'pong':
        break;
      default:
        document.dispatchEvent(new CustomEvent('s365:' + msg.type, { detail: msg }));
        break;
    }
  }

  function _dispatchBackendState(source) {
    var durationMs = _backendDownSince ? Date.now() - _backendDownSince : 0;
    document.dispatchEvent(new CustomEvent('s365:backend-state', {
      detail: {
        reachable: _backendReachable,
        retrying: !_backendReachable,
        source: source || null,
        down_since: _backendDownSince,
        duration_seconds: Math.floor(durationMs / 1000),
        hard_failed: durationMs >= 180000
      }
    }));
  }

  function _markBackendUp(source) {
    var changed = !_backendReachable || _backendDownSince !== null;
    _backendReachable = true;
    _backendDownSince = null;
    if (changed) {
      _dispatchBackendState(source || 'heartbeat');
    }
  }

  function _markBackendDown(source) {
    if (_backendDownSince === null) {
      _backendDownSince = Date.now();
    }
    if (_backendReachable) {
      _backendReachable = false;
    }
    _dispatchBackendState(source || 'heartbeat');
  }

  function _fetchStatus(timeoutMs) {
    var controller = new AbortController();
    var timeout = setTimeout(function () {
      controller.abort();
    }, timeoutMs || 4000);

    return fetch('/api/status', {
      cache: 'no-store',
      signal: controller.signal
    }).then(function (response) {
      clearTimeout(timeout);
      if (!response.ok) {
        throw new Error('HTTP ' + response.status);
      }
      return response.json();
    }).catch(function (err) {
      clearTimeout(timeout);
      throw err;
    });
  }

  function _startHealthMonitor() {
    if (_healthTimer) return;

    function pollHealth() {
      _fetchStatus(4000)
        .then(function () {
          _markBackendUp('heartbeat');
        })
        .catch(function () {
          _markBackendDown('heartbeat');
        });
    }

    pollHealth();
    _healthTimer = setInterval(pollHealth, _healthInterval);
  }

  function _stopHealthMonitor() {
    if (_healthTimer) {
      clearInterval(_healthTimer);
      _healthTimer = null;
    }
  }

  /* -------------------------------------------------------------------
   * HTTP Polling fallback
   * ------------------------------------------------------------------- */
  function _startPolling() {
    if (_pollTimer) return;
    _usePolling = true;
    if (!_pollingAnnounced) {
      console.log('[S365] WebSocket unavailable, using HTTP polling');
      _pollingAnnounced = true;
    }
    _dispatchConnectionState(false);

    function pollOnce() {
      _fetchStatus(4000)
        .then(function (data) {
          _markBackendUp('poll');
          _dispatch({ type: 'status', ...data });
        })
        .catch(function () {
          _markBackendDown('poll');
        });
    }

    pollOnce();
    _pollTimer = setInterval(pollOnce, _pollInterval);
  }

  function _stopPolling() {
    if (_pollTimer) {
      clearInterval(_pollTimer);
      _pollTimer = null;
    }
    _usePolling = false;
    _pollingAnnounced = false;
  }

  /* -------------------------------------------------------------------
   * WebSocket connection
   * ------------------------------------------------------------------- */
  function _connectWs() {
    _connecting = true;
    if (_ws) {
      try { _ws.close(); } catch (_) { /* ignore */ }
      _ws = null;
    }
    clearTimeout(_reconnectTimer);
    clearInterval(_keepaliveTimer);
    clearTimeout(_wsOpenTimer);
    _reconnectTimer = null;
    _wsOpenTimer = null;

    var proto = location.protocol === 'https:' ? 'wss:' : 'ws:';
    var url = proto + '//' + location.host + '/ws/live';

    try {
      _ws = new WebSocket(url);
    } catch (err) {
      _connecting = false;
      _onWsFail();
      return;
    }

    /* If the WebSocket doesn't open within 3 seconds, consider it failed */
    var wsRef = _ws;
    _wsOpenTimer = setTimeout(function () {
      if (wsRef && wsRef.readyState !== WebSocket.OPEN) {
        try { wsRef.close(); } catch (_) { /* ignore */ }
      }
    }, 3000);

    _ws.onopen = function () {
      clearTimeout(_wsOpenTimer);
      _wsOpenTimer = null;
      clearTimeout(_reconnectTimer);
      _reconnectTimer = null;
      _connecting = false;
      _connected = true;
      _wsFailCount = 0;
      _stopPolling();
      _dispatchConnectionState(true);
      _markBackendUp('ws');

      _keepaliveTimer = setInterval(function () {
        if (_ws && _ws.readyState === WebSocket.OPEN) {
          _ws.send(JSON.stringify({ type: 'ping' }));
        }
      }, 25000);
    };

    _ws.onmessage = function (e) {
      try {
        _markBackendUp('ws');
        _dispatch(JSON.parse(e.data));
      } catch (err) {
        /* ignore parse errors */
      }
    };

    _ws.onclose = function () {
      clearTimeout(_wsOpenTimer);
      _wsOpenTimer = null;
      _connecting = false;
      var wasConnected = _connected;
      _connected = false;
      clearInterval(_keepaliveTimer);
      _ws = null;

      if (!wasConnected) {
        /* Never opened successfully */
        _onWsFail();
      } else {
        /* Fall back to polling while trying to restore the WS. */
        _dispatchConnectionState(false);
        _startPolling();
        _reconnectTimer = setTimeout(_connectWs, _reconnectDelay);
      }
    };

    _ws.onerror = function () {
      /* onerror always followed by onclose */
    };
  }

  function _onWsFail() {
    _wsFailCount++;
    if (_wsFailCount >= _maxWsRetries) {
      _startPolling();
    } else {
      clearTimeout(_reconnectTimer);
      _reconnectTimer = setTimeout(_connectWs, _reconnectDelay);
    }
  }

  /* -------------------------------------------------------------------
   * Public API
   * ------------------------------------------------------------------- */
  function connect() {
    if (_started || _connecting || _connected || _ws) {
      return;
    }
    _started = true;
    _wsFailCount = 0;
    _dispatchBackendState('init');
    _startHealthMonitor();
    _connectWs();
  }

  function disconnect() {
    _started = false;
    _connecting = false;
    clearTimeout(_reconnectTimer);
    clearTimeout(_wsOpenTimer);
    clearInterval(_keepaliveTimer);
    _stopPolling();
    _stopHealthMonitor();
    _connected = false;
    if (_ws) {
      try { _ws.close(1000, 'Client disconnect'); } catch (_) { /* ignore */ }
      _ws = null;
    }
    _dispatchConnectionState(false);
  }

  function isConnected() {
    return _connected || _usePolling;
  }

  function _dispatchConnectionState(connected) {
    document.dispatchEvent(new CustomEvent('s365:ws-connection', {
      detail: { connected: connected }
    }));
  }

  window.S365WS = {
    connect: connect,
    disconnect: disconnect,
    isConnected: isConnected
  };
})();
