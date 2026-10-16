# Changelog

## 1.0.0 - 2026-10-16

### Added
- One-command installer for CoachProxy owners (`get.sh` -> `install_coachproxy.py`): checks the machine, copies the files, finds
  the Watchdog over Bluetooth in short scans (the label code picks yours when several are in range), takes a test reading,
  saves a backup of the Node-RED flows and adds the widget to the Status tab. Idempotent (re-run = upgrade), `--dry-run`,
  `--uninstall`. It only ever adds or replaces its own `pwv_` pieces and switches the first release's polling nodes off.
- Error codes now show the way the unit and the Hughes app do, with the manual's meaning for E1 to E9 (for example `Error E7: Missing ground`).
- README rewritten for non-developers: plain-language install first, developer material below.
- `power_watchdog_stream.py`: on-demand live reader (JSON lines, clean stop on SIGTERM / `--max-seconds` / `--once` / stdin EOF),
  with unit tests that replay real recorded packets.
- Node-RED flow rebuilt around a viewer-driven widget: the Bluetooth connection is held only while the widget is on screen, on
  any dashboard tab, and released shortly after. States: Live, Connecting, Unavailable (with Retry), Paused.
- `docs/protocol.md`, `docs/coachproxy.md`, `docs/examples.md`, `install.sh`, `tools/build_flow.py`, `tests/`.

- Keep-alive file stop mechanism (`--keepalive-file`) so the reader can be stopped even when it cannot be signalled (a reader
  running as root through sudo, launched by a normal-user Node-RED). Found on a real Pi: signals were silently refused and the
  Watchdog stayed connected after the "release". A stop now removes the file first and waits for the reader's exit;
  signals are only a last resort, because in Node-RED 2.x a refused signal makes the exec node forget the process and
  never report its exit. The controller no longer claims "idle" until the exit is confirmed.

### Fixed
- The polling script stopped `bluetoothd` on every run, which left the adapter unable to make LE connections after a reboot
  (gatttool: "Function not implemented (38)"). It now leaves `bluetoothd` alone.
- README: the command characteristic is `FFF5` (there is no `FFE1`), real Python requirement (3.7+), real behavior.

- Widget: the 5 s lease heartbeat was echoed back by classic node-red-dashboard 2.x ("store output messages" was on) and
  replaced the readings with the heartbeat, so the panel blanked and said "Connecting..." every 5 s. The setting is now off.
  Found on a real dashboard; the 3.x dashboard used for the sandbox does not do this.
- Controller: the keep-alive file is now also removed when the reader reports `unavailable` and exits (a stale file was left).
- Widget: stale readings are shown dim red instead of dim green, so they cannot be mistaken for live ones.

### Changed
- No more fixed-interval background polling in the Node-RED flow. The old inject-based flow is gone from this repository.
