#!/usr/bin/env python3
"""Install the Power Watchdog live reader and dashboard widget on a CoachProxy (or any Node-RED machine).

Run it with sudo on the machine that runs Node-RED and has Bluetooth:

    sudo python3 install_coachproxy.py             install or upgrade
    sudo python3 install_coachproxy.py --dry-run   show what it would do, change nothing
    sudo python3 install_coachproxy.py --uninstall remove the dashboard widget again

What it does, in order: checks the machine, copies two Python files, finds your Watchdog over Bluetooth, takes one test
reading, then adds the widget to the Node-RED dashboard (after saving a backup of your flows). It only ever adds or replaces
its own pieces (the ones whose id starts with "pwv_"); it never edits anything else, and it never writes to the Watchdog.
Standard library only, Python 3.7 or newer.
"""
import argparse
import copy
import datetime
import json
import os
import re
import shutil
import signal
import subprocess
import sys
import time
import urllib.error
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
FILES = ["power_watchdog_stream.py", "power_watchdog_ble.py"]
FLOW_FILE = os.path.join(HERE, "nodered", "power_watchdog_flow.json")
LEGACY_IDS = ("pw_inject_001", "pw_template_001")   # the first public release: 15 s poll + its widget
LEGACY_GROUP = "pw_group_001"
OWN_PREFIX = "pwv_"
OWN_TAB, OWN_GROUP = "pwv_ui_tab", "pwv_ui_group"
GET_URL = "https://raw.githubusercontent.com/schmiechen/power-watchdog-coachproxy/main/get.sh"
MAC_RE = re.compile(r"^[0-9A-Fa-f]{2}(:[0-9A-Fa-f]{2}){5}$")
DEFAULT_CCCD = "0x0021"
SCAN_BURSTS, SCAN_SECONDS = 3, 3   # three 3-second looks; short on purpose (see scan())

REASONS = {
    "in_use_or_unreachable": "The Watchdog did not answer. Another phone or tablet may be connected to it (close the Hughes app "
                             "everywhere: swipe it away, and on iPhone/iPad switch Bluetooth off in Settings, not Control "
                             "Center), or it is off or out of range.",
    "adapter_not_ready": "This computer's Bluetooth is not ready. Make sure the Bluetooth service is running "
                         "(sudo systemctl status bluetooth).",
    "no_data": "It connected but no readings arrived. Try again in a minute; if it keeps happening, switch the "
               "Watchdog's power off and on.",
    "not_configured": "No Watchdog address is set up yet.",
}


class InstallError(Exception):
    """Something went wrong that the user should read about, in plain words."""


# ---------------------------------------------------------------------------------------------------------------- output
def say(text=""):
    print(text, flush=True)


def step(n, total, title):
    say("")
    say("Step %d of %d: %s" % (n, total, title))


def ok(text):
    say("  OK   " + text)


def note(text):
    say("       " + text)


def warn(text):
    say("  !!   " + text)


# ------------------------------------------------------------------------------------------------------------ Node-RED
class NodeRedError(Exception):
    def __init__(self, status, message):
        Exception.__init__(self, message)
        self.status = status


class NodeRed(object):
    """The little bit of the Node-RED admin API we need. Version 2 of the flows API carries a revision number, so a
    deploy fails safely instead of overwriting someone else's change."""

    def __init__(self, base):
        self.base = base.rstrip("/")

    def _call(self, path, data=None, headers=None, method=None):
        h = {"Node-RED-API-Version": "v2", "Accept": "application/json"}
        h.update(headers or {})
        body = None
        if data is not None:
            body = json.dumps(data).encode("utf-8")
            h["Content-Type"] = "application/json"
        req = urllib.request.Request(self.base + path, data=body, headers=h, method=method or ("POST" if body else "GET"))
        try:
            with urllib.request.urlopen(req, timeout=40) as resp:
                raw = resp.read()
                return json.loads(raw.decode("utf-8")) if raw else {}
        except urllib.error.HTTPError as exc:
            raise NodeRedError(exc.code, "Node-RED answered HTTP %d" % exc.code)
        except (urllib.error.URLError, OSError, ValueError) as exc:
            raise NodeRedError(None, "cannot reach Node-RED at %s (%s)" % (self.base, exc))

    def get_flows(self):
        res = self._call("/flows")
        if not isinstance(res, dict) or "flows" not in res:
            raise NodeRedError(None, "this Node-RED did not return the expected flow list (is it version 1.x?)")
        return res["flows"], res.get("rev")

    def post_flows(self, flows, rev):
        return self._call("/flows", {"rev": rev, "flows": flows}, {"Node-RED-Deployment-Type": "nodes"})

    def modules(self):
        res = self._call("/nodes")
        return [m.get("module") for m in res] if isinstance(res, list) else []


# --------------------------------------------------------------------------------------------------- flow construction
def is_ours(node):
    return str(node.get("id", "")).startswith(OWN_PREFIX)


def find_tab(live, name):
    want = (name or "").strip().lower()
    for n in live:
        if n.get("type") == "ui_tab" and (n.get("name") or "").strip().lower() == want:
            return n
    return None


def build_payload(live, repo, install_dir, config_path, launcher, tab_name="Status"):
    """Return (new_flow_list, report). Pure function: nothing is sent anywhere. Raises InstallError if the result would
    touch anything that is not ours."""
    before = {n["id"]: n for n in live}
    new = [copy.deepcopy(n) for n in repo]
    placement = None

    legacy_group = before.get(LEGACY_GROUP)
    status_tab = find_tab(live, tab_name)
    if legacy_group is not None and legacy_group.get("type") == "ui_group":
        group_id = LEGACY_GROUP
        new = [n for n in new if n["id"] not in (OWN_TAB, OWN_GROUP)]
        placement = "the existing '%s' group" % (legacy_group.get("name") or LEGACY_GROUP)
    elif status_tab is not None:
        group_id = OWN_GROUP
        new = [n for n in new if n["id"] != OWN_TAB]
        for n in new:
            if n["id"] == OWN_GROUP:
                n["tab"] = status_tab["id"]
                n["name"] = "Watchdog"
                previous = before.get(OWN_GROUP)
                if previous is not None and previous.get("order") is not None:
                    n["order"] = previous["order"]
                else:
                    orders = [g.get("order", 0) for g in live if g.get("type") == "ui_group" and g.get("tab") == status_tab["id"]
                              and isinstance(g.get("order"), int)]
                    n["order"] = (max(orders) + 1) if orders else 1
        placement = "a new 'Watchdog' group on the '%s' tab" % status_tab.get("name")
    else:
        group_id = OWN_GROUP
        placement = "its own new 'Power Watchdog' tab (no tab called '%s' was found)" % tab_name

    for n in new:
        if n["id"] == "pwv_widget":
            n["group"] = group_id
            n["order"] = 1
        if n.get("type") == "tab" and n["id"] == "pwv_tab":
            for e in n.get("env", []):
                if e["name"] == "PW_DIR":
                    e["value"] = install_dir
                elif e["name"] == "PW_CONFIG":
                    e["value"] = config_path
                elif e["name"] == "PW_LAUNCHER":
                    e["value"] = launcher

    clash = [n["id"] for n in new if n["id"] in before and not is_ours(before[n["id"]])]
    if clash:
        raise InstallError("A piece of this install has the same id as something already in your flows (%s). "
                           "Nothing was changed." % ", ".join(clash))

    disabled = []
    out = []
    for n in live:
        if is_ours(n):
            continue                                   # replaced below (upgrade)
        if n["id"] in LEGACY_IDS and not n.get("d"):
            n = copy.deepcopy(n)
            n["d"] = True                              # switched off, never deleted
            disabled.append(n["id"])
        out.append(n)
    out += new

    after = {n["id"]: n for n in out}
    changed = [i for i in before if i in after and before[i] != after[i]]
    removed = [i for i in before if i not in after]
    bad = [i for i in changed if not (is_ours(before[i]) or i in LEGACY_IDS)] + [i for i in removed if not is_ours(before[i])]
    if bad:
        raise InstallError("Safety check failed: this would change something that is not ours (%s). Nothing was changed."
                           % ", ".join(bad))
    report = {"placement": placement, "added": sorted(i for i in after if i not in before),
              "replaced": sorted(i for i in before if is_ours(before[i]) and i in after),
              "disabled": disabled}
    return out, report


def remove_ours(live):
    out = [n for n in live if not is_ours(n)]
    gone = [n["id"] for n in live if is_ours(n)]
    return out, gone


def remove_ours_keep_tab(live):
    out = []
    for n in live:
        if not is_ours(n):
            out.append(n)
        elif n.get("type") == "tab":
            n = copy.deepcopy(n)
            n["disabled"] = True
            out.append(n)
    return out


def deploy(api, build, label):
    """Backup-free core of a deploy: build(live) -> new list. Retries once if somebody changed the flows meanwhile."""
    for attempt in (1, 2):
        live, rev = api.get_flows()
        out, info = build(live)
        try:
            api.post_flows(out, rev)
            return info
        except NodeRedError as exc:
            if exc.status == 409 and attempt == 1:
                note("Node-RED's flows changed while we were working; reading them again.")
                continue
            raise
    raise InstallError("Could not deploy %s: Node-RED kept changing." % label)


# ---------------------------------------------------------------------------------------------------------------- BLE
def code_key(code):
    s = re.sub(r"[^A-Z0-9]", "", (code or "").upper())
    return s[-9:]


def code_matches(name, code):
    key = code_key(code)
    return bool(key) and key in re.sub(r"[^A-Z0-9]", "", (name or "").upper())


def parse_scan(text):
    """hcitool lescan prints 'AA:BB:CC:DD:EE:FF name' lines; the name is '(unknown)' until the second advert."""
    found = {}
    for line in text.splitlines():
        parts = line.strip().split(None, 1)
        if len(parts) == 2 and MAC_RE.match(parts[0]) and parts[1] != "(unknown)":
            name = parts[1].strip()
            if name.upper().startswith("PMD"):
                found[parts[0].upper()] = name
    return found


def scan(hcitool, bursts=None, seconds=None, stop_when=None):
    """Look for Watchdogs in short bursts. Long Bluetooth operations on a Pi Zero W can starve its Wi-Fi, so each burst is
    3 s and ends with SIGINT (which makes hcitool switch scanning off properly)."""
    bursts, seconds = bursts or SCAN_BURSTS, seconds or SCAN_SECONDS
    found, problem = {}, None
    for _ in range(bursts):
        try:
            proc = subprocess.Popen([hcitool, "lescan"], stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                    universal_newlines=True)
        except OSError as exc:
            return found, "cannot run %s (%s)" % (hcitool, exc)
        time.sleep(seconds)
        if proc.poll() is None:
            proc.send_signal(signal.SIGINT)
        try:
            out, _ = proc.communicate(timeout=4)
        except subprocess.TimeoutExpired:
            proc.kill()
            out, _ = proc.communicate()
        found.update(parse_scan(out))
        low = out.lower()
        if "operation not permitted" in low or "permission denied" in low:
            return found, "permission"
        if "input/output error" in low or "set scan parameters failed" in low:
            problem = "scan_error"
        if stop_when and stop_when(found):
            break
    return found, problem


def choose_device(devices, code):
    """-> ('chosen', mac) | ('ask', [macs]) | ('none', [])"""
    if not devices:
        return "none", []
    if code:
        hits = [m for m, n in devices.items() if code_matches(n, code)]
        if len(hits) == 1:
            return "chosen", hits[0]
        if hits:
            return "ask", sorted(hits)
    if len(devices) == 1:
        return "chosen", list(devices)[0]
    return "ask", sorted(devices)


def discover_cccd(gatttool, mac):
    """The 'enable notifications' handle is the data characteristic's value handle + 1. Falls back to the usual 0x0021."""
    try:
        res = subprocess.run([gatttool, "-b", mac, "--characteristics"], stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                             universal_newlines=True, timeout=12)
    except (OSError, subprocess.TimeoutExpired):
        return DEFAULT_CCCD, False
    for line in res.stdout.splitlines():
        if "ffe2" in line.lower():
            m = re.search(r"char value handle = (0x[0-9a-fA-F]+)", line)
            if m:
                return "0x%04x" % (int(m.group(1), 16) + 1), True
    return DEFAULT_CCCD, False


def take_reading(install_dir, config_path, gatttool):
    """Run the real reader once. -> (reading_dict, None) or (None, reason_code)."""
    cmd = [sys.executable, os.path.join(install_dir, "power_watchdog_stream.py"), "--once", "--config", config_path]
    if gatttool != "gatttool":
        cmd += ["--gatttool", gatttool]
    try:
        res = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, universal_newlines=True, timeout=45)
    except (OSError, subprocess.TimeoutExpired):
        return None, "no_data"
    reason = None
    for line in res.stdout.splitlines():
        try:
            m = json.loads(line)
        except ValueError:
            continue
        if m.get("state") == "live":
            return m, None
        if m.get("state") == "unavailable":
            reason = m.get("reason")
    return None, reason or "no_data"


def plain_reading(r):
    parts = []
    for key, label in (("line1", "Line 1"), ("line2", "Line 2")):
        ln = r.get(key)
        if ln:
            parts.append("%s: %.1f V, %.2f A, %d W" % (label, ln.get("voltage", 0), ln.get("amps", 0), round(ln.get("watts", 0))))
    return "   ".join(parts) if parts else "a reading arrived"


# ---------------------------------------------------------------------------------------------------------- machine
def detect_nodered_user():
    try:
        out = subprocess.run(["ps", "-eo", "user=,args="], stdout=subprocess.PIPE, universal_newlines=True, timeout=10).stdout
    except (OSError, subprocess.TimeoutExpired):
        return None
    for line in out.splitlines():
        low = line.lower()
        if ("node-red" in low or "red.js" in low) and "install_coachproxy" not in low and "ps -eo" not in low:
            return line.split(None, 1)[0]
    return None


def stamp():
    return datetime.datetime.now().strftime("%Y%m%d-%H%M%S")


def backup_dir(install_dir):
    d = os.path.join(install_dir, "backups")
    os.makedirs(d, exist_ok=True)
    return d


def copy_files(install_dir, dry):
    changed = []
    for name in FILES:
        src, dst = os.path.join(HERE, name), os.path.join(install_dir, name)
        if not os.path.exists(src):
            raise InstallError("%s is missing next to the installer. Download the whole project again." % name)
        same = False
        if os.path.exists(dst):
            with open(src, "rb") as a, open(dst, "rb") as b:
                same = a.read() == b.read()
        if same:
            ok("%s is already up to date" % name)
            continue
        if dry:
            note("would copy %s to %s" % (name, install_dir))
            continue
        os.makedirs(install_dir, exist_ok=True)
        if os.path.exists(dst):
            shutil.copy2(dst, os.path.join(backup_dir(install_dir), "%s.%s" % (name, stamp())))
        shutil.copyfile(src, dst)
        os.chmod(dst, 0o755)
        changed.append(name)
        ok("copied %s" % name)
    return changed


def read_config(path):
    try:
        with open(path) as fh:
            c = json.load(fh)
        return c if isinstance(c, dict) else {}
    except (OSError, ValueError):
        return {}


def write_config(path, mac, cccd, install_dir):
    if os.path.exists(path):
        shutil.copy2(path, os.path.join(backup_dir(install_dir), os.path.basename(path) + "." + stamp()))
    with open(path, "w") as fh:
        json.dump({"mac": mac, "cccd": cccd}, fh, indent=2)
        fh.write("\n")
    os.chmod(path, 0o644)


def ask(prompt, interactive, default=None):
    if not interactive:
        return default
    try:
        ans = input(prompt).strip()
    except EOFError:
        return default
    return ans or default


# ---------------------------------------------------------------------------------------------------------------- main
def find_watchdog(args, interactive):
    """-> (mac, cccd_or_None)."""
    if args.mac:
        if not MAC_RE.match(args.mac):
            raise InstallError("'%s' is not a Bluetooth address (it looks like AA:BB:CC:DD:EE:FF)." % args.mac)
        return args.mac.upper(), None
    note("Looking for your Watchdog over Bluetooth (about 10 seconds)...")
    code = args.code
    stop = (lambda f: any(code_matches(n, code) for n in f.values())) if code else (lambda f: bool(f))
    devices, problem = scan(args.hcitool, stop_when=stop)
    if problem == "permission":
        raise InstallError("Bluetooth needs administrator rights. Run the installer with sudo.")
    status, pick = choose_device(devices, code)
    if status == "none":
        note("No Watchdog was found.")
        if interactive and not code:
            note("The code printed on the Watchdog's label (it looks like APMDB7C41E9F2) helps pick the right one.")
            code = ask("  Type the code, or press Enter to type the Bluetooth address instead: ", True)
            if code:
                devices, problem = scan(args.hcitool, stop_when=lambda f: any(code_matches(n, code) for n in f.values()))
                status, pick = choose_device(devices, code)
        if status == "none":
            mac = ask("  Type the Watchdog's Bluetooth address (AA:BB:CC:DD:EE:FF), or press Enter to stop: ", interactive)
            if mac and MAC_RE.match(mac):
                return mac.upper(), None
            raise InstallError("The Watchdog was not found. Check that it has shore power, close the Hughes app on every "
                               "phone and tablet, and try again. If you know its address, run the installer again with "
                               "--mac AA:BB:CC:DD:EE:FF." + (" (A Bluetooth scan problem was reported.)" if problem else ""))
    if status == "chosen":
        mac = pick
        ok("Found your Watchdog (%s)" % " ".join(devices[mac].split()))
        return mac, None
    note("More than one Watchdog is in range:")
    for i, mac in enumerate(pick, 1):
        note("  %d) %s" % (i, " ".join(devices[mac].split())))
    if not interactive:
        raise InstallError("More than one Watchdog is in range. Run again with --code followed by the code printed on "
                           "your Watchdog's label.")
    choice = ask("  Which one is yours? Type its number: ", True)
    if not (choice and choice.isdigit() and 1 <= int(choice) <= len(pick)):
        raise InstallError("No choice made, so nothing was changed.")
    return pick[int(choice) - 1], None


def run(args):
    interactive = sys.stdin.isatty() and not args.yes
    dry = args.dry_run
    install_dir = args.dir or ("/coachproxy" if os.path.isdir("/coachproxy") else "/opt/power-watchdog")
    config_path = args.config
    api = NodeRed(args.nodered)
    total = 5

    say("Power Watchdog installer" + ("  (preview only: nothing will be changed)" if dry else ""))
    say("It reads your Watchdog's power numbers; it never writes anything to it.")

    # ---- 1. check
    step(1, total, "Checking this computer")
    if sys.version_info < (3, 7):
        raise InstallError("Python 3.7 or newer is needed (this is %d.%d)." % sys.version_info[:2])
    ok("Python %d.%d" % sys.version_info[:2])
    for tool in (args.hcitool, args.gatttool):
        if not shutil.which(tool):
            raise InstallError("'%s' is not installed. Install it with: sudo apt-get install bluez" % tool)
    ok("Bluetooth tools found")
    bt = subprocess.run(["systemctl", "is-active", "bluetooth"], stdout=subprocess.PIPE, universal_newlines=True) \
        if shutil.which("systemctl") else None
    if bt is not None and bt.stdout.strip() != "active":
        warn("The Bluetooth service is not running. Start it with: sudo systemctl start bluetooth")
    api_ok = True
    try:
        mods = api.modules()
        ok("Node-RED answered")
        if mods and "node-red-dashboard" not in mods:
            raise InstallError("Node-RED's classic dashboard (node-red-dashboard) is not installed, so there is nowhere to "
                               "show the widget.")
    except NodeRedError as exc:
        api_ok = False
        warn("Could not use Node-RED automatically: %s." % exc)
        note("The files and Bluetooth setup below can still be done; at the end you will get the manual steps.")
    launcher = args.launcher
    if launcher is None:
        user = detect_nodered_user()
        launcher = "" if user == "root" else "sudo"
        note("Node-RED runs as '%s', so the reader will %s." % (user or "an unknown user",
                                                                 "run directly" if launcher == "" else "be started with sudo"))
    note("Install folder: %s" % install_dir)

    # ---- 2. files
    step(2, total, "Copying the program files")
    copy_files(install_dir, dry)

    # ---- 3. find the Watchdog
    step(3, total, "Finding your Watchdog")
    existing = read_config(config_path)
    if existing.get("mac") and not (args.mac or args.code):
        ok("Using the Watchdog already set up (%s)" % existing["mac"])
    elif dry:
        note("would scan for your Watchdog and write %s" % config_path)
    else:
        mac, cccd = find_watchdog(args, interactive)
        if cccd is None:
            cccd, found = discover_cccd(args.gatttool, mac)
            note("Notification handle %s%s" % (cccd, "" if found else " (the usual one)"))
        write_config(config_path, mac, cccd, install_dir)
        ok("Saved %s" % config_path)

    # ---- 4. test reading
    step(4, total, "Taking one test reading")
    if dry:
        note("would connect for a few seconds and show one reading")
    else:
        reading, reason = take_reading(install_dir, config_path, args.gatttool)
        if reading:
            ok("It works. " + plain_reading(reading))
        else:
            warn(REASONS.get(reason, "It could not read the Watchdog (%s)." % reason))
            note("The dashboard widget will still be added; it shows the same message and has a Retry button.")

    # ---- 5. dashboard
    step(5, total, "Adding the widget to the dashboard")
    if not os.path.exists(FLOW_FILE):
        raise InstallError("nodered/power_watchdog_flow.json is missing. Download the whole project again.")
    with open(FLOW_FILE, encoding="utf-8") as fh:
        repo = json.load(fh)
    if not api_ok:
        say("")
        say("Node-RED could not be reached automatically. Add the widget by hand:")
        note("1. Open Node-RED, menu > Import, and choose %s" % FLOW_FILE)
        note("2. Double-click the new 'Power Watchdog' flow tab and set PW_DIR=%s PW_CONFIG=%s PW_LAUNCHER=%s"
             % (install_dir, config_path, launcher))
        note("3. Drag the 'Watchdog Display' widget into a dashboard group, then Deploy.")
        return 2

    live, _ = api.get_flows()
    _, report = build_payload(live, repo, install_dir, config_path, launcher, args.tab)
    note("Widget goes in %s." % report["placement"])
    if report["disabled"]:
        note("Switching off (not deleting) the old polling pieces: %s" % ", ".join(report["disabled"]))
    if dry:
        note("preview only: %d pieces would be added, %d of ours replaced" % (len(report["added"]), len(report["replaced"])))
        return 0
    path = os.path.join(backup_dir(install_dir), "flows-%s.json" % stamp())
    with open(path, "w") as fh:
        json.dump(live, fh)
    ok("Saved a backup of your current flows: %s" % path)
    deploy(api, lambda cur: build_payload(cur, repo, install_dir, config_path, launcher, args.tab), "the widget")
    after, _ = api.get_flows()
    ids = {n["id"] for n in after}
    if "pwv_widget" not in ids or "pwv_ctrl" not in ids:
        raise InstallError("Node-RED accepted the change but the widget is not there. Your backup is at %s" % path)
    ok("The widget is installed")

    say("")
    say("All done. Open your dashboard, go to %s and look for the Watchdog panel."
        % ("the Status tab" if "Status" in report["placement"] else "the 'Power Watchdog' tab"))
    say("The panel connects to the Watchdog only while someone is looking at it. If it says Unavailable, close the Hughes")
    say("app on every phone and tablet, then press Retry.")
    say("To remove it later, run the same command you used to install, with --uninstall added at the end")
    say("(for example: curl -fsSL %s | bash -s -- --uninstall)." % GET_URL)
    return 0


def run_uninstall(args):
    api = NodeRed(args.nodered)
    say("Power Watchdog uninstall" + ("  (preview only)" if args.dry_run else ""))
    try:
        live, _ = api.get_flows()
    except NodeRedError as exc:
        raise InstallError("Could not reach Node-RED to remove the widget: %s" % exc)
    out, gone = remove_ours(live)
    if not gone:
        say("Nothing to remove: no Power Watchdog widget was found in Node-RED.")
        return 0
    say("Will remove %d Node-RED pieces (all with ids starting 'pwv_')." % len(gone))
    if args.dry_run:
        return 0
    install_dir = args.dir or ("/coachproxy" if os.path.isdir("/coachproxy") else "/opt/power-watchdog")
    path = os.path.join(backup_dir(install_dir), "flows-before-uninstall-%s.json" % stamp())
    with open(path, "w") as fh:
        json.dump(live, fh)
    try:
        deploy(api, lambda cur: (remove_ours(cur)[0], None), "the removal")
        ok("Removed. A backup of the flows before removal is at %s" % path)
    except NodeRedError as exc:
        if exc.status != 400:
            raise
        # Some newer Node-RED versions refuse to delete a whole flow tab through the API. Remove everything on it instead
        # and switch the empty tab off; deleting an empty tab by hand is one click.
        deploy(api, lambda cur: (remove_ours_keep_tab(cur), None), "the removal")
        ok("Removed the widget. A backup of the flows before removal is at %s" % path)
        warn("This version of Node-RED would not delete the empty 'Power Watchdog' flow tab. It is switched off and empty.")
        note("To tidy up: in Node-RED, right-click that tab's name and choose Delete.")
    if any(n["id"] in LEGACY_IDS and n.get("d") for n in live):
        note("The old polling pieces from the first release are still switched off; they were not touched.")
    if args.remove_files:
        for name in FILES:
            p = os.path.join(install_dir, name)
            if os.path.exists(p):
                os.remove(p)
        ok("Removed the program files from %s (your settings file was kept)" % install_dir)
    return 0


def parse_args(argv):
    p = argparse.ArgumentParser(description="Install the Power Watchdog live reader and dashboard widget.")
    p.add_argument("--dry-run", action="store_true", help="show what would happen; change nothing")
    p.add_argument("--yes", action="store_true", help="do not ask questions; use the defaults")
    p.add_argument("--uninstall", action="store_true", help="remove the dashboard widget (and with --remove-files, the program files)")
    p.add_argument("--remove-files", action="store_true")
    p.add_argument("--nodered", default="http://127.0.0.1:1880", help="Node-RED address (default %(default)s)")
    p.add_argument("--dir", help="install folder (default /coachproxy if it exists, else /opt/power-watchdog)")
    p.add_argument("--config", default="/etc/power_watchdog.conf", help="settings file (default %(default)s)")
    p.add_argument("--mac", help="the Watchdog's Bluetooth address, if you know it")
    p.add_argument("--code", help="the code printed on the Watchdog's label; picks the right one if several are in range")
    p.add_argument("--tab", default="Status", help="dashboard tab to put the widget on (default %(default)s)")
    p.add_argument("--launcher", help="'sudo' or '' (default: decided from the user Node-RED runs as)")
    p.add_argument("--hcitool", default=os.environ.get("PW_HCITOOL", "hcitool"))
    p.add_argument("--gatttool", default=os.environ.get("PW_GATTTOOL", "gatttool"))
    return p.parse_args(argv)


def main(argv=None):
    args = parse_args(argv)
    try:
        return run_uninstall(args) if args.uninstall else run(args)
    except InstallError as exc:
        say("")
        say("Stopped: %s" % exc)
        return 1
    except NodeRedError as exc:
        say("")
        say("Stopped: Node-RED problem: %s. Your flows were not changed unless a backup path was shown above." % exc)
        return 1
    except KeyboardInterrupt:
        say("")
        say("Cancelled. Anything already done is listed above; nothing in Node-RED was changed unless it said so.")
        return 1


if __name__ == "__main__":
    sys.exit(main())
