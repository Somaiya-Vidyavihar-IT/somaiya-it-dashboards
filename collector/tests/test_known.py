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
