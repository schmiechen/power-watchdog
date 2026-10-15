#!/bin/bash
# install.sh - copy the Power Watchdog Python files into place.
#
# Usage: ./install.sh [--prefix DIR] [--dry-run]       (default prefix: /opt/power-watchdog; use sudo for system folders)
#
# It only copies power_watchdog_stream.py and power_watchdog_ble.py (the stream reader imports the second one).
# It does not touch Bluetooth, services, or any other setting.

set -e

PREFIX="/opt/power-watchdog"
DRY=0
HERE="$(cd "$(dirname "$0")" && pwd)"

usage() {
    sed -n '2,8p' "$0" | sed 's/^# \{0,1\}//'
}

while [ $# -gt 0 ]; do
    case "$1" in
        --prefix) PREFIX="$2"; shift 2 ;;
        --dry-run) DRY=1; shift ;;
        -h|--help) usage; exit 0 ;;
        *) echo "Unknown option: $1" >&2; usage >&2; exit 2 ;;
    esac
done

run() {
    if [ "$DRY" -eq 1 ]; then echo "+ $*"; else "$@"; fi
}

if ! python3 -c 'import sys; sys.exit(0 if sys.version_info >= (3, 7) else 1)' 2>/dev/null; then
    echo "Error: Python 3.7 or newer is required (python3 --version)." >&2
    exit 1
fi

if ! command -v gatttool >/dev/null 2>&1; then
    echo "Warning: gatttool was not found. Install BlueZ first: sudo apt-get install bluez" >&2
    echo "         (the files will still be copied)." >&2
fi

for f in power_watchdog_stream.py power_watchdog_ble.py; do
    if [ ! -f "$HERE/$f" ]; then
        echo "Error: $f not found next to install.sh." >&2
        exit 1
    fi
done

run install -d -m 755 "$PREFIX"
run install -m 755 "$HERE/power_watchdog_stream.py" "$PREFIX/power_watchdog_stream.py"
run install -m 755 "$HERE/power_watchdog_ble.py" "$PREFIX/power_watchdog_ble.py"

echo ""
if [ "$DRY" -eq 1 ]; then echo "Dry run: nothing was copied (would install to $PREFIX)"; else echo "Installed to $PREFIX"; fi
echo "Next:"
echo "  1. Write the device config:   sudo ./setup.sh"
echo "  2. Try the reader:            sudo python3 $PREFIX/power_watchdog_stream.py --once"
echo "  3. Optional dashboard widget: import nodered/power_watchdog_flow.json into Node-RED and set PW_DIR=$PREFIX"
echo "Keep bluetoothd running; do not stop it."
