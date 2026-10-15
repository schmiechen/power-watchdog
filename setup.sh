#!/bin/bash
# setup.sh - Discover Power Watchdog BLE device and write config
# (install_coachproxy.py does this and more, with shorter Bluetooth scans; this script is the manual alternative.)
#
# Usage: sudo ./setup.sh [config_path]
# Default config path: /etc/power_watchdog.conf

set -e

CONFIG_PATH="${1:-/etc/power_watchdog.conf}"
SCAN_TIMEOUT=15

echo "=== Power Watchdog BLE Setup ==="
echo ""

# Check for root
if [ "$EUID" -ne 0 ]; then
    echo "Error: Must run as root (sudo ./setup.sh)"
    exit 1
fi

# Check for gatttool
if ! command -v gatttool &> /dev/null; then
    echo "Error: gatttool not found. Install bluez: sudo apt-get install bluez"
    exit 1
fi

# Ensure adapter is up (do NOT disable bluetoothd or reset adapter on Pi Zero W)
echo "Ensuring BLE adapter is up..."
hciconfig hci0 up
sleep 1

# Scan for PMD device
echo "Scanning for Power Watchdog (${SCAN_TIMEOUT}s)..."
echo "Make sure the device is powered on (connected to shore power)."
echo ""

MAC=""
SCAN_OUTPUT=$(timeout "$SCAN_TIMEOUT" hcitool lescan 2>&1 || true)

# Parse scan results for PMD
MAC=$(echo "$SCAN_OUTPUT" | grep -i "PMD" | head -1 | awk '{print $1}')

if [ -z "$MAC" ]; then
    echo "Error: No Power Watchdog (PMD) device found."
    echo ""
    echo "Troubleshooting:"
    echo "  - Is the Power Watchdog connected to shore power?"
    echo "  - Is the Hughes app disconnected? (only one connection at a time)"
    echo "  - Are you within BLE range (~30 ft)?"
    echo ""
    echo "You can manually specify the MAC if you know it:"
    echo "  echo '{\"mac\": \"AA:BB:CC:DD:EE:FF\", \"cccd\": \"0x0021\"}' > $CONFIG_PATH"
    exit 1
fi

echo "Found Power Watchdog: $MAC"
echo ""

# Discover GATT characteristics
echo "Discovering GATT handles..."
CHARS=$(gatttool -b "$MAC" --characteristics 2>&1)

# Find FFE2 handle (TX notifications)
FFE2_LINE=$(echo "$CHARS" | grep -i "ffe2" | head -1)
if [ -z "$FFE2_LINE" ]; then
    echo "Error: Could not find FFE2 characteristic."
    echo "Raw output:"
    echo "$CHARS"
    exit 1
fi

# Extract the value handle and compute CCCD (value handle + 1)
VALUE_HANDLE=$(echo "$FFE2_LINE" | grep -oP 'char value handle = \K0x[0-9a-fA-F]+')
if [ -z "$VALUE_HANDLE" ]; then
    echo "Error: Could not parse FFE2 value handle."
    echo "Line: $FFE2_LINE"
    exit 1
fi

# CCCD is typically value_handle + 1
HANDLE_DEC=$((VALUE_HANDLE + 1))
CCCD_HANDLE=$(printf "0x%04x" "$HANDLE_DEC")

echo "FFE2 value handle: $VALUE_HANDLE"
echo "CCCD handle: $CCCD_HANDLE"
echo ""

# Quick test - try to get data
echo "Testing connection (5s)..."
TEST_OUTPUT=$(timeout 8 gatttool -b "$MAC" --char-write-req --handle="$CCCD_HANDLE" --value=0100 --listen 2>&1 || true)

if echo "$TEST_OUTPUT" | grep -q "Notification handle"; then
    echo "Success! Receiving data from Power Watchdog."
else
    echo "Warning: Connected but no data received. The device may need a moment."
    echo "Config will be written anyway - you can test with:"
    echo "  sudo python3 power_watchdog_stream.py --once"
fi

echo ""

# Write config
cat > "$CONFIG_PATH" << EOF
{
  "mac": "$MAC",
  "cccd": "$CCCD_HANDLE"
}
EOF

echo "Config written to: $CONFIG_PATH"
echo ""
cat "$CONFIG_PATH"
echo ""
echo "Setup complete! Test with:"
echo "  sudo python3 power_watchdog_stream.py --once"
