"""Structural tests for nodered/power_watchdog_flow.json (the importable flow). Stdlib only.
Run: python3 -m unittest discover -s tests -v
"""
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FLOW = os.path.join(ROOT, "nodered", "power_watchdog_flow.json")


def load():
    with open(FLOW, encoding="utf-8") as fh:
        return json.load(fh)


class FlowTests(unittest.TestCase):
    def test_flow_file_matches_its_sources(self):
        proc = subprocess.run([sys.executable, os.path.join(ROOT, "tools", "build_flow.py"), "--check"],
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        self.assertEqual(proc.returncode, 0, proc.stderr.decode())

    def test_ids_are_unique_and_wires_point_at_real_nodes(self):
        nodes = load()
        ids = [n["id"] for n in nodes]
        self.assertEqual(len(ids), len(set(ids)))
        for n in nodes:
            for out in n.get("wires", []):
                for target in out:
                    self.assertIn(target, ids, "%s wires to a missing node" % n["id"])
        for n in nodes:
            if "z" in n:
                self.assertIn(n["z"], ids)
            for ref in ("group", "tab"):
                if ref in n:
                    self.assertIn(n[ref], ids)

    def test_it_brings_its_own_dashboard_tab_and_group(self):
        types = {n["type"] for n in load()}
        self.assertTrue({"tab", "ui_tab", "ui_group", "ui_template", "function", "exec"} <= types)

    def test_exec_node_streams_in_spawn_mode_with_the_payload_as_arguments(self):
        ex = [n for n in load() if n["type"] == "exec"][0]
        self.assertEqual(ex["useSpawn"], "true")
        self.assertTrue(ex["addpay"])
        self.assertEqual(ex["command"], "env")
        self.assertEqual(len(ex["wires"]), 3)  # stdout, stderr, return code

    def test_settings_are_documented_flow_environment_variables(self):
        tab = [n for n in load() if n["type"] == "tab"][0]
        names = {e["name"] for e in tab["env"]}
        self.assertEqual(names, {"PW_DIR", "PW_CONFIG", "PW_LAUNCHER", "PW_ARGS", "PW_GRACE", "PW_LEASE", "PW_CAP",
                                 "PW_KEEPALIVE", "PW_KA_TIMEOUT"})
        for name in names:
            self.assertIn(name, tab["info"])

    def test_widget_does_not_echo_its_input_back_to_the_controller(self):
        w = [n for n in load() if n["type"] == "ui_template"][0]
        self.assertFalse(w["fwdInMessages"])
        self.assertTrue(w["resendOnRefresh"])
        self.assertEqual(w["wires"], [["pwv_ctrl"]])

    def test_widget_heartbeats_are_not_stored_as_widget_state(self):
        # classic node-red-dashboard 2.x echoes every scope.send() back to all browsers as the widget's new data when
        # storeOutMessages is on; the 5 s heartbeat then blanked the readings (seen on a real dashboard)
        w = [n for n in load() if n["type"] == "ui_template"][0]
        self.assertFalse(w["storeOutMessages"])

    def test_widget_shows_error_codes_the_way_the_unit_and_the_app_do_with_the_manual_meanings(self):
        # codes: app code (Util.getErrorCodeString). E1-E9 meanings: Hughes manual. F-codes: bare (not in the manual page checked)
        w = [n for n in load() if n["type"] == "ui_template"][0]["format"]
        m = re.search(r"var PW_ERRORS = \{.*?\};\s*scope\.pwErrorText = function.*?\n    \};", w, re.S)
        self.assertTrue(m, "error helper not found in the widget")
        js = "var scope = {};\n" + m.group(0) + "\nconsole.log(JSON.stringify([0,1,2,3,4,5,6,7,8,9,10,11,12].map(scope.pwErrorText)));"
        if not shutil.which("node"):
            self.skipTest("node is not installed")
        out = subprocess.run(["node", "-e", js], stdout=subprocess.PIPE, universal_newlines=True, check=True).stdout
        got = json.loads(out)
        self.assertEqual(got[1:10], ["Error E1: Line 1 voltage", "Error E2: Line 2 voltage", "Error E3: Line 1 overcurrent",
                                     "Error E4: Line 2 overcurrent", "Error E5: Line 1 neutral reversed",
                                     "Error E6: Line 2 neutral reversed", "Error E7: Missing ground",
                                     "Error E8: Neutral missing", "Error E9: Surge protection used up"])
        self.assertEqual(got[10:], ["Error F0", "Error F1", "Error F2"])

    def test_widget_reports_when_it_is_on_screen(self):
        fmt = [n for n in load() if n["type"] == "ui_template"][0]["format"]
        for needle in ("visibilitychange", "pagehide", "$destroy", "getClientRects", "topic: 'lease'", "topic: 'retry'"):
            self.assertIn(needle, fmt)

    def test_nothing_machine_specific_is_baked_in(self):
        with open(FLOW, encoding="utf-8") as fh:
            text = fh.read()
        flat = re.sub(r"base64,[A-Za-z0-9+/=]{200,}", "base64,", text)
        for banned in ("/coachproxy", "192.168."):
            self.assertNotIn(banned, flat, "found machine-specific text: " + banned)
        self.assertFalse(re.search(r"\b[0-9A-F]{2}(:[0-9A-F]{2}){5}\b", flat), "a MAC address is baked in")

    @unittest.skipUnless(shutil.which("node"), "node is not installed")
    def test_function_node_code_is_valid_javascript(self):
        for n in load():
            if n["type"] != "function":
                continue
            for field in ("func", "initialize", "finalize"):
                with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False) as fh:
                    fh.write("function f(msg,node,context,env,flow,global,setInterval,clearInterval){\n" + n[field] + "\n}\n")
                try:
                    proc = subprocess.run(["node", "--check", fh.name], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
                    self.assertEqual(proc.returncode, 0, "%s.%s: %s" % (n["name"], field, proc.stderr.decode()))
                finally:
                    os.unlink(fh.name)


if __name__ == "__main__":
    unittest.main()
