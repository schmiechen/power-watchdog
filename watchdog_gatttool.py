#!/usr/bin/env python3
"""
watchdog_gatttool.py - Read Hughes Power Watchdog via BLE GATT.
No pip dependencies. Uses gatttool subprocess.
Must run as root: sudo python3 watchdog_gatttool.py <MAC> [--handle 0xNNNN] [--cccd 0xNNNN]

Outputs one JSON line to stdout, then exits.
"""

import subprocess
import sys
import json
import time
import re
import signal

DEFAULT_CCCD_HANDLE = '0x000f'
LISTEN_SECONDS = 6
TIMEOUT_SECONDS = 12


def byte4(data, offset):
    """Read 4 bytes as big-endian unsigned 32-bit integer."""
    return (data[offset] * 16777216 +
            data[offset + 1] * 65536 +
            data[offset + 2] * 256 +
            data[offset + 3])


def parse_packet_pair(combined):
    """Parse a 40-byte combined packet pair into measurements."""
    if len(combined) != 40:
        return None

    volt = byte4(combined, 3) / 10000.0
    amp = byte4(combined, 7) / 10000.0
    watt = byte4(combined, 11) / 10000.0
    energy = byte4(combined, 15) / 10000.0
    freq = byte4(combined, 31) / 100.0
    error_code = combined[19]
    is_line2 = (combined[37] == 1 and combined[38] == 1 and combined[39] == 1)

    return {
        'line': 2 if is_line2 else 1,
        'voltage': round(volt, 1),
        'amps': round(amp, 1),
        'watts': round(watt, 1),
        'frequency': round(freq, 1),
        'energy_kwh': round(energy, 1),
        'error_code': error_code,
    }


def parse_hex_line(line):
    """Extract bytes from gatttool notification output line."""
    match = re.search(r'value:\s+((?:[0-9a-fA-F]{2}\s*)+)', line)
    if not match:
        return None
    hex_str = match.group(1).strip()
    return [int(b, 16) for b in hex_str.split()]


def main():
    if len(sys.argv) < 2:
        sys.stderr.write(
            "Usage: sudo python3 watchdog_gatttool.py <MAC> [--cccd 0xNNNN]\n"
            "  MAC:  Power Watchdog BLE address (from hcitool lescan)\n"
            "  --cccd: CCCD handle to enable notifications (default: 0x000f)\n"
            "\n"
            "To find the correct handle:\n"
            "  sudo gatttool -b <MAC> --characteristics\n"
            "  Look for UUID ffe2 (TX), CCCD handle is typically +1\n"
        )
        sys.exit(1)

    mac = sys.argv[1]
    cccd_handle = DEFAULT_CCCD_HANDLE

    for i, arg in enumerate(sys.argv[2:], 2):
        if arg == '--cccd' and i + 1 < len(sys.argv):
            cccd_handle = sys.argv[i + 1]

    cmd = [
        'gatttool', '-b', mac,
        '--char-write-req',
        '--handle=' + cccd_handle,
        '--value=0100',
        '--listen'
    ]

    try:
        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            universal_newlines=True
        )
    except FileNotFoundError:
        print(json.dumps({'error': 'gatttool not found'}))
        sys.exit(1)

    line1_data = None
    line2_data = None
    temp_bytes = None
    start = time.time()

    try:
        while time.time() - start < TIMEOUT_SECONDS:
            line = proc.stdout.readline()
            if not line:
                if proc.poll() is not None:
                    break
                continue

            line = line.strip()
            if 'Notification' not in line:
                continue

            pkt = parse_hex_line(line)
            if not pkt or len(pkt) != 20:
                continue

            # First packet of pair: starts with 01 03 20
            if pkt[0] == 0x01 and pkt[1] == 0x03 and pkt[2] == 0x20:
                temp_bytes = pkt
                continue

            # Second packet: combine with first
            if temp_bytes and len(temp_bytes) == 20:
                combined = temp_bytes + pkt
                temp_bytes = None

                result = parse_packet_pair(combined)
                if result:
                    if result['line'] == 1:
                        line1_data = result
                    else:
                        line2_data = result

                    # Once we have both lines, we're done
                    if line1_data and line2_data:
                        break
            else:
                temp_bytes = None

            # If we've been listening long enough, accept partial data
            if time.time() - start > LISTEN_SECONDS:
                if line1_data or line2_data:
                    break

    finally:
        proc.terminate()
        try:
            proc.wait(timeout=3)
        except subprocess.TimeoutExpired:
            proc.kill()

    if not line1_data and not line2_data:
        print(json.dumps({'error': 'timeout', 'message': 'no data received'}))
        sys.exit(0)

    output = {'timestamp': int(time.time())}

    if line1_data:
        output['line1'] = {
            'voltage': line1_data['voltage'],
            'amps': line1_data['amps'],
            'watts': line1_data['watts'],
            'frequency': line1_data['frequency'],
            'energy_kwh': line1_data['energy_kwh'],
        }

    if line2_data:
        output['line2'] = {
            'voltage': line2_data['voltage'],
            'amps': line2_data['amps'],
            'watts': line2_data['watts'],
            'frequency': line2_data['frequency'],
            'energy_kwh': line2_data['energy_kwh'],
        }

    if line1_data and line2_data:
        output['total_energy_kwh'] = round(
            line1_data['energy_kwh'] + line2_data['energy_kwh'], 1
        )
        output['error_code'] = line2_data['error_code']

    print(json.dumps(output))


if __name__ == '__main__':
    main()
