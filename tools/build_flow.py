#!/usr/bin/env python3
"""Assemble nodered/power_watchdog_flow.json from the sources in nodered/src/.

  python3 tools/build_flow.py            write nodered/power_watchdog_flow.json
  python3 tools/build_flow.py --check    exit 1 if the committed file is out of date

The flow is self-contained: it brings its own dashboard tab and group, so it imports into any Node-RED that has the
classic node-red-dashboard installed. After import, drag the widget into whichever dashboard tab/group you like.
"""
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(ROOT, "nodered", "src")
OUT = os.path.join(ROOT, "nodered", "power_watchdog_flow.json")

INFO = """# Power Watchdog (viewer-driven)

The reader only runs while a browser is **showing the Power Watchdog widget** (on any dashboard tab). When the last
viewer leaves, switches tab, hides the page or closes it, the Bluetooth connection is dropped after a short grace period
so the Hughes phone app can use the Watchdog.

**Settings** (this flow tab's *Environment* variables): `PW_DIR` install folder of power_watchdog_stream.py,
`PW_CONFIG` config file (optional), `PW_LAUNCHER` `sudo` if gatttool needs root (leave empty if you gave gatttool the
needed capabilities), `PW_ARGS` extra reader options, `PW_GRACE` seconds before releasing (15), `PW_LEASE` seconds a
widget heartbeat stays valid (15), `PW_CAP` longest continuous session in seconds (3600), `PW_KEEPALIVE` keep-alive file the reader watches
(/dev/shm/power-watchdog.keepalive; must be writable by Node-RED and readable by the reader), `PW_KA_TIMEOUT` seconds
without a refresh before the reader stops by itself (15).

Requires: node-red-dashboard (classic), python3, gatttool (BlueZ), and `power_watchdog_stream.py` + `power_watchdog_ble.py`
installed in `PW_DIR`. Unofficial; not affiliated with Hughes Autoformers. Read-only: nothing is written to the device
except the standard notification enable."""


def read(name):
    with open(os.path.join(SRC, name), encoding="utf-8") as fh:
        return fh.read()


def build():
    tab = "pwv_tab"
    nodes = [
        {"id": tab, "type": "tab", "label": "Power Watchdog", "disabled": False, "info": INFO,
         "env": [
             {"name": "PW_DIR", "value": "/opt/power-watchdog", "type": "str"},
             {"name": "PW_CONFIG", "value": "", "type": "str"},
             {"name": "PW_LAUNCHER", "value": "", "type": "str"},
             {"name": "PW_ARGS", "value": "", "type": "str"},
             {"name": "PW_GRACE", "value": "15", "type": "num"},
             {"name": "PW_LEASE", "value": "15", "type": "num"},
             {"name": "PW_CAP", "value": "3600", "type": "num"},
             {"name": "PW_KEEPALIVE", "value": "/dev/shm/power-watchdog.keepalive", "type": "str"},
             {"name": "PW_KA_TIMEOUT", "value": "15", "type": "num"},
         ]},
        {"id": "pwv_ui_tab", "type": "ui_tab", "name": "Power Watchdog", "icon": "dashboard",
         "disabled": False, "hidden": False},
        {"id": "pwv_ui_group", "type": "ui_group", "name": "Power Watchdog", "tab": "pwv_ui_tab",
         "order": 1, "disp": False, "width": "6", "collapse": False},
        {"id": "pwv_widget", "type": "ui_template", "z": tab, "group": "pwv_ui_group", "name": "Watchdog Display",
         "order": 1, "width": "6", "height": "9", "format": read("widget.html"), "storeOutMessages": False,
         "fwdInMessages": False, "resendOnRefresh": True, "templateScope": "local", "className": "",
         "x": 150, "y": 100, "wires": [["pwv_ctrl"]]},
        {"id": "pwv_ctrl", "type": "function", "z": tab, "name": "Session controller", "func": read("controller.js"),
         "outputs": 5, "noerr": 0, "initialize": read("controller_on_start.js"),
         "finalize": "", "libs": [],
         "x": 400, "y": 100, "wires": [["pwv_exec"], ["pwv_widget"], ["pwv_tick"], ["pwv_ka_write"], ["pwv_ka_delete"]]},
        {"id": "pwv_ka_write", "type": "file", "z": tab, "name": "Keep-alive: refresh", "filename": "",
         "appendNewline": False, "createDir": False, "overwriteFile": "true", "encoding": "none",
         "x": 660, "y": 180, "wires": [[]]},
        {"id": "pwv_ka_delete", "type": "file", "z": tab, "name": "Keep-alive: remove", "filename": "",
         "appendNewline": False, "createDir": False, "overwriteFile": "delete", "encoding": "none",
         "x": 660, "y": 220, "wires": [[]]},
        {"id": "pwv_tick", "type": "delay", "z": tab, "name": "1 s tick", "pauseType": "delay", "timeout": "1",
         "timeoutUnits": "seconds", "rate": "1", "nbRateUnits": "1", "rateUnits": "second", "randomFirst": "1",
         "randomLast": "5", "randomUnits": "seconds", "drop": False, "allowrate": False, "outputs": 1,
         "x": 400, "y": 160, "wires": [["pwv_ctrl"]]},
        {"id": "pwv_exec", "type": "exec", "z": tab, "command": "env", "addpay": True, "append": "",
         "useSpawn": "true", "timer": "", "winHide": False, "oldrc": False, "name": "Watchdog reader",
         "x": 640, "y": 100, "wires": [["pwv_tag_out"], ["pwv_stderr"], ["pwv_tag_exit"]]},
        {"id": "pwv_tag_out", "type": "change", "z": tab, "name": "topic: stdout",
         "rules": [{"t": "set", "p": "topic", "pt": "msg", "to": "stdout", "tot": "str"}],
         "action": "", "property": "", "from": "", "to": "", "reg": False,
         "x": 880, "y": 60, "wires": [["pwv_ctrl"]]},
        {"id": "pwv_tag_exit", "type": "change", "z": tab, "name": "topic: exit",
         "rules": [{"t": "set", "p": "topic", "pt": "msg", "to": "exit", "tot": "str"}],
         "action": "", "property": "", "from": "", "to": "", "reg": False,
         "x": 880, "y": 140, "wires": [["pwv_ctrl"]]},
        {"id": "pwv_stderr", "type": "debug", "z": tab, "name": "Reader diagnostics (stderr)", "active": False,
         "tosidebar": True, "console": False, "tostatus": False, "complete": "payload", "targetType": "msg",
         "statusVal": "", "statusType": "auto", "x": 900, "y": 100, "wires": []},
        {"id": "pwv_note", "type": "comment", "z": tab, "name": "Power Watchdog: see this flow's info tab",
         "info": INFO, "x": 190, "y": 40, "wires": []},
    ]
    return json.dumps(nodes, indent=2, ensure_ascii=False) + "\n"


if __name__ == "__main__":
    text = build()
    if "--check" in sys.argv:
        current = open(OUT, encoding="utf-8").read() if os.path.exists(OUT) else ""
        if current != text:
            sys.exit("nodered/power_watchdog_flow.json is out of date: run python3 tools/build_flow.py")
        print("flow file is up to date")
    else:
        with open(OUT, "w", encoding="utf-8") as fh:
            fh.write(text)
        print("wrote %s (%d bytes, %d nodes)" % (OUT, len(text), len(json.loads(text))))
