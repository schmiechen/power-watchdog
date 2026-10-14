"""Tests for power_watchdog_stream.py using a fake gatttool that replays real recorded notifications.

Run:  python3 -m unittest discover -s tests -v        (stdlib only; works on Python 3.7+)
"""
import json
import os
import signal
import subprocess
import sys
import tempfile
import time
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
STREAM = os.path.join(ROOT, "power_watchdog_stream.py")
FAKE = os.path.join(HERE, "fake_gatttool.py")
MAC = "AA:BB:CC:DD:EE:FF"
LINUX = sys.platform.startswith("linux")


def run(extra_args=(), scenario="normal", speed="4", timeout=25, stdin=None, env_extra=None):
    """Run the stream script to completion; return (exit code, [json lines], seconds)."""
    env = dict(os.environ, PW_FAKE_SCENARIO=scenario, PW_FAKE_SPEED=str(speed))
    env.update(env_extra or {})
    cmd = [sys.executable, STREAM, MAC, "--gatttool", FAKE, "--skip-adapter-check", "--interval", "0.2",
           "--settle", "0.5", "--retry-delay", "0.3"] + list(extra_args)
    t0 = time.time()
    proc = subprocess.run(cmd, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=timeout,
                          stdin=stdin if stdin is not None else subprocess.DEVNULL)
    lines = [json.loads(x) for x in proc.stdout.decode().splitlines() if x.strip()]
    return proc.returncode, lines, time.time() - t0


def read_int(path):
    with open(path) as fh:
        return int(fh.read())


def read_lines(path):
    with open(path) as fh:
        return [json.loads(x) for x in fh.read().splitlines()]


def wait_int(path, timeout=20):
    """Wait for a pid file to appear with content (slow hardware starts processes slowly)."""
    end = time.time() + timeout
    while time.time() < end:
        try:
            value = read_int(path)
            return value
        except (OSError, ValueError):
            time.sleep(0.1)
    raise AssertionError("pid file never appeared: %s" % path)


def wait_gone(pid, timeout=10):
    end = time.time() + timeout
    while time.time() < end:
        if not alive(pid):
            return True
        time.sleep(0.1)
    return not alive(pid)


def alive(pid):
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


class StreamTests(unittest.TestCase):
    def test_normal_stream_has_both_lines_and_valid_values(self):
        code, lines, _ = run(["--once"])
        self.assertEqual(code, 0)
        self.assertEqual(lines[0]["state"], "connecting")
        live = [x for x in lines if x["state"] == "live"]
        self.assertEqual(len(live), 1)
        r = live[0]
        self.assertIn("line1", r)
        self.assertIn("line2", r)
        self.assertTrue(100 < r["line1"]["voltage"] < 140)
        self.assertTrue(59 < r["line1"]["frequency"] < 61)
        self.assertEqual(r["error_code"], 0)
        self.assertAlmostEqual(r["total_energy_kwh"], round(r["line1"]["energy_kwh"] + r["line2"]["energy_kwh"], 1), 1)

    def test_every_output_line_is_json_with_a_state(self):
        code, lines, _ = run(["--max-seconds", "3"])
        self.assertEqual(code, 0)
        self.assertTrue(lines)
        self.assertTrue(all("state" in x for x in lines))
        self.assertTrue(sum(1 for x in lines if x["state"] == "live") >= 3)

    def test_single_line_unit_has_no_line2(self):
        code, lines, _ = run(["--once"], scenario="line1_only")
        self.assertEqual(code, 0)
        live = [x for x in lines if x["state"] == "live"][0]
        self.assertIn("line1", live)
        self.assertNotIn("line2", live)

    def test_connection_refused_is_reported_as_unavailable(self):
        code, lines, _ = run(scenario="connect_error")
        self.assertEqual(code, 3)
        self.assertEqual(lines[-1]["state"], "unavailable")
        self.assertEqual(lines[-1]["reason"], "in_use_or_unreachable")
        self.assertEqual(sum(1 for x in lines if x["state"] == "connecting"), 2)  # two attempts, no tight loop

    def test_adapter_error_gets_its_own_reason(self):
        code, lines, _ = run(scenario="adapter_error")
        self.assertEqual(code, 3)
        self.assertEqual(lines[-1]["reason"], "adapter_not_ready")

    def test_connected_but_silent_is_no_data(self):
        code, lines, _ = run(["--stale-timeout", "1", "--connect-timeout", "5"], scenario="no_data")
        self.assertEqual(code, 3)
        self.assertEqual(lines[-1]["reason"], "no_data")

    def test_slow_connect_times_out(self):
        code, lines, took = run(["--connect-timeout", "1"], scenario="slow_connect")
        self.assertEqual(code, 3)
        self.assertEqual(lines[-1]["state"], "unavailable")
        self.assertLess(took, 12)

    def test_garbage_output_never_produces_a_reading(self):
        code, lines, _ = run(["--connect-timeout", "1", "--stale-timeout", "1"], scenario="garbage")
        self.assertEqual(code, 3)
        self.assertFalse([x for x in lines if x["state"] == "live"])

    def test_drop_mid_stream_reconnects_then_gives_up(self):
        code, lines, _ = run(["--attempts", "2"], scenario="drop_after", speed="1", timeout=40)
        states = [x["state"] for x in lines]
        self.assertIn("live", states)
        self.assertGreaterEqual(states.count("connecting"), 2)  # it tried to reconnect after the drop

    def test_max_seconds_stops_cleanly(self):
        code, _lines, took = run(["--max-seconds", "2"])
        self.assertEqual(code, 0)
        self.assertLess(took, 6)

    def _no_address(self, config_path):
        proc = subprocess.run([sys.executable, STREAM, "--gatttool", FAKE, "--skip-adapter-check",
                               "--config", config_path], stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                              stdin=subprocess.DEVNULL, timeout=10)
        self.assertEqual(proc.returncode, 2, proc.stderr.decode())
        first = json.loads(proc.stdout.decode().splitlines()[0])
        self.assertEqual(first["reason"], "not_configured")
        self.assertNotIn("Traceback", proc.stderr.decode())

    def test_empty_config_file_is_reported_not_crashed(self):
        self._no_address(os.devnull)

    def test_malformed_config_file_is_reported_not_crashed(self):
        with tempfile.NamedTemporaryFile("w", suffix=".conf") as cf:
            cf.write("this is not json")
            cf.flush()
            self._no_address(cf.name)

    def test_config_without_address_is_reported(self):
        with tempfile.NamedTemporaryFile("w", suffix=".conf") as cf:
            cf.write('{"cccd": "0x0021"}')
            cf.flush()
            self._no_address(cf.name)

    def _start(self, extra=(), scenario="normal", pidfile=None, stdin=subprocess.DEVNULL):
        env = dict(os.environ, PW_FAKE_SCENARIO=scenario, PW_FAKE_SPEED="4")
        if pidfile:
            env["PW_FAKE_PIDFILE"] = pidfile
        cmd = [sys.executable, STREAM, MAC, "--gatttool", FAKE, "--skip-adapter-check", "--interval", "0.2",
               "--settle", "0.5"] + list(extra)
        proc = subprocess.Popen(cmd, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, stdin=stdin)

        def cleanup():
            if proc.poll() is None:
                proc.kill()
            proc.wait()
            for stream in (proc.stdout, proc.stderr, proc.stdin):
                if stream and not stream.closed:
                    stream.close()
        self.addCleanup(cleanup)
        return proc

    def _wait_live(self, proc):
        while True:
            line = proc.stdout.readline()
            self.assertTrue(line, "process ended before going live")
            if json.loads(line)["state"] == "live":
                return

    def test_sigterm_disconnects_and_leaves_no_child(self):
        with tempfile.NamedTemporaryFile() as pf:
            proc = self._start(pidfile=pf.name)
            self._wait_live(proc)
            child = wait_int(pf.name)
            proc.send_signal(signal.SIGTERM)
            self.assertEqual(proc.wait(timeout=8), 0)
            self.assertTrue(wait_gone(child, 5), "gatttool child was left running")

    def test_child_that_ignores_sigterm_is_killed(self):
        with tempfile.NamedTemporaryFile() as pf:
            proc = self._start(scenario="ignore_term", pidfile=pf.name)
            self._wait_live(proc)
            child = wait_int(pf.name)
            t0 = time.time()
            proc.send_signal(signal.SIGTERM)
            self.assertEqual(proc.wait(timeout=10), 0)
            self.assertLess(time.time() - t0, 8)
            self.assertTrue(wait_gone(child, 5))

    def test_stdin_eof_stops_when_requested(self):
        proc = self._start(extra=["--stop-on-stdin-eof"], stdin=subprocess.PIPE)
        self._wait_live(proc)
        proc.stdin.close()
        self.assertEqual(proc.wait(timeout=8), 0)

    def test_stdin_eof_is_ignored_by_default(self):
        proc = self._start(stdin=subprocess.PIPE)
        self._wait_live(proc)
        proc.stdin.close()
        time.sleep(1.0)
        self.assertIsNone(proc.poll(), "must keep running when stdin closes unless asked")
        proc.send_signal(signal.SIGTERM)
        proc.wait(timeout=8)

    @unittest.skipUnless(LINUX, "parent-death signal is Linux-only")
    def test_child_is_cleaned_up_if_the_parent_is_killed(self):
        with tempfile.NamedTemporaryFile() as pf:
            wrapper = subprocess.Popen(
                [sys.executable, "-c",
                 "import os,sys,subprocess,time\n"
                 "env=dict(os.environ,PW_FAKE_SCENARIO='normal',PW_FAKE_SPEED='4',PW_FAKE_PIDFILE=sys.argv[1])\n"
                 "p=subprocess.Popen([sys.executable,sys.argv[2],'%s','--gatttool',sys.argv[3],'--skip-adapter-check',"
                 "'--exit-with-parent'],env=env,stdout=subprocess.PIPE)\n"
                 "print(p.pid,flush=True)\n"
                 "p.stdout.readline(); p.stdout.readline(); time.sleep(60)\n" % MAC,
                 pf.name, STREAM, FAKE], stdout=subprocess.PIPE)
            stream_pid = int(wrapper.stdout.readline())
            child = wait_int(pf.name)
            time.sleep(1.0)
            wrapper.kill()
            wrapper.wait()
            wrapper.stdout.close()
            self.assertTrue(wait_gone(stream_pid), "stream script outlived its parent")
            self.assertTrue(wait_gone(child), "gatttool outlived the stream script")

    def test_nothing_but_the_notification_enable_is_ever_written(self):
        with tempfile.NamedTemporaryFile(mode="r") as log:
            run(["--once"], env_extra={"PW_FAKE_ARGV_LOG": log.name})
            calls = read_lines(log.name)
        self.assertTrue(calls)
        for argv in calls:
            self.assertNotIn("--char-write", argv)  # plain write command is never used
            writes = [a for a in argv if a.startswith("--char-write")]
            self.assertEqual(writes, ["--char-write-req"])
            self.assertIn("--value=0100", argv)  # the only value ever written: enable notifications
            self.assertNotIn("--char-read", argv)

    def test_gatttool_exiting_cleanly_without_data_is_no_data(self):
        code, lines, _ = run(scenario="exit_clean")
        self.assertEqual(code, 3)
        self.assertEqual(lines[-1]["reason"], "no_data")


if __name__ == "__main__":
    unittest.main()
