import copy
import os
import tempfile
import unittest
from unittest import mock

from netmon import config, run
from netmon.store import Store
from tests.test_build import WLC, campus


class FullRunOrchestration(unittest.TestCase):
    """Runs the LLD workflow end to end with SSH, sockets and credentials mocked."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.cfg = config.validate({
            "collector": {"switch_username": "u", "data_dir": self.tmp.name},
            "subnets": [{"cidr": "10.0.0.0/30", "institute": "DC"}, {"cidr": "10.0.1.0/24", "institute": "Engineering", "sweep": False}],
            "wlc": {"hosts": ["10.0.0.5"]},
        })
        self.records = {r["ip"]: r for r in campus()}

    def tearDown(self):
        self.tmp.cleanup()

    def test_sweep_then_recursive_crawl_then_reports(self):
        probed = []

        def probe(ips, port=22, timeout=1, concurrency=1):
            ips = list(ips)
            probed.append(ips)
            return {ip: (1.0 if ip in self.records and ip != "10.0.1.3" or ip == "10.0.0.5" else None) for ip in ips}

        collected = []

        def collect(ip, platform, u, p, cfg):
            collected.append((ip, platform))
            return self.records[ip]

        with mock.patch("netmon.scan.probe", side_effect=probe), \
                mock.patch("netmon.creds.switch_sets", return_value=[("set1", "u", "p")]), \
                mock.patch("netmon.creds.get", return_value=("nms", "p")), \
                mock.patch("netmon.devices.detect_platform", side_effect=lambda ip, *a: self.records[ip]["platform"]), \
                mock.patch("netmon.devices.collect", side_effect=collect), \
                mock.patch("netmon.wlc.collect", return_value=WLC[0]):
            self.assertEqual(run.full_run(self.cfg, "manual", "net@somaiya.edu"), 0)
            # Sweep covers only the /30 (2 hosts); 10.0.1.x is found through CDP/LLDP neighbours.
            self.assertEqual(sorted(probed[0]), ["10.0.0.1", "10.0.0.2"])
            self.assertEqual([ip for ip, _ in collected][0], "10.0.0.1")
            self.assertIn(("10.0.1.1", "cisco_ios"), collected)
            self.assertIn(("10.0.1.2", "cisco_s300"), collected, "found via dsw-01's LLDP neighbour")

        store = Store(self.tmp.name)
        latest = store.read("latest.json")
        self.assertEqual(latest["trigger"], "manual")
        self.assertEqual(latest["triggeredBy"], "net@somaiya.edu")
        self.assertEqual(latest["summary"]["collected"], 3)
        self.assertTrue(os.path.exists(store.path("topology-latest.dot")))
        self.assertTrue(os.path.exists(store.path("inventory-latest.xlsx")))
        status = store.read("run-status.json")
        self.assertEqual((status["state"], status["phase"]), ("idle", "done"))
        self.assertFalse(store.running(), "lock released")
        self.assertEqual(store.read("state", "platforms.json")["10.0.1.2"], "cisco_s300")

        with mock.patch("netmon.scan.probe", return_value={"10.0.0.1": 1.2, "10.0.1.1": None, "10.0.1.2": 3.0, "10.0.0.5": 2.0}):
            self.assertEqual(run.reach_run(self.cfg), 0)
        reach = store.read("reachability.json")
        self.assertFalse(reach["results"]["10.0.1.1"]["up"])
        self.assertEqual(reach["results"]["10.0.0.1"]["latencyMs"], 1.2)

    def test_credential_sets_tried_in_order_and_remembered(self):
        logins = []

        def detect(ip, user, pw, cfg):
            logins.append((ip, user))
            if user != "new-user" or ip == "10.0.0.2":
                raise type("NetmikoAuthenticationException", (Exception,), {})("auth failed")
            return self.records[ip]["platform"]

        sets = [("old", "old-user", "p1"), ("new", "new-user", "p2"), ("spare", "spare-user", "p3")]
        cfg = config.validate({"collector": {"switch_credential_sets": [{"name": n, "username": u} for n, u, _ in sets],
                                             "data_dir": self.tmp.name, "seeds": ["10.0.0.1", "10.0.0.2"]},
                               "subnets": [{"cidr": "10.0.0.0/30", "sweep": False}]})
        core_only = dict(self.records["10.0.0.1"], neighbors=[])
        with mock.patch("netmon.scan.probe", side_effect=lambda ips, *a: {ip: 1.0 for ip in ips}), \
                mock.patch("netmon.creds.switch_sets", return_value=sets), \
                mock.patch("netmon.devices.detect_platform", side_effect=detect), \
                mock.patch("netmon.devices.collect", return_value=core_only):
            self.assertEqual(run.full_run(cfg), 0)
            self.assertEqual([u for ip, u in logins if ip == "10.0.0.1"], ["old-user", "new-user"])
            store = Store(self.tmp.name)
            self.assertEqual(store.read("state", "credentials.json"), {"10.0.0.1": "new"})
            failed = next(d for d in store.read("latest.json")["devices"] if d["ip"] == "10.0.0.2")
            self.assertEqual(failed["error"], "SSH authentication failed with all 3 credential sets")
            logins.clear()
            os.remove(store.path("state", "platforms.json"))  # force a fresh login on the next run
            self.assertEqual(run.full_run(cfg), 0)
            self.assertEqual([u for ip, u in logins if ip == "10.0.0.1"], ["new-user"], "remembered set is tried first")

    def test_login_timeout_with_one_account_tries_the_next(self):
        """Regression (live): a switch stalled with one account; the run gave up instead of trying others."""
        cfg = config.validate({"collector": {"switch_username": "u", "data_dir": self.tmp.name, "seeds": ["10.0.0.1", "10.0.0.2"]},
                               "subnets": [{"cidr": "10.0.0.0/30", "sweep": False}]})
        stall = type("NetmikoTimeoutException", (Exception,), {})

        def detect(ip, user, pw, cfg):
            if user == "u1" or ip == "10.0.0.2":
                raise stall("stalled")
            return "cisco_s300"
        sets = [("set1", "u1", "p"), ("set2", "u2", "p")]
        with mock.patch("netmon.scan.probe", side_effect=lambda ips, *a: {ip: 1.0 for ip in ips}), \
                mock.patch("netmon.creds.switch_sets", return_value=sets), \
                mock.patch("netmon.devices.detect_platform", side_effect=detect), \
                mock.patch("netmon.devices.collect", side_effect=lambda ip, *a: dict(self.records["10.0.0.1"], ip=ip, neighbors=[])):
            self.assertEqual(run.full_run(cfg), 0)
        store = Store(self.tmp.name)
        self.assertEqual(store.read("state", "credentials.json"), {"10.0.0.1": "set2"})
        failed = next(d for d in store.read("latest.json")["devices"] if d["ip"] == "10.0.0.2")
        self.assertEqual(failed["error"], "Login failed with every credential set: set1: SSH timed out; set2: SSH timed out")

    def test_neighbour_platform_hint_skips_catalyst_detection(self):
        """A switch its neighbours report as CBS/SG/C1300 goes straight to the SG/CBS driver."""
        store = Store(self.tmp.name)
        # As on campus: last run listed the CBS switch as failed, and a neighbour reported its model.
        store.write({"devices": [{"ip": "10.0.0.1", "neighbors": [
            {"ip": "10.0.0.2", "platform": "Cisco CBS350-24P-4G (PID:CBS350-24P-4G)-VSD"}]},
            {"ip": "10.0.0.2", "status": "failed", "neighbors": []}]}, "latest.json")
        cfg = config.validate({"collector": {"switch_username": "u", "data_dir": self.tmp.name},
                               "subnets": [{"cidr": "10.0.0.0/30", "sweep": False}]})
        used = {}

        def collect(ip, platform, *a):
            used[ip] = platform
            return dict(self.records["10.0.0.1"], ip=ip, neighbors=[])
        with mock.patch("netmon.scan.probe", side_effect=lambda ips, *a: {ip: 1.0 for ip in ips}), \
                mock.patch("netmon.creds.switch_sets", return_value=[("set1", "u", "p")]), \
                mock.patch("netmon.devices.detect_platform", return_value="cisco_ios") as detect, \
                mock.patch("netmon.devices.collect", side_effect=collect):
            self.assertEqual(run.full_run(cfg), 0)
        self.assertEqual(used["10.0.0.2"], "cisco_s300")
        self.assertEqual([c.args[0] for c in detect.call_args_list], ["10.0.0.1"], "only the unhinted switch is detected")

    def _full_run(self):
        reachable = lambda ips, *a, **k: {ip: (1.0 if ip in self.records and ip != "10.0.1.3" or ip == "10.0.0.5" else None)
                                          for ip in ips}
        with mock.patch("netmon.scan.probe", side_effect=reachable), \
                mock.patch("netmon.creds.switch_sets", return_value=[("set1", "u", "p")]), \
                mock.patch("netmon.creds.get", return_value=("nms", "p")), \
                mock.patch("netmon.devices.detect_platform", side_effect=lambda ip, *a: self.records[ip]["platform"]), \
                mock.patch("netmon.devices.collect", side_effect=lambda ip, *a, **k: copy.deepcopy(self.records[ip])), \
                mock.patch("netmon.wlc.collect", return_value=copy.deepcopy(WLC[0])):
            self.assertEqual(run.full_run(self.cfg), 0)
        return Store(self.tmp.name)

    def _refresh(self, ip, result, sets=(("set1", "u", "p"),)):
        logins = []

        def collect(ip_, platform, *a, **k):
            logins.append(ip_)
            return copy.deepcopy(result)
        with mock.patch("netmon.creds.switch_sets", return_value=list(sets)), \
                mock.patch("netmon.devices.detect_platform", side_effect=AssertionError("platform is already known")), \
                mock.patch("netmon.devices.collect", side_effect=collect), \
                mock.patch("netmon.wlc.collect", side_effect=AssertionError("WLCs are not re-read")), \
                mock.patch("netmon.scan.probe", side_effect=AssertionError("no sweep")):
            code = run.refresh_switch(self.cfg, ip, "net@somaiya.edu")
        return code, logins

    def test_refresh_one_switch_rebuilds_the_snapshot_around_it(self):
        store = self._full_run()
        before = store.read("latest.json")
        fresh = dict(copy.deepcopy(self.records["10.0.1.2"]), cpu=97)
        code, logins = self._refresh("10.0.1.2", fresh)
        self.assertEqual(code, 0)
        self.assertEqual(logins, ["10.0.1.2"], "only that switch is logged in to")
        latest = store.read("latest.json")
        dev = next(d for d in latest["devices"] if d["ip"] == "10.0.1.2")
        self.assertEqual(dev["cpu"], 97)
        self.assertIn("cpu", dev["flags"])
        self.assertEqual(dev["refreshedBy"], "net@somaiya.edu")
        self.assertEqual(latest["generatedAt"], before["generatedAt"], "still dated by its full run")
        self.assertEqual(latest["refreshedAt"], dev["refreshedAt"])
        self.assertEqual(latest["summary"]["cpuHigh"], before["summary"]["cpuHigh"] + 1)
        for key in ("switches", "collected", "links", "aps"):
            self.assertEqual(latest["summary"][key], before["summary"][key], key)
        self.assertNotIn("refreshedAt", next(d for d in latest["devices"] if d["ip"] == "10.0.0.1"))
        status = store.read("refresh-status.json")
        self.assertEqual((status["state"], status["ok"], status["ip"]), ("done", True, "10.0.1.2"))
        self.assertEqual(status["snapshotRefreshedAt"], latest["refreshedAt"])
        self.assertFalse(store.running("refresh.lock"))
        self.assertTrue(os.path.exists(store.path("topology-latest.dot")))
        # A second refresh builds on the first (the refreshed record was saved).
        self.assertEqual(self._refresh("10.0.0.1", self.records["10.0.0.1"])[0], 0)
        self.assertEqual(next(d for d in store.read("latest.json")["devices"] if d["ip"] == "10.0.1.2")["cpu"], 97)

    def test_reconnect_that_still_fails_is_recorded(self):
        store = self._full_run()
        failed = {"ip": "10.0.1.2", "ok": False, "error": "SSH authentication failed", "platform": "cisco_s300",
                  "neighbors": [], "ports": [], "warnings": []}
        code, logins = self._refresh("10.0.1.2", failed, sets=(("set1", "u", "p"), ("set2", "u2", "p2")))
        self.assertEqual(code, 1)
        self.assertEqual(logins, ["10.0.1.2", "10.0.1.2"], "every credential set is tried again")
        dev = next(d for d in store.read("latest.json")["devices"] if d["ip"] == "10.0.1.2")
        self.assertEqual(dev["status"], "failed")
        self.assertEqual(dev["error"], "SSH authentication failed with all 2 credential sets")
        status = store.read("refresh-status.json")
        self.assertEqual((status["state"], status["ok"]), ("done", False))
        self.assertNotIn("10.0.1.2", store.read("state", "credentials.json"))

    def test_no_ssh_switch_listed_skipped_by_reach_and_reconnects_when_open(self):
        store = self._full_run()  # 10.0.1.50 (unknown-sw) is asw-02's neighbour and does not answer tcp/22
        latest = store.read("latest.json")
        self.assertEqual(next(d for d in latest["devices"] if d["ip"] == "10.0.1.50")["status"], "no-ssh")
        with mock.patch("netmon.scan.probe", side_effect=lambda ips, *a, **k: {ip: 1.0 for ip in ips}) as probe:
            run.reach_run(self.cfg)
        self.assertNotIn("10.0.1.50", probe.call_args.args[0], "no-ssh switches are not up/down checked")
        # Reconnect while still blocked: one tcp/22 check, no login, nothing rebuilt.
        with mock.patch("netmon.scan.probe", return_value={"10.0.1.50": None}), \
                mock.patch("netmon.devices.collect", side_effect=AssertionError("no login")):
            self.assertEqual(run.refresh_switch(self.cfg, "10.0.1.50", "net@somaiya.edu"), 1)
        self.assertEqual(store.read("refresh-status.json")["error"], "Still no answer on SSH (tcp/22) from the collector.")
        self.assertFalse(store.running("refresh.lock"))
        # Once SSH is allowed: logged in, becomes a normal collected switch.
        fresh = dict(copy.deepcopy(self.records["10.0.1.2"]), ip="10.0.1.50", hostname="asw-50", neighbors=[])
        with mock.patch("netmon.scan.probe", return_value={"10.0.1.50": 1.0}), \
                mock.patch("netmon.creds.switch_sets", return_value=[("set1", "u", "p")]), \
                mock.patch("netmon.devices.detect_platform", return_value="cisco_s300"), \
                mock.patch("netmon.devices.collect", return_value=fresh):
            self.assertEqual(run.refresh_switch(self.cfg, "10.0.1.50", "net@somaiya.edu"), 0)
        dev = next(d for d in store.read("latest.json")["devices"] if d["ip"] == "10.0.1.50")
        self.assertEqual((dev["status"], dev["hostname"]), ("ok", "asw-50"))
        self.assertNotIn("10.0.1.50", store.read("state", "last-run.json")["closed"])

    def test_progress_lists_every_switch_and_wlc_with_its_result(self):
        store = self._full_run()
        prog = store.read("run-progress.json")
        self.assertEqual(prog["phase"], "done")
        self.assertIsNotNone(prog["finishedAt"])
        items = {i["ip"]: i for i in prog["items"]}
        self.assertEqual(items["10.0.0.1"]["state"], "ok")
        self.assertEqual(items["10.0.0.1"]["hostname"], "core-01")
        self.assertIsInstance(items["10.0.0.1"]["seconds"], float)
        self.assertEqual(items["10.0.1.2"]["source"], "neighbour of dsw-01 Gi1/0/1", "found through CDP/LLDP")
        self.assertEqual((items["10.0.1.50"]["state"], items["10.0.1.50"]["hostname"]), ("no-ssh", "unknown-sw"))
        self.assertEqual((items["10.0.0.5"]["kind"], items["10.0.0.5"]["state"]), ("wlc", "ok"))
        self.assertFalse([i for i in prog["items"] if i["state"] in ("waiting", "running")], "nothing left pending")

    def test_phones_are_never_probed_or_logged_in_to(self):
        """A phone model neighbour is not crawled, and one an older run listed as a switch is dropped."""
        store = Store(self.tmp.name)
        store.write({"devices": [{"ip": "10.0.0.1", "hostname": "core-01", "neighbors": [
            {"ip": "10.0.0.2", "name": "X301G", "platform": "", "kind": "switch"}]},
            {"ip": "10.0.0.2", "hostname": "", "status": "failed", "neighbors": []}]}, "latest.json")
        cfg = config.validate({"collector": {"switch_username": "u", "data_dir": self.tmp.name, "seeds": ["10.0.0.1"]},
                               "subnets": [{"cidr": "10.0.0.0/24", "sweep": False}]})
        core = dict(copy.deepcopy(self.records["10.0.0.1"]), neighbors=[
            {"localPort": "Gi1/0/9", "name": "X3SG", "ip": "10.0.0.3", "remotePort": "LAN", "platform": "", "caps": "B",
             "via": ["lldp"], "desc": "", "chassis": ""}])
        probed, logins = [], []

        def probe(ips, *a, **k):
            probed.extend(ips)
            return {ip: 1.0 for ip in ips}

        def collect(ip, *a, **k):
            logins.append(ip)
            return copy.deepcopy(core)
        with mock.patch("netmon.scan.probe", side_effect=probe), \
                mock.patch("netmon.creds.switch_sets", return_value=[("set1", "u", "p")]), \
                mock.patch("netmon.devices.detect_platform", return_value="cisco_nxos"), \
                mock.patch("netmon.devices.collect", side_effect=collect):
            self.assertEqual(run.full_run(cfg), 0)
        self.assertEqual(logins, ["10.0.0.1"])
        self.assertNotIn("10.0.0.2", probed, "phone from the last run is not probed again")
        self.assertNotIn("10.0.0.3", probed, "phone neighbour is not crawled")
        self.assertEqual([d["ip"] for d in store.read("latest.json")["devices"]], ["10.0.0.1"])

    def test_refresh_refusals(self):
        store = Store(self.tmp.name)
        self.assertEqual(self._refresh("10.0.1.2", None)[0], 1)
        self.assertIn("one full discovery run", store.read("refresh-status.json")["error"])
        self._full_run()
        self.assertEqual(self._refresh("10.0.9.9", None), (1, []))
        self.assertIn("not a switch from the last full discovery run", store.read("refresh-status.json")["error"])
        self.assertTrue(store.acquire({"trigger": "scheduled"}))
        self.assertEqual(self._refresh("10.0.1.2", None), (1, []))
        self.assertIn("full discovery run is in progress", store.read("refresh-status.json")["error"])
        store.release()
        self.assertTrue(store.acquire({"ip": "10.0.0.1"}, "refresh.lock"))
        self.assertEqual(self._refresh("10.0.1.2", None), (2, []), "one refresh at a time")
        self.assertTrue(store.running("refresh.lock"), "the other refresh keeps its lock")
        self.assertFalse(store.running(), "a refresh never holds the full-run lock")

    def test_second_run_refused_while_locked(self):
        store = Store(self.tmp.name)
        self.assertTrue(store.acquire({"trigger": "scheduled"}))
        self.assertEqual(run.full_run(self.cfg), 2)
        store.release()

    def test_failure_is_recorded_and_lock_released(self):
        with mock.patch("netmon.scan.probe", side_effect=RuntimeError("socket boom")), \
                mock.patch("netmon.creds.switch_sets", return_value=[("set1", "u", "p")]):
            self.assertEqual(run.full_run(self.cfg), 1)
        store = Store(self.tmp.name)
        self.assertEqual(store.read("run-status.json")["state"], "failed")
        self.assertFalse(store.running())


if __name__ == "__main__":
    unittest.main()
