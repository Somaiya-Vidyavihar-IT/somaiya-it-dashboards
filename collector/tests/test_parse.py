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
        self.assertEqual(lldp, {"ap-lib-01": {"switch": "asw-test-02", "switchIp": "10.0.1.2", "port": "Gi1/0/1"},
                                "ap-hall-07": {"switch": "asw-test-03-SG300", "switchIp": "", "port": "Gi22"}},
                         "switch comes from 'Chassis Name/ID', never the 'Neighbor' index; 'Not received' is blank")

    def test_sx_cdp_real_format_gives_ips(self):
        """Regression (live SG/CBS captures): 65 switch neighbours had no IP because SG/CBS print
        'Addresses: / IP x' and 'Primary Management Address: IP x,' rather than 'IP address: x'."""
        rows = {n["localPort"]: n for n in parse.cdp_detail_text(fx.SX_CDP_DETAIL_REAL)}
        self.assertEqual((rows["Gi21"]["name"], rows["Gi21"]["ip"], rows["Gi21"]["chassis"]),
                         ("switch000001", "10.0.19.85", "00:7e:95:00:00:01"), "SysName used when Device-ID is a MAC")
        self.assertEqual((rows["Gi23"]["ip"], rows["Gi23"]["remotePort"]), ("10.0.19.5", "Gi1/0/10"))

    def test_sx_lldp_summary_wraps_and_capabilities(self):
        from netmon import build
        rows = {n["localPort"]: n for n in parse.lldp_summary_sx(fx.SX_LLDP_SUMMARY_REAL)}
        self.assertEqual(sorted(rows), ["Gi16", "Gi21", "Gi24", "Gi25", "Gi27"], "a wrapped '-…' line must not end the table")
        self.assertEqual(rows["Gi25"]["name"], "asw-test-2ND-CLS1-AS02-SG350")
        self.assertEqual(rows["Gi24"]["name"], "LAB_2ndFloor_StaffRoom")
        self.assertEqual(rows["Gi16"]["remotePort"], "d8:cb:8a:00:12:10")
        self.assertEqual({p: build.neighbor_kind(n) for p, n in rows.items()},
                         {"Gi16": "other", "Gi21": "switch", "Gi24": "ap", "Gi25": "switch", "Gi27": "switch"})

    def test_sx_identity_both_firmware_families(self):
        sg = parse.version_sx([], [], [], fx.SG300_VERSION, fx.CBS_SYSTEM, fx.SG300_SYSTEM_ID)
        self.assertEqual((sg["version"], sg["serial"]), ("1.4.11.5", "DNITEST0001"))
        cbs = parse.version_sx([], [], [], fx.CBS_VERSION, fx.CBS_SYSTEM, fx.CBS_SYSTEM_ID)
        self.assertEqual((cbs["hostname"], cbs["model"], cbs["version"], cbs["serial"], cbs["mac"], cbs["uptime"]),
                         ("asw-test-cbs", "CBS350-24FP-4G", "3.2.1.1", "FOCTEST0002", "40:14:82:00:00:78", "86,05:32:10"))

    def test_c1300_model_is_not_catalyst(self):
        """Regression (live, 2026-11-06): C1300 descriptions start with 'Catalyst', so every C1300
        showed model 'Catalyst'. The per-unit Type table wins; otherwise skip the family word."""
        desc_only = "System Description:      Catalyst 1300-48P-4X 48-Port Gigabit PoE+ Managed Switch\n"
        self.assertEqual(parse.version_sx([], [], [], "", desc_only, "")["model"], "C1300-48P-4X")
        with_unit = desc_only + "\nUnit          Type          \n---- ---------------------- \n 1        C1300-48P-4X  \n"
        self.assertEqual(parse.version_sx([], [], [], "", with_unit, "")["model"], "C1300-48P-4X")
        self.assertEqual(parse.sx_model_from_description("SG300-28 28-Port Gigabit Managed Switch"), "SG300-28")
        self.assertEqual(parse.sx_model_from_description(""), "")

    def test_sx_lldp_port_detail_both_layouts(self):
        a = parse.lldp_port_detail_sx(fx.SX_LLDP_PORT_SIMPLE)
        self.assertEqual((a["ip"], a["caps"], a["name"]), ("10.0.28.21", "Bridge, Router", "asw-test-hpc-01"))
        b = parse.lldp_port_detail_sx(fx.SX_LLDP_PORT_BLOCK)
        self.assertEqual((b["ip"], b["caps"]), ("10.0.28.22", "B, R"), "IPv6 and ifIndex lines are skipped")
        self.assertEqual(parse.lldp_port_detail_sx("% No neighbors found")["ip"], "")

    def test_lookup_fills_ips_for_lldp_only_switches_and_stays_read_only(self):
        """Regression (live): 68 switches were seen only in SG/CBS LLDP summaries, which carry no IP."""
        from netmon import devices
        rec = {"warnings": [], "neighbors": parse.merge_neighbors([], parse.lldp_summary_sx(fx.SX_LLDP_SUMMARY_REAL))}
        bad = parse._nbr("gi1; reload", "evil", "", "gi1", caps="B")  # device-supplied text must never become a command
        bad["via"] = ["lldp"]
        rec["neighbors"].append(bad)
        sent, raw = [], {}

        class Conn:
            def send_command(self, cmd, read_timeout=None):
                sent.append(cmd)
                return fx.SX_LLDP_PORT_SIMPLE if cmd.endswith("gi21") else fx.SX_LLDP_PORT_BLOCK

        devices.lldp_details_sx(Conn(), rec, 10, raw)
        self.assertEqual(sent, ["show lldp neighbors gi21", "show lldp neighbors gi25", "show lldp neighbors gi27"],
                         "switches only: not the AP (W), the 'O' device, or a malformed port")
        ips = {n["localPort"]: n["ip"] for n in rec["neighbors"]}
        self.assertEqual((ips["Gi21"], ips["Gi25"], ips["Gi24"]), ("10.0.28.21", "10.0.28.22", ""))
        self.assertEqual(sorted(raw), sorted(sent), "capture keeps the raw per-port output")
        self.assertTrue(all(c.startswith("show lldp neighbors ") for c in sent))

    def test_sg500x_per_unit_tables_and_prompt_hostname(self):
        """Regression (live SG500X distributions): name, model and version came out empty."""
        ident = parse.version_sx([], [], [], fx.SG500X_VERSION, fx.SG500X_SYSTEM, fx.CBS_SYSTEM_ID)
        self.assertEqual((ident["model"], ident["version"], ident["uptime"], ident["serial"]),
                         ("SG500X-24", "1.4.11.2", "05,20:52:32", "FOCTEST0002"))
        self.assertEqual(ident["hostname"], "")
        from unittest import mock
        from netmon import config, devices
        cfg = config.validate({"collector": {"switch_username": "u"}, "subnets": [{"cidr": "10.0.0.0/24"}]})
        conn = mock.Mock(base_prompt="dsw-test-sg500x")
        texts = {"show version": fx.SG500X_VERSION, "show system": fx.SG500X_SYSTEM}
        conn.send_command.side_effect = lambda cmd, **kw: texts.get(cmd, "")
        with mock.patch.object(devices, "_connect", return_value=conn):
            rec = devices.collect("10.0.0.9", "cisco_s300", "u", "p", cfg)
        self.assertEqual((rec["hostname"], rec["model"]), ("dsw-test-sg500x", "SG500X-24"), "hostname from the CLI prompt")

    def test_collect_logs_every_command_and_how_it_was_read(self):
        """The dashboard lists the commands sent to each switch so the team can see which to change."""
        from unittest import mock
        from netmon import config, devices
        cfg = config.validate({"collector": {"switch_username": "u"}, "subnets": [{"cidr": "10.0.0.0/24"}]})
        conn = mock.Mock(base_prompt="asw-test")

        def answer(cmd, **kw):
            if cmd == "show cpu utilization":
                raise TimeoutError("read timed out")
            if cmd == "show power inline":
                return "                                     ^\n% Invalid input detected at '^' marker."
            return {"show version": fx.SG500X_VERSION, "show system": fx.SG500X_SYSTEM}.get(cmd, "")
        conn.send_command.side_effect = answer
        with mock.patch.object(devices, "_connect", return_value=conn):
            rec = devices.collect("10.0.0.9", "cisco_s300", "u", "p", cfg)
        log = {c["command"]: c for c in rec["commands"]}
        self.assertEqual([c["command"] for c in rec["commands"]][:2], devices.SESSION_SETUP["cisco_s300"])
        self.assertEqual(log["terminal datadump"]["result"], "setup")
        sent = [c[0][0] for c in conn.send_command.call_args_list]
        self.assertEqual([c for c in log if c.startswith("show ")], sent, "every command sent is listed, in order")
        self.assertEqual(log["show cpu utilization"]["result"], "failed")
        self.assertEqual(log["show power inline"]["result"], "rejected")
        self.assertIn("Invalid input", log["show power inline"]["detail"])
        self.assertEqual(log["show version"]["result"], "ok")
        self.assertGreater(log["show version"]["lines"], 3)
        self.assertTrue(all(c["purpose"] for c in rec["commands"]))

    def test_cbs_poe_table(self):
        self.assertEqual(parse.poe_totals(fx.CBS_POE), (370.0, 183.0))
        ports = {p["port"]: (p["oper"], p["powerW"], p["class"]) for p in parse.poe_ports_sx(fx.CBS_POE)}
        self.assertEqual(ports, {"Gi1": ("fault", 0.0, ""), "Gi3": ("on", 10.138, "4"), "Gi12": ("on", 0.798, "0"),
                                 "Gi14": ("searching", 0.0, "")})

    def test_mac_device_id_becomes_chassis(self):
        n = parse._nbr("Gi1/0/1", "5017ff000001", "", "gi28")
        self.assertEqual(n["chassis"], "50:17:ff:00:00:01")
        self.assertEqual(parse._nbr("Gi1/0/1", "dsw-01", "", "gi28")["chassis"], "")

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

    def test_nxos_cdp_interface_address_ip_is_kept(self):
        """Regression (live Nexus capture): the template returned no IP for 18 of 33 neighbours that
        only send 'Interface address(es)', so the crawl could not reach their institutes."""
        rows = parse_output(platform="cisco_nxos", command="show cdp neighbors detail", data=fx.NXOS_CDP_INTERFACE_ADDRESS)
        self.assertTrue(all(not parse.cdp_textfsm(rows)[i]["ip"] for i in range(len(rows))), "template still drops it")
        from netmon import devices
        rec = {"warnings": [], "hostname": ""}
        devices._normalise(rec, "cisco_nxos", {"cdp": (rows, fx.NXOS_CDP_INTERFACE_ADDRESS)})
        got = {n["name"]: (n["ip"], n["chassis"]) for n in rec["neighbors"]}
        self.assertEqual(got["dsw-test-sg5"], ("10.0.21.5", ""))
        # MAC Device-ID with a System Name line: named by hostname, MAC kept as chassis, listed once.
        self.assertEqual(got["asw-test-sg300"], ("10.0.21.9", "50:17:ff:00:00:01"))
        # MAC Device-ID and no System Name: the MAC is the name and the chassis.
        self.assertEqual(got["5017ff000002"], ("10.0.21.10", "50:17:ff:00:00:02"))
        self.assertEqual(len(rec["neighbors"]), 3, [n["name"] for n in rec["neighbors"]])
        self.assertEqual(len({n["localPort"] for n in rec["neighbors"]}), 3, "one entry per port")

    def test_ap_database_reads_every_block(self):
        """Regression (live WLC, 348 APs): only the first ~32-row block was read, and the template put
        'Down' into Flags when Flags was empty, leaving Status blank."""
        text = fx.ARUBA_AP_DATABASE_BLOCKS
        rows = parse_output(platform="aruba_os", command="show ap database long", data=text)
        best = parse.ap_database_best(rows, text)
        self.assertEqual([(a["name"], a["status"].split()[0], a["flags"]) for a in best],
                         [("ap-blk1-up", "Up", ""), ("ap-blk1-down", "Down", ""),
                          ("LAB Block Two Corridor AP", "Up", ""), ("ap-blk2-flagged", "Down", "N")])
        self.assertEqual(best[2]["group"], "campus-west")

    def test_wlc_warns_when_ap_count_differs_from_its_total(self):
        from unittest import mock
        from netmon import config, wlc
        cfg = config.validate({"collector": {"switch_username": "u"}, "subnets": [{"cidr": "10.0.0.0/24"}]})
        good = fx.ARUBA_AP_DATABASE_BLOCKS
        short = good.replace("Total APs:4", "Total APs:5")
        for text, warned in ((good, False), (short, True)):
            conn = mock.Mock()
            conn.send_command.side_effect = lambda cmd, read_timeout=None, t=text: t if "database" in cmd else ""
            with mock.patch("netmon.wlc._connect", return_value=conn):
                rec = wlc.collect("10.0.0.5", "u", "p", cfg)
            self.assertEqual(len(rec["aps"]), 4)
            self.assertEqual(any("Total APs: 5" in w for w in rec["warnings"]), warned)

    def test_ap_database_keeps_names_with_spaces(self):
        text = fx.ARUBA_AP_DATABASE_SPACES
        rows = parse_output(platform="aruba_os", command="show ap database long", data=text)
        best = parse.ap_database_best(rows, text)
        self.assertEqual([a["name"] for a in best], ["ap-lib-01", "LAB_ Research Room", "LAB_IT LAB"])
        self.assertEqual(best[2]["mac"], "20:4c:03:00:00:32")

    def test_ios_poe_ports(self):
        rows = parse.poe_ports_ios(parse_output(platform="cisco_ios", command="show power inline", data=fx.IOS_POE))
        self.assertEqual([(r["port"], r["oper"], r["powerW"]) for r in rows], [("Gi1/0/1", "on", 15.4), ("Gi1/0/2", "off", 0.0)])


if __name__ == "__main__":
    unittest.main()
