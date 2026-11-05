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
        live = [l for l in self.snap["links"] if l["status"] == "up"]
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
        for t in ("ssh-failed", "cpu", "poe", "ap-not-in-wlc", "ap-down", "neighbor-not-collected"):
            self.assertIn(t, types)
        msg = next(f["message"] for f in self.snap["findings"] if f["type"] == "neighbor-not-collected")
        self.assertIn("does not answer SSH", msg)

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
