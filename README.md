# Hughes Power Watchdog BLE Protocol

Reverse-engineered BLE protocol for the Hughes Autoformers Power Watchdog RV surge protector. Enables integration with Home Assistant, Node-RED, and other home automation platforms without the proprietary app.

## Supported Hardware

- Hughes Power Watchdog Gen I 50A (confirmed)
- Likely compatible with other BLE-equipped Power Watchdog models (Gen I 30A, etc.)

## Protocol Overview

The Power Watchdog uses a BLE UART service for data transmission:

| Property | Value |
|----------|-------|
| Device name prefix | `PMD` |
| Service UUID | `0xFFE0` |
| TX Characteristic (data out) | `0xFFE2` (Notify, Read) |
| RX Characteristic (commands in) | `0xFFE1` (Read, Write) |

### Packet Format

Data arrives as pairs of 20-byte BLE notifications (~1 pair per second per line):

- **Packet 1:** Header — starts with `01 03 20`
- **Packet 2:** Continuation data

Combined into a 40-byte frame and parsed as:

| Byte Offset | Field | Conversion |
|-------------|-------|------------|
| 3–6 | Voltage | `byte4(3) / 10000` → Volts |
| 7–10 | Current | `byte4(7) / 10000` → Amps |
| 11–14 | Power | `byte4(11) / 10000` → Watts |
| 15–18 | Energy | `byte4(15) / 10000` → kWh |
| 31–34 | Frequency | `byte4(31) / 100` → Hz |
| 19 | Error Code | `0` = OK |
| 37–39 | Line ID | `01,01,01` = Line 2; `00,00,00` = Line 1 |

Where `byte4` reads 4 bytes as a big-endian unsigned 32-bit integer:

```
value = byte[0] * 16777216 + byte[1] * 65536 + byte[2] * 256 + byte[3]
```

### Line Identification (50A / Split-Phase)

The 50A model monitors two legs of a split-phase 240V connection. Both lines stream independently at ~1 Hz. Bytes 37–39 distinguish them.

## Quick Start

### Node.js (macOS / Linux with BLE)

```bash
npm install
node watchdog.js
```

Requires a BLE adapter. On macOS, uses CoreBluetooth natively. On Linux, needs BlueZ and appropriate permissions.

### Python + gatttool (Raspberry Pi / Linux)

```bash
# Find the device
sudo hcitool lescan  # look for "PMD ..."

# Discover handles
sudo gatttool -b XX:XX:XX:XX:XX:XX --characteristics

# Run the monitor
sudo python3 watchdog_gatttool.py XX:XX:XX:XX:XX:XX
```

No pip dependencies — uses only Python stdlib + `gatttool` subprocess.

## Output Example

```json
{
  "line1": {"voltage": 124.6, "amps": 0.5, "watts": 21.2, "frequency": 60.1, "energy_kwh": 4204.8},
  "line2": {"voltage": 124.8, "amps": 1.0, "watts": 76.3, "frequency": 60.1, "energy_kwh": 3162.6},
  "total_energy_kwh": 7367.4,
  "error_code": 0,
  "timestamp": 1718644800
}
```

## Integration Examples

### Node-RED

Use an `exec` node to run the script on a 30-second timer, parse the JSON output, and feed to dashboard widgets.

### Home Assistant

Run as a command-line sensor or wrap as an MQTT publisher for real-time updates.

## How This Was Reverse-Engineered

1. Connected to the device with [LightBlue](https://punchthrough.com/lightblue/) BLE explorer
2. Identified the UART service (0xFFE0) and TX/RX characteristics
3. Captured raw notification data under varying loads
4. Decompiled the official Android app (`com.hughes.epo`) to extract parsing logic
5. Key source: `radinet.powerwatchdog2.Util.Bluetooth.BluetoothHandle.getPowerWatchdogData()`

## Limitations

- BLE range is limited (~30 ft typical, depends on environment)
- Only one device can hold an active GATT connection at a time
- The Hughes app must be disconnected before connecting with these tools
- Energy readings are per-line (total = line1 + line2)
- Error code meanings are not fully documented (only `0` = OK is confirmed)

## License

MIT
