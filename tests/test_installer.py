"""Tests for install_coachproxy.py: flow construction (pure), Bluetooth discovery helpers, and whole runs against a fake
Node-RED admin API with fake hcitool / gatttool. Run: python3 -m unittest tests.test_installer"""
import contextlib
import copy
import io
import json
import os
import sys
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
import install_coachproxy as ic  # noqa: E402

FAKE_HCI = os.path.join(HERE, "fake_hcitool.py")
FAKE_GATT = os.path.join(HERE, "fake_gatttool.py")
MAC1, MAC2 = "AA:BB:CC:0B:7C:41", "AA:BB:CC:0C:11:22"
NAME1, NAME2 = "PMD      0B7C41E9F2", "PMD      0C1122AB77"
def _load(path):
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


REPO = _load(os.path.join(ROOT, "nodered", "power_watchdog_flow.json"))


def coach_flows(legacy=False):
    """A small stand-in for a CoachProxy flow file: a Status dashboard tab with groups, and some unrelated nodes (one of them
    named like a temporary thing, on purpose: nothing but our own ids may ever be touched)."""
    flows = [
        {"id": "cp_tab", "type": "tab", "label": "Coach", "disabled": False, "info": ""},
        {"id": "ui_status", "type": "ui_tab", "name": "Status", "icon": "dashboard", "order": 1},
        {"id": "ui_climate", "type": "ui_tab", "name": "Climate", "icon": "dashboard", "order": 2},
        {"id": "g_read", "type": "ui_group", "name": "Readings", "tab": "ui_status", "order": 1, "width": 6},
        {"id": "g_power", "type": "ui_group", "name": "Power", "tab": "ui_status", "order": 2, "width": 6},
        {"id": "g_gen", "type": "ui_group", "name": "Generator", "tab": "ui_status", "order": 3, "width": 6},
        {"id": "g_other", "type": "ui_group", "name": "Vent Fans", "tab": "ui_climate", "order": 1, "width": 6},
        {"id": "3e5bd4e9.96126c", "type": "mqtt out", "z": "cp_tab", "name": "CP/THERMISTOR_TEMP/ZoneName",
         "topic": "", "broker": "b1", "wires": []},
        {"id": "gauge1", "type": "ui_gauge", "z": "cp_tab", "group": "g_read", "name": "Fresh", "wires": []},
    ]
    if legacy:
        flows += [
            {"id": "pw_group_001", "type": "ui_group", "name": "Watchdog Status", "tab": "ui_status", "order": 4, "width": 6},
            {"id": "pw_inject_001", "type": "inject", "z": "cp_tab", "name": "Power Watchdog Poll", "repeat": "15",
             "wires": [["pw_template_001"]]},
            {"id": "pw_template_001", "type": "ui_template", "z": "cp_tab", "group": "pw_group_001", "name": "Old widget",
             "format": "<div>old</div>", "wires": []},
        ]
    return flows


class FakeNodeRed(object):
    def __init__(self, flows, modules=("node-red-dashboard",), status=None, conflict_once=False, refuse_tab_removal=False):
        self.flows, self.rev, self.modules = copy.deepcopy(flows), 1, list(modules)
        self.status, self.conflict_once, self.refuse_tab_removal = status, conflict_once, refuse_tab_removal
        self.posts, self.deploy_types = [], []
        outer = self

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def _send(self, code, obj):
                body = json.dumps(obj).encode()
                self.send_response(code)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def do_GET(self):
                if outer.status:
                    return self._send(outer.status, {"message": "no"})
                if self.path == "/flows":
                    return self._send(200, {"rev": str(outer.rev), "flows": outer.flows})
                if self.path == "/nodes":
                    return self._send(200, [{"module": m} for m in outer.modules])
                self._send(404, {})

            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))).decode())
                if outer.status:
                    return self._send(outer.status, {})
                if outer.conflict_once:
                    outer.conflict_once = False
                    outer.rev += 1
                    return self._send(409, {"code": "version_mismatch"})
                if body.get("rev") != str(outer.rev):
                    return self._send(409, {"code": "version_mismatch"})
                if outer.refuse_tab_removal and {n["id"] for n in outer.flows if n["type"] == "tab"} - \
                        {n["id"] for n in body["flows"] if n["type"] == "tab"}:
                    return self._send(400, {"code": "unexpected_error", "message": "Cannot read properties of undefined (reading 'env')"})
                outer.posts.append(body["flows"])
                outer.deploy_types.append(self.headers.get("Node-RED-Deployment-Type"))
                outer.flows = body["flows"]
                outer.rev += 1
                self._send(200, {"rev": str(outer.rev)})

        self.server = HTTPServer(("127.0.0.1", 0), H)
        self.url = "http://127.0.0.1:%d" % self.server.server_address[1]
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)

    def __enter__(self):
        self.thread.start()
        return self

    def __exit__(self, *a):
        self.server.shutdown()
        self.server.server_close()


def by_id(flows):
    return {n["id"]: n for n in flows}


class BuildPayload(unittest.TestCase):
    def build(self, live, **kw):
        kw.setdefault("install_dir", "/coachproxy")
        kw.setdefault("config_path", "/etc/power_watchdog.conf")
        kw.setdefault("launcher", "sudo")
        return ic.build_payload(live, REPO, **kw)

    def test_fresh_coachproxy_gets_a_watchdog_group_on_the_status_tab_and_nothing_else_changes(self):
        live = coach_flows()
        out, report = self.build(live)
        o, b = by_id(out), by_id(live)
        for i, node in b.items():
            self.assertEqual(o[i], node, "existing node %s must be untouched" % i)
        grp = o["pwv_ui_group"]
        self.assertEqual((grp["tab"], grp["name"], grp["order"]), ("ui_status", "Watchdog", 4))
        self.assertEqual(o["pwv_widget"]["group"], "pwv_ui_group")
        self.assertNotIn("pwv_ui_tab", o)
        self.assertIn("Status", report["placement"])

    def test_settings_are_filled_in_for_this_machine(self):
        out, _ = self.build(coach_flows(), install_dir="/coachproxy", config_path="/etc/x.conf", launcher="sudo")
        env = {e["name"]: e["value"] for e in by_id(out)["pwv_tab"]["env"]}
        self.assertEqual((env["PW_DIR"], env["PW_CONFIG"], env["PW_LAUNCHER"]), ("/coachproxy", "/etc/x.conf", "sudo"))

    def test_without_a_status_tab_the_widget_keeps_its_own_tab(self):
        live = [n for n in coach_flows() if n["id"] != "ui_status" and n.get("tab") != "ui_status"]
        out, report = self.build(live)
        o = by_id(out)
        self.assertIn("pwv_ui_tab", o)
        self.assertEqual(o["pwv_ui_group"]["tab"], "pwv_ui_tab")
        self.assertIn("own new", report["placement"])

    def test_first_release_pieces_are_switched_off_not_deleted_and_the_old_group_is_reused(self):
        live = coach_flows(legacy=True)
        out, report = self.build(live)
        o, b = by_id(out), by_id(live)
        self.assertTrue(o["pw_inject_001"]["d"] and o["pw_template_001"]["d"])
        self.assertEqual(o["pwv_widget"]["group"], "pw_group_001")
        self.assertNotIn("pwv_ui_group", o)
        for i in b:
            if i not in ("pw_inject_001", "pw_template_001"):
                self.assertEqual(o[i], b[i], i)
        self.assertEqual(sorted(report["disabled"]), ["pw_inject_001", "pw_template_001"])

    def test_running_it_twice_changes_nothing_the_second_time(self):
        first, _ = self.build(coach_flows(legacy=True))
        second, report = self.build(first)
        self.assertEqual(first, second)
        self.assertEqual(report["disabled"], [])

    def test_an_upgrade_keeps_the_position_the_owner_moved_the_group_to(self):
        first, _ = self.build(coach_flows())
        for n in first:
            if n["id"] == "pwv_ui_group":
                n["order"] = 9
        second, _ = self.build(first)
        self.assertEqual(by_id(second)["pwv_ui_group"]["order"], 9)

    def test_it_refuses_if_one_of_its_ids_is_already_used_by_something_else(self):
        # ids with our prefix are by definition ours and are replaced; the guard is for a new id that collides with
        # someone else's node, which cannot happen with the shipped flow, so check it directly with a bad flow:
        repo_bad = copy.deepcopy(REPO) + [{"id": "gauge1", "type": "debug", "z": "pwv_tab", "wires": []}]
        with self.assertRaises(ic.InstallError):
            ic.build_payload(coach_flows(), repo_bad, "/coachproxy", "/etc/x", "sudo")

    def test_uninstall_removes_only_our_pieces(self):
        installed, _ = self.build(coach_flows(legacy=True))
        out, gone = ic.remove_ours(installed)
        self.assertTrue(all(g.startswith("pwv_") for g in gone))
        self.assertEqual(out, [n for n in installed if not n["id"].startswith("pwv_")])


class Discovery(unittest.TestCase):
    def test_the_label_code_matches_the_advertised_name(self):
        self.assertTrue(ic.code_matches(NAME1, "APMDB7C41E9F2"))
        self.assertTrue(ic.code_matches(NAME1, "b7c41e9f2"))
        self.assertFalse(ic.code_matches(NAME2, "APMDB7C41E9F2"))
        self.assertFalse(ic.code_matches(NAME1, ""))

    def test_parse_scan_keeps_only_named_watchdogs(self):
        text = "LE Scan ...\n%s (unknown)\n%s %s\n11:22:33:44:55:66 JBL Speaker\n" % (MAC1, MAC1, NAME1)
        self.assertEqual(ic.parse_scan(text), {MAC1: NAME1})

    def test_choose(self):
        one, two = {MAC1: NAME1}, {MAC1: NAME1, MAC2: NAME2}
        self.assertEqual(ic.choose_device({}, None), ("none", []))
        self.assertEqual(ic.choose_device(one, None), ("chosen", MAC1))
        self.assertEqual(ic.choose_device(two, "APMDB7C41E9F2"), ("chosen", MAC1))
        self.assertEqual(ic.choose_device(two, None)[0], "ask")
        self.assertEqual(ic.choose_device(two, "ZZZ999999")[0], "ask")


class WholeRun(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = os.path.join(self.tmp.name, "install")
        self.conf = os.path.join(self.tmp.name, "pw.conf")
        patcher = mock.patch.dict(os.environ, {"PW_FAKE_SCENARIO": "normal", "PW_FAKE_SPEED": "8",
                                               "PW_FAKE_ADVERTS": "%s|%s" % (MAC1, NAME1)})
        patcher.start()
        self.addCleanup(patcher.stop)
        for attr, val in (("SCAN_SECONDS", 0.3), ("SCAN_BURSTS", 2)):
            p = mock.patch.object(ic, attr, val)
            p.start()
            self.addCleanup(p.stop)
        self.addCleanup(self.tmp.cleanup)

    def go(self, server, *extra):
        argv = ["--dir", self.dir, "--config", self.conf, "--nodered", server.url, "--hcitool", FAKE_HCI,
                "--gatttool", FAKE_GATT, "--launcher", "sudo", "--yes"] + list(extra)
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            code = ic.main(argv)
        return code, buf.getvalue()

    def test_fresh_install(self):
        with FakeNodeRed(coach_flows()) as srv:
            code, out = self.go(srv)
            self.assertEqual(code, 0, out)
            self.assertIn("It works", out)
            self.assertIn("Line 1:", out)
            self.assertEqual(srv.deploy_types, ["nodes"])
            self.assertIn("pwv_widget", by_id(srv.flows))
        for name in ic.FILES:
            self.assertTrue(os.access(os.path.join(self.dir, name), os.X_OK), name)
        conf = _load(self.conf)
        self.assertEqual(conf, {"mac": MAC1, "cccd": "0x0021"})   # the fake unit's FFE2 value handle 0x0020, plus one
        backups = os.listdir(os.path.join(self.dir, "backups"))
        self.assertTrue(any(b.startswith("flows-") for b in backups))
        saved = _load(os.path.join(self.dir, "backups", [b for b in backups if b.startswith("flows-")][0]))
        self.assertEqual(saved, coach_flows(), "the backup is the flows as they were before")

    def test_second_run_is_an_upgrade_that_changes_nothing(self):
        with FakeNodeRed(coach_flows(legacy=True)) as srv:
            self.assertEqual(self.go(srv)[0], 0)
            first = copy.deepcopy(srv.flows)
            code, out = self.go(srv)
            self.assertEqual(code, 0, out)
            self.assertEqual(srv.flows, first)
            self.assertIn("already up to date", out)
            self.assertIn("already set up", out)

    def test_dry_run_changes_nothing_anywhere(self):
        with FakeNodeRed(coach_flows()) as srv:
            code, out = self.go(srv, "--dry-run")
            self.assertEqual(code, 0, out)
            self.assertEqual(srv.posts, [])
            self.assertEqual(srv.flows, coach_flows())
        self.assertFalse(os.path.exists(self.dir))
        self.assertFalse(os.path.exists(self.conf))

    def test_node_red_asking_for_a_password_gives_manual_steps_and_still_sets_up_bluetooth(self):
        with FakeNodeRed(coach_flows(), status=401) as srv:
            code, out = self.go(srv)
            self.assertEqual(code, 2, out)
            self.assertIn("Add the widget by hand", out)
            self.assertEqual(srv.posts, [])
        self.assertTrue(os.path.exists(self.conf))

    def test_missing_dashboard_is_explained(self):
        with FakeNodeRed(coach_flows(), modules=("node-red",)) as srv:
            code, out = self.go(srv)
            self.assertEqual(code, 1)
            self.assertIn("node-red-dashboard", out)
            self.assertEqual(srv.posts, [])

    def test_flows_changed_by_someone_else_midway_are_read_again(self):
        with FakeNodeRed(coach_flows(), conflict_once=True) as srv:
            code, out = self.go(srv)
            self.assertEqual(code, 0, out)
            self.assertIn("pwv_widget", by_id(srv.flows))

    def test_an_unreadable_watchdog_does_not_block_the_install_and_says_why(self):
        with mock.patch.dict(os.environ, {"PW_FAKE_SCENARIO": "connect_error"}), FakeNodeRed(coach_flows()) as srv:
            code, out = self.go(srv)
            self.assertEqual(code, 0, out)
            self.assertIn("Hughes app", out)
            self.assertIn("pwv_widget", by_id(srv.flows))

    def test_two_watchdogs_need_the_label_code_when_not_asking(self):
        both = "%s|%s;%s|%s" % (MAC1, NAME1, MAC2, NAME2)
        with mock.patch.dict(os.environ, {"PW_FAKE_ADVERTS": both}), FakeNodeRed(coach_flows()) as srv:
            code, out = self.go(srv)
            self.assertEqual(code, 1)
            self.assertIn("--code", out)
            self.assertEqual(srv.posts, [])
            code, out = self.go(srv, "--code", "APMDB7C41E9F2")
            self.assertEqual(code, 0, out)
        self.assertEqual(_load(self.conf)["mac"], MAC1)

    def test_nothing_found_says_what_to_check(self):
        with mock.patch.dict(os.environ, {"PW_FAKE_ADVERTS": ""}), FakeNodeRed(coach_flows()) as srv:
            code, out = self.go(srv)
            self.assertEqual(code, 1)
            self.assertIn("shore power", out)
            self.assertIn("--mac", out)
            self.assertEqual(srv.posts, [])

    def test_bluetooth_permission_problem_says_use_sudo(self):
        with mock.patch.dict(os.environ, {"PW_FAKE_HCI": "permission"}), FakeNodeRed(coach_flows()) as srv:
            code, out = self.go(srv)
            self.assertEqual(code, 1)
            self.assertIn("sudo", out)

    def test_a_typed_address_skips_the_scan(self):
        with mock.patch.dict(os.environ, {"PW_FAKE_ADVERTS": ""}), FakeNodeRed(coach_flows()) as srv:
            code, out = self.go(srv, "--mac", "aa:bb:cc:dd:ee:ff")
            self.assertEqual(code, 0, out)
        self.assertEqual(_load(self.conf)["mac"], "AA:BB:CC:DD:EE:FF")

    def test_a_malformed_address_is_rejected_in_plain_words(self):
        with FakeNodeRed(coach_flows()) as srv:
            code, out = self.go(srv, "--mac", "nonsense")
            self.assertEqual(code, 1)
            self.assertIn("not a Bluetooth address", out)

    def test_uninstall_restores_the_original_flows(self):
        original = coach_flows(legacy=True)
        with FakeNodeRed(original) as srv:
            self.assertEqual(self.go(srv)[0], 0)
            code, out = self.go(srv, "--uninstall")
            self.assertEqual(code, 0, out)
            after = by_id(srv.flows)
            self.assertFalse([i for i in after if i.startswith("pwv_")])
            want = by_id(original)
            for i in ("pw_inject_001", "pw_template_001"):
                want[i] = dict(want[i], d=True)       # still switched off, as the install left them
            self.assertEqual(after, want)
            self.assertTrue(any(b.startswith("flows-before-uninstall") for b in os.listdir(os.path.join(self.dir, "backups"))))

    def test_uninstall_copes_with_a_node_red_that_refuses_to_delete_a_flow_tab(self):
        with FakeNodeRed(coach_flows(), refuse_tab_removal=True) as srv:
            self.assertEqual(self.go(srv)[0], 0)
            code, out = self.go(srv, "--uninstall")
            self.assertEqual(code, 0, out)
            self.assertIn("Delete", out)
            left = [n for n in srv.flows if n["id"].startswith("pwv_")]
            self.assertEqual([(n["id"], n["disabled"]) for n in left], [("pwv_tab", True)])

    def test_the_detected_name_is_shown_without_the_units_padding(self):
        with FakeNodeRed(coach_flows()) as srv:
            code, out = self.go(srv)
            self.assertIn("Found your Watchdog (PMD 0B7C41E9F2)", out)
            self.assertNotIn("To remove it later: sudo", out)
            self.assertIn("--uninstall", out)


if __name__ == "__main__":
    unittest.main()
