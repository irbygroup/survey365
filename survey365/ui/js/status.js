/**
 * S365WS -- WebSocket client for Survey365 live status updates.
 *
 * Connects to /ws/live on page load with automatic reconnection.
 * Parses incoming JSON messages and dispatches custom DOM events
 * that the Alpine.js app component listens to.
 *
 * Message types handled:
 *   - "status"              -> s365:status (GNSS, mode, services)
 *   - "mode_change"         -> s365:mode_change
 *   - "establish_progress"  -> s365:establish_progress
 *   - "pong"                -> (internal keepalive ack)
 *
 * Exposes window.S365WS for use by other modules.
 */
(function () {
  'use strict';

  var _ws = null;
  var _reconnectTimer = null;
  var _keepaliveTimer = null;
  var _connected = false;
  var _reconnectDelay = 2000;
  var _maxReconnectDelay = 30000;
  var _currentDelay = 2000;

  /* -------------------------------------------------------------------
   * connect -- establish WebSocket connection to /ws/live
   * ------------------------------------------------------------------- */
  function connect() {
    /* Clean up any existing connection */
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
      console.error('[S365WS] WebSocket creation failed:', err);
      _scheduleReconnect();
      return;
    }

    _ws.onopen = function () {
      _connected = true;
      _currentDelay = _reconnectDelay; /* reset backoff */
      _dispatchConnectionState(true);

      /* Start keepalive ping every 25 seconds */
      _keepaliveTimer = setInterval(function () {
        if (_ws && _ws.readyState === WebSocket.OPEN) {
          _ws.send(JSON.stringify({ type: 'ping' }));
        }
      }, 25000);
    };

    _ws.onmessage = function (e) {
      var msg;
      try {
        msg = JSON.parse(e.data);
      } catch (err) {
        console.warn('[S365WS] Invalid JSON:', e.data);
        return;
      }

      if (!msg || !msg.type) return;

      /* Dispatch as a custom DOM event */
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
          /* Keepalive acknowledged -- no action needed */
          break;

        default:
          /* Forward unknown types for extensibility */
          document.dispatchEvent(new CustomEvent('s365:' + msg.type, { detail: msg }));
          break;
      }
    };

    _ws.onclose = function (e) {
      var wasConnected = _connected;
      _connected = false;
      clearInterval(_keepaliveTimer);
      _dispatchConnectionState(false);

      if (wasConnected) {
        console.log('[S365WS] Connection closed (code=' + e.code + '). Reconnecting...');
      }
      _scheduleReconnect();
    };

    _ws.onerror = function () {
      /* onerror is always followed by onclose, so just log */
      console.warn('[S365WS] WebSocket error');
    };
  }

  /* -------------------------------------------------------------------
   * disconnect -- cleanly close the WebSocket
   * ------------------------------------------------------------------- */
  function disconnect() {
    clearTimeout(_reconnectTimer);
    clearInterval(_keepaliveTimer);
    _connected = false;
    if (_ws) {
      try { _ws.close(1000, 'Client disconnect'); } catch (_) { /* ignore */ }
      _ws = null;
    }
    _dispatchConnectionState(false);
  }

  /* -------------------------------------------------------------------
   * isConnected -- check connection state
   * ------------------------------------------------------------------- */
  function isConnected() {
    return _connected;
  }

  /* -------------------------------------------------------------------
   * _scheduleReconnect -- exponential backoff reconnection
   * ------------------------------------------------------------------- */
  function _scheduleReconnect() {
    clearTimeout(_reconnectTimer);
    _reconnectTimer = setTimeout(function () {
      connect();
    }, _currentDelay);

    /* Exponential backoff with jitter, capped at max */
    _currentDelay = Math.min(_currentDelay * 1.5 + Math.random() * 500, _maxReconnectDelay);
  }

  /* -------------------------------------------------------------------
   * _dispatchConnectionState -- notify UI of WS connection state
   * ------------------------------------------------------------------- */
  function _dispatchConnectionState(connected) {
    document.dispatchEvent(new CustomEvent('s365:ws-connection', {
      detail: { connected: connected }
    }));
  }

  /* -------------------------------------------------------------------
   * Public API
   * ------------------------------------------------------------------- */
  window.S365WS = {
    connect: connect,
    disconnect: disconnect,
    isConnected: isConnected
  };
})();
