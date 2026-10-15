#!/usr/bin/env python3
"""A stand-in for `hcitool lescan` used by the installer tests.

  PW_FAKE_ADVERTS  'MAC|name;MAC|name' devices to "see" (each is printed first as (unknown), then with its name, like the real tool)
  PW_FAKE_HCI      permission | io    simulate the two common scan errors
"""
import os
import signal
import sys
import time

if sys.argv[1:2] != ["lescan"]:
    sys.exit(1)
mode = os.environ.get("PW_FAKE_HCI", "")
if mode == "permission":
    print("Set scan parameters failed: Operation not permitted")
    sys.exit(1)
if mode == "io":
    print("Set scan parameters failed: Input/output error")
    sys.exit(1)

signal.signal(signal.SIGINT, lambda *a: sys.exit(0))
print("LE Scan ...", flush=True)
for item in filter(None, os.environ.get("PW_FAKE_ADVERTS", "").split(";")):
    mac, name = item.split("|", 1)
    print(mac + " (unknown)", flush=True)
    print(mac + " " + name, flush=True)
time.sleep(30)
