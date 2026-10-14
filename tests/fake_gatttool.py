#!/usr/bin/env python3
"""A stand-in for `gatttool` used by the tests. It replays real Power Watchdog notification packets recorded from a
genuine session (tests/fixtures/notifications.json) in the same text format gatttool prints.

Controlled by environment variables:
  PW_FAKE_SCENARIO   normal | line1_only | connect_error | adapter_error | no_data | slow_connect | drop_after |
                     ignore_term | garbage | exit_clean
  PW_FAKE_SPEED      replay speed multiplier (default 1)
  PW_FAKE_ARGV_LOG   append the arguments it was started with (JSON, one line) to this file
  PW_FAKE_PIDFILE    write its own pid here (so tests can check it really exits)
"""
import json
import os
import signal
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
import power_watchdog_ble as pw  # noqa: E402

scenario = os.environ.get("PW_FAKE_SCENARIO", "normal")
speed = float(os.environ.get("PW_FAKE_SPEED", "1"))

if os.environ.get("PW_FAKE_ARGV_LOG"):
    with open(os.environ["PW_FAKE_ARGV_LOG"], "a") as fh:
        fh.write(json.dumps(sys.argv[1:]) + "\n")
if os.environ.get("PW_FAKE_PIDFILE"):
    with open(os.environ["PW_FAKE_PIDFILE"], "w") as fh:
        fh.write(str(os.getpid()))
if scenario == "ignore_term":
    signal.signal(signal.SIGTERM, signal.SIG_IGN)


def say(text):
    sys.stdout.write(text + "\n")
    sys.stdout.flush()


def fail(text):
    sys.stderr.write(text + "\n")
    sys.stderr.flush()
    sys.exit(1)


def load_packets(line1_only):
    packets = json.load(open(os.path.join(HERE, "fixtures", "notifications.json")))["packets"]
    if not line1_only:
        return [(p["t"], p["hex"]) for p in packets]
    keep, i = [], 0
    raw = [(p["t"], list(bytes.fromhex(p["hex"]))) for p in packets]
    while i < len(raw) - 1:
        if raw[i][1][:3] == [1, 3, 32]:
            res = pw.parse_packet_pair(raw[i][1], raw[i + 1][1])
            if res and res["line_id"] == 1:
                keep += [(raw[i][0], bytes(raw[i][1]).hex()), (raw[i + 1][0], bytes(raw[i + 1][1]).hex())]
            i += 2
        else:
            i += 1
    return keep


def replay(packets, stop_after=None):
    start, t0 = time.time(), packets[0][0]
    for t, hexstr in packets:
        wait = (t - t0) / speed - (time.time() - start)
        if wait > 0:
            time.sleep(wait)
        if stop_after is not None and time.time() - start > stop_after:
            return
        say("Notification handle = 0x0020 value: " + " ".join(hexstr[i:i + 2] for i in range(0, 40, 2)) + " ")


if scenario == "connect_error":
    fail("connect error: Connection refused (111)")
if scenario == "adapter_error":
    fail("connect error: Function not implemented (38)")
if scenario == "slow_connect":
    time.sleep(30)
    sys.exit(0)

time.sleep(0.2)
say("Characteristic value was written successfully")
if scenario == "no_data":
    time.sleep(60)
elif scenario == "garbage":
    say("Notification handle = 0x0020 value: 01 02 03 ")
    say("something unrelated")
    time.sleep(60)
elif scenario == "exit_clean":
    sys.exit(0)
elif scenario == "drop_after":
    replay(load_packets(False), stop_after=1.0)
    sys.exit(1)
else:
    replay(load_packets(scenario == "line1_only"))
    time.sleep(60)
