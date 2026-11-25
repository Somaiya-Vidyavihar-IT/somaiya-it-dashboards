import copy
import os
import tempfile
import unittest
from unittest import mock

from netmon import config, known, run
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
            self.assertIn("only a Down switch can be removed", store.read("refresh-status.json")["error"])
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


if __name__ == "__main__":
    unittest.main()
