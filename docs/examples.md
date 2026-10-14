# Recipes for other platforms (UNTESTED examples)

These are starting points built on the reader's simple contract. **They have not been run against Home Assistant or the other
systems named here**; treat them as ideas and check them yourself. The Power Watchdog accepts one Bluetooth connection at a
time, so every recipe should connect briefly and often enough only to be useful, never around the clock.

## One reading from a shell

```bash
sudo python3 /opt/power-watchdog/power_watchdog_stream.py --once | tail -1
```

Prints a single JSON object (the last line is the reading) and exits, which frees the Watchdog.

## Home Assistant: slow polling with a command-line sensor (untested)

```yaml
command_line:
  - sensor:
      name: "Power Watchdog line 1 power"
      command: "sudo python3 /opt/power-watchdog/power_watchdog_stream.py --once | tail -1"
      scan_interval: 300          # every 5 minutes at most; each read locks the Hughes app out for a few seconds
      unit_of_measurement: "W"
      value_template: "{{ value_json.line1.watts if value_json.state == 'live' else none }}"
```

Home Assistant needs permission to run the command (passwordless sudo, or give the user access to the Bluetooth adapter).

## Start and stop on demand from any automation (untested)

The reader stops cleanly on `SIGTERM` or when its standard input closes (`--stop-on-stdin-eof`), so an automation can start it
when a dashboard or panel is opened and stop it when it is closed, for example piping its output to an MQTT publisher:

```bash
python3 /opt/power-watchdog/power_watchdog_stream.py --stop-on-stdin-eof \
  | while read -r line; do mosquitto_pub -t powerwatchdog/state -m "$line"; done
```

## Fault alerts

Readings include `error_code` (0 = OK; meanings in `protocol.md`). Because the connection is only held on demand, this
project does not watch for faults in the background; if you need alerts, the Hughes phone app does that.
