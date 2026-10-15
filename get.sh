#!/bin/bash
# get.sh - download the Power Watchdog project and run its installer.
#
#   curl -fsSL https://raw.githubusercontent.com/schmiechen/power-watchdog/main/get.sh | bash
#   curl -fsSL https://raw.githubusercontent.com/schmiechen/power-watchdog/main/get.sh | bash -s -- --dry-run
#
# What it does: downloads the project as one archive into a new temporary folder, then runs install_coachproxy.py with
# sudo. Nothing else. Read install_coachproxy.py first if you like (it is in the same project). Options after "--" are
# passed to the installer (--dry-run shows what would happen without changing anything).
#
# Test/advanced: POWER_WATCHDOG_ARCHIVE can point to another .tar.gz (a URL or a local file).

set -e

ARCHIVE="${POWER_WATCHDOG_ARCHIVE:-https://github.com/schmiechen/power-watchdog/archive/refs/heads/main.tar.gz}"

say() { printf '%s\n' "$*"; }

if ! command -v python3 >/dev/null 2>&1; then
    say "Python 3 is needed but was not found. Install it with: sudo apt-get install python3"
    exit 1
fi

WORK="$(mktemp -d "${TMPDIR:-/tmp}/power-watchdog.XXXXXX")"
trap 'rm -rf "$WORK"' EXIT

say "Downloading Power Watchdog..."
case "$ARCHIVE" in
    http://*|https://*)
        if command -v curl >/dev/null 2>&1; then
            curl -fsSL "$ARCHIVE" -o "$WORK/pw.tar.gz"
        elif command -v wget >/dev/null 2>&1; then
            wget -q "$ARCHIVE" -O "$WORK/pw.tar.gz"
        else
            say "Neither curl nor wget was found, so the download cannot start."
            exit 1
        fi ;;
    *) cp "$ARCHIVE" "$WORK/pw.tar.gz" ;;
esac

mkdir "$WORK/src"
tar -xzf "$WORK/pw.tar.gz" -C "$WORK/src" --strip-components=1

if [ ! -f "$WORK/src/install_coachproxy.py" ]; then
    say "The download did not contain the installer. Please report this."
    exit 1
fi

# Under "curl | bash" the script itself is the standard input, so questions must be read from the keyboard instead.
# (-r is not enough: /dev/tty can look readable and still fail to open when there is no terminal, so really try it.)
if [ ! -t 0 ] && ( : < /dev/tty ) 2>/dev/null; then
    exec_in=/dev/tty
else
    exec_in=/dev/stdin
fi

if [ "$(id -u)" -eq 0 ]; then
    python3 "$WORK/src/install_coachproxy.py" "$@" < "$exec_in"
else
    say "The installer needs administrator rights (sudo) to talk to Bluetooth."
    sudo python3 "$WORK/src/install_coachproxy.py" "$@" < "$exec_in"
fi
