# Hughes Power Watchdog BLE Integration

Reverse-engineered BLE protocol for the Hughes Autoformers Power Watchdog RV surge protector. Enables integration with Node-RED, Home Assistant, and other platforms without the proprietary app.

## Supported Hardware

- Hughes Power Watchdog Gen I 50A (confirmed)
- Likely compatible with other BLE-equipped Power Watchdog models (Gen I 30A, etc.)

## Quick Start (Raspberry Pi)

### 1. Install

```bash
git clone https://github.com/youruser/power-watchdog.git
cd power-watchdog
```

### 2. Run Setup

The setup script discovers your Power Watchdog device and writes the config:

```bash
sudo ./setup.sh
```

This will:
- Scan for BLE devices named "PMD"
- Discover the correct GATT handles for your device
- Test the connection
- Write `/etc/power_watchdog.conf` with your device's MAC and handle

### 3. Test

```bash
sudo python3 power_watchdog_ble.py
```

### 4. Deploy to CoachProxy (optional)

```bash
sudo cp power_watchdog_ble.py /coachproxy/power_watchdog_ble.py
```

Then import `nodered/power_watchdog_flow.json` into Node-RED.

## Requirements

- Raspberry Pi (or any Linux with BLE)
- BlueZ (`gatttool` command): `sudo apt-get install bluez`
- Python 3.5+ (stdlib only, no pip)
- Power Watchdog connected to shore power and within BLE range (~30 ft)

## Configuration

The script reads device settings from a JSON config file. Searched in order:

1. `/etc/power_watchdog.conf`
2. `/coachproxy/power_watchdog.conf`
3. `./config.json` (same directory as script)

Config format:

```json
{
  "mac": "F0:F8:F2:FA:24:03",
  "cccd": "0x0021"
}
```

You can also pass these as arguments:

```bash
sudo python3 power_watchdog_ble.py AA:BB:CC:DD:EE:FF --cccd 0x0021
```

## Protocol

The Power Watchdog uses a BLE UART service:

| Property | Value |
|----------|-------|
| Device name prefix | `PMD` |
| Service UUID | `0xFFE0` |
| TX Characteristic (notifications) | `0xFFE2` |
| RX Characteristic (commands) | `0xFFE1` |

Data arrives as 20-byte notification pairs (~1 pair/sec/line). Combined into 40 bytes:

| Byte Offset | Field | Conversion |
|-------------|-------|------------|
| 3-6 | Voltage | `byte4(3) / 10000` → Volts |
| 7-10 | Current | `byte4(7) / 10000` → Amps |
| 11-14 | Power | `byte4(11) / 10000` → Watts |
| 15-18 | Energy | `byte4(15) / 10000` → kWh |
| 31-34 | Frequency | `byte4(31) / 100` → Hz |
| 19 | Error Code | `0` = OK |
| 37-39 | Line ID | `01,01,01` = Line 2; `00,00,00` = Line 1 |

`byte4` = big-endian unsigned 32-bit: `b[0]*16777216 + b[1]*65536 + b[2]*256 + b[3]`

## Output

```json
{
  "line1": {"voltage": 124.6, "amps": 0.47, "watts": 21.2, "frequency": 60.05, "energy_kwh": 4204.8},
  "line2": {"voltage": 124.8, "amps": 1.82, "watts": 144.1, "frequency": 60.05, "energy_kwh": 3162.6},
  "total_energy_kwh": 7367.4,
  "error_code": 0,
  "timestamp": 1718644800
}
```

On failure: `{"error": "timeout"}` or `{"error": "no_config"}`

## Node-RED Dashboard

Import `nodered/power_watchdog_flow.json` for a ready-made dashboard with:
- Digital 7-segment display (DSEG7 font, embedded offline)
- Power Watchdog logo
- Two-column Line 1 / Line 2 layout
- Voltage, Amps, Frequency, Watts per line
- Total energy counter
- Last-updated timestamp

The flow polls every 30 seconds via an exec node. No manual wiring needed after import.

## Files

| File | Purpose |
|------|---------|
| `power_watchdog_ble.py` | Production script (config-based, for deployment) |
| `setup.sh` | Auto-discover device and write config |
| `config.json.example` | Config template |
| `watchdog_gatttool.py` | Standalone script (MAC as CLI arg, no config needed) |
| `watchdog.js` | Node.js prototype (macOS/Linux with noble) |
| `nodered/power_watchdog_flow.json` | Node-RED flow with dashboard |
| `assets/logo.png` | Transparent logo for dashboard |

## Troubleshooting

**"No PMD device found" during setup:**
- Power Watchdog must be on shore power to broadcast BLE
- Disconnect the Hughes app first (only one BLE connection at a time)
- Try resetting the BLE adapter: `sudo hciconfig hci0 down && sudo hciconfig hci0 up`

**Script returns `{"error": "timeout"}`:**
- Device may be out of range or asleep
- Another process may hold the BLE connection (Hughes app, another script instance)
- Try running setup again to verify connectivity

**CCCD handle is wrong:**
- Run `sudo gatttool -b <MAC> --characteristics` and find the UUID `ffe2` line
- The CCCD is typically the value handle + 1
- Update your config file with the correct handle

## How This Was Reverse-Engineered

1. Captured BLE traffic with LightBlue explorer
2. Identified UART service (0xFFE0) and TX/RX characteristics
3. Decompiled the Android app (`com.hughes.epo`)
4. Key source: `BluetoothHandle.getPowerWatchdogData()` + `Util.byte4ToDouble()`

## License

MIT
