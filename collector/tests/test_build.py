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
