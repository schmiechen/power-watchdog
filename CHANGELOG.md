# Changelog

## Unreleased (branch viewer-driven-integration)

### Added
- `power_watchdog_stream.py`: on-demand live reader (JSON lines, clean stop on SIGTERM / `--max-seconds` / `--once` / stdin EOF),
  with unit tests that replay real recorded packets.
- Node-RED flow rebuilt around a viewer-driven widget: the Bluetooth connection is held only while the widget is on screen, on
  any dashboard tab, and released shortly after. States: Live, Connecting, Unavailable (with Retry), Paused.
- `docs/protocol.md`, `docs/coachproxy.md`, `docs/examples.md`, `install.sh`, `tools/build_flow.py`, `tests/`.

- Keep-alive file stop mechanism (`--keepalive-file`) so the reader can be stopped even when it cannot be signalled (a reader
  running as root through sudo, launched by a normal-user Node-RED). Found on a real Pi: signals were silently refused and the
  Watchdog stayed connected after the "release".

### Fixed
- The polling script stopped `bluetoothd` on every run, which left the adapter unable to make LE connections after a reboot
  (gatttool: "Function not implemented (38)"). It now leaves `bluetoothd` alone.
- README: the command characteristic is `FFF5` (there is no `FFE1`), real Python requirement (3.7+), real behavior.

### Changed
- No more fixed-interval background polling in the Node-RED flow. The old inject-based flow is gone from this repository.
