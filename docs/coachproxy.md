# Using the Power Watchdog with CoachProxyOS (optional)

CoachProxy is just one place this can run; nothing in the core depends on it. The easy way to install is in the
[README](../README.md#install-on-coachproxy-about-5-minutes). This page says exactly what that installer does, so you can
check it or do the same by hand.

**Verified on CoachProxyOS** (Raspberry Pi Zero W, Raspbian 10 "Buster", BlueZ 5.50, Python 3.7.3, Node-RED 2.2.2,
node-red-dashboard 2.19.2): the live reader connects, streams and disconnects cleanly, and the dashboard widget works in
desktop and phone browsers. The one-command installer was also run on a real CoachProxyOS Pi: a dry run, an uninstall, and
a fresh install (real Bluetooth scan, test reading, widget) with all of CoachProxy's own dashboard nodes left untouched.

## What the installer does

1. **Checks** Python 3.7+, `hcitool`/`gatttool`, that the Bluetooth service is running (it never stops it), that Node-RED's
   admin API answers on `127.0.0.1:1880` and that the classic `node-red-dashboard` is installed.
2. **Copies** `power_watchdog_stream.py` and `power_watchdog_ble.py` to `/coachproxy/` (or `/opt/power-watchdog` on a machine
   without `/coachproxy`), owned by root, mode 755. Anything it replaces is first copied to `<folder>/backups/`.
3. **Finds the Watchdog** with three short Bluetooth looks of 3 seconds each (short on purpose: long Bluetooth operations on a
   Pi Zero W can starve its Wi-Fi). It picks the device whose name starts with `PMD`. If several are in range it uses the code
   from the Watchdog's label to choose: the label code ends with the last five hex digits of the Bluetooth address, which also
   appear in the advertised name (checked on one unit). It then writes `/etc/power_watchdog.conf`
   (`{"mac": ..., "cccd": ...}`); an existing config is kept unless you pass `--mac` or `--code`.
4. **Takes one test reading** with `power_watchdog_stream.py --once` and prints it in plain words.
5. **Adds the widget to Node-RED** through its admin API: it saves your current flows to `<folder>/backups/flows-<time>.json`,
   then deploys an *additive* change (deployment type "nodes", with Node-RED's revision check so it cannot overwrite someone
   else's edit). It only adds or replaces nodes whose id starts with `pwv_`, and refuses to deploy if the result would change
   anything else. The widget goes into a new **Watchdog** group on the **Status** tab (or, if there is no Status tab, its own
   **Power Watchdog** tab). The flow tab's environment variables are filled in: `PW_DIR`, `PW_CONFIG`, and `PW_LAUNCHER=sudo`
   when Node-RED does not run as root (the case on CoachProxyOS, where it runs as `pi`).
6. **First-release owners:** if the old 15-second poll nodes (`pw_inject_001`, `pw_template_001`) exist they are switched off,
   not deleted, and the old `pw_group_001` group is reused so the widget stays where it was.

Re-running it is an upgrade: the same pieces are replaced in place and nothing else changes. `--dry-run` prints the plan and
changes nothing; `--uninstall` removes the `pwv_` pieces (a copy of the flows before removal is saved first; add
`--remove-files` to delete the two Python files as well). Some newer Node-RED versions refuse to delete a whole flow tab through
the API; the installer then empties the tab, switches it off and tells you to delete it by hand.

## By hand

1. Copy `power_watchdog_stream.py` and `power_watchdog_ble.py` to `/coachproxy/` (owner root, mode 755).
2. Put your device address in `/etc/power_watchdog.conf` (or `/coachproxy/power_watchdog.conf`):
   `{"mac": "AA:BB:CC:DD:EE:FF", "cccd": "0x0021"}`
3. Import `nodered/power_watchdog_flow.json` into Node-RED, then set the flow tab's environment variables:
   `PW_DIR=/coachproxy` and `PW_LAUNCHER=sudo` (Node-RED runs as the `pi` user there, which has passwordless sudo).
4. Drag the *Watchdog Display* widget into the dashboard group you want (for example your Status tab) and deploy.

## CoachProxyOS notes

- Node-RED runs as the `pi` user and the reader runs as root through `sudo`, so Node-RED cannot signal it. The flow therefore
  stops the reader through a keep-alive file (`PW_KEEPALIVE`, in `/dev/shm`), which works regardless of who owns the reader.
  (Verified on the Pi: the reader and its Bluetooth process are gone within about 2 seconds of the file being removed.)
- `bluetoothd` is started at boot there (via `bthelper@hci0`). Leave it running; the old polling script that stopped it
  stopped working after a reboot (gatttool: "Function not implemented (38)").
- The Pi Zero W has one core and shares one chip between Wi-Fi and Bluetooth. If its Wi-Fi drops, turn Wi-Fi power save off
  (`sudo /sbin/iw dev wlan0 set power_save off`; add a small systemd timer if you want it to persist).
- The old 15-second polling flow keeps the Watchdog connected most of the time and locks the Hughes app out. The installer
  switches it off; if you imported it by hand, disable its inject node.
- CoachProxyOS does not advertise a `.local` name on the network (mDNS is off), so use the **IP Address** shown under
  **System > Network Information** on the dashboard to reach it over SSH.
- The dashboard is the classic `node-red-dashboard` (no longer maintained upstream, but it is what CoachProxyOS ships). In this
  dashboard a widget's "store output messages" setting must be off for widgets that report to the flow; the shipped flow
  already has it off (with it on, the widget's own heartbeat was echoed back as its data and blanked the readings).
- Whether a CoachProxyOS re-image or update replaces the flows file has not been checked. After either, run the installer again.
