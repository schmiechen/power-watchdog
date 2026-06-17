#!/usr/bin/env python3
"""Read Hughes Power Watchdog (Gen I, 50A) shore power data via BLE.

Reads device config from a JSON config file or command-line args.
Outputs one JSON line to stdout per invocation, then exits.

Usage:
  sudo python3 power_watchdog_ble.py                         # uses /etc/power_watchdog.conf
  sudo python3 power_watchdog_ble.py --config /path/to.conf  # custom config path
  sudo python3 power_watchdog_ble.py AA:BB:CC:DD:EE:FF       # direct MAC (uses default CCCD)
"""

import sys
import json
import time
import subprocess
import re
import argparse
import os

DEFAULT_CONFIG_PATHS = [
    '/etc/power_watchdog.conf',
    '/coachproxy/power_watchdog.conf',
    os.path.join(os.path.dirname(os.path.abspath(__file__)), 'config.json'),
]
DEFAULT_CCCD = "0x0021"
COLLECT_SECONDS = 5
HARD_TIMEOUT = 10


def load_config(config_path=None):
    paths = [config_path] if config_path else DEFAULT_CONFIG_PATHS
    for path in paths:
        if path and os.path.exists(path):
            with open(path) as f:
                return json.load(f)
    return {}


def byte4(arr, offset):
    return (arr[offset] * 16777216 + arr[offset+1] * 65536 +
            arr[offset+2] * 256 + arr[offset+3])


def parse_packet_pair(pkt1, pkt2):
    combined = pkt1 + pkt2
    if len(combined) != 40:
        return None
    if combined[0:3] != [0x01, 0x03, 0x20]:
        return None

    voltage = byte4(combined, 3) / 10000.0
    amps = byte4(combined, 7) / 10000.0
    watts = byte4(combined, 11) / 10000.0
    energy_kwh = byte4(combined, 15) / 10000.0
    error_code = combined[19]
    frequency = byte4(combined, 31) / 100.0

    if combined[37] == 1 and combined[38] == 1 and combined[39] == 1:
        line_id = 2
    else:
        line_id = 1

    return {
        "line_id": line_id,
        "voltage": round(voltage, 1),
        "amps": round(amps, 2),
        "watts": round(watts, 1),
        "frequency": round(frequency, 2),
        "energy_kwh": round(energy_kwh, 1),
        "error_code": error_code,
    }


def parse_hex_line(line):
    m = re.search(r"value:\s*((?:[0-9a-fA-F]{2}\s*)+)", line)
    if not m:
        return None
    return [int(b, 16) for b in m.group(1).strip().split()]


def main():
    parser = argparse.ArgumentParser(
        description="Read Hughes Power Watchdog BLE data"
    )
    parser.add_argument("mac", nargs="?", default=None,
                        help="BLE MAC address (overrides config)")
    parser.add_argument("--cccd", default=None,
                        help="CCCD handle (default: from config or 0x0021)")
    parser.add_argument("--config", default=None,
                        help="Path to config JSON file")
    args = parser.parse_args()

    config = load_config(args.config)

    mac = args.mac or config.get("mac")
    cccd = args.cccd or config.get("cccd", DEFAULT_CCCD)

    if not mac:
        sys.stderr.write(
            "Error: No MAC address. Provide via argument, config file, or run setup.sh\n"
            "Searched: " + ", ".join(DEFAULT_CONFIG_PATHS) + "\n"
        )
        print(json.dumps({"error": "no_config"}))
        sys.exit(1)

    cmd = [
        "gatttool", "-b", mac,
        "--char-write-req",
        "--handle=" + cccd,
        "--value=0100",
        "--listen",
    ]

    try:
        proc = subprocess.Popen(
            cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE
        )
    except OSError as e:
        print(json.dumps({"error": "failed to start gatttool: " + str(e)}))
        sys.exit(1)

    line1 = None
    line2 = None
    packets = []
    start = time.time()

    try:
        while time.time() - start < HARD_TIMEOUT:
            if line1 and line2:
                break

            raw = proc.stdout.readline()
            if not raw:
                if proc.poll() is not None:
                    break
                continue

            text = raw.decode("utf-8", errors="replace").strip()
            if "Notification handle" not in text:
                continue

            hex_bytes = parse_hex_line(text)
            if hex_bytes is None or len(hex_bytes) != 20:
                continue

            if hex_bytes[0:3] == [0x01, 0x03, 0x20]:
                packets = [hex_bytes]
            elif packets:
                packets.append(hex_bytes)
                result = parse_packet_pair(packets[0], packets[1])
                packets = []
                if result is None:
                    continue
                if result["line_id"] == 1:
                    line1 = result
                else:
                    line2 = result

            if time.time() - start >= COLLECT_SECONDS and (line1 or line2):
                break

    finally:
        proc.terminate()
        try:
            proc.wait(timeout=3)
        except subprocess.TimeoutExpired:
            proc.kill()

    if not line1 and not line2:
        print(json.dumps({"error": "timeout"}))
        sys.exit(1)

    output = {"timestamp": int(time.time())}

    if line1:
        output["line1"] = {
            "voltage": line1["voltage"],
            "amps": line1["amps"],
            "watts": line1["watts"],
            "frequency": line1["frequency"],
            "energy_kwh": line1["energy_kwh"],
        }
    if line2:
        output["line2"] = {
            "voltage": line2["voltage"],
            "amps": line2["amps"],
            "watts": line2["watts"],
            "frequency": line2["frequency"],
            "energy_kwh": line2["energy_kwh"],
        }

    if line1 and line2:
        output["total_energy_kwh"] = round(
            line1["energy_kwh"] + line2["energy_kwh"], 1
        )

    error_code = 0
    if line1:
        error_code = line1["error_code"]
    elif line2:
        error_code = line2["error_code"]
    output["error_code"] = error_code

    print(json.dumps(output))


if __name__ == "__main__":
    main()
