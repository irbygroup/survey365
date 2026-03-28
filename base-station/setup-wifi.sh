#!/usr/bin/env bash
# setup-wifi.sh — Configure WiFi networks on rtkbase-pi from wifi-networks.conf
# Assigns each network to both wlan0 (internal, fallback) and wlan1 (Alfa USB, preferred).
# Safe to re-run: deletes old managed connections before recreating.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
CONF="$SCRIPT_DIR/wifi-networks.conf"
WLAN0_METRIC_BUMP=550
PREFIX="rtk-"

if [[ ! -f "$CONF" ]]; then
  echo "ERROR: $CONF not found"
  exit 1
fi

if [[ $EUID -ne 0 ]]; then
  echo "ERROR: run as root (sudo $0)"
  exit 1
fi

# Delete all previous rtk- managed connections
echo "Cleaning old rtk- connections..."
nmcli -t -f NAME connection show | { grep "^${PREFIX}" || true; } | while IFS= read -r name; do
  echo "  Removing: $name"
  nmcli connection delete "$name" 2>/dev/null || true
done

# Read config and create connections
while IFS='|' read -r ssid psk priority metric; do
  # Skip comments and blank lines
  [[ -z "$ssid" || "$ssid" =~ ^[[:space:]]*# ]] && continue

  # Trim whitespace
  ssid="$(echo "$ssid" | xargs)"
  psk="$(echo "$psk" | xargs)"
  priority="$(echo "$priority" | xargs)"
  metric="$(echo "$metric" | xargs)"

  echo ""
  echo "Configuring: $ssid (priority=$priority, metric=$metric)"

  # wlan1 (Alfa USB) — preferred adapter, lower metric
  con_name="${PREFIX}wlan1-${ssid// /-}"
  echo "  wlan1: $con_name (metric $metric)"
  nmcli connection add \
    type wifi \
    con-name "$con_name" \
    ifname wlan1 \
    ssid "$ssid" \
    wifi-sec.key-mgmt wpa-psk \
    wifi-sec.psk "$psk" \
    ipv4.method auto \
    ipv4.route-metric "$metric" \
    connection.autoconnect yes \
    connection.autoconnect-priority "$priority" \
    > /dev/null

  # wlan0 (internal) — fallback, higher metric
  wlan0_metric=$((metric + WLAN0_METRIC_BUMP))
  con_name="${PREFIX}wlan0-${ssid// /-}"
  echo "  wlan0: $con_name (metric $wlan0_metric)"
  nmcli connection add \
    type wifi \
    con-name "$con_name" \
    ifname wlan0 \
    ssid "$ssid" \
    wifi-sec.key-mgmt wpa-psk \
    wifi-sec.psk "$psk" \
    ipv4.method auto \
    ipv4.route-metric "$wlan0_metric" \
    connection.autoconnect yes \
    connection.autoconnect-priority "$priority" \
    > /dev/null

done < "$CONF"

# Lower priority of the original netplan connection so ours win
if nmcli connection show netplan-wlan0-TranquilityHarbor &>/dev/null; then
  echo ""
  echo "Lowering priority of netplan-wlan0-TranquilityHarbor (fallback only)..."
  nmcli connection modify netplan-wlan0-TranquilityHarbor \
    connection.autoconnect-priority -10 \
    ipv4.route-metric 900
fi

echo ""
echo "Activating connections..."

# Try to bring up wlan1 on the highest-priority visible network
activated=false
while IFS='|' read -r ssid psk priority metric; do
  [[ -z "$ssid" || "$ssid" =~ ^[[:space:]]*# ]] && continue
  ssid="$(echo "$ssid" | xargs)"
  con_name="${PREFIX}wlan1-${ssid// /-}"
  if nmcli connection up "$con_name" 2>/dev/null; then
    echo "  wlan1 connected: $ssid"
    activated=true
    break
  fi
done < <(sort -t'|' -k3 -rn "$CONF")

if [[ "$activated" == false ]]; then
  echo "  wlan1: no configured network in range (will auto-connect when available)"
fi

echo ""
echo "Final state:"
nmcli device status
echo ""
echo "Connections:"
nmcli -t -f NAME,DEVICE,TYPE connection show --active
echo ""
echo "Routes:"
ip route show default
