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
