import unittest

from netmon import parse
from tests import fixtures as fx

try:
    from ntc_templates.parse import parse_output
except ImportError:  # parsers that rely on TextFSM need the collector requirements installed
    parse_output = None


class Helpers(unittest.TestCase):
    def test_short_interface_names_join_across_sources(self):
        cases = {"GigabitEthernet1/0/1": "Gi1/0/1", "gi1/0/1": "Gi1/0/1", "GE1/0/5": "Gi1/0/5",
                 "TenGigabitEthernet1/1/4": "Te1/1/4", "te1/0/1": "Te1/0/1", "XG1/0/2": "Te1/0/2",
                 "Ethernet1/10": "Eth1/10", "Eth1/10": "Eth1/10", "TwentyFiveGigE1/0/1": "Twe1/0/1",
                 "Port-channel10": "Po10", "FastEthernet0/1": "Fa0/1", "mgmt0": "mgmt0", "Port 1": "Port 1"}
        for raw, want in cases.items():
            self.assertEqual(parse.short_if(raw), want, raw)

    def test_mac_and_names(self):
        self.assertEqual(parse.norm_mac("00AA.BBCC.DD01"), "00:aa:bb:cc:dd:01")
        self.assertEqual(parse.norm_mac("00-aa-bb-cc-dd-01"), "00:aa:bb:cc:dd:01")
        self.assertEqual(parse.norm_mac("not a mac"), "")
        self.assertEqual(parse.base_name("core-n9k-01(FDO12345ABC)"), "core-n9k-01")
        self.assertEqual(parse.base_name("DSW-Test-01.example.local"), "dsw-test-01")

    def test_platform_classification(self):
        self.assertEqual(parse.classify_show_version("Cisco Nexus Operating System (NX-OS) Software"), "cisco_nxos")
        self.assertEqual(parse.classify_show_version(fx.IOS_VERSION), "cisco_ios")
        self.assertEqual(parse.classify_show_version("Active-image: flash://system/images/image.bin\n  Version: 3.2.1.1"), "cisco_s300")
        self.assertEqual(parse.classify_show_version("garbage"), "")


class CustomParsers(unittest.TestCase):
    def test_cdp_detail_text_ios_and_sx(self):
        ios = parse.cdp_detail_text(fx.IOS_CDP_DETAIL)
        self.assertEqual(len(ios), 2)
        self.assertEqual(ios[0]["name"], "dsw-test-01.example.local")
        self.assertEqual((ios[0]["localPort"], ios[0]["remotePort"], ios[0]["ip"]), ("Te1/0/1", "Te1/1/4", "10.0.1.1"))
        self.assertIn("Switch", ios[0]["caps"])
        self.assertIn("Phone", ios[1]["caps"])
        sx = parse.cdp_detail_text(fx.SX_CDP_DETAIL)
        self.assertEqual(len(sx), 1)
        self.assertEqual((sx[0]["name"], sx[0]["ip"], sx[0]["localPort"], sx[0]["remotePort"]),
                         ("asw-test-02", "10.0.1.2", "Gi1/0/1", "Gi1/0/28"))

    def test_error_counters_use_receive_errors_and_skip_dashes(self):
        self.assertEqual(parse.error_counters(fx.IOS_COUNTERS_ERRORS), {"Gi1/0/1": 0, "Gi1/0/2": 150, "Te1/1/1": 0})
        self.assertEqual(parse.error_counters(fx.NXOS_COUNTERS_ERRORS), {"Eth1/1": 7})

    def test_poe_totals_three_formats(self):
        self.assertEqual(parse.poe_totals(fx.IOS_POE), (1480.0, 645.8))
        self.assertEqual(parse.poe_totals(fx.SX_POE_TABLE), (375.0, 300.0))
        self.assertEqual(parse.poe_totals(fx.SG300_POE_KV), (180.0, 10.0))
        self.assertIsNone(parse.poe_totals("% Invalid input detected"))

    def test_poe_ports_sx(self):
        rows = parse.poe_ports_sx(fx.SX_POE_TABLE)
        self.assertEqual([(r["port"], r["oper"], r["powerW"], r["class"]) for r in rows],
                         [("Gi1/0/1", "on", 12.4, "4"), ("Gi1/0/2", "off", 0.0, "0")])

    def test_cpu(self):
        self.assertEqual(parse.cpu_percent("CPU utilization for five seconds: 9%/1%; one minute: 7%; five minutes: 6%"), 7)
        self.assertEqual(parse.cpu_percent("five seconds: 12%; one minute: 85%; five minutes: 40%"), 85)
        self.assertEqual(parse.cpu_percent("CPU states  :   3.06% user,   2.80% kernel,   94.14% idle"), 6)
        self.assertIsNone(parse.cpu_percent(""))

    def test_aruba_tables(self):
        clients = parse.ap_clients(parse.fixed_table(fx.ARUBA_AP_ACTIVE))
        self.assertEqual(clients, {"ap-lib-01": 12, "ap-lib-02": 4})
        self.assertEqual(parse.ap_clients(parse.fixed_table(fx.ARUBA_AP_ACTIVE_RADIO)), {"ap-hall-01": 9})
        lldp = parse.ap_lldp(parse.fixed_table(fx.ARUBA_AP_LLDP))
        self.assertEqual(lldp, {"ap-lib-01": {"switch": "asw-test-02", "switchIp": "10.0.1.2", "port": "Gi1/0/1"}})

    def test_interface_status_fallback_reads_every_status(self):
        rows = {r["port"]: r for r in parse.ports_status_text(fx.NXOS_INTERFACE_STATUS)}
        self.assertEqual([rows[p]["status"] for p in ("Eth1/1", "Eth1/37", "Eth1/38", "Eth1/39", "Eth1/49")],
                         ["connected", "inactive", "err-disabled", "notconnect", "sfpAbsent"])
        self.assertEqual(rows["Eth1/1"]["name"], "UPLINK TO DSW 01")
        self.assertEqual((rows["Eth1/37"]["vlan"], rows["Eth1/1"]["speed"]), ("30", "10G"))
        self.assertEqual(rows["Eth1/37"]["name"], "")
        self.assertEqual(len(rows), 6)

    def test_aruba_ap_database_fallback(self):
        aps = parse.ap_database_table(parse.fixed_table(fx.ARUBA_AP_DATABASE_LONG))
        self.assertEqual([(a["name"], a["model"], a["ip"], a["serial"], a["mac"]) for a in aps],
                         [("ap-lib-01", "515", "10.60.0.11", "CNTEST0001", "20:4c:03:00:00:01"),
                          ("ap-hall-09", "303", "10.60.0.19", "CNTEST0009", "20:4c:03:00:00:09")])
        self.assertTrue(aps[0]["status"].startswith("Up"))

    def test_merge_neighbors_records_both_protocols(self):
        cdp = [parse._nbr("Te1/0/1", "dsw-test-01.example.local", "10.0.1.1", "Te1/1/4", caps="Router Switch")]
        lldp = [parse._nbr("TenGigabitEthernet1/0/1", "dsw-test-01", "", "Te1/1/4", chassis="00aa.bbcc.dd99")]
        merged = parse.merge_neighbors(cdp, lldp)
        self.assertEqual(len(merged), 1)
        self.assertEqual(merged[0]["via"], ["cdp", "lldp"])
        self.assertEqual(merged[0]["chassis"], "00:aa:bb:cc:dd:99")
        self.assertEqual(merged[0]["ip"], "10.0.1.1")


@unittest.skipIf(parse_output is None, "ntc-templates not installed (pip install -r requirements.txt)")
class TextFsmNormalisers(unittest.TestCase):
    def test_ios_version(self):
        v = parse.version_ios(parse_output(platform="cisco_ios", command="show version", data=fx.IOS_VERSION))
        self.assertEqual(v["hostname"], "asw-test-01")
        self.assertEqual(v["model"], "WS-C2960X-48FPD-L")
        self.assertEqual(v["serial"], "FOC0000TEST")
        self.assertEqual(v["version"], "15.2(7)E8")
        self.assertTrue(v["uptime"].startswith("1 year"))

    def test_ios_cdp_template_matches_custom_parser(self):
        rows = parse.cdp_textfsm(parse_output(platform="cisco_ios", command="show cdp neighbors detail", data=fx.IOS_CDP_DETAIL))
        custom = parse.cdp_detail_text(fx.IOS_CDP_DETAIL)
        key = lambda n: (n["name"], n["ip"], n["localPort"], n["remotePort"])
        self.assertEqual([key(n) for n in rows], [key(n) for n in custom])

    def test_template_failure_is_contained(self):
        """Regression (live Nexus capture, 2026-11-05): the NX-OS template raises on status 'inactive'.
        parse_rows must turn that into a warning, and the device record must still get its ports."""
        from netmon import devices
        warnings = []
        rows = devices.parse_rows("cisco_nxos", "show interface status", fx.NXOS_INTERFACE_STATUS, warnings)
        self.assertEqual(rows, [])
        self.assertTrue(warnings and "used fallback" in warnings[0], warnings)
        rec = {"warnings": [], "hostname": ""}
        results = {"ports": (rows, fx.NXOS_INTERFACE_STATUS)}
        devices._normalise(rec, "cisco_nxos", results)
        self.assertEqual(len(rec["ports"]), 6)

    def test_ios_poe_ports(self):
        rows = parse.poe_ports_ios(parse_output(platform="cisco_ios", command="show power inline", data=fx.IOS_POE))
        self.assertEqual([(r["port"], r["oper"], r["powerW"]) for r in rows], [("Gi1/0/1", "on", 15.4), ("Gi1/0/2", "off", 0.0)])


if __name__ == "__main__":
    unittest.main()
