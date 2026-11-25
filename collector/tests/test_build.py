import os
import tempfile
import unittest

from netmon import build, config, reports
from netmon.parse import _nbr

CFG = config.validate({
    "collector": {"switch_username": "<USER>", "keep_days": 30, "error_delta_threshold": 100},
    "subnets": [{"cidr": "10.0.0.0/24", "institute": "DC"}, {"cidr": "10.0.1.0/24", "institute": "Engineering"},
                {"cidr": "10.0.0.0/16", "institute": "Campus"}],
})


def nbr(local, name, ip, remote, caps="Switch", via=("cdp", "lldp"), **kw):
    n = _nbr(local, name, ip, remote, caps=caps, **kw)
    n["via"] = list(via)
    return n


def rec(ip, hostname, platform, neighbors, ports=(), **kw):
    r = {"ip": ip, "ok": True, "error": None, "platform": platform, "warnings": [], "hostname": hostname,
         "model": kw.pop("model", "X"), "serial": "S-" + hostname, "version": "1", "uptime": "1 day", "mac": "",
         "neighbors": neighbors, "ports": [{"port": p, "status": s, "vlan": "", "speed": "", "duplex": "", "name": ""}
                                           for p, s in ports],
         "errors": None, "poe": None, "poePorts": [], "cpu": 5}
    r.update(kw)
    return r


def campus(include_uplink=True, errors=None):
    core = rec("10.0.0.1", "core-01", "cisco_nxos",
               [nbr("Eth1/10", "dsw-01(FDO1)", "10.0.1.1", "Te1/1/4")] if include_uplink else [],
               [("Eth1/10", "connected")])
    dsw = rec("10.0.1.1", "dsw-01", "cisco_ios",
              ([nbr("Te1/1/4", "core-01", "10.0.0.1", "Ethernet1/10")] if include_uplink else [])
              + [nbr("Gi1/0/1", "asw-02", "10.0.1.2", "gi1/0/28", via=["lldp"])],
              [("Te1/1/4", "connected"), ("Gi1/0/1", "connected"), ("Gi1/0/9", "err-disabled")],
              errors=errors or {"Gi1/0/1": 10}, cpu=92, poe=(740.0, 700.0))
    asw = rec("10.0.1.2", "asw-02", "cisco_s300",
              [nbr("Gi1/0/28", "dsw-01", "10.0.1.1", "GigabitEthernet1/0/1", via=["cdp"]),
               nbr("Gi1/0/1", "ap-lib-01", "10.60.0.11", "eth0", caps="Bridge, WLAN Access Point", via=["lldp"],
                   desc="ArubaOS (MODEL: 515), Version 8.10.0.6", chassis="20:4c:03:00:00:01"),
               nbr("Gi1/0/2", "rogue-ap", "", "eth0", caps="B,W", via=["lldp"], chassis="20:4c:03:00:00:99"),
               nbr("Gi1/0/3", "unknown-sw", "10.0.1.50", "Gi0/1")],
              [("Gi1/0/28", "connected"), ("Gi1/0/1", "connected")],
              poePorts=[{"port": "Gi1/0/1", "oper": "on", "powerW": 12.4, "device": "", "class": "4"}])
    failed = {"ip": "10.0.1.3", "ok": False, "error": "SSH authentication failed", "platform": "", "neighbors": [],
              "ports": [], "warnings": []}
    return [core, dsw, asw, failed]


WLC = [{"host": "10.0.0.5", "ok": True, "error": None, "warnings": [],
        "aps": [{"name": "ap-lib-01", "group": "campus", "model": "515", "ip": "10.60.0.11", "status": "Up 3d:1h",
                 "mac": "20:4c:03:00:00:01", "serial": "CNTEST0001", "flags": ""},
                {"name": "ap-hall-09", "group": "campus", "model": "303", "ip": "10.60.0.19", "status": "Down",
                 "mac": "20:4c:03:00:00:09", "serial": "CNTEST0009", "flags": ""}],
        "clients": {"ap-lib-01": 12}, "apLldp": {"ap-lib-01": {"switch": "asw-02", "switchIp": "10.0.1.2", "port": "Gi1/0/1"}}}]


class BuildSnapshot(unittest.TestCase):
    def setUp(self):
        self.snap, self.baseline, self.counters = build.build(CFG, campus(), WLC, now="2026-11-05T02:30:00Z",
                                                              ssh_closed=frozenset({"10.0.1.50"}))
        self.dev = {d["ip"]: d for d in self.snap["devices"]}

    def test_roles_follow_hops_from_core_and_institutes_use_most_specific_subnet(self):
        self.assertEqual([self.dev[ip]["role"] for ip in ("10.0.0.1", "10.0.1.1", "10.0.1.2", "10.0.1.3")],
                         ["core", "distribution", "access", "unknown"])
        self.assertEqual(self.dev["10.0.1.1"]["institute"], "Engineering")
        self.assertEqual(self.dev["10.0.0.1"]["institute"], "DC")

    def test_links_merge_both_sides_and_mark_uplinks(self):
        live = [l for l in self.snap["links"] if l["status"] == "up" and "10.0.1.50" not in (l["a"], l["b"])]
        self.assertEqual(len(live), 2, live)
        core_link = next(l for l in live if "10.0.0.1" in (l["a"], l["b"]))
        self.assertTrue(core_link["both"])
        self.assertTrue(core_link["uplink"])
        self.assertEqual(core_link["downstream"], "10.0.1.1")
        self.assertEqual({core_link["aPort"], core_link["bPort"]}, {"Eth1/10", "Te1/1/4"})

    def test_health_flags_and_findings(self):
        d = self.dev["10.0.1.1"]
        self.assertIn("cpu", d["flags"])
        self.assertIn("poe", d["flags"])
        self.assertEqual(d["poe"]["pct"], 95)
        self.assertEqual(d["ports"]["errDisabled"], 1)
        self.assertIsNone(d["errDelta"], "no delta on the first run")
        self.assertEqual(self.dev["10.0.1.3"]["status"], "failed")
        types = [f["type"] for f in self.snap["findings"]]
        for t in ("ssh-failed", "cpu", "poe", "ap-not-in-wlc", "ap-down", "no-ssh"):
            self.assertIn(t, types)

    def test_login_account_shown_per_switch(self):
        """The dashboard shows which credential set (and its username, never the password) logged in."""
        recs = campus()
        recs[0]["credentialSet"] = "default"
        recs[3]["credentialSet"] = "default"  # a failed record must not claim a working account
        snap, _, _ = build.build(CFG, recs, WLC, now="2026-11-05T02:30:00Z", ssh_closed=frozenset({"10.0.1.50"}))
        dev = {d["ip"]: d for d in snap["devices"]}
        self.assertEqual((dev["10.0.0.1"]["credentialSet"], dev["10.0.0.1"]["loginUser"]), ("default", "<USER>"))
        self.assertEqual((dev["10.0.1.1"]["credentialSet"], dev["10.0.1.1"]["loginUser"]), ("", ""), "older record")
        for ip in ("10.0.1.3", "10.0.1.50"):
            self.assertEqual((dev[ip]["credentialSet"], dev[ip]["loginUser"]), ("", ""), ip)
        self.assertNotIn("password", " ".join(dev["10.0.0.1"]))

    def test_neighbor_issues_lists_failed_rejected_fallback_and_empty(self):
        def c(purpose, result, lines=20, detail=""):
            return {"command": f"show {purpose} neighbors detail", "purpose": purpose, "result": result,
                    "detail": detail, "lines": lines}
        recs = [
            rec("10.0.1.1", "a-01", "cisco_ios", [nbr("Gi1/0/1", "x", "10.0.1.9", "g1", via=["lldp"])],
                commands=[c("cdp", "fallback", 12, "text parser"), c("lldp", "ok")]),
            rec("10.0.1.2", "b-01", "cisco_ios", [nbr("Gi1/0/1", "x", "10.0.1.9", "g1", via=["lldp"])],
                commands=[c("cdp", "rejected", 2, "% Invalid input"), c("lldp", "ok")]),
            rec("10.0.1.3", "c-01", "cisco_ios", [nbr("Gi1/0/1", "x", "10.0.1.9", "g1", via=["cdp"])],
                commands=[c("cdp", "ok"), c("lldp", "failed", None, "timeout")]),
            rec("10.0.1.4", "d-01", "cisco_ios", [nbr("Gi1/0/1", "x", "10.0.1.9", "g1", via=["cdp"])],
                commands=[c("cdp", "ok"), c("lldp", "ok", 9)]),
        ]
        recs[2]["commands"][1].pop("lines")
        snap, _, _ = build.build(CFG, recs, [], now="2026-11-05T02:30:00Z")
        got = {d["ip"]: [(i["purpose"], i["result"]) for i in d["neighborIssues"]] for d in snap["devices"]
               if d["status"] == "ok"}
        self.assertEqual(got["10.0.1.1"], [("cdp", "empty")])  # fallback read nothing from 12 lines
        self.assertEqual(got["10.0.1.2"], [("cdp", "rejected")])
        self.assertEqual(got["10.0.1.3"], [("lldp", "failed")])
        self.assertEqual(got["10.0.1.4"], [("lldp", "empty")])
        self.assertEqual(snap["summary"]["neighborIssues"], 4)

    def test_neighbor_issue_fallback_that_read_neighbours_stays_fallback(self):
        r = rec("10.0.1.1", "a-01", "cisco_ios", [nbr("Gi1/0/1", "x", "10.0.1.9", "g1", via=["cdp", "lldp"])],
                commands=[{"command": "show cdp neighbors detail", "purpose": "cdp", "result": "fallback",
                           "detail": "text parser", "lines": 12}])
        snap, _, _ = build.build(CFG, [r], [], now="2026-11-05T02:30:00Z")
        self.assertEqual([i["result"] for i in snap["devices"][0]["neighborIssues"]], ["fallback"])

    def test_clean_switch_has_no_neighbor_issues(self):
        r = rec("10.0.1.1", "a-01", "cisco_ios", [nbr("Gi1/0/1", "x", "10.0.1.9", "g1")],
                commands=[{"command": "show cdp neighbors detail", "purpose": "cdp", "result": "ok", "detail": "", "lines": 30},
                          {"command": "show lldp neighbors detail", "purpose": "lldp", "result": "ok", "detail": "", "lines": 3},
                          {"command": "show version", "purpose": "version", "result": "ok", "detail": "", "lines": 50}])
        snap, _, _ = build.build(CFG, [r, campus()[3]], [], now="2026-11-05T02:30:00Z")
        d = {x["ip"]: x for x in snap["devices"]}
        self.assertEqual(d["10.0.1.1"]["neighborIssues"], [])
        self.assertEqual(d["10.0.1.3"]["neighborIssues"], [])
        self.assertEqual(snap["summary"]["neighborIssues"], 0)

    def test_neighbour_switch_without_ssh_gets_its_own_row(self):
        """Seen live: the hostel distribution switches under the core block SSH from the collector and
        were only an info finding, so the dashboard showed no hostel switches at all."""
        d = self.dev["10.0.1.50"]
        self.assertEqual((d["status"], d["hostname"], d["institute"], d["flags"]), ("no-ssh", "unknown-sw", "Engineering", ["no-ssh"]))
        self.assertEqual(d["role"], "access", "linked under asw-02, three hops from the core")
        self.assertEqual(d["seenFrom"], [{"ip": "10.0.1.2", "hostname": "asw-02", "port": "Gi1/0/3", "remotePort": "Gi0/1"}])
        link = next(l for l in self.snap["links"] if "10.0.1.50" in (l["a"], l["b"]))
        self.assertEqual((link["downstream"], link["uplink"]), ("10.0.1.50", True))
        msg = next(f["message"] for f in self.snap["findings"] if f["type"] == "no-ssh")
        self.assertIn("asw-02 Gi1/0/3, does not answer SSH", msg)
        s = self.snap["summary"]
        self.assertEqual((s["switches"], s["collected"], s["failed"], s["noSsh"]), (5, 3, 1, 1))
        self.assertNotIn("neighbor-not-collected", [f["type"] for f in self.snap["findings"]])

    def test_configured_phone_models_are_phones_not_switches(self):
        """Seen live: Fanvil X1SG / X301G / X3SG phones advertise LLDP bridge and were taken for switches."""
        recs = campus()
        recs[2]["neighbors"] += [nbr("Gi1/0/6", "X301G", "10.0.1.60", "WAN", caps="B"),
                                 nbr("Gi1/0/7", "x3sg", "10.0.1.61", "LAN"),
                                 nbr("Gi1/0/8", "SEP-X1SG-LOBBY", "10.0.1.62", "eth0", platform="Fanvil X1SG")]
        snap, _, _ = build.build(CFG, recs, WLC, now="2026-11-05T02:30:00Z",
                                 ssh_closed=frozenset({"10.0.1.50", "10.0.1.60", "10.0.1.61", "10.0.1.62"}))
        ips = {d["ip"] for d in snap["devices"]}
        self.assertFalse(ips & {"10.0.1.60", "10.0.1.61", "10.0.1.62"}, "phones get no switch row")
        self.assertIn("10.0.1.50", ips, "a real switch without SSH still does")
        kinds = {n["name"]: n["kind"] for d in snap["devices"] if d["ip"] == "10.0.1.2" for n in d["neighbors"]}
        self.assertEqual((kinds["X301G"], kinds["x3sg"], kinds["SEP-X1SG-LOBBY"]), ("phone", "phone", "phone"))
        self.assertFalse([f for f in snap["findings"] if "X301G" in f["message"] or "x3sg" in f["message"]])
        self.assertEqual(build.neighbor_kind({"name": "X301GX", "caps": "B"}, phone_re=CFG["collector"]["phone_re"]),
                         "switch", "whole model names only")
        no_phones = config.validate({"collector": {"switch_username": "u", "phone_models": []},
                                     "subnets": [{"cidr": "10.0.0.0/24"}]})
        self.assertIsNone(no_phones["collector"]["phone_re"])
        with self.assertRaises(config.ConfigError):
            config.validate({"collector": {"switch_username": "u", "phone_models": "X1SG"}, "subnets": []})

    def test_neighbour_switch_without_ip_or_out_of_scope_stays_a_finding(self):
        recs = campus()
        recs[2]["neighbors"].append(nbr("Gi1/0/4", "noip-sw", "", "Gi0/1"))
        recs[2]["neighbors"].append(nbr("Gi1/0/5", "far-sw", "192.0.2.9", "Gi0/1"))
        snap, _, _ = build.build(CFG, recs, WLC, now="2026-11-05T02:30:00Z", ssh_closed=frozenset({"192.0.2.9"}))
        msgs = [f["message"] for f in snap["findings"] if f["type"] == "neighbor-not-collected"]
        self.assertTrue(any("noip-sw" in m and "advertised no management IP" in m for m in msgs), msgs)
        self.assertTrue(any("far-sw" in m and "outside the configured subnets" in m for m in msgs), msgs)
        self.assertNotIn("192.0.2.9", [d["ip"] for d in snap["devices"]])

    def test_aruba_inventory_joins_wlc_switch_and_ap_side(self):
        aps = {a["name"]: a for a in self.snap["aps"]}
        lib = aps["ap-lib-01"]
        self.assertEqual((lib["switch"], lib["port"], lib["poeW"], lib["clients"], lib["serial"]),
                         ("asw-02", "Gi1/0/1", 12.4, 12, "CNTEST0001"))
        self.assertEqual(lib["seenBy"], ["ap-lldp", "lldp", "wlc"])
        self.assertEqual(aps["rogue-ap"]["status"], "Not in WLC")
        self.assertEqual(aps["ap-hall-09"]["switch"], "")
        self.assertEqual(self.snap["summary"]["aps"], 3)
        self.assertEqual(self.snap["summary"]["apsUp"], 1)

    def test_sx_lldp_neighbour_known_to_wlc_is_an_ap(self):
        """SG/CBS 'show lldp neighbors' gives only name, port ID (often the AP's MAC) and chassis."""
        from netmon.parse import _nbr
        records = campus()
        bare = _nbr("Gi1/0/9", "LAB_IT LAB", "", "20:4c:03:00:00:32")
        bare["via"] = ["lldp"]
        oui_only = _nbr("Gi1/0/10", "SSA_TT_1", "", "20:4c:03:aa:bb:cc")
        oui_only["via"] = ["lldp"]
        records[2]["neighbors"] += [bare, oui_only]
        wlc = [dict(WLC[0], aps=WLC[0]["aps"] + [{"name": "LAB_IT LAB", "group": "campus", "model": "315", "ip": "10.60.0.32",
                                                "status": "Up 1d", "mac": "20:4c:03:00:00:32", "serial": "CNTEST0032", "flags": ""}])]
        snap, _, _ = build.build(CFG, records, wlc, now="2026-11-05T02:30:00Z")
        kinds = {n["name"]: n["kind"] for d in snap["devices"] if d["ip"] == "10.0.1.2" for n in d["neighbors"]}
        self.assertEqual((kinds["LAB_IT LAB"], kinds["SSA_TT_1"], kinds["dsw-01"]), ("ap", "ap", "switch"))
        lab = next(a for a in snap["aps"] if a["name"] == "LAB_IT LAB")
        self.assertEqual((lab["switch"], lab["port"], lab["serial"]), ("asw-02", "Gi1/0/9", "CNTEST0032"))

    def test_lost_uplink_and_error_delta_on_next_run(self):
        snap2, _, _ = build.build(CFG, campus(include_uplink=False, errors={"Gi1/0/1": 400}), WLC, self.baseline,
                                  self.counters, now="2026-11-05T11:00:00Z")
        lost = [l for l in snap2["links"] if l["status"] == "lost"]
        self.assertEqual(len(lost), 1)
        self.assertTrue(lost[0]["uplink"])
        dev = {d["ip"]: d for d in snap2["devices"]}
        self.assertIn("uplink", dev["10.0.1.1"]["flags"])
        self.assertEqual(dev["10.0.1.1"]["errDelta"], 390)
        self.assertTrue(any(p["issue"] == "errors" and p["port"] == "Gi1/0/1" for p in snap2["problemPorts"]))
        self.assertTrue(any(f["type"] == "link-lost" for f in snap2["findings"]))

    def test_old_lost_links_age_out(self):
        _, base2, _ = build.build(CFG, campus(include_uplink=False), WLC, self.baseline, self.counters,
                                  now="2026-12-31T00:00:00Z")
        self.assertFalse(any("10.0.0.1" in k for k in base2))


class DownSwitches(unittest.TestCase):
    """A switch read before that stops answering tcp/22 stays listed as Down (2 missed checks)."""

    def setUp(self):
        from netmon import known
        snap, _, _ = build.build(CFG, campus(), WLC, now="2026-11-05T02:30:00Z", ssh_closed=frozenset({"10.0.1.50"}))
        self.known = known.update_from_records({}, campus(), snap["devices"], "2026-11-05T02:30:00Z")
        # Next run: asw-02 does not answer tcp/22, so it has no record and dsw-01 no longer sees it.
        recs = campus()
        recs[1]["neighbors"] = [n for n in recs[1]["neighbors"] if n["name"] != "asw-02"]
        self.recs = [r for r in recs if r["ip"] != "10.0.1.2"]

    def _build(self, misses, closed=frozenset({"10.0.1.2"})):
        reach = {"10.0.1.2": {"up": False, "since": "2026-11-05T09:15:00Z", "latencyMs": None, "misses": misses}}
        snap, _, _ = build.build(CFG, self.recs, WLC, now="2026-11-05T11:00:00Z", ssh_closed=closed,
                                 known=self.known, reach=reach)
        return snap, {d["ip"]: d for d in snap["devices"]}

    def test_known_switch_missing_twice_is_down_with_last_inventory(self):
        snap, dev = self._build(2)
        d = dev["10.0.1.2"]
        self.assertEqual((d["status"], d["flags"], d["hostname"], d["model"], d["serial"], d["platform"], d["role"]),
                         ("down", ["down"], "asw-02", "X", "S-asw-02", "cisco_s300", "access"))
        self.assertEqual(d["downSince"], "2026-11-05T09:15:00Z")
        self.assertEqual([u["name"] for u in d["lastUplinks"]], ["dsw-01", "unknown-sw"])
        self.assertEqual(d["lastUplinks"][0], {"localPort": "Gi1/0/28", "name": "dsw-01", "ip": "10.0.1.1",
                                               "remotePort": "Gi1/0/1"})
        self.assertEqual((d["neighbors"], d["commands"], d["cpu"], d["ports"]), ([], [], None, None))
        down = [f for f in snap["findings"] if f["type"] == "down"]
        self.assertEqual(len(down), 1)
        self.assertEqual((down[0]["severity"], down[0]["ip"], down[0]["hostname"]), ("critical", "10.0.1.2", "asw-02"))
        self.assertIn("Remove", down[0]["message"])
        s = snap["summary"]
        self.assertEqual((s["down"], s["switches"], s["failed"]), (1, 4, 1))

    def test_one_miss_is_not_down(self):
        snap, dev = self._build(1)
        self.assertNotIn("10.0.1.2", dev)
        self.assertEqual(snap["summary"]["down"], 0)
        self.assertFalse([f for f in snap["findings"] if f["type"] == "down"])
        self.assertEqual(build.build(CFG, self.recs, WLC, now="2026-11-05T11:00:00Z")[0]["summary"]["down"], 0,
                         "no known list: nothing is down")

    def test_no_ssh_rows_never_go_down(self):
        self.known["10.0.1.50"] = dict(self.known["10.0.1.1"], ip="10.0.1.50", hostname="unknown-sw")
        recs = campus()  # asw-02 answers again and still sees 10.0.1.50, which does not answer SSH
        reach = {ip: {"up": False, "since": "2026-11-05T09:15:00Z", "latencyMs": None, "misses": 5}
                 for ip in ("10.0.1.50", "10.0.1.2", "10.0.1.3")}
        snap, _, _ = build.build(CFG, recs, WLC, now="2026-11-05T11:00:00Z", ssh_closed=frozenset({"10.0.1.50"}),
                                 known=self.known, reach=reach)
        dev = {d["ip"]: d for d in snap["devices"]}
        self.assertEqual(dev["10.0.1.50"]["status"], "no-ssh")
        self.assertEqual(dev["10.0.1.2"]["status"], "ok", "a collected record is never Down")
        self.assertEqual(dev["10.0.1.3"]["status"], "failed")
        self.assertEqual(snap["summary"]["down"], 0)


class Reports(unittest.TestCase):
    def test_excel_cells_never_become_formulas(self):
        self.assertEqual(reports.safe_cell("=HYPERLINK(\"x\")"), "'=HYPERLINK(\"x\")")
        self.assertEqual(reports.safe_cell("@SUM(1)"), "'@SUM(1)")
        self.assertEqual(reports.safe_cell("ok\x07name"), "okname")
        self.assertEqual(reports.safe_cell(["cdp", "lldp"]), "cdp, lldp")

    def test_excel_and_dot_written(self):
        records = campus()
        records[2]["neighbors"][1]["name"] = '=cmd|"/c calc"!A1'
        snap, _, _ = build.build(CFG, records, WLC, now="2026-11-05T02:30:00Z")
        with tempfile.TemporaryDirectory() as tmp:
            dot = os.path.join(tmp, "t.dot")
            reports.write_dot(snap, dot)
            with open(dot, encoding="utf-8") as fh:
                text = fh.read()
            self.assertIn('subgraph cluster_', text)
            self.assertIn('"10.0.0.1" -- "10.0.1.1"', text)
            # A backslash or quote in device text must not escape the DOT label.
            snap["devices"][0]["hostname"] = 'core\\"x'
            reports.write_dot(snap, dot)
            with open(dot, encoding="utf-8") as fh:
                self.assertIn('[label="core  x\\n', fh.read())
            try:
                import openpyxl
            except ImportError:
                self.skipTest("openpyxl not installed")
            xlsx = os.path.join(tmp, "t.xlsx")
            reports.write_excel(snap, xlsx)
            wb = openpyxl.load_workbook(xlsx)
            self.assertEqual(wb.sheetnames[:2], ["Devices", "Connectivity Matrix"])
            values = [c.value for row in wb["Neighbours"].iter_rows() for c in row]
            self.assertIn("'=cmd|\"/c calc\"!A1", values)
            self.assertFalse(any(isinstance(v, str) and v.startswith("=") for v in values))


class Config(unittest.TestCase):
    def test_rejects_bad_input(self):
        with self.assertRaises(config.ConfigError):
            config.validate({"collector": {"switch_username": "u"}, "subnets": [{"cidr": "10.0.0.0/8"}]})
        with self.assertRaises(config.ConfigError):
            config.validate({"collector": {"switch_username": "u"}, "subnets": [{"cidr": "10.0.0.0/24", "platform": "junos"}]})
        with self.assertRaises(config.ConfigError):
            config.validate({"collector": {}, "subnets": [{"cidr": "10.0.0.0/24"}]})

    def test_credential_sets(self):
        cfg = config.validate({"collector": {"switch_username": "legacy"}, "subnets": [{"cidr": "10.0.0.0/24"}]})
        self.assertEqual(cfg["collector"]["switch_credential_sets"], [{"name": "default", "username": "legacy"}])
        with self.assertRaises(config.ConfigError):
            config.validate({"collector": {"switch_credential_sets": [{"name": "a", "username": "x"}, {"name": "a", "username": "y"}]},
                             "subnets": [{"cidr": "10.0.0.0/24"}]})
        with self.assertRaises(config.ConfigError):
            config.validate({"collector": {"switch_credential_sets": [{"name": "bad name", "username": "x"}]},
                             "subnets": [{"cidr": "10.0.0.0/24"}]})

    def test_new_defaults(self):
        col = config.validate({"collector": {"switch_username": "u"}, "subnets": [{"cidr": "10.0.0.0/24"}]})["collector"]
        self.assertEqual(col["poe_threshold"], 75)
        self.assertEqual(col["uplink_threshold"], 75)
        self.assertEqual(col["uplink_fast_minutes"], 10)
        self.assertEqual(col["uplink_slow_minutes"], 30)
        self.assertEqual(col["uplink_workers"], 16)
        self.assertEqual(col["scan_ports"], [22, 443, 80])
        self.assertTrue(col["cisco_ssh_re"][0].search("SSH-2.0-Cisco-1.25"))
        self.assertTrue(col["cisco_web_re"][0].search("Cisco Systems"))

    def test_bad_cisco_pattern_rejected(self):
        with self.assertRaises(config.ConfigError) as ctx:
            config.validate({"collector": {"switch_username": "u", "cisco_ssh_banners": ["("]},
                             "subnets": [{"cidr": "10.0.0.0/24"}]})
        self.assertIn("collector.cisco_ssh_banners", str(ctx.exception))

    def test_bad_uplink_values_rejected(self):
        for key, bad in (("uplink_threshold", 0), ("uplink_threshold", 101), ("poe_threshold", "x"),
                         ("uplink_fast_minutes", 0), ("uplink_workers", 0), ("scan_ports", [0]),
                         ("cisco_web_patterns", "cisco")):
            with self.assertRaises(config.ConfigError, msg=key):
                config.validate({"collector": {"switch_username": "u", key: bad}, "subnets": [{"cidr": "10.0.0.0/24"}]})

    def test_example_config_parses(self):
        import tomllib
        path = os.path.join(os.path.dirname(__file__), "..", "config.example.toml")
        with open(path, "rb") as fh:
            cfg = config.validate(tomllib.load(fh))
        self.assertEqual(cfg["subnets"][0]["cidr"], "10.255.0.0/23")
        self.assertEqual(len(list(__import__("netmon.scan").scan.sweep_targets(cfg))), 510)
        self.assertEqual([s["name"] for s in cfg["collector"]["switch_credential_sets"]], ["set1", "set2", "set3"])


if __name__ == "__main__":
    unittest.main()
