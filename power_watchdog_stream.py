#!/usr/bin/env python3
"""Stream live readings from a Hughes Power Watchdog (Gen I) over Bluetooth LE, on demand.

Start it and it connects, then prints one JSON line per second on stdout. Stop it (SIGTERM or SIGINT; closing its stdin
with --stop-on-stdin-eof; --max-seconds; --once) and it disconnects cleanly, which frees the unit for other apps. The
unit allows only ONE connection at a time, so run this only while someone is actually looking at the data.

Read-only: the only thing ever written to the device is the standard "enable notifications" descriptor write, the same
one the official app performs. It never stops bluetoothd and never resets the adapter.

Usage:
  python3 power_watchdog_stream.py [MAC] [--config PATH] [--once] [--max-seconds N]
                                   [--stop-on-stdin-eof] [--exit-with-parent]

Output (one JSON object per line):
  {"state":"connecting","attempt":1}
  {"state":"live","timestamp":1790000000,"line1":{...},"line2":{...},"total_energy_kwh":8533.1,"error_code":0}
  {"state":"unavailable","reason":"in_use_or_unreachable","detail":"connect error: Connection refused (111)"}
  "line2" is absent on single-line (30 A) units. "reason" is one of: not_configured, adapter_not_ready,
  in_use_or_unreachable, no_data.

Exit codes: 0 stopped normally (signal, --once, --max-seconds, stdin EOF, keep-alive file); 2 no device address
            configured; 3 unavailable after the retries.

Keep-alive file (--keepalive-file PATH): a way to stop the reader that needs no signals, so it also works when the
controlling program cannot signal it (for example the reader runs as root through sudo and the controller does not).
The controller keeps changing the file's CONTENT while it wants the reader to run; the reader stops when the file
disappears, or its content has not changed for --keepalive-timeout seconds (measured with a monotonic clock, so a jump
in the system time cannot trigger it). A short start-up grace lets the controller create the file first.
"""

import argparse
import json
import os
import select
import signal
import subprocess
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from power_watchdog_ble import (  # noqa: E402  (shared protocol helpers)
    DEFAULT_CCCD,
    ensure_adapter_ready,
    load_config,
    parse_hex_line,
    parse_packet_pair,
)

HEADER = [0x01, 0x03, 0x20]
STOP = {"flag": False}


def emit(obj):
    """Write one JSON line to stdout. A closed pipe means the consumer is gone: stop."""
    try:
        sys.stdout.write(json.dumps(obj, separators=(",", ":")) + "\n")
        sys.stdout.flush()
    except (BrokenPipeError, ValueError):
        STOP["flag"] = True


def log(args, message):
    if args.verbose:
        sys.stderr.write("[stream] %s\n" % message)
        sys.stderr.flush()


def classify(text):
    """Map gatttool's error text to a short machine-readable reason."""
    t = (text or "").lower()
    if ("function not implemented" in t or "no such device" in t or "network is down" in t
            or "rf-kill" in t or "can't init device" in t):
        return "adapter_not_ready"
    return "in_use_or_unreachable"


def set_pdeathsig():
    """Linux: ask the kernel to send SIGTERM to this process if its parent dies."""
    if not sys.platform.startswith("linux"):
        return
    try:
        import ctypes
        ctypes.CDLL("libc.so.6", use_errno=True).prctl(1, int(signal.SIGTERM))  # PR_SET_PDEATHSIG
    except Exception:
        pass


def terminate(proc):
    """Stop gatttool; its exit closes the socket, which makes the kernel send a clean HCI Disconnect."""
    if proc is None or proc.poll() is not None:
        return
    proc.terminate()
    try:
        proc.wait(timeout=3)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait()


def interruptible_sleep(seconds, keepalive=None):
    end = time.monotonic() + seconds
    while not STOP["flag"] and time.monotonic() < end:
        if keepalive is not None and keepalive.expired(time.monotonic()):
            STOP["flag"] = True
        time.sleep(0.1)


class KeepAlive(object):
    """Stop condition driven by a file: it must exist and its content must keep changing."""

    def __init__(self, path, timeout, startup):
        self.path = path
        self.timeout = timeout
        self.startup = startup
        self.begin = time.monotonic()
        self.seen = False
        self.last_content = None
        self.last_change = self.begin
        self.next_check = 0.0

    def expired(self, now):
        if not self.path or now < self.next_check:
            return False
        self.next_check = now + 0.5
        try:
            with open(self.path, "rb") as fh:
                content = fh.read(256)
        except OSError:
            if not self.seen:
                return now - self.begin > self.startup      # never appeared: give the controller time to create it
            return True                                      # it existed and is now gone: stop
        self.seen = True
        if content != self.last_content:
            self.last_content, self.last_change = content, now
            return False
        return now - self.last_change > self.timeout


class Reading(object):
    """Latest value of each line; decides when a combined reading may be emitted."""

    def __init__(self, interval, settle):
        self.latest = {1: None, 2: None}
        self.first_seen = None
        self.last_emit = 0.0
        self.interval = interval
        self.settle = settle

    def update(self, result, now):
        self.latest[result["line_id"]] = result
        if self.first_seen is None:
            self.first_seen = now

    def ready(self, now):
        if self.first_seen is None:
            return False
        # Wait briefly for the second line; single-line (30 A) units never send one.
        if not (self.latest[1] and self.latest[2]) and now - self.first_seen < self.settle:
            return False
        return now - self.last_emit >= self.interval

    def payload(self, now):
        self.last_emit = now
        out = {"state": "live", "timestamp": int(time.time())}
        energy, errors = 0.0, [0]
        for key, line_id in (("line1", 1), ("line2", 2)):
            r = self.latest[line_id]
            if r:
                out[key] = {k: r[k] for k in ("voltage", "amps", "watts", "frequency", "energy_kwh")}
                energy += r["energy_kwh"]
                errors.append(r["error_code"])
        out["total_energy_kwh"] = round(energy, 1)
        out["error_code"] = max(errors)
        return out


def build_command(args, mac, cccd):
    # --listen only keeps gatttool open together with a write action, so the notification enable is that action.
    return [args.gatttool, "-b", mac, "--char-write-req", "--handle=" + cccd, "--value=0100", "--listen"]


def run_attempt(args, mac, cccd, started_at, keepalive):
    """One connect-and-stream attempt. Returns (outcome, reason, detail, got_data)."""
    reading = Reading(args.interval, args.settle)
    pkt, buf, last_error = None, b"", ""
    connected, got_data, last_data = False, False, None
    try:
        proc = subprocess.Popen(
            build_command(args, mac, cccd), stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            preexec_fn=set_pdeathsig,
        )
    except OSError as exc:
        return "failed", "adapter_not_ready", "cannot run %s: %s" % (args.gatttool, exc), False
    fd = proc.stdout.fileno()
    began = time.monotonic()
    try:
        while True:
            now = time.monotonic()
            if STOP["flag"]:
                return "stopped", None, None, got_data
            if args.max_seconds and now - started_at >= args.max_seconds:
                return "stopped", None, None, got_data
            if keepalive.expired(now):
                log(args, "keep-alive file gone or stale: stopping")
                return "stopped", None, None, got_data
            watch = [fd] + ([0] if args.stop_on_stdin_eof else [])
            ready, _, _ = select.select(watch, [], [], 0.2)
            now = time.monotonic()
            if 0 in ready:
                if not os.read(0, 4096):
                    STOP["flag"] = True
                    continue
            if fd in ready:
                chunk = os.read(fd, 4096)
                if not chunk:  # gatttool exited
                    rc = proc.wait()
                    detail = last_error or "gatttool exited (%s)" % rc
                    if last_error:
                        reason = classify(last_error)
                    else:  # exited on its own without an error message or any readings
                        reason = "no_data" if rc == 0 else "in_use_or_unreachable"
                    return "failed", reason, detail, got_data
                buf += chunk
                while b"\n" in buf:
                    raw, buf = buf.split(b"\n", 1)
                    text = raw.decode("utf-8", "replace").strip()
                    if not text:
                        continue
                    low = text.lower()
                    if "error" in low or "refused" in low or "not implemented" in low:
                        last_error = text
                    if ("written successfully" in low or "attr handle" in low
                            or "notification handle" in low):
                        connected = True
                    if "notification handle" not in low:
                        continue
                    data = parse_hex_line(text)
                    if data is None or len(data) != 20:
                        continue
                    if data[0:3] == HEADER:
                        pkt = data
                    elif pkt is not None:
                        result = parse_packet_pair(pkt, data)
                        pkt = None
                        if result:
                            reading.update(result, now)
                            got_data = True
                            last_data = now
            if not connected and now - began > args.connect_timeout:
                return "failed", classify(last_error), last_error or "no response from the device", got_data
            if connected and not got_data and now - began > args.connect_timeout + args.stale_timeout:
                return "failed", "no_data", "connected but no readings arrived", got_data
            if got_data and now - last_data > args.stale_timeout:
                return "failed", "no_data", "readings stopped arriving", got_data
            if got_data and reading.ready(now):
                emit(reading.payload(now))
                if args.once:
                    return "stopped", None, None, got_data
    finally:
        terminate(proc)
        proc.stdout.close()


def main():
    p = argparse.ArgumentParser(description="Stream live Hughes Power Watchdog readings over BLE (read-only).")
    p.add_argument("mac", nargs="?", default=None, help="device address (default: from the config file)")
    p.add_argument("--config", default=None, help="config JSON path (default: /etc/power_watchdog.conf, ...)")
    p.add_argument("--cccd", default=None, help="notification descriptor handle (default: config or 0x0021)")
    p.add_argument("--once", action="store_true", help="exit after the first reading")
    p.add_argument("--max-seconds", type=float, default=0, help="stop after this many seconds (0 = no limit)")
    p.add_argument("--interval", type=float, default=1.0, help="seconds between readings (default 1)")
    p.add_argument("--settle", type=float, default=1.5, help="wait this long for a second line (default 1.5)")
    p.add_argument("--connect-timeout", type=float, default=5.0)
    p.add_argument("--stale-timeout", type=float, default=6.0)
    p.add_argument("--attempts", type=int, default=2, help="connect attempts before giving up (default 2)")
    p.add_argument("--retry-delay", type=float, default=2.0)
    p.add_argument("--stop-on-stdin-eof", action="store_true", help="stop when stdin is closed")
    p.add_argument("--exit-with-parent", action="store_true", help="Linux: exit if the parent process dies")
    p.add_argument("--keepalive-file", default=None, help="stop when this file disappears or stops changing")
    p.add_argument("--keepalive-timeout", type=float, default=15.0,
                   help="seconds without a change to the keep-alive file before stopping (default 15)")
    p.add_argument("--keepalive-startup", type=float, default=10.0,
                   help="seconds to wait for the keep-alive file to first appear (default 10)")
    p.add_argument("--gatttool", default=os.environ.get("PW_GATTTOOL", "gatttool"))
    p.add_argument("--skip-adapter-check", action="store_true")
    p.add_argument("-v", "--verbose", action="store_true")
    args = p.parse_args()

    try:
        config = load_config(args.config)
    except (OSError, ValueError) as exc:  # unreadable or malformed config file
        emit({"state": "unavailable", "reason": "not_configured", "detail": "cannot read config: %s" % exc})
        sys.exit(2)
    mac = args.mac or config.get("mac")
    cccd = args.cccd or config.get("cccd", DEFAULT_CCCD)
    if not mac:
        emit({"state": "unavailable", "reason": "not_configured",
              "detail": "no device address: pass MAC or set it in the config file"})
        sys.exit(2)

    def handler(signum, _frame):
        STOP["flag"] = True

    for sig in (signal.SIGTERM, signal.SIGINT, signal.SIGHUP):
        signal.signal(sig, handler)
    if args.exit_with_parent:
        set_pdeathsig()
        if os.getppid() == 1:
            sys.exit(0)

    if not args.skip_adapter_check:
        ensure_adapter_ready()

    started_at = time.monotonic()
    keepalive = KeepAlive(args.keepalive_file, args.keepalive_timeout, args.keepalive_startup)
    failures, reconnects, window_start = 0, 0, time.monotonic()
    code, last_reason, last_detail = 0, "in_use_or_unreachable", ""
    while not STOP["flag"]:
        emit({"state": "connecting", "attempt": failures + 1})
        outcome, reason, detail, got_data = run_attempt(args, mac, cccd, started_at, keepalive)
        log(args, "attempt ended: %s %s %s" % (outcome, reason, detail))
        if outcome == "stopped":
            break
        last_reason, last_detail = reason, detail
        if got_data:  # it was live and then dropped: allow a few quick reconnects
            if time.monotonic() - window_start > 60:
                reconnects, window_start = 0, time.monotonic()
            reconnects += 1
            failures = 0 if reconnects <= 3 else args.attempts
        else:
            failures += 1
        if failures >= args.attempts:
            emit({"state": "unavailable", "reason": last_reason, "detail": (last_detail or "")[:160]})
            code = 3
            break
        interruptible_sleep(args.retry_delay, keepalive)
    sys.exit(code)


if __name__ == "__main__":
    main()
