import tempfile
import unittest

from netmon import build, known
from netmon.store import Store
from tests.test_build import CFG, WLC, campus


class KnownSwitches(unittest.TestCase):
    def setUp(self):
        self.records = campus()
        self.snap, _, _ = build.build(CFG, self.records, WLC, now="2026-11-05T02:30:00Z",
                                      ssh_closed=frozenset({"10.0.1.50"}))

    def test_update_keeps_first_seen_and_uplinks(self):
        first = known.update_from_records({}, self.records, self.snap["devices"], "2026-11-05T02:30:00Z")
        self.assertEqual(sorted(first), ["10.0.0.1", "10.0.1.1", "10.0.1.2", "10.0.1.3"], "no-ssh rows are not remembered")
        dsw = first["10.0.1.1"]
        self.assertEqual((dsw["hostname"], dsw["model"], dsw["serial"], dsw["version"], dsw["institute"], dsw["role"],
                          dsw["platform"]), ("dsw-01", "X", "S-dsw-01", "1", "Engineering", "distribution", "cisco_ios"))
        self.assertEqual(dsw["uplinks"], [{"localPort": "Te1/1/4", "name": "core-01", "ip": "10.0.0.1", "remotePort": "Eth1/10"},
                                          {"localPort": "Gi1/0/1", "name": "asw-02", "ip": "10.0.1.2", "remotePort": "Gi1/0/28"}])
        asw = first["10.0.1.2"]
        self.assertEqual([u["name"] for u in asw["uplinks"]], ["dsw-01", "unknown-sw"], "APs are not uplinks")
        self.assertEqual((dsw["firstSeen"], dsw["lastSeenOk"]), ("2026-11-05T02:30:00Z", "2026-11-05T02:30:00Z"))
        again = known.update_from_records(first, self.records, self.snap["devices"], "2026-11-05T11:00:00Z")
        self.assertEqual(again["10.0.1.1"]["firstSeen"], "2026-11-05T02:30:00Z")
        self.assertEqual(again["10.0.1.1"]["lastSeenOk"], "2026-11-05T11:00:00Z")
        self.assertEqual(first["10.0.1.1"]["lastSeenOk"], "2026-11-05T02:30:00Z", "input is not changed")

    def test_failed_record_keeps_last_inventory(self):
        first = known.update_from_records({}, self.records, self.snap["devices"], "2026-11-05T02:30:00Z")
        failed = [r if r["ip"] != "10.0.1.1" else {"ip": "10.0.1.1", "ok": False, "error": "SSH timed out", "platform": "",
                                                    "neighbors": [], "ports": [], "warnings": []} for r in self.records]
        snap, _, _ = build.build(CFG, failed, WLC, now="2026-11-05T11:00:00Z")
        later = known.update_from_records(first, failed, snap["devices"], "2026-11-05T11:00:00Z")
        dsw = later["10.0.1.1"]
        self.assertEqual((dsw["hostname"], dsw["model"], dsw["platform"]), ("dsw-01", "X", "cisco_ios"))
        self.assertEqual(len(dsw["uplinks"]), 2)
        self.assertEqual((dsw["lastSeenOk"], dsw["lastSeen"]), ("2026-11-05T02:30:00Z", "2026-11-05T11:00:00Z"))
        never = later["10.0.1.3"]  # SSH failed on every run: remembered with what is known (its address)
        self.assertEqual((never["hostname"], never["lastSeenOk"], never["firstSeen"]), ("", None, "2026-11-05T02:30:00Z"))

    def test_apply_probe_counts_and_resets_misses(self):
        r1 = known.apply_probe({}, {"10.0.0.1": None, "10.0.0.2": 1.5}, "T1")
        self.assertEqual(r1["10.0.0.1"], {"up": False, "since": "T1", "latencyMs": None, "misses": 1})
        self.assertEqual(r1["10.0.0.2"], {"up": True, "since": "T1", "latencyMs": 1.5, "misses": 0})
        r2 = known.apply_probe(r1, {"10.0.0.1": None, "10.0.0.2": 2.0}, "T2")
        self.assertEqual((r2["10.0.0.1"]["misses"], r2["10.0.0.1"]["since"]), (2, "T1"))
        self.assertEqual((r2["10.0.0.2"]["misses"], r2["10.0.0.2"]["since"]), (0, "T1"))
        r3 = known.apply_probe(r2, {"10.0.0.1": 3.0}, "T3")
        self.assertEqual(r3, {"10.0.0.1": {"up": True, "since": "T3", "latencyMs": 3.0, "misses": 0}},
                         "only probed addresses are returned")
        self.assertEqual(known.apply_probe(r3, {"10.0.0.1": None}, "T4")["10.0.0.1"]["misses"], 1, "counting starts again")

    def test_old_reachability_file_without_misses(self):
        old = {"10.0.0.1": {"up": False, "since": "T0", "latencyMs": None}, "10.0.0.2": {"up": True, "since": "T0"}}
        r = known.apply_probe(old, {"10.0.0.1": None, "10.0.0.2": None}, "T1")
        self.assertEqual((r["10.0.0.1"]["misses"], r["10.0.0.1"]["since"]), (1, "T0"))
        self.assertEqual((r["10.0.0.2"]["misses"], r["10.0.0.2"]["since"]), (1, "T1"))

    def test_is_down_follows_the_latest_check(self):
        m = lambda n: {"up": n == 0, "since": "T", "latencyMs": None, "misses": n}
        self.assertTrue(known.is_down("down", None), "no live result: the snapshot status stands")
        self.assertTrue(known.is_down("down", m(3)))
        self.assertFalse(known.is_down("unreachable", None))
        self.assertFalse(known.is_down("unreachable", m(2)), "R7: Not answering in the snapshot is never removable")
        self.assertFalse(known.is_down("unreachable", m(1)))
        self.assertFalse(known.is_down("down", m(0)), "answering again")
        self.assertFalse(known.is_down("down", {"up": False, "since": "T"}), "old file without misses")
        for status in ("ok", "failed", "no-ssh", None):
            self.assertFalse(known.is_down(status, m(5)), status)

    def test_two_probes_minutes_apart_count_as_one_miss(self):
        """IMPORTANT 2: a check less than 5 minutes after the last one does not add a second miss."""
        r1 = known.apply_probe({}, {"10.0.0.1": None, "10.0.0.2": 1.0}, "2026-11-25T02:30:00Z")
        self.assertEqual(r1["10.0.0.1"]["misses"], 1)
        r2 = known.apply_probe(r1, {"10.0.0.1": None, "10.0.0.2": None}, "2026-11-25T02:30:40Z",
                               prev_checked_at="2026-11-25T02:30:00Z")
        self.assertEqual(r2["10.0.0.1"]["misses"], 1, "08:00 run and 08:00 check: still one miss")
        self.assertEqual((r2["10.0.0.2"]["up"], r2["10.0.0.2"]["misses"]), (False, 1), "a first miss always counts")
        r3 = known.apply_probe(r2, {"10.0.0.1": None}, "2026-11-25T02:35:00Z", prev_checked_at="2026-11-25T02:30:40Z")
        self.assertEqual(r3["10.0.0.1"]["misses"], 1, "4 min 20 s later: still too close")
        r4 = known.apply_probe(r3, {"10.0.0.1": None}, "2026-11-25T02:45:00Z", prev_checked_at="2026-11-25T02:35:00Z")
        self.assertEqual((r4["10.0.0.1"]["misses"], r4["10.0.0.1"]["since"]), (2, "2026-11-25T02:30:00Z"))
        r5 = known.apply_probe(r4, {"10.0.0.1": 1.0}, "2026-11-25T02:46:00Z", prev_checked_at="2026-11-25T02:45:00Z")
        self.assertEqual(r5["10.0.0.1"]["misses"], 0, "an answer always resets")
        r6 = known.apply_probe(r4, {"10.0.0.1": None}, "2026-11-25T02:50:00Z", prev_checked_at="not a time")
        self.assertEqual(r6["10.0.0.1"]["misses"], 3, "unreadable checkedAt: counted as usual")

    def test_forget_refusal(self):
        """R7/R8 for Down rows; R10: an SSH-failed row can be removed too; seeds never; out of scope always."""
        m = lambda n: {"up": n == 0, "since": "T", "latencyMs": None, "misses": n}
        remembered = {"10.0.1.2": {}, "10.0.1.3": {}, "10.0.0.1": {}, "10.9.0.1": {}}
        seeds = ["10.0.0.1"]
        ok = lambda ip, status, live=None, scope=True: known.forget_refusal(ip, status, live, remembered, seeds, scope)
        self.assertIsNone(ok("10.0.1.2", "down"))
        self.assertIsNone(ok("10.0.1.2", "down", m(2)))
        self.assertIsNone(ok("10.0.1.3", "failed"), "SSH failed row of a known switch")
        self.assertIsNone(ok("10.0.1.4", "failed"), "every SSH-failed row is a switch the next run logs in to")
        self.assertIsNone(ok("10.9.0.1", None, scope=False), "a known switch now out of scope or excluded")
        for ip, status, live in (("10.0.1.2", "down", m(1)), ("10.0.1.2", "down", m(0)), ("10.0.1.2", "unreachable", m(3)),
                                 ("10.0.1.2", "ok", None), ("10.0.1.2", "no-ssh", None), ("10.0.9.9", None, None)):
            msg = ok(ip, status, live)
            self.assertIn("only a Down switch or one whose SSH login fails can be removed", msg or "", (ip, status, live))
        self.assertIsNotNone(ok("10.0.9.9", None, scope=False), "out of scope but never known")
        for status in ("down", "failed", None):
            self.assertIn("core seed", ok("10.0.0.1", status, m(5), scope=status is not None) or "", status)

    def test_seed_from_previous_snapshot(self):
        """MINOR 4: the first v3 run remembers every switch the previous run listed as read or SSH failed."""
        devs = [{"ip": "10.0.1.1", "hostname": "dsw-01", "model": "X", "serial": "S1", "version": "1", "institute": "E",
                 "role": "distribution", "platform": "cisco_ios", "status": "ok",
                 "neighbors": [{"localPort": "Te1/1/4", "name": "core-01", "ip": "10.0.0.1", "remotePort": "Eth1/10",
                                "kind": "switch"}, {"localPort": "Gi1/0/5", "name": "ap-1", "ip": "", "kind": "ap"}]},
                {"ip": "10.0.1.3", "hostname": "", "status": "failed", "neighbors": []},
                {"ip": "10.0.1.50", "hostname": "asw-50", "status": "no-ssh", "neighbors": []}]
        out = known.seed_from_snapshot({"generatedAt": "2026-11-20T02:30:00Z", "devices": devs})
        self.assertEqual(sorted(out), ["10.0.1.1", "10.0.1.3"])
        dsw = out["10.0.1.1"]
        self.assertEqual((dsw["hostname"], dsw["role"], dsw["platform"], dsw["lastSeenOk"], dsw["firstSeen"]),
                         ("dsw-01", "distribution", "cisco_ios", "2026-11-20T02:30:00Z", "2026-11-20T02:30:00Z"))
        self.assertEqual(dsw["uplinks"], [{"localPort": "Te1/1/4", "name": "core-01", "ip": "10.0.0.1", "remotePort": "Eth1/10"}])
        self.assertIsNone(out["10.0.1.3"]["lastSeenOk"])
        self.assertEqual(known.seed_from_snapshot({}), {})

    def test_load_save_and_forget(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = Store(tmp)
            self.assertEqual(known.load(store), {})
            data = known.update_from_records({}, self.records, self.snap["devices"], "2026-11-05T02:30:00Z")
            known.save(store, data)
            self.assertEqual(known.load(store), data)
            self.assertTrue(store.read("state", "known-switches.json"))
            self.assertTrue(known.forget(data, "10.0.1.1"))
            self.assertFalse(known.forget(data, "10.0.1.1"))
            self.assertNotIn("10.0.1.1", data)


if __name__ == "__main__":
    unittest.main()
