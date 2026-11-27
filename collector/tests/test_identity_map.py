import copy
import tempfile
import unittest

from netmon import config, identity
from netmon.store import Store

CFG = config.validate({"collector": {"switch_username": "u", "management_ranges": ["10.20.0.0/16"]},
                       "subnets": [{"cidr": "10.20.0.0/16"}]})
T0, T1 = "2026-11-27T08:00:00Z", "2026-11-28T08:00:00Z"
A, B, C, D = "FOC0000AAA", "FOC0000BBB", "FOC0000CCC", "FOC0000DDD"


def rec(ip, serials, ok=True, hostname="asw-test-01", model="C9200L-TEST"):
    r = {"ip": ip, "ok": ok, "hostname": hostname if ok else "", "model": model if ok else "",
         "version": "17.9.4" if ok else "", "platform": "cisco_ios", "serial": (serials or [""])[0] if ok else ""}
    if serials is not None:
        r["serials"] = serials
    return r


def switch(serials, ips, main, hostname="asw-test-01"):
    """A hand-made map entry; ips: {ip: firstSeen}."""
    return {"serials": serials, "main": main, "hostname": hostname, "firstSeen": T0, "lastRead": T0, "replacedBy": None,
            "ips": {ip: {"firstSeen": fs, "lastLogin": T0} for ip, fs in ips.items()}}


def run(ident, records, now=T1, answered=(), first_seen=None):
    return identity.update(ident, records, CFG, now, set(answered), first_seen or {})


class IdentityMap(unittest.TestCase):
    def test_real_serials(self):
        self.assertEqual(identity.real_serials(["foc0000test", "FOC0000TEST", " N/A", "0000000000", "unknown", "abc12",
                                                "NotAvailable", "", None, "FOC0000TWO"]), ["FOC0000TEST", "FOC0000TWO"])
        self.assertEqual(identity.real_serials(None), [])

    def test_load_save(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = Store(tmp)
            self.assertEqual(identity.load(store), {"switches": {}}, "no identity.json yet")
            ident = {"switches": {A: switch([A], {"10.20.0.5": T0}, "10.20.0.5")}}
            identity.save(store, ident)
            self.assertEqual(identity.load(store), ident)
            self.assertTrue(store.path("state", identity.FILE).endswith("identity.json"))

    def test_first_run_builds_map(self):
        ident = {"switches": {}}
        run(ident, [rec("10.30.0.5", ["FOC0000TEST"]), rec("10.20.0.5", ["FOC0000TEST"])], now=T0)
        self.assertEqual(list(ident["switches"]), ["FOC0000TEST"])
        sw = ident["switches"]["FOC0000TEST"]
        self.assertEqual(sw["main"], "10.20.0.5", "the management range wins")
        self.assertEqual((sw["serials"], sw["hostname"], sw["firstSeen"], sw["lastRead"]), (["FOC0000TEST"], "asw-test-01", T0, T0))
        self.assertIsNone(sw.get("mainChangedFrom"), "a first choice is not a change")
        self.assertEqual(identity.other_ips(ident, "FOC0000TEST"), ["10.30.0.5"])
        self.assertEqual(identity.alias(ident), {"10.30.0.5": "10.20.0.5", "10.20.0.5": "10.20.0.5"})
        self.assertEqual(identity.owner(ident, "10.30.0.5"), "FOC0000TEST")
        self.assertIsNone(identity.owner(ident, "10.30.0.99"))

    def test_stack_matches_on_any_member(self):
        ident = {"switches": {}}
        run(ident, [rec("10.20.0.5", [A, B])], now=T0)
        run(ident, [rec("10.20.0.5", [B, C]), rec("10.30.0.7", [C])])
        self.assertEqual(list(ident["switches"]), [A], "the key never changes")
        self.assertEqual(ident["switches"][A]["serials"], [B, C], "replaced, not accumulated")
        self.assertEqual(identity.other_ips(ident, A), ["10.30.0.7"])

    def test_placeholder_serials_never_merge(self):
        ident = {"switches": {}}
        run(ident, [rec("10.20.0.5", ["0000000000"]), rec("10.20.0.6", ["N/A"]), rec("10.20.0.7", [""]),
                    rec("10.20.0.8", [""])])
        self.assertEqual(ident["switches"], {})
        self.assertEqual(identity.alias(ident), {})

    def test_ambiguous_match_takes_most_shared(self):
        ident = {"switches": {A: switch([A, B, C], {"10.20.0.10": T0}, "10.20.0.10"),
                              D: switch([C, D], {"10.20.0.11": T0}, "10.20.0.11")}}
        logs = run(ident, [rec("10.20.0.12", [A, C])])
        self.assertEqual(identity.owner(ident, "10.20.0.12"), A)
        self.assertEqual(ident["switches"][D]["serials"], [C, D], "the other switch is untouched")
        self.assertTrue(any("ambiguous" in l for l in logs), logs)

    def test_old_serial_list_needs_same_hostname(self):
        ident = {"switches": {A: switch([A, B], {"10.20.0.5": T0}, "10.20.0.5", hostname="stack-test-01")}}
        logs = run(ident, [rec("10.20.0.9", [B], hostname="asw-test-09")])
        self.assertEqual(identity.owner(ident, "10.20.0.9"), B, "a stack member that became its own switch")
        self.assertEqual(list(ident["switches"][A]["ips"]), ["10.20.0.5"])
        self.assertEqual(ident["switches"][A]["serials"], [A, B])
        self.assertTrue(any("10.20.0.9" in l and A in l for l in logs), logs)

        ident = {"switches": {A: switch([A, B], {"10.20.0.5": T0}, "10.20.0.5", hostname="stack-test-01")}}
        run(ident, [rec("10.20.0.9", [B], hostname="Stack-Test-01.example.test")])
        self.assertEqual(identity.owner(ident, "10.20.0.9"), A)
        self.assertEqual(list(ident["switches"]), [A])

    def test_main_ip_rules(self):
        # (a) the current main is inside the ranges: kept, although another range IP is older
        ident = {"switches": {A: switch([A], {"10.20.0.5": T1, "10.20.0.6": T0}, "10.20.0.5")}}
        run(ident, [rec("10.20.0.5", [A]), rec("10.20.0.6", [A])])
        self.assertEqual(ident["switches"][A]["main"], "10.20.0.5")
        # (b) nothing in the ranges: the current main is kept while it is a candidate ...
        ident = {"switches": {A: switch([A], {"10.30.0.5": T1, "10.30.0.9": T0}, "10.30.0.5")}}
        run(ident, [rec("10.30.0.5", [A]), rec("10.30.0.9", [A])])
        self.assertEqual(ident["switches"][A]["main"], "10.30.0.5")
        # ... else the earliest firstSeen, tie: the lowest IP numerically (10.30.0.9 before 10.30.0.10)
        # (the old main answers tcp/22 here; a main that does not answer is kept: test_silent_main_kept_for_7_days)
        ident = {"switches": {A: switch([A], {"10.30.0.5": T0, "10.30.0.9": T0, "10.30.0.10": T0}, "10.30.0.5")}}
        run(ident, [rec("10.30.0.5", None, ok=False), rec("10.30.0.9", [A]), rec("10.30.0.10", [A])],
            answered={"10.30.0.5"})
        self.assertEqual(ident["switches"][A]["main"], "10.30.0.9")
        # ... firstSeen from the known switches wins over the map's own
        ident = {"switches": {A: switch([A], {"10.30.0.5": T0, "10.30.0.9": T0, "10.30.0.10": T0}, "10.30.0.5")}}
        run(ident, [rec("10.30.0.9", [A]), rec("10.30.0.10", [A])], first_seen={"10.30.0.10": "2026-01-01T00:00:00Z"},
            answered={"10.30.0.5"})
        self.assertEqual(ident["switches"][A]["main"], "10.30.0.10")
        # (c) an IP whose login failed is not chosen while another logged in; (d) the change is recorded
        ident = {"switches": {A: switch([A], {"10.30.0.5": T0, "10.30.0.6": T1}, "10.30.0.5")}}
        logs = run(ident, [rec("10.30.0.5", None, ok=False), rec("10.30.0.6", [A])], answered={"10.30.0.5"})
        sw = ident["switches"][A]
        self.assertEqual((sw["main"], sw["mainChangedFrom"], sw["mainChangedAt"]), ("10.30.0.6", "10.30.0.5", T1))
        self.assertTrue(any("10.30.0.5" in l and "10.30.0.6" in l for l in logs), logs)

    def test_silent_main_kept_for_7_days(self):
        """Ruling 6: main not answering tcp/22, another IP logs in: the main stays (Up with a warning) until it
        has not answered for MAIN_SILENT_DAYS; a main that answers but cannot log in still moves."""
        m, o, t8 = "10.20.0.5", "10.30.0.6", "2026-12-05T08:00:00Z"  # t8: 8 days after T0
        self.assertEqual(identity.MAIN_SILENT_DAYS, 7)
        ident = {"switches": {A: switch([A], {m: T0, o: T0}, m)}}
        run(ident, [rec(o, [A])], now=T1, answered={o})
        sw = ident["switches"][A]
        self.assertEqual(sw["main"], m, "silent 1 day (no lastAnswered: its firstSeen counts): kept")
        self.assertNotIn("mainChangedFrom", sw)
        self.assertNotIn("lastAnswered", sw["ips"][m], "unchanged: it did not answer")
        self.assertEqual(sw["ips"][o]["lastAnswered"], T1)
        run(ident, [rec(o, [A])], now=t8, answered={o})
        self.assertEqual((sw["main"], sw["mainChangedFrom"], sw["mainChangedAt"]), (o, m, t8), "silent 8 days: moves")
        # lastAnswered wins over firstSeen: answered 1 day before t8 -> kept, and left as it was
        ident = {"switches": {A: switch([A], {m: T0, o: T0}, m)}}
        ident["switches"][A]["ips"][m]["lastAnswered"] = "2026-12-04T08:00:00Z"
        run(ident, [rec(o, [A])], now=t8, answered={o})
        self.assertEqual(ident["switches"][A]["main"], m)
        self.assertEqual(ident["switches"][A]["ips"][m]["lastAnswered"], "2026-12-04T08:00:00Z")
        # answered tcp/22 but its login failed while the other IP logged in: moves (design 7.3, unchanged)
        ident = {"switches": {A: switch([A], {m: T0, o: T0}, m)}}
        run(ident, [rec(m, None, ok=False), rec(o, [A])], now=T1, answered={m, o})
        self.assertEqual(ident["switches"][A]["main"], o)
        self.assertEqual(ident["switches"][A]["ips"][m]["lastAnswered"], T1)

    def test_replacement_moves_ip(self):
        ident = {"switches": {}}
        run(ident, [rec("10.20.0.5", ["FOC0000OLD"], hostname="asw-test-05", model="C2960X-TEST")], now=T0)
        new = rec("10.20.0.5", ["NEWSER0001"], hostname="asw-test-05", model="C9200L-TEST")
        self.assertEqual(identity.replacement_rechecks(ident, new), [], "OLD has no other IPs")
        logs = run(ident, [new])
        old = ident["switches"]["FOC0000OLD"]
        self.assertEqual((old["ips"], old["replacedBy"], old["main"]), ({}, "NEWSER0001", None))
        self.assertEqual((old["last"]["hostname"], old["last"]["model"]), ("asw-test-05", "C2960X-TEST"))
        self.assertEqual(old["last"]["ip"], "10.20.0.5", "the IP it answered on, for 'its IP X now answers as ...'")
        sw = ident["switches"]["NEWSER0001"]
        self.assertEqual((sw["main"], sw["serials"], sw["ips"]["10.20.0.5"]["firstSeen"]), ("10.20.0.5", ["NEWSER0001"], T0))
        self.assertEqual(identity.alias(ident), {"10.20.0.5": "10.20.0.5"})
        self.assertTrue(any("10.20.0.5 moved from FOC0000OLD to NEWSER0001" in l for l in logs), logs)

    def test_replacement_rechecks_and_remaining_main(self):
        ident = {"switches": {}}
        run(ident, [rec("10.20.0.5", ["FOC0000OLD"]), rec("10.30.0.6", ["FOC0000OLD"]), rec("10.30.0.7", ["FOC0000OLD"])],
            now=T0)
        new = rec("10.20.0.5", ["NEWSER0001"])
        self.assertEqual(identity.replacement_rechecks(ident, new), ["10.30.0.6", "10.30.0.7"])
        self.assertEqual(identity.replacement_rechecks(ident, rec("10.20.0.5", ["FOC0000OLD"])), [], "same switch")
        self.assertEqual(identity.replacement_rechecks(ident, rec("10.20.0.5", None, ok=False)), [])
        self.assertEqual(identity.replacement_rechecks(ident, rec("10.30.0.99", ["NEWSER0001"])), [], "unknown IP")
        self.assertEqual(identity.replacement_rechecks(ident, rec("10.30.0.6", ["NEWSER0001"])), ["10.30.0.7"])
        run(ident, [new], answered={"10.30.0.7"})
        old = ident["switches"]["FOC0000OLD"]
        self.assertEqual(sorted(old["ips"]), ["10.30.0.6", "10.30.0.7"])
        self.assertIsNone(old["replacedBy"], "it still has IPs")
        self.assertEqual((old["main"], old["mainChangedFrom"]), ("10.30.0.7", "10.20.0.5"), "an IP that answers tcp/22")

    def test_ip_moves_without_replacement_when_old_switch_read_elsewhere(self):
        ident = {"switches": {}}
        run(ident, [rec("10.20.0.5", ["FOC0000OLD"]), rec("10.20.0.6", ["FOC0000OLD"])], now=T0)
        self.assertEqual(ident["switches"]["FOC0000OLD"]["main"], "10.20.0.5")
        logs = run(ident, [rec("10.20.0.6", ["FOC0000OLD"]), rec("10.20.0.5", ["NEWSER0001"])])
        self.assertEqual(identity.owner(ident, "10.20.0.5"), "NEWSER0001")
        self.assertEqual(ident["switches"]["NEWSER0001"]["ips"]["10.20.0.5"]["lastLogin"], T1)
        old = ident["switches"]["FOC0000OLD"]
        self.assertEqual((list(old["ips"]), old["replacedBy"], old["main"]), (["10.20.0.6"], None, "10.20.0.6"),
                         "not a replacement: OLD keeps its other IP")
        self.assertTrue(any("answers for more than one switch" in l for l in logs), logs)
        self.assertFalse(any("hardware replaced" in l for l in logs), logs)

    def test_needs_login(self):
        ident = {"switches": {}}
        run(ident, [rec("10.20.0.5", [A]), rec("10.30.0.6", [A])], now=T0)
        self.assertTrue(identity.needs_login(ident, "10.20.0.5", "2026-11-27T09:00:00Z"), "the main IP")
        self.assertFalse(identity.needs_login(ident, "10.30.0.6", "2026-11-28T03:00:00Z"), "19 h")
        self.assertTrue(identity.needs_login(ident, "10.30.0.6", "2026-11-28T04:00:00Z"), "20 h")
        self.assertTrue(identity.needs_login(ident, "10.30.0.99", "2026-11-27T09:00:00Z"), "not in the map")
        run(ident, [rec("10.30.0.6", None, ok=False)], now="2026-11-28T04:00:00Z")
        self.assertEqual(ident["switches"][A]["ips"]["10.30.0.6"]["lastTry"], "2026-11-28T04:00:00Z")
        self.assertEqual(ident["switches"][A]["ips"]["10.30.0.6"]["lastLogin"], T0)
        self.assertFalse(identity.needs_login(ident, "10.30.0.6", "2026-11-28T05:00:00Z"), "not retried every run")

    def test_records_without_serials_are_ignored(self):
        ident = {"switches": {}}
        run(ident, [rec("10.20.0.5", [A])], now=T0)
        before = copy.deepcopy(ident)
        old_record = {"ip": "10.20.0.7", "ok": True, "hostname": "asw-test-07", "serial": "FOC0000OLDREC"}
        logs = run(ident, [rec("10.20.0.5", None), rec("10.20.0.6", None), old_record, rec("10.20.0.8", None, ok=False),
                           rec("10.20.0.9", [A], ok=False)])
        before["switches"][A]["ips"]["10.20.0.5"]["lastTry"] = T1
        self.assertEqual(ident, before)
        self.assertEqual(logs, [])

    def test_fold_one_record_per_switch(self):
        """main's ok record, else the newest ok record, else main's failed record, else any."""
        ident = {"switches": {"FOC0000TEST": switch(["FOC0000TEST"], {"10.20.0.5": T0, "10.30.0.5": T0, "10.30.0.6": T1},
                                                    "10.20.0.5")}}
        main_ok, main_bad = rec("10.20.0.5", ["FOC0000TEST"]), rec("10.20.0.5", None, ok=False)
        o1, o2 = rec("10.30.0.5", ["FOC0000TEST"]), dict(rec("10.30.0.6", ["FOC0000TEST"]), refreshedAt=T1)
        o_bad = rec("10.30.0.5", None, ok=False)
        unknown = rec("10.30.0.99", ["FOC0000ELSE"])
        cases = [([o1, main_ok, o2], main_ok), ([main_bad, o1, o2], o2), ([main_bad, o1], o1),
                 ([o_bad, main_bad], main_bad), ([o_bad], o_bad)]
        for recs, want in cases:
            out, alias = identity.fold([unknown] + recs, ident, {})
            self.assertEqual(len(out), 2)
            self.assertEqual((out[0]["ip"], out[0]["id"], out[0]["ips"], out[0]["otherIps"], out[0]["serials"]),
                             ("10.30.0.99", "ip:10.30.0.99", ["10.30.0.99"], [], ["FOC0000ELSE"]))
            got = out[1]
            self.assertEqual((got["ip"], got["id"], got["ok"], got["serials"], got["ips"], got["otherIps"]),
                             ("10.20.0.5", "FOC0000TEST", want["ok"], ["FOC0000TEST"],
                              ["10.20.0.5", "10.30.0.5", "10.30.0.6"], ["10.30.0.5", "10.30.0.6"]))
            self.assertEqual(got.get("refreshedAt"), want.get("refreshedAt"))
            if want["ip"] == "10.20.0.5":
                self.assertNotIn("ipWarning", got)
            else:
                self.assertEqual(got["ipWarning"], f"Read on {want['ip']}; main IP 10.20.0.5 did not answer")
            self.assertIsNot(got, want, "a copy")
        self.assertEqual(alias, {"10.20.0.5": "10.20.0.5", "10.30.0.5": "10.20.0.5", "10.30.0.6": "10.20.0.5"})
        self.assertNotIn("mainIpChanged", out[1])
        ident["switches"]["FOC0000TEST"].update(mainChangedFrom="10.30.0.5", mainChangedAt=T0)
        self.assertEqual(identity.fold([main_ok], ident, {})[0][0]["mainIpChanged"], {"from": "10.30.0.5", "at": T0})
        self.assertEqual(identity.fold([unknown], {"switches": {}}, {}), ([dict(unknown, id="ip:10.30.0.99", ips=["10.30.0.99"],
                                                                               otherIps=[], serials=["FOC0000ELSE"])], {}))
        self.assertEqual(identity.fold([], {}, None), ([], {}))

    def test_forget(self):
        ident = {"switches": {A: switch([A], {"10.20.0.5": T0}, "10.20.0.5")}}
        self.assertTrue(identity.forget(ident, A))
        self.assertFalse(identity.forget(ident, A))
        self.assertEqual(ident, {"switches": {}})


if __name__ == "__main__":
    unittest.main()
