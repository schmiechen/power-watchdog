# Using the Power Watchdog with CoachProxyOS (optional)

CoachProxy is just one place this can run; nothing in the core depends on it.

**Verified on CoachProxyOS** (Raspberry Pi Zero W, Raspbian 10 "Buster", BlueZ 5.50, Python 3.7.3, Node-RED 2.2.2,
node-red-dashboard 2.19.2): the live reader connects, streams, and disconnects cleanly. The dashboard widget on CoachProxy is
**pending verification** at the time of writing.

## Setup

1. Copy `power_watchdog_stream.py` and `power_watchdog_ble.py` to `/coachproxy/` (owner root, mode 755).
2. Put your device address in `/etc/power_watchdog.conf` (or `/coachproxy/power_watchdog.conf`):
   `{"mac": "AA:BB:CC:DD:EE:FF", "cccd": "0x0021"}`
3. Import `nodered/power_watchdog_flow.json` into Node-RED, then set the flow tab's environment variables:
   `PW_DIR=/coachproxy` and `PW_LAUNCHER=sudo` (Node-RED runs as the `pi` user there, which has passwordless sudo).
4. Drag the *Watchdog Display* widget into the dashboard group you want (for example your Status tab) and deploy.

## CoachProxyOS notes

- `bluetoothd` is started at boot there (via `bthelper@hci0`). Leave it running; the old polling script that stopped it
  stopped working after a reboot (gatttool: "Function not implemented (38)").
- The Pi Zero W has one core and shares one chip between Wi-Fi and Bluetooth. If its Wi-Fi drops, turn Wi-Fi power save off
  (`sudo /sbin/iw dev wlan0 set power_save off`; add a small systemd timer if you want it to persist).
- The old 15-second polling flow keeps the Watchdog connected most of the time and locks the Hughes app out. If you used it,
  disable its inject node before using the new widget.
- The dashboard is the classic `node-red-dashboard` (no longer maintained upstream, but it is what CoachProxyOS ships).
