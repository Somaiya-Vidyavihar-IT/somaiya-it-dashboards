import copy
import os
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from unittest import mock

from netmon import config, devices, identity, known, run
from netmon.store import Store
from tests.test_build import WLC, campus, nbr, rec

CISCO = "SSH-2.0-Cisco-1.25"


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
        # Never read a real banner or web page: every scan-found host identifies as Cisco unless a test says otherwise.
        for target, kw in (("netmon.scan.ssh_banners", {"side_effect": lambda ips, *a, **k: {ip: CISCO for ip in ips}}),
                           ("netmon.identify.web_hint", {"return_value": ""})):
            patcher = mock.patch(target, **kw)
            patcher.start()
            self.addCleanup(patcher.stop)
        # Checks in these tests run seconds apart; the 5-minute rule has its own tests (gap patched back there).
        patcher = mock.patch.object(known, "MIN_CHECK_GAP_SECONDS", 0)
        patcher.start()
        self.addCleanup(patcher.stop)

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

    def _reach(self, answers):
        """15-minute check; answers: {ip: latency or None}, everything else up."""
        probed = []

        def probe(ips, *a, **k):
            probed.extend(ips)
            return {ip: answers.get(ip, 1.0) for ip in ips}
        with mock.patch("netmon.scan.probe", side_effect=probe):
            self.assertEqual(run.reach_run(self.cfg), 0)
        return probed

    def _full_run_asw_gone(self, extra_neighbors=()):
        """A full run while asw-02 (10.0.1.2) is switched off: no tcp/22 and dsw-01 no longer sees it."""
        saved = self.records
        dsw = copy.deepcopy(saved["10.0.1.1"])
        dsw["neighbors"] = [n for n in dsw["neighbors"] if n["name"] != "asw-02"] + list(extra_neighbors)
        self.records = {ip: r for ip, r in dict(saved, **{"10.0.1.1": dsw}).items() if ip != "10.0.1.2"}
        try:
            return self._full_run()
        finally:
            self.records = saved

    def _make_down(self):
        store = self._full_run()
        self.assertIn("10.0.1.2", known.load(store))
        self._reach({"10.0.1.2": None})
        self.assertEqual(store.read("reachability.json")["results"]["10.0.1.2"]["misses"], 1)
        self._full_run_asw_gone()
        return store

    def test_down_switch_recovers(self):
        store = self._make_down()
        self.assertEqual(store.read("reachability.json")["results"]["10.0.1.2"]["misses"], 2,
                         "the full run's own tcp/22 probe counts as a check")
        latest = store.read("latest.json")
        dev = next(d for d in latest["devices"] if d["ip"] == "10.0.1.2")
        self.assertEqual((dev["status"], dev["hostname"], dev["model"]), ("down", "asw-02", "X"))
        self.assertEqual(dev["lastUplinks"][0]["name"], "dsw-01")
        self.assertEqual(latest["summary"]["down"], 1)
        self.assertIn("10.0.1.2", self._reach({}), "Down switches are still checked every 15 minutes")
        self.assertEqual(store.read("reachability.json")["results"]["10.0.1.2"]["misses"], 0)
        self._full_run()
        dev = next(d for d in store.read("latest.json")["devices"] if d["ip"] == "10.0.1.2")
        self.assertEqual(dev["status"], "ok")
        self.assertEqual(store.read("latest.json")["summary"]["down"], 0)

    def _status(self, store, ip):
        return next((d["status"] for d in store.read("latest.json")["devices"] if d["ip"] == ip), None)

    def test_one_missed_check_is_not_answering_then_down(self):
        """R5: a switch that goes off just before a full run is listed at once (Not answering), Down after 2 misses."""
        store = self._full_run()
        self._full_run_asw_gone()
        self.assertEqual(store.read("reachability.json")["results"]["10.0.1.2"]["misses"], 1)
        dev = next(d for d in store.read("latest.json")["devices"] if d["ip"] == "10.0.1.2")
        self.assertEqual((dev["status"], dev["hostname"], dev["lastUplinks"][0]["name"]), ("unreachable", "asw-02", "dsw-01"))
        self.assertEqual(store.read("latest.json")["summary"]["unreachable"], 1)
        self.assertIn("10.0.1.2", known.load(store), "still remembered")
        self.assertIn("10.0.1.2", self._reach({"10.0.1.2": None}), "still checked every 15 minutes")
        self.assertEqual(store.read("reachability.json")["results"]["10.0.1.2"]["misses"], 2)
        self._full_run_asw_gone()
        self.assertEqual(self._status(store, "10.0.1.2"), "down")

    def test_rebuild_uses_current_reachability(self):
        """R6: a refresh or Remove decides Down / Not answering from reachability.json as it is now."""
        store = self._full_run()
        self._full_run_asw_gone()
        self.assertEqual(self._status(store, "10.0.1.2"), "unreachable")
        self._reach({"10.0.1.2": None})  # second miss, between full runs
        self.assertEqual(self._refresh("10.0.0.1", self.records["10.0.0.1"])[0], 0)
        self.assertEqual(self._status(store, "10.0.1.2"), "down")
        self._reach({})  # answers again
        self.assertEqual(self._refresh("10.0.0.1", self.records["10.0.0.1"])[0], 0)
        self.assertEqual(self._status(store, "10.0.1.2"), "unreachable", "not Down any more; read at the next full run")
        self.assertEqual(run.forget_switch(self.cfg, "10.0.1.2", "net@somaiya.edu"), 1, "only a Down switch can be removed")
        self.assertIn("10.0.1.2", known.load(store))

    def test_down_survives_a_neighbour_crawl_in_the_same_run(self):
        """Review fix: the crawl's own tcp/22 probe of a new neighbour must not replace the run's up/down results."""
        store = self._full_run()
        self._reach({"10.0.1.2": None})
        closed_nb = nbr("Gi1/0/20", "asw-60", "10.0.1.60", "Gi0/1")  # a switch neighbour that does not answer tcp/22
        self._full_run_asw_gone(extra_neighbors=[closed_nb])
        self.assertEqual(store.read("reachability.json")["results"]["10.0.1.2"]["misses"], 2)
        latest = store.read("latest.json")
        dev = next(d for d in latest["devices"] if d["ip"] == "10.0.1.2")
        self.assertEqual(self._status(store, "10.0.1.60"), "no-ssh", "the crawl did probe the new neighbour")
        self.assertEqual(dev["status"], "down")
        self.assertEqual(dev["downSince"], store.read("reachability.json")["results"]["10.0.1.2"]["since"])
        self.assertIsNotNone(dev["downSince"])
        self.assertEqual([(f["severity"], f["ip"]) for f in latest["findings"] if f["type"] == "down"],
                         [("critical", "10.0.1.2")])
        self.assertEqual(latest["summary"]["down"], 1)

    def test_forget_refuses_a_not_answering_switch(self):
        """R7: only a row the last snapshot lists as Down can be removed, even after 2 live misses."""
        store = self._full_run()
        self._full_run_asw_gone()
        self.assertEqual(run.forget_switch(self.cfg, "10.0.1.2", "net@somaiya.edu"), 1, "one miss: not Down yet")
        self._reach({"10.0.1.2": None})
        self.assertEqual(run.forget_switch(self.cfg, "10.0.1.2", "net@somaiya.edu"), 1,
                         "Not answering in the snapshot: refused although the live check counts 2 misses")
        self.assertEqual(self._status(store, "10.0.1.2"), "unreachable")
        self.assertIn("10.0.1.2", known.load(store))
        self._full_run_asw_gone()  # the next full run lists it as Down
        self.assertEqual(run.forget_switch(self.cfg, "10.0.1.2", "net@somaiya.edu"), 0)
        self.assertIsNone(self._status(store, "10.0.1.2"))
        self.assertNotIn("10.0.1.2", known.load(store))

    def test_reach_probes_known_switches_missing_from_latest(self):
        store = self._full_run()
        entries = known.load(store)
        self.assertEqual(sorted(entries), ["10.0.0.1", "10.0.1.1", "10.0.1.2"], "10.0.1.3 never answered tcp/22")
        entries["10.0.1.9"] = dict(entries["10.0.1.2"], ip="10.0.1.9", hostname="asw-09")
        known.save(store, entries)
        probed = self._reach({"10.0.1.9": None})
        self.assertIn("10.0.1.9", probed)
        self.assertIn("10.0.0.5", probed, "WLCs too")
        self.assertNotIn("10.0.1.50", probed, "no-ssh rows are not up/down checked")
        self.assertEqual(len(probed), len(set(probed)))
        res = store.read("reachability.json")["results"]
        self.assertEqual((res["10.0.1.9"]["up"], res["10.0.1.9"]["misses"], res["10.0.0.1"]["misses"]), (False, 1, 0))

    def test_forget_removes_down_switch_and_rebuilds(self):
        store = self._make_down()
        before = store.read("latest.json")
        # A single-switch refresh in between keeps the Down row (same rebuild as forget).
        self.assertEqual(self._refresh("10.0.0.1", self.records["10.0.0.1"])[0], 0)
        self.assertEqual(next(d for d in store.read("latest.json")["devices"] if d["ip"] == "10.0.1.2")["status"], "down")
        with mock.patch("netmon.devices.collect", side_effect=AssertionError("no login")), \
                mock.patch("netmon.scan.probe", side_effect=AssertionError("no probe")):
            self.assertEqual(run.forget_switch(self.cfg, "10.0.1.2", "net@somaiya.edu"), 0)
        latest = store.read("latest.json")
        self.assertNotIn("10.0.1.2", [d["ip"] for d in latest["devices"]])
        self.assertEqual(latest["summary"]["down"], 0)
        self.assertEqual(latest["summary"]["switches"], before["summary"]["switches"] - 1)
        self.assertEqual(latest["generatedAt"], before["generatedAt"], "still dated by its full run")
        self.assertFalse([f for f in latest["findings"] if f["type"] == "down"])
        self.assertNotIn("10.0.1.2", known.load(store))
        status = store.read("refresh-status.json")
        self.assertEqual((status["state"], status["action"], status["ok"], status["ip"]), ("done", "forget", True, "10.0.1.2"))
        self.assertEqual(status["snapshotRefreshedAt"], latest["refreshedAt"])
        self.assertFalse(store.running("refresh.lock"))
        with open(store.path("audit.log"), encoding="utf-8") as fh:
            self.assertIn("forget ip=10.0.1.2 by=net@somaiya.edu", fh.read())
        self.assertNotIn("10.0.1.2", self._reach({}), "no longer checked")

    def test_forget_refuses_a_switch_that_is_not_down(self):
        store = self._full_run()
        before = store.read("latest.json")
        for ip in ("10.0.1.1", "10.0.1.50", "10.0.9.9"):
            self.assertEqual(run.forget_switch(self.cfg, ip, "net@somaiya.edu"), 1, ip)
            self.assertIn("only a Down switch or one whose SSH login fails can be removed",
                          store.read("refresh-status.json")["error"])
        self.assertEqual(store.read("latest.json"), before)
        self.assertIn("10.0.1.1", known.load(store))
        self.assertFalse(store.running("refresh.lock"))
        self.assertTrue(store.acquire({"ip": "10.0.0.1"}, "refresh.lock"))
        self.assertEqual(run.forget_switch(self.cfg, "10.0.1.1", "net@somaiya.edu"), 2, "one refresh or removal at a time")
        store.release("refresh.lock")
        self.assertTrue(store.acquire({"trigger": "scheduled"}))
        self.assertEqual(run.forget_switch(self.cfg, "10.0.1.1", "net@somaiya.edu"), 1)
        self.assertIn("full discovery run is in progress", store.read("refresh-status.json")["error"])
        store.release()

    def test_forget_an_ssh_failed_switch_stops_logins(self):
        """R10: Remove on an SSH-failed row: forgotten, off the dashboard, and no login unless found again."""
        store = self._full_run()
        failed = {"ip": "10.0.1.2", "ok": False, "error": "SSH authentication failed", "platform": "cisco_s300",
                  "neighbors": [], "ports": [], "warnings": []}
        self.assertEqual(self._refresh("10.0.1.2", failed)[0], 1)
        self.assertEqual(self._status(store, "10.0.1.2"), "failed")
        with mock.patch("netmon.devices.collect", side_effect=AssertionError("no login")), \
                mock.patch("netmon.scan.probe", side_effect=AssertionError("no probe")):
            self.assertEqual(run.forget_switch(self.cfg, "10.0.1.2", "net@somaiya.edu"), 0)
        self.assertIsNone(self._status(store, "10.0.1.2"))
        self.assertNotIn("10.0.1.2", known.load(store))
        self.assertNotIn("10.0.1.2", [r["ip"] for r in store.read("state", "last-run.json")["records"]])
        with open(store.path("audit.log"), encoding="utf-8") as fh:
            self.assertIn("forget ip=10.0.1.2 by=net@somaiya.edu status=failed", fh.read())
        # Next full run: it still answers tcp/22, but nothing lists it any more (not a neighbour, not swept).
        saved = self.records
        dsw = copy.deepcopy(saved["10.0.1.1"])
        dsw["neighbors"] = [n for n in dsw["neighbors"] if n["name"] != "asw-02"]
        self.records = {ip: r for ip, r in dict(saved, **{"10.0.1.1": dsw}).items() if ip != "10.0.1.2"}
        logins = []
        with mock.patch("netmon.devices.collect", side_effect=lambda ip, *a, **k: logins.append(ip) or copy.deepcopy(self.records[ip])):
            self._full_run_logins_only(also_up=("10.0.1.2",))
        self.records = saved
        self.assertNotIn("10.0.1.2", logins, "the switch passwords no longer go to that address")
        self.assertIsNone(self._status(store, "10.0.1.2"))

    def _full_run_logins_only(self, also_up=()):
        reachable = lambda ips, *a, **k: {ip: (1.0 if ip in self.records and ip != "10.0.1.3" or ip == "10.0.0.5"
                                               or ip in also_up else None) for ip in ips}
        with mock.patch("netmon.scan.probe", side_effect=reachable), \
                mock.patch("netmon.creds.switch_sets", return_value=[("set1", "u", "p")]), \
                mock.patch("netmon.creds.get", return_value=("nms", "p")), \
                mock.patch("netmon.devices.detect_platform", side_effect=lambda ip, *a: self.records[ip]["platform"]), \
                mock.patch("netmon.wlc.collect", return_value=copy.deepcopy(WLC[0])):
            self.assertEqual(run.full_run(self.cfg), 0)

    def test_forget_refuses_a_seed(self):
        store = self._full_run()
        failed = {"ip": "10.0.1.2", "ok": False, "error": "SSH authentication failed", "platform": "cisco_s300",
                  "neighbors": [], "ports": [], "warnings": []}
        self._refresh("10.0.1.2", failed)
        cfg = dict(self.cfg, collector=dict(self.cfg["collector"], seeds=["10.0.1.2"]))
        self.assertEqual(run.forget_switch(cfg, "10.0.1.2", "net@somaiya.edu"), 1)
        self.assertIn("core seed", store.read("refresh-status.json")["error"])
        self.assertEqual(self._status(store, "10.0.1.2"), "failed")

    def test_up_down_check_is_written_before_the_sweep(self):
        """IMPORTANT 2: known switches are probed first and reachability.json is written at once; the sweep
        that follows does not probe them again and never overwrites a 15-minute check made meanwhile."""
        store = self._full_run()
        saved = self.records
        dsw = copy.deepcopy(saved["10.0.1.1"])
        dsw["neighbors"] = [n for n in dsw["neighbors"] if n["name"] != "asw-02"]
        self.records = {ip: r for ip, r in dict(saved, **{"10.0.1.1": dsw}).items() if ip != "10.0.1.2"}
        calls = []
        later = {"checkedAt": "2026-11-25T02:45:00Z", "results": {"10.0.1.2": {"up": True, "since": "2026-11-25T02:45:00Z",
                                                                               "latencyMs": 1.0, "misses": 0}}}

        def probe(ips, port=22, *a, **k):
            ips = list(ips)
            calls.append((port, ips, store.read("reachability.json")))
            if port == 443:  # a 15-minute check finishes while the sweep is still on the web ports
                store.write(later, "reachability.json")
            return {ip: (1.0 if ip in self.records and ip != "10.0.1.3" or ip == "10.0.0.5" else None) for ip in ips}
        try:
            with mock.patch("netmon.scan.probe", side_effect=probe), \
                    mock.patch("netmon.creds.switch_sets", return_value=[("set1", "u", "p")]), \
                    mock.patch("netmon.creds.get", return_value=("nms", "p")), \
                    mock.patch("netmon.devices.detect_platform", side_effect=lambda ip, *a: self.records[ip]["platform"]), \
                    mock.patch("netmon.devices.collect", side_effect=lambda ip, *a, **k: copy.deepcopy(self.records[ip])), \
                    mock.patch("netmon.wlc.collect", return_value=copy.deepcopy(WLC[0])):
                self.assertEqual(run.full_run(self.cfg), 0)
        finally:
            self.records = saved
        port, first, _ = calls[0]
        self.assertEqual(port, 22)
        self.assertIn("10.0.1.2", first, "known switches first")
        self.assertNotIn("10.0.0.2", first, "not the sweep-only addresses")
        sweep = next((ips, reach) for p, ips, reach in calls if p == 22 and "10.0.0.2" in ips)
        self.assertNotIn("10.0.0.1", sweep[0], "a switch already probed is not probed again")
        self.assertEqual((sweep[1]["results"]["10.0.1.2"]["up"], sweep[1]["results"]["10.0.1.2"]["misses"]), (False, 1),
                         "written before the sweep starts")
        self.assertEqual(store.read("reachability.json"), later, "not overwritten by the run's older result")

    def test_run_and_check_seconds_apart_count_one_miss(self):
        """IMPORTANT 2: the 08:00 run and the 08:00 15-minute check do not make 2 misses in a row."""
        store = self._full_run()
        with mock.patch.object(known, "MIN_CHECK_GAP_SECONDS", 300):
            self._reach({"10.0.1.2": None})
            self._full_run_asw_gone()
        self.assertEqual(store.read("reachability.json")["results"]["10.0.1.2"]["misses"], 1)
        self.assertEqual(self._status(store, "10.0.1.2"), "unreachable")

    def test_excluded_or_out_of_scope_known_switch(self):
        """MINOR 3: never probed, never listed, and it can be removed from known-switches.json."""
        store = self._full_run()
        entries = known.load(store)
        entries["10.0.1.9"] = dict(entries["10.0.1.2"], ip="10.0.1.9", hostname="asw-09")
        entries["10.9.0.1"] = dict(entries["10.0.1.2"], ip="10.9.0.1", hostname="old-sw")
        known.save(store, entries)
        self.cfg = config.validate({
            "collector": {"switch_username": "u", "data_dir": self.tmp.name, "exclude": ["10.0.1.9/32"]},
            "subnets": [{"cidr": "10.0.0.0/30", "institute": "DC"}, {"cidr": "10.0.1.0/24", "institute": "Engineering", "sweep": False}],
            "wlc": {"hosts": ["10.0.0.5"]}})
        probed = self._reach({})
        self.assertFalse({"10.0.1.9", "10.9.0.1"} & set(probed), "the 15-minute check skips them")
        self._full_run()
        self.assertIsNone(self._status(store, "10.0.1.9"))
        self.assertIsNone(self._status(store, "10.9.0.1"))
        for ip in ("10.0.1.9", "10.9.0.1"):
            self.assertEqual(run.forget_switch(self.cfg, ip, "net@somaiya.edu"), 0, ip)
            self.assertNotIn(ip, known.load(store))

    def test_first_run_with_v3_remembers_the_previous_snapshot(self):
        """MINOR 4: no known-switches.json yet: seeded from the previous latest.json, so a switch that is off
        during the first v3 run is still listed (Not answering) instead of forgotten."""
        store = self._full_run()
        os.remove(store.path("state", "known-switches.json"))
        self._full_run_asw_gone()
        dev = next(d for d in store.read("latest.json")["devices"] if d["ip"] == "10.0.1.2")
        self.assertEqual((dev["status"], dev["hostname"], dev["lastUplinks"][0]["name"]), ("unreachable", "asw-02", "dsw-01"))
        self.assertIn("10.0.1.2", known.load(store))

    # ---- Cisco-only campus scan ----
    def _scan_cfg(self, **col):
        return config.validate({"collector": {"switch_username": "u", "data_dir": self.tmp.name, **col},
                                "subnets": [{"cidr": "10.0.2.0/29", "institute": "Engineering"}]})

    def _scan_run(self, open_ports, banners, pages, collect=None, cfg=None):
        """Full run with the scan mocked: open_ports {(ip, port)}, banners {ip: line}, pages {ip: web hint}.
        _login_collect is replaced; returns (store, logins, probe calls, web_hint calls)."""
        probed, logins, web_calls = [], [], []

        def probe(ips, port=22, *a, **k):
            ips = list(ips)
            probed.append((port, ips))
            return {ip: (1.0 if (ip, port) in open_ports else None) for ip in ips}

        def login(cfg_, ip, *a):
            logins.append(ip)
            return copy.deepcopy((collect or {}).get(ip) or rec(ip, "sw-" + ip.split(".")[-1], "cisco_ios", []))

        def web_hint(ip, ports, timeout=4.0):
            web_calls.append((ip, list(ports)))
            return pages.get(ip, "")
        with mock.patch("netmon.scan.probe", side_effect=probe), \
                mock.patch("netmon.scan.ssh_banners", side_effect=lambda ips, *a, **k: {ip: banners.get(ip) for ip in ips}), \
                mock.patch("netmon.identify.web_hint", side_effect=web_hint), \
                mock.patch("netmon.creds.switch_sets", return_value=[("set1", "u", "p")]), \
                mock.patch("netmon.run._login_collect", side_effect=login):
            self.assertEqual(run.full_run(cfg or self._scan_cfg()), 0)
        return Store(self.tmp.name), logins, probed, web_calls

    def test_sweep_logs_in_only_to_cisco_hosts(self):
        a, b, c, d = "10.0.2.1", "10.0.2.2", "10.0.2.3", "10.0.2.4"
        open_ports = {(a, 22), (b, 22), (b, 80), (c, 443), (d, 80)}
        banners = {a: CISCO, b: "SSH-2.0-OpenSSH_8.9p1"}
        pages = {b: "Welcome | nginx", c: "Cisco Business Dashboard <b>x</b> | ", d: "Router | lighttpd"}
        store, logins, probed, web_calls = self._scan_run(open_ports, banners, pages)
        self.assertEqual(logins, [a], "only the host that identified as Cisco gets the switch passwords")
        self.assertEqual(sorted(p for p, _ in probed), [22, 80, 443], "tcp 22, 443 and 80 are probed")
        self.assertEqual(sorted(ip for ip, _ in web_calls), [b, c, d], "web page read only where SSH did not say Cisco")
        self.assertIn((c, [443]), web_calls)
        latest = store.read("latest.json")
        self.assertEqual([x["ip"] for x in latest["devices"]], [a])
        self.assertEqual(latest["webOnly"], [{"ip": c, "ports": [443], "hint": pages[c], "institute": "Engineering"}])
        self.assertEqual({k: latest["scan"][k] for k in ("addresses", "sshOpen", "webOpen", "ciscoSsh", "ciscoWebOnly",
                                                         "nonCiscoSkipped")},
                         {"addresses": 6, "sshOpen": 2, "webOpen": 3, "ciscoSsh": 1, "ciscoWebOnly": 1, "nonCiscoSkipped": 2})
        self.assertNotIn(b, known.load(store), "a non-Cisco host is never remembered")
        last = store.read("state", "last-run.json")
        self.assertEqual(last["webOnly"], [{"ip": c, "ports": [443], "hint": pages[c]}], "raw; institute added by build")
        self.assertEqual(last["closed"], [], "scan-only addresses that did not answer are not kept")
        prog = store.read("run-progress.json")
        self.assertEqual([i["ip"] for i in prog["items"]], [a], "non-Cisco and web-only hosts are not listed")
        self.assertEqual(prog["items"][0]["source"], "IP scan (Cisco)")
        self.assertEqual(prog["sweepTotal"], 6 * 3, "tcp/22 on every address, then 443 and 80 on the scan-only ones")
        self.assertEqual(prog["sweepDone"], prog["sweepTotal"])
        self.assertEqual(prog["scan"]["nonCiscoSkipped"], 2)
        # Reconnect from the dashboard cannot reach a host the scan dropped or only listed.
        for ip in (b, c, d):
            with mock.patch("netmon.run._login_collect", side_effect=AssertionError("no login")), \
                    mock.patch("netmon.scan.probe", side_effect=AssertionError("no probe")):
                self.assertEqual(run.refresh_switch(self._scan_cfg(), ip, "net@somaiya.edu"), 1, ip)
            self.assertIn("not a switch from the last full discovery run", store.read("refresh-status.json")["error"])
        # A rebuild (refresh of the Cisco switch) keeps the web-only list.
        with mock.patch("netmon.creds.switch_sets", return_value=[("set1", "u", "p")]), \
                mock.patch("netmon.run._login_collect", return_value=rec(a, "sw-1", "cisco_ios", [])):
            self.assertEqual(run.refresh_switch(self._scan_cfg(), a, "net@somaiya.edu"), 0)
        self.assertEqual(store.read("latest.json")["webOnly"], latest["webOnly"])

    def test_ssh_host_whose_web_page_says_cisco_is_logged_in(self):
        """SG/CBS-style: an OpenSSH banner, but the web page identifies the host as Cisco."""
        e = "10.0.2.5"
        store, logins, _, web_calls = self._scan_run({(e, 22), (e, 443)}, {e: "SSH-2.0-OpenSSH_7.4"},
                                                     {e: "Cisco CBS350 | "})
        self.assertEqual(logins, [e])
        self.assertEqual(web_calls, [(e, [443])])
        scan_counts = store.read("latest.json")["scan"]
        self.assertEqual((scan_counts["ciscoSsh"], scan_counts["ciscoWebOnly"], scan_counts["nonCiscoSkipped"]), (1, 0, 0))

    def test_neighbour_found_switch_not_gated(self):
        a, n = "10.0.2.1", "10.0.2.5"
        sw1 = rec(a, "sw-1", "cisco_ios", [nbr("Gi1/0/1", "asw-05", n, "Gi0/1")])
        store, logins, _, _ = self._scan_run({(a, 22), (n, 22)}, {a: CISCO, n: "SSH-2.0-OpenSSH_8.9p1"}, {}, collect={a: sw1})
        self.assertEqual(logins, [a, n], "a switch its neighbour reports over CDP/LLDP is read even with an OpenSSH banner")
        self.assertEqual(store.read("latest.json")["scan"]["nonCiscoSkipped"], 1, "skipped by the scan, then found as a neighbour")
        items = {i["ip"]: i for i in store.read("run-progress.json")["items"]}
        self.assertEqual(items[n]["source"], "neighbour of sw-1 Gi1/0/1")

    def test_seeds_and_known_switches_are_not_gated(self):
        seed, old = "10.0.2.1", "10.0.2.2"
        known.save(Store(self.tmp.name), {old: {"ip": old, "hostname": "asw-02", "uplinks": []}})
        nexus = "SSH-2.0-OpenSSH_8.3"
        _, logins, probed, web_calls = self._scan_run({(seed, 22), (old, 22)}, {seed: nexus, old: nexus}, {},
                                                      cfg=self._scan_cfg(seeds=[seed]))
        self.assertEqual(sorted(logins), [seed, old])
        self.assertEqual(web_calls, [])
        self.assertEqual(sorted({ip for p, ips in probed if p != 22 for ip in ips}),
                         ["10.0.2.3", "10.0.2.4", "10.0.2.5", "10.0.2.6"], "web ports only on scan-only addresses")

    def test_cisco_host_with_every_set_rejected_is_listed_failed(self):
        a = "10.0.2.1"
        sets = [("set1", "u1", "p1"), ("set2", "u2", "p2"), ("set3", "u3", "p3")]
        tried = []

        def detect(ip, user, pw, cfg_):
            tried.append((ip, user))
            raise type("NetmikoAuthenticationException", (Exception,), {})("auth failed")
        with mock.patch("netmon.scan.probe", side_effect=lambda ips, port=22, *x, **k: {
                    ip: (1.0 if (ip, port) == (a, 22) else None) for ip in ips}), \
                mock.patch("netmon.creds.switch_sets", return_value=sets), \
                mock.patch("netmon.devices.detect_platform", side_effect=detect):
            self.assertEqual(run.full_run(self._scan_cfg()), 0)
        self.assertEqual(tried, [(a, "u1"), (a, "u2"), (a, "u3")])
        dev = Store(self.tmp.name).read("latest.json")["devices"]
        self.assertEqual([(d["ip"], d["status"], d["error"]) for d in dev],
                         [(a, "failed", "SSH authentication failed with all 3 credential sets")])
        self.assertIn(a, known.load(Store(self.tmp.name)), "a Cisco host is remembered like any switch")

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


MAIN, OTHER, THIRD, DSW = "10.20.0.5", "10.30.0.5", "10.30.0.6", "10.20.0.1"
SER, OLD, NEW = "FOC0000TEST", "FOC0000OLD", "FOC0000NEW"
IT0 = "2026-11-01T08:00:00Z"


def _ago(hours):
    return (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat(timespec="seconds").replace("+00:00", "Z")


def _fail(ip, error):
    return {"ip": ip, "ok": False, "error": error, "platform": "", "neighbors": [], "ports": [], "warnings": []}


class IdentityRuns(unittest.TestCase):
    """Full run, Refresh and Remove per switch (identity map, design 7.3 / 7.3a / 7.6)."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.cfg = config.validate({
            "collector": {"switch_username": "u", "data_dir": self.tmp.name, "management_ranges": ["10.20.0.0/16"]},
            "subnets": [{"cidr": "10.20.0.0/24", "sweep": False}, {"cidr": "10.30.0.0/24", "sweep": False}]})
        self.store = Store(self.tmp.name)
        self._known(MAIN, OTHER)
        self.closed, self.replies = set(), {}
        for target, kw in (("netmon.scan.ssh_banners", {"side_effect": lambda ips, *a, **k: {ip: CISCO for ip in ips}}),
                           ("netmon.identify.web_hint", {"return_value": ""})):
            patcher = mock.patch(target, **kw)
            patcher.start()
            self.addCleanup(patcher.stop)
        patcher = mock.patch.object(known, "MIN_CHECK_GAP_SECONDS", 0)
        patcher.start()
        self.addCleanup(patcher.stop)

    def _known(self, *ips):
        known.save(self.store, {ip: {"ip": ip, "hostname": "asw-test-01", "uplinks": [], "firstSeen": IT0,
                                     "lastSeenOk": IT0, "lastSeen": IT0} for ip in ips})

    def _map(self, switches):
        """Hand-made identity.json; switches: {key: (serials, main, {ip: hours since its last login})}."""
        ident = {"switches": {}}
        for key, (serials, main, ips) in switches.items():
            ident["switches"][key] = {"serials": serials, "main": main, "hostname": "asw-test-01", "firstSeen": IT0,
                                      "lastRead": IT0, "replacedBy": None,
                                      "ips": {ip: {"firstSeen": IT0, "lastLogin": _ago(h)} for ip, h in ips.items()}}
        identity.save(self.store, ident)

    def _ok(self, ip, serials=(SER,), hostname="asw-test-01", neighbors=()):
        return rec(ip, hostname, "cisco_ios", list(neighbors), serial=serials[0], serials=list(serials))

    def _probe(self, probed):
        def probe(ips, port=22, *a, **k):
            ips = list(ips)
            probed.append(ips)
            return {ip: (None if ip in self.closed or port != 22 else 1.0) for ip in ips}
        return probe

    def _patches(self, logins, probed):
        def login(cfg_, ip, *a):
            logins.append(ip)
            return copy.deepcopy(self.replies.get(ip) or self._ok(ip))
        return (mock.patch("netmon.scan.probe", side_effect=self._probe(probed)),
                mock.patch("netmon.creds.switch_sets", return_value=[("set1", "u", "p")]),
                mock.patch("netmon.run._login_collect", side_effect=login))

    def _run(self):
        logins, probed = [], []
        p1, p2, p3 = self._patches(logins, probed)
        with p1, p2, p3:
            self.assertEqual(run.full_run(self.cfg), 0)
        return sorted(logins), probed

    def _refresh(self, ip):
        logins, probed = [], []
        p1, p2, p3 = self._patches(logins, probed)
        with p1, p2, p3:
            code = run.refresh_switch(self.cfg, ip, "net@somaiya.edu")
        return code, logins

    def _devices(self):
        return self.store.read("latest.json")["devices"]

    def _progress(self):
        return {i["ip"]: i for i in self.store.read("run-progress.json")["items"]}

    def test_first_run_logs_in_to_every_ip(self):
        self.assertIsNone(self.store.read("state", "identity.json"))
        self.assertEqual(self._run()[0], [MAIN, OTHER], "no identity.json yet: every IP, as before")
        ident = identity.load(self.store)
        self.assertEqual(list(ident["switches"]), [SER])
        self.assertEqual(ident["switches"][SER]["main"], MAIN, "the IP in the management range")
        self.assertEqual(identity.other_ips(ident, SER), [OTHER])
        devs = self._devices()
        self.assertEqual([(d["id"], d["ip"], d["otherIps"]) for d in devs], [(SER, MAIN, [OTHER])])

    def test_other_ip_not_logged_in_until_20h(self):
        self._run()
        self.assertEqual(self._run()[0], [MAIN], "an other IP read minutes ago gets no login")
        item = self._progress()[OTHER]
        self.assertEqual((item["state"], item["error"]), ("skipped", "Other IP of asw-test-01; re-checked daily"))
        self.assertEqual(identity.load(self.store)["switches"][SER]["ips"][OTHER].get("lastTry"), None, "not tried")
        self._map({SER: ([SER], MAIN, {MAIN: 1, OTHER: 21})})
        self.assertEqual(self._run()[0], [MAIN, OTHER], "21 h after its last login: the daily re-check")
        self._map({SER: ([SER], MAIN, {MAIN: 1, OTHER: 19})})
        self.assertEqual(self._run()[0], [MAIN], "19 h: not yet")

    def test_fallback_when_main_does_not_answer(self):
        self._map({SER: ([SER], MAIN, {MAIN: 1, OTHER: 1})})
        self.closed = {MAIN}
        self.assertEqual(self._run()[0], [OTHER], "the switch is still read, on its other IP")
        self.assertNotEqual(self._progress()[OTHER]["state"], "skipped")
        devs = [d for d in self._devices() if d["id"] == SER]
        self.assertEqual(len(devs), 1, "one row for the switch")
        self.assertEqual(devs[0]["status"], "ok")
        # identity.update (Task 2; design 7.3 "main candidates are the IPs that logged in this run") makes the IP
        # that logged in the main, so the row is on that IP with "main IP changed", not on the old main with
        # ipWarning (see the Task 4 report: the brief expected the latter).
        self.assertEqual(identity.load(self.store)["switches"][SER]["main"], OTHER)
        self.assertEqual((devs[0]["ip"], devs[0]["mainIpChanged"]["from"]), (OTHER, MAIN))
        self.assertNotIn("ipWarning", devs[0])

    def test_timeout_falls_back_but_auth_failure_does_not(self):
        self._known(MAIN, OTHER, THIRD)
        for error, want in (("SSH timed out", [MAIN, OTHER]),
                            ("Login failed with every credential set: set1: SSH timed out", [MAIN, OTHER]),
                            (devices.AUTH_FAILED, [MAIN]),
                            ("SSH authentication failed with all 2 credential sets", [MAIN]),
                            ("Login failed with every credential set: set1: SSH authentication failed; set2: SSH timed out",
                             [MAIN])):
            with self.subTest(error=error):
                self._map({SER: ([SER], MAIN, {MAIN: 1, OTHER: 1, THIRD: 1})})
                self.replies = {MAIN: _fail(MAIN, error), OTHER: _fail(OTHER, "SSH timed out")}
                self.assertEqual(self._run()[0], want, "at most one fallback login per switch; never after an auth failure")

    def test_login_on_other_ip_tries_main_set_first(self):
        self._map({SER: ([SER], MAIN, {MAIN: 1, OTHER: 21})})
        self.store.write({MAIN: "set2"}, "state", "credentials.json")
        first = {}

        def detect(ip, user, pw, cfg_):
            first.setdefault(ip, user)
            return "cisco_ios"
        with mock.patch("netmon.scan.probe", side_effect=self._probe([])), \
                mock.patch("netmon.creds.switch_sets", return_value=[("set1", "u1", "p"), ("set2", "u2", "p")]), \
                mock.patch("netmon.devices.detect_platform", side_effect=detect), \
                mock.patch("netmon.devices.collect", side_effect=lambda ip, *a, **k: self._ok(ip)):
            self.assertEqual(run.full_run(self.cfg), 0)
        self.assertEqual(first, {MAIN: "u2", OTHER: "u2"}, "the switch's working set (main IP's entry) first")

    def test_crawl_skips_known_other_ip(self):
        self._known(DSW, MAIN, OTHER)
        self.replies = {DSW: self._ok(DSW, ("FOC0000DSW",), "dsw-test-01", [nbr("Gi1/0/1", "asw-test-01", OTHER, "Gi0/1")])}
        self._run()
        logins, probed = self._run()
        self.assertEqual(logins, [DSW, MAIN])
        self.assertEqual(sum(ips.count(OTHER) for ips in probed), 1, "only the up/down check of the known IPs")
        self.assertEqual(self._progress()[OTHER]["state"], "skipped")

    def test_replacement_rechecks_old_other_ips(self):
        self._map({OLD: ([OLD], MAIN, {MAIN: 1, OTHER: 1})})
        self.replies = {MAIN: self._ok(MAIN, (NEW,)), OTHER: self._ok(OTHER, (NEW,))}
        self.assertEqual(self._run()[0], [MAIN, OTHER], "the old switch's other IP is re-checked in the same run")
        ident = identity.load(self.store)
        self.assertEqual((identity.owner(ident, MAIN), identity.owner(ident, OTHER)), (NEW, NEW))
        self.assertEqual((ident["switches"][OLD]["ips"], ident["switches"][OLD]["replacedBy"]), ({}, NEW))
        old = next(d for d in self._devices() if d["id"] == OLD)
        self.assertEqual((old["ip"], old["status"]), ("", "down"))

    def test_refresh_uses_main_or_answering_ip(self):
        self._run()
        before = identity.load(self.store)["switches"][SER]["ips"][OTHER]["lastLogin"]
        self.assertEqual(self._refresh(OTHER), (0, [MAIN]), "posted with the other IP: the main is logged in to")
        self.closed = {MAIN}
        self.assertEqual(self._refresh(MAIN), (0, [OTHER]), "main closed: its answering other IP")
        sw = identity.load(self.store)["switches"][SER]
        self.assertNotEqual(sw["ips"][OTHER]["lastLogin"], before, "identity.json updated by the refresh")
        status = self.store.read("refresh-status.json")
        self.assertEqual((status["ok"], status["ip"], status["loginIp"]), (True, MAIN, OTHER))
        self.assertEqual([d["ip"] for d in self._devices() if d["id"] == SER], [sw["main"]])
        self.closed = {MAIN, OTHER}
        self.assertEqual(self._refresh(OTHER), (1, []), "no IP of the switch answers: no login")
        self.assertEqual(self.store.read("refresh-status.json")["error"], "Still no answer on SSH (tcp/22) from the collector.")

    def test_forget_removes_every_ip(self):
        self._run()
        self.assertEqual(run.forget_switch(self.cfg, OTHER, "net@somaiya.edu"), 1, "an Up switch is not removed")
        self.closed = {MAIN, OTHER}
        self._run()
        self._run()
        dev = next(d for d in self._devices() if d["id"] == SER)
        self.assertEqual((dev["ip"], dev["status"]), (MAIN, "down"))
        seeded = dict(self.cfg, collector=dict(self.cfg["collector"], seeds=[OTHER]))
        self.assertEqual(run.forget_switch(seeded, MAIN, "net@somaiya.edu"), 1, "any IP of the switch is a seed")
        self.assertIn("core seed", self.store.read("refresh-status.json")["error"])
        with mock.patch("netmon.run._login_collect", side_effect=AssertionError("no login")), \
                mock.patch("netmon.scan.probe", side_effect=AssertionError("no probe")):
            self.assertEqual(run.forget_switch(self.cfg, MAIN, "net@somaiya.edu"), 0)
        self.assertFalse({MAIN, OTHER} & set(known.load(self.store)))
        last = self.store.read("state", "last-run.json")
        self.assertFalse({MAIN, OTHER} & ({r["ip"] for r in last["records"]} | set(last["closed"])))
        self.assertNotIn(SER, identity.load(self.store)["switches"])
        self.assertFalse([d for d in self._devices() if d["id"] == SER])
        self.assertEqual(self.store.read("refresh-status.json")["id"], SER)
        # A replaced switch (no IPs left) is removed by its key.
        ident = identity.load(self.store)
        ident["switches"][OLD] = {"serials": [OLD], "main": None, "hostname": "asw-test-01", "ips": {}, "firstSeen": IT0,
                                  "lastRead": IT0, "replacedBy": NEW, "last": {"hostname": "asw-test-01", "ip": MAIN}}
        identity.save(self.store, ident)
        self.assertEqual(run.forget_switch(self.cfg, key="bad key!"), 1)
        self.assertEqual(run.forget_switch(self.cfg, key="FOC0000NONE"), 1)
        self.assertIn(OLD, identity.load(self.store)["switches"])
        self.assertEqual(run.forget_switch(self.cfg, by="net@somaiya.edu", key=OLD), 0)
        self.assertNotIn(OLD, identity.load(self.store)["switches"])
        status = self.store.read("refresh-status.json")
        self.assertEqual((status["state"], status["id"], status["ip"]), ("done", OLD, ""))
        with open(self.store.path("audit.log"), encoding="utf-8") as fh:
            self.assertIn(f"forget id={OLD} by=net@somaiya.edu", fh.read())

    def test_reach_probes_every_ip_and_skips_replaced_rows(self):
        self._run()
        ident = identity.load(self.store)
        ident["switches"][OLD] = {"serials": [OLD], "main": None, "hostname": "asw-test-09", "ips": {}, "firstSeen": IT0,
                                  "lastRead": IT0, "replacedBy": SER, "last": {"hostname": "asw-test-09", "ip": "10.20.0.9"}}
        identity.save(self.store, ident)
        self._run()
        self.assertIn("", [d["ip"] for d in self._devices()], "the replaced switch's row")
        probed = []
        with mock.patch("netmon.scan.probe", side_effect=self._probe(probed)):
            self.assertEqual(run.reach_run(self.cfg), 0)
        self.assertEqual(sorted(probed[0]), [MAIN, OTHER])


if __name__ == "__main__":
    unittest.main()
