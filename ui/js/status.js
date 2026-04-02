/**
 * S365Status -- Live status updates for Survey365.
 *
 * Tries WebSocket first (/ws/live). If WebSocket fails 3 times in a row,
 * falls back to HTTP polling (/api/status every 1 second).
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
  var _connected = false;
  var _wsFailCount = 0;
  var _maxWsRetries = 3;
  var _usePolling = false;
  var _pollInterval = 1000;
  var _reconnectDelay = 2000;

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

  /* -------------------------------------------------------------------
   * HTTP Polling fallback
   * ------------------------------------------------------------------- */
  function _startPolling() {
    if (_pollTimer) return;
    _usePolling = true;
    console.log('[S365] WebSocket unavailable, using HTTP polling');
    _dispatchConnectionState(true);

    function pollOnce() {
      fetch('/api/status')
        .then(function (r) { return r.json(); })
        .then(function (data) {
          _dispatch({ type: 'status', ...data });
        })
        .catch(function () {
          /* network error - will retry next interval */
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
  }

  /* -------------------------------------------------------------------
   * WebSocket connection
   * ------------------------------------------------------------------- */
  function _connectWs() {
    if (_ws) {
      try { _ws.close(); } catch (_) { /* ignore */ }
      _ws = null;
    }
    clearTimeout(_reconnectTimer);
    clearInterval(_keepaliveTimer);

    var proto = location.protocol === 'https:' ? 'wss:' : 'ws:';
    var url = proto + '//' + location.host + '/ws/live';

    try {
      _ws = new WebSocket(url);
    } catch (err) {
      _onWsFail();
      return;
    }

    /* If the WebSocket doesn't open within 3 seconds, consider it failed */
    var openTimeout = setTimeout(function () {
      if (_ws && _ws.readyState !== WebSocket.OPEN) {
        try { _ws.close(); } catch (_) { /* ignore */ }
        _onWsFail();
      }
    }, 3000);

    _ws.onopen = function () {
      clearTimeout(openTimeout);
      _connected = true;
      _wsFailCount = 0;
      _stopPolling();
      _dispatchConnectionState(true);

      _keepaliveTimer = setInterval(function () {
        if (_ws && _ws.readyState === WebSocket.OPEN) {
          _ws.send(JSON.stringify({ type: 'ping' }));
        }
      }, 25000);
    };

    _ws.onmessage = function (e) {
      try {
        _dispatch(JSON.parse(e.data));
      } catch (err) {
        /* ignore parse errors */
      }
    };

    _ws.onclose = function () {
      clearTimeout(openTimeout);
      var wasConnected = _connected;
      _connected = false;
      clearInterval(_keepaliveTimer);

      if (!wasConnected) {
        /* Never opened successfully */
        _onWsFail();
      } else {
        /* Was connected, try to reconnect via WS */
        _wsFailCount = 0;
        _dispatchConnectionState(false);
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
      _reconnectTimer = setTimeout(_connectWs, _reconnectDelay);
    }
  }

  /* -------------------------------------------------------------------
   * Public API
   * ------------------------------------------------------------------- */
  function connect() {
    _wsFailCount = 0;
    if (location.hostname.endsWith('.ts.net')) {
      _startPolling();
      return;
    }
    _connectWs();
  }

  function disconnect() {
    clearTimeout(_reconnectTimer);
    clearInterval(_keepaliveTimer);
    _stopPolling();
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
