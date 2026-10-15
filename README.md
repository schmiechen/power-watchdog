# Hughes Power Watchdog BLE Integration

Reverse-engineered BLE protocol for the Hughes Autoformers Power Watchdog RV EPO surge protector. Enables integration with CoachProxy, Node-RED, Home Assistant, and other platforms without the proprietary app.

> Unofficial. Not affiliated with or endorsed by Hughes Autoformers. Use at your own risk. The software in this repository
> only **reads** from the Watchdog; it never sends it commands.

![Dashboard screenshot](assets/Watchdog_CoachProxy_Status.png)

## What's in here

| Piece | What it is | Needed for |
|---|---|---|
| [`docs/protocol.md`](docs/protocol.md) | The decoded Bluetooth protocol: GATT layout, packet format, error codes, commands to avoid | everyone |
| `power_watchdog_stream.py` | **On-demand live reader.** Start it and it prints one JSON reading per second; stop it and it disconnects cleanly | everyone |
| `power_watchdog_ble.py` | One-shot reader (one reading, then exit) plus the shared protocol helpers | polling-style use, and required by the stream reader |
| `nodered/` | Importable Node-RED flow with a dashboard widget that connects **only while someone is looking at it** | Node-RED users (optional) |
| [`docs/coachproxy.md`](docs/coachproxy.md) | Notes for CoachProxyOS | CoachProxy users (optional) |
| [`docs/examples.md`](docs/examples.md) | Recipes for Home Assistant and other platforms (**untested examples**) | optional |

## Read this first: the Watchdog accepts ONE Bluetooth connection

While anything is connected to the Watchdog (the Hughes phone app, this software, a script), it stops advertising and **no
one else can see it**. So polling it around the clock locks your phone out. This project is built around that fact: connect
only while you actually need the data, and release it promptly. The Node-RED widget does this automatically (it connects when
the widget is on screen and lets go a few seconds after you leave); with the reader you decide when to start and stop it.

## Status and what has been tested

| Item | Status |
|---|---|
| Reader (`power_watchdog_stream.py`) on a Raspberry Pi Zero W (Raspbian 10 "Buster", BlueZ 5.50, Python 3.7) with a real Gen I 50 A unit | **Verified**: live readings, clean disconnect (the unit advertises again about 2 s later), stops cleanly on SIGTERM |
| Reader unit tests (`python3 -m unittest discover -s tests`) | 20+ tests, pass on macOS and on the Pi; they replay 620 real recorded packets through a fake `gatttool` |
| Node-RED flow, server side (start/stop, grace period, shared viewers, unavailable handling, no orphan after a hard kill of Node-RED) | **Verified** in a fresh Node-RED 5.0.8 + node-red-dashboard 3.6.6; controller logic has its own tests (`node tests/test_controller.js`) |
| Widget behavior in real browsers on a real Pi (Node-RED 2.2.2, node-red-dashboard 2.19.2; desktop Chrome and a phone browser): open, tab switch, two viewers, phone lock, all pages closed, Hughes app holding the unit then released + Retry | **Verified** (one session; a 48 h soak is still pending) |
| Gen I 30 A (single line) | Expected to work (the widget hides the missing second line); **untested** |
| Home Assistant and other platforms | Examples only, **untested** |
| Not supported | Gen II / Wi-Fi Power Watchdogs; FlowFuse "Dashboard 2.0" (a different widget system); systems without `gatttool` |

## Requirements

- A Linux machine within Bluetooth range (about 30 ft) of the Watchdog, with BlueZ's **`gatttool`** (`sudo apt-get install bluez`).
  Newer distributions have dropped `gatttool`; it is not supported there yet.
- Python 3.7 or newer (standard library only, nothing to `pip install`).
- `bluetoothd` left **running**. Do not stop it: stopping it leaves the adapter unable to make LE connections.
- The Watchdog on shore power.
- For the dashboard widget (optional): Node-RED 2.x or newer with the classic `node-red-dashboard` installed. Note that the
  classic dashboard is no longer maintained upstream.

## Quick start

```bash
git clone https://github.com/schmiechen/power-watchdog.git
cd power-watchdog
sudo ./install.sh              # copies the two Python files to /opt/power-watchdog (see --help)
sudo ./setup.sh                # finds your Watchdog and writes /etc/power_watchdog.conf
sudo python3 /opt/power-watchdog/power_watchdog_stream.py --once
```

The last command should print a connecting line and then one live reading, and exit. If it says `unavailable`, see
Troubleshooting.

### Configuration

Searched in order: `/etc/power_watchdog.conf`, `/coachproxy/power_watchdog.conf`, `config.json` next to the script.

```json
{ "mac": "AA:BB:CC:DD:EE:FF", "cccd": "0x0021" }
```

Or pass the address on the command line: `python3 power_watchdog_stream.py AA:BB:CC:DD:EE:FF`.

## The live reader

```
python3 power_watchdog_stream.py [MAC] [--config PATH] [--once] [--max-seconds N] [--interval S]
                                 [--stop-on-stdin-eof] [--exit-with-parent]
                                 [--keepalive-file PATH [--keepalive-timeout S]] [-v]
```

It prints one JSON object per line:

```json
{"state":"connecting","attempt":1}
{"state":"live","timestamp":1790000000,"line1":{"voltage":122.4,"amps":0.7,"watts":52.4,"frequency":60.04,"energy_kwh":4680.6},"line2":{"voltage":123.3,"amps":2.12,"watts":173.3,"frequency":60.05,"energy_kwh":3853.1},"total_energy_kwh":8533.1,"error_code":0}
{"state":"unavailable","reason":"in_use_or_unreachable","detail":"connect error: Connection refused (111)"}
```

- `line2` is absent on single-line units. `error_code` is `0` when healthy (codes are listed in [`docs/protocol.md`](docs/protocol.md)).
- `reason` is one of `not_configured`, `adapter_not_ready`, `in_use_or_unreachable`, `no_data`.
- **Stopping it frees the Watchdog.** Send `SIGTERM` or `SIGINT`, use `--max-seconds N` or `--once`, run with
  `--stop-on-stdin-eof` and close its input, or run with `--keepalive-file PATH` and delete that file (or stop changing its
  content for `--keepalive-timeout` seconds). It disconnects cleanly in all of these cases.
- Exit codes: `0` stopped normally, `2` no device address configured, `3` unavailable after retrying.
- It makes at most two connection attempts, then gives up instead of retrying in a loop.

The older one-shot script is still there for polling-style use: `python3 power_watchdog_ble.py` prints a single reading.
If you poll, poll sparingly (see the single-connection note above).

## Node-RED dashboard widget (optional)

1. Copy `power_watchdog_stream.py` and `power_watchdog_ble.py` to the machine running Node-RED (for example `/opt/power-watchdog`).
2. In Node-RED choose **Import** and paste `nodered/power_watchdog_flow.json`. It brings its own dashboard tab and group, so it
   imports anywhere; afterwards you can drag the *Watchdog Display* widget into any group on any tab.
3. Open the imported flow tab, double-click the tab name, and set its **Environment variables** (see below), at least `PW_DIR`.
4. Deploy, open the dashboard, and look at the widget.

**How it behaves.** The widget tells the flow when it is actually visible in a browser (on any tab). The first viewer starts the
reader; when the last viewer leaves (switches tab, hides the page, closes it, loses Wi-Fi), the connection is dropped after a
short grace period. The widget shows **Live**, **Connecting...**, **Unavailable** (with a plain-language reason and a **Retry**
button) or **Paused**; the last readings stay on screen, dimmed, while there is no live data.
If the Watchdog is busy (for example the Hughes app is connected), the widget says so and does not keep retrying on its own.

| Variable | Default | Meaning |
|---|---|---|
| `PW_DIR` | `/opt/power-watchdog` | folder containing `power_watchdog_stream.py` |
| `PW_CONFIG` | (empty) | config file path, if not one of the default locations |
| `PW_LAUNCHER` | (empty) | set to `sudo` if `gatttool` needs root and Node-RED does not run as root (needs passwordless sudo) |
| `PW_ARGS` | (empty) | extra reader options, for example the device address |
| `PW_GRACE` | `15` | seconds to wait after the last viewer leaves before disconnecting |
| `PW_LEASE` | `15` | seconds a widget heartbeat stays valid (a vanished browser is released after this plus the grace) |
| `PW_CAP` | `3600` | longest continuous session in seconds; then it pauses ("Resume" to continue), so a forgotten wall tablet cannot hold the Watchdog |
| `PW_KEEPALIVE` | `/dev/shm/power-watchdog.keepalive` | small file the flow keeps refreshing while the reader should run; the reader stops by itself when it disappears (see below) |
| `PW_KA_TIMEOUT` | `15` | seconds without a refresh before the reader stops by itself |

**Why a keep-alive file?** A normal user may not signal a process owned by root. If the reader runs through `sudo`
(`PW_LAUNCHER=sudo`), a stop signal is silently refused and the Watchdog would stay connected. So the flow refreshes a small
file while the reader should run and **deletes it to stop it**; the reader exits by itself when the file disappears or stops
changing, and Node-RED then reports its exit. Signals are only a last resort after a few seconds of silence. (They are not
sent first on purpose: in Node-RED 2.x a refused signal makes the exec node forget the process, so its exit is never
reported.) The file also frees the Watchdog if Node-RED itself dies. It must be writable by the user Node-RED runs as and
readable by the reader; the default in `/dev/shm` is fine on Linux. If the controller cannot confirm the reader is gone, the
widget says so (`stop_failed`) instead of claiming the Watchdog is free, and it will not start a second reader meanwhile.

## Troubleshooting

**`adapter_not_ready` / "Function not implemented (38)":** make sure `bluetoothd` is running (`systemctl status bluetooth`) and
the adapter is up (`hciconfig`). Do not stop `bluetoothd`.

**`in_use_or_unreachable` (widget: "Watchdog not reachable"):** the Watchdog is switched off, out of range, or something else is
connected. Close the Hughes app on every phone and tablet. Note that on iPhone/iPad the Control Center Bluetooth button does
not fully switch Bluetooth off; use Settings > Bluetooth. Then press Retry.

**`no_data`:** the connection was made but no readings arrived. Try again; if it persists, power-cycle the Watchdog.

**Permission errors / nothing happens when started from Node-RED:** `gatttool` usually needs root. Set `PW_LAUNCHER=sudo`
(and allow passwordless sudo for the Node-RED user), or run Node-RED as a user that may use the adapter.

**Raspberry Pi Zero W:** Wi-Fi and Bluetooth share one chip. If the Pi's Wi-Fi drops often, turn Wi-Fi power save off
(`sudo iw dev wlan0 set power_save off`; it does not persist across reboots by itself).

**"No PMD device found" during `setup.sh`:** the Watchdog must be on shore power and not connected to the Hughes app.

## Files

| File | Purpose |
|---|---|
| `power_watchdog_stream.py` | On-demand live reader (JSON lines) |
| `power_watchdog_ble.py` | One-shot reader and shared protocol helpers |
| `install.sh` | Copies the Python files into place |
| `setup.sh` | Finds the device, discovers its handles, writes the config |
| `config.json.example` | Config template |
| `nodered/power_watchdog_flow.json` | Importable Node-RED flow and dashboard widget (generated by `tools/build_flow.py` from `nodered/src/`) |
| `tests/` | Unit tests (`python3 -m unittest discover -s tests`, `node tests/test_controller.js`) |
| `docs/` | Protocol notes, CoachProxy notes, examples |
| `watchdog_gatttool.py`, `watchdog.js` | Early prototypes, kept for reference |

## How this was reverse-engineered

1. Captured Bluetooth traffic with a BLE explorer and a GATT dump of a real unit.
2. Identified the UART-style service (`0xFFE0`) and its characteristics.
3. Decompiled the vendor's Android app (`com.hughes.epo`) and read its scan/connect/command code
   (`BluetoothService`, `BluetoothHandle.getPowerWatchdogData()`, `Util.byte4ToDouble()`).
4. Confirmed the behavior against packet captures of the iOS app talking to a real unit.

## License

MIT. See [`LICENSE`](LICENSE).
