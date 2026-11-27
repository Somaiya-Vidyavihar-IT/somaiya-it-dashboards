import os
import re
import tempfile
import time
import unittest
from datetime import datetime, timedelta, timezone
from unittest import mock

from netmon import config, parse, uplinks
from netmon.store import Store
from tests import fixtures as fx

T0 = datetime(2026, 11, 25, 4, 0, 0, tzinfo=timezone.utc)
SAFE = re.compile(r"[A-Za-z][A-Za-z\-]*\d+(/\d+){0,3}(\.\d+)?")


def nb(port, name, ip="", kind="switch", platform="", peer=""):
    return {"localPort": port, "name": name, "ip": ip, "remotePort": "Gi1/0/1", "platform": platform,
            "via": ["cdp"], "kind": kind, "peer": peer}


def dev(ip, role, platform, neighbors, status="ok", hostname=None):
    return {"ip": ip, "hostname": hostname or f"sw-{ip.rsplit('.', 1)[1]}", "institute": "Engineering", "role": role,
            "platform": platform, "status": status, "neighbors": neighbors}


def iface(port, out_bps, bw_kbps=1000000, platform="cisco_ios"):
    if platform == "cisco_nxos":
        return (f"{port} is up\n  MTU 1500 bytes, BW {bw_kbps} Kbit , DLY 10 usec\n"
                f"    30 seconds output rate {out_bps} bits/sec, 10 packets/sec\n")
    return (f"{port} is up, line protocol is up (connected)\n  MTU 1500 bytes, BW {bw_kbps} Kbit/sec, DLY 10 usec,\n"
            f"  5 minute output rate {out_bps} bits/sec, 10 packets/sec\n")


class NetmikoAuthenticationException(Exception):
    """Same class name as netmiko's, so devices._short_error reports it the same way."""


class FakeConn:
    """Answers show commands from a dict; anything else (or unknown) returns an IOS-style rejection."""

    def __init__(self, answers):
        self.answers, self.sent, self.closed = answers, [], False

    def send_command(self, cmd, **kw):
        self.sent.append(cmd)
        answer = self.answers(cmd) if callable(self.answers) else self.answers.get(cmd)
        if isinstance(answer, Exception):
            raise answer
        return "% Invalid input detected at '^' marker." if answer is None else answer

    def disconnect(self):
        self.closed = True


class Selection(unittest.TestCase):
    def test_core_selects_only_switch_neighbours(self):
        d = dev("10.0.0.1", "core", "cisco_nxos", [
            nb("Eth1/1", "dsw-test-01(FDO1)", "10.0.1.1", platform="N9K-C93180YC-FX", peer="10.0.1.1"),
            nb("Eth1/2", "aruba-test-sw", "10.0.1.9", platform="Aruba JL259A 2930F"),
            nb("Eth1/3", "ap-test-01", "10.60.0.11", kind="ap", platform="Cisco AIR-AP"),
            nb("Eth1/4", "SEP00AABB000001", "10.70.0.1", kind="phone", platform="Cisco IP Phone"),
            nb("Eth1/5", "server-01", "10.0.9.9", kind="other"),
            nb("Eth1/1", "dsw-test-01", "10.0.1.1", peer="10.0.1.1")])  # same port seen twice (CDP + LLDP)
        self.assertEqual(uplinks.select_ports(d), [
            {"port": "Eth1/1", "neighbor": "dsw-test-01(FDO1)", "neighborIp": "10.0.1.1"},
            {"port": "Eth1/2", "neighbor": "aruba-test-sw", "neighborIp": "10.0.1.9"}])
        self.assertEqual([p["port"] for p in uplinks.select_ports(dict(d, role="distribution"))], ["Eth1/1", "Eth1/2"])

    def test_access_selects_cisco_neighbours_only(self):
        d = dev("10.0.1.2", "access", "cisco_ios", [
            nb("Gi1/0/49", "dsw-test-01", "10.0.1.1", peer="10.0.1.1"),                 # a collected switch
            nb("Gi1/0/50", "asw-test-07", "10.0.1.7", platform="cisco WS-C2960X-48FPS-L"),  # Cisco by platform
            nb("Gi1/0/51", "Cisco-test-sg", "10.0.1.8"),                                    # Cisco by name
            nb("Gi1/0/47", "aruba-test-sw", "10.0.1.9", platform="Aruba JL259A 2930F"),    # non-Cisco switch
            nb("Gi1/0/46", "ap-test-01", "10.60.0.11", kind="ap", platform="Aruba AP-515"),  # Aruba AP
            nb("Gi1/0/45", "hp-test-sw", "10.0.1.10", peer="10.0.1.10")])                  # peer not collected
        ports = [p["port"] for p in uplinks.select_ports(d, collected={"10.0.1.1"})]
        self.assertEqual(ports, ["Gi1/0/49", "Gi1/0/50", "Gi1/0/51"])
        self.assertEqual([p["port"] for p in uplinks.select_ports(dict(d, role="unknown"), collected={"10.0.1.1"})], ports)

    def test_unsafe_port_names_are_never_sent(self):
        d = dev("10.0.0.1", "core", "cisco_ios", [
            nb("00:aa:bb:cc:dd:ee", "dsw-mac"), nb("Gi1/0/1;x", "dsw-inject"), nb("", "dsw-blank"),
            nb("Gi1/0/1\nreload", "dsw-newline"), nb("Gi1/0/1 | include", "dsw-pipe"), nb(None, "dsw-none"),
            nb("Gi1/0/48", "dsw-good")])
        self.assertEqual([p["port"] for p in uplinks.select_ports(d)], ["Gi1/0/48"])
        for bad in ("00:aa:bb:cc:dd:ee", "Gi1/0/1;x", "", "Gi1/0/1\n", "Gi1/0/1 x", "1/0/1"):
            with self.assertRaises(ValueError, msg=repr(bad)):
                uplinks.command("cisco_ios", "interface", bad)
        self.assertEqual(uplinks.command("cisco_ios", "interface", "Gi1/0/48"), "show interfaces Gi1/0/48")
        self.assertEqual(uplinks.command("cisco_nxos", "interface", "Eth1/49"), "show interface Eth1/49")
        self.assertEqual(uplinks.command("cisco_ios", "interface", "Te1/1/1.100"), "show interfaces Te1/1/1.100")


class Due(unittest.TestCase):
    def test_due_10_and_30_minutes(self):
        cfg = config.validate({"collector": {"switch_username": "u"}, "subnets": [{"cidr": "10.0.0.0/24"}]})
        at = lambda minutes: {"10.0.0.1": (T0 - timedelta(minutes=minutes)).isoformat().replace("+00:00", "Z")}
        for role in ("core", "distribution"):
            d = dev("10.0.0.1", role, "cisco_ios", [])
            self.assertTrue(uplinks.due(d, {}, T0, cfg), "never polled")
            self.assertTrue(uplinks.due(d, at(10), T0, cfg))
            self.assertTrue(uplinks.due(d, at(9.75), T0, cfg), "a scheduler start a few seconds early still counts")
            self.assertFalse(uplinks.due(d, at(9), T0, cfg))
        for role in ("access", "unknown"):
            d = dev("10.0.0.1", role, "cisco_ios", [])
            self.assertTrue(uplinks.due(d, {}, T0, cfg))
            self.assertTrue(uplinks.due(d, at(30), T0, cfg))
            self.assertFalse(uplinks.due(d, at(20), T0, cfg))
            self.assertFalse(uplinks.due(d, at(29), T0, cfg))
        self.assertTrue(uplinks.due(dev("10.0.0.1", "core", "cisco_ios", []), {"10.0.0.1": "garbage"}, T0, cfg))


class Poll(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.cfg = config.validate({"collector": {"switch_username": "u", "data_dir": self.tmp.name},
                                    "subnets": [{"cidr": "10.0.0.0/16"}]})
        self.store = Store(self.tmp.name)
        self.sets = [("set1", "u1", "p1"), ("set2", "u2", "p2")]
        self.conns, self.logins = {}, []

    def snapshot(self, devices, creds=None, platforms=None):
        self.store.write({"devices": devices}, "latest.json")
        self.store.write(creds if creds is not None else {d["ip"]: "set1" for d in devices}, "state", "credentials.json")
        self.store.write(platforms or {}, "state", "platforms.json")

    def connect(self, platform, ip, username, password, cfg):
        self.logins.append((platform, ip, username, password))
        conn = self.conns.get(ip)
        if isinstance(conn, Exception):
            raise conn
        return conn

    def poll(self, now=T0):
        with mock.patch("netmon.creds.switch_sets", return_value=self.sets), \
                mock.patch("netmon.devices._connect", side_effect=self.connect):
            code = uplinks.poll(self.cfg, now=now)
        return code, self.store.read("uplinks.json")

    def test_alert_at_75_and_clears_below(self):
        core = dev("10.0.0.1", "core", "cisco_ios", [nb("Gi1/0/48", "dsw-test-01", "10.0.1.1", peer="10.0.1.1")])
        self.snapshot([core])
        rate = {"out": 750000000}
        self.conns["10.0.0.1"] = FakeConn(lambda cmd: "" if cmd == "show etherchannel summary"
                                          else iface("Gi1/0/48", rate["out"]) if cmd == "show interfaces Gi1/0/48" else None)
        code, data = self.poll()
        self.assertEqual(code, 0)
        self.assertEqual((data["threshold"], data["checkedAt"]), (75, "2026-11-25T04:00:00Z"))
        row = data["ports"][0]
        self.assertEqual({k: row[k] for k in ("ip", "hostname", "institute", "role", "port", "kind", "bundle", "neighbor",
                                              "bwMbps", "outBps", "outPct", "interval", "state", "alert")},
                         {"ip": "10.0.0.1", "hostname": "sw-1", "institute": "Engineering", "role": "core",
                          "port": "Gi1/0/48", "kind": "port", "bundle": "", "neighbor": "dsw-test-01", "bwMbps": 1000,
                          "outBps": 750000000, "outPct": 75.0, "interval": "5 minute", "state": "ok", "alert": True})
        self.assertTrue(row["readAt"])
        rate["out"] = 749000000
        _, data = self.poll(T0 + timedelta(minutes=10))
        self.assertEqual((data["ports"][0]["outPct"], data["ports"][0]["alert"]), (74.9, False), "clears below 75")
        self.cfg["collector"]["uplink_threshold"] = 70
        _, data = self.poll(T0 + timedelta(minutes=20))
        self.assertEqual((data["threshold"], data["ports"][0]["alert"]), (70, True), "threshold from the config")

    def test_bundle_and_members_both_reported(self):
        core = dev("10.0.0.1", "core", "cisco_ios", [
            nb("Gi1/0/49", "dsw-test-01", "10.0.1.1", peer="10.0.1.1"),   # member of Po1; Gi1/0/50 has no CDP entry
            nb("Te1/1/1", "dsw-test-02", "10.0.1.2", peer="10.0.1.2"),    # member of Po2 (4 members)
            nb("Po1", "dsw-test-01", "10.0.1.1", peer="10.0.1.1"),        # LLDP on the bundle itself
            nb("Gi1/0/48", "asw-test-03", "10.0.1.3", peer="10.0.1.3")])  # not bundled
        self.snapshot([core])
        answers = {"show etherchannel summary": fx.IOS_ETHERCHANNEL_SUMMARY,
                   "show interfaces Po1": iface("Port-channel1", 1600000000, 2000000),
                   "show interfaces Gi1/0/49": iface("Gi1/0/49", 900000000),
                   "show interfaces Gi1/0/50": iface("Gi1/0/50", 700000000),
                   "show interfaces Po2": iface("Port-channel2", 100000000, 40000000),
                   "show interfaces Te1/1/1": iface("Te1/1/1", 25000000, 10000000),
                   "show interfaces Te1/1/2": iface("Te1/1/2", 25000000, 10000000),
                   "show interfaces Te1/1/3": RuntimeError("read timed out"),
                   "show interfaces Gi1/0/48": iface("Gi1/0/48", 1000)}
        conn = self.conns["10.0.0.1"] = FakeConn(answers)
        code, data = self.poll()
        self.assertEqual(code, 0)
        rows = [(r["port"], r["kind"], r["bundle"], r["state"], r["alert"]) for r in data["ports"]]
        self.assertEqual(rows, [("Po1", "bundle", "Po1", "ok", True), ("Gi1/0/49", "member", "Po1", "ok", True),
                                ("Gi1/0/50", "member", "Po1", "ok", False),
                                ("Po2", "bundle", "Po2", "ok", False), ("Te1/1/1", "member", "Po2", "ok", False),
                                ("Te1/1/2", "member", "Po2", "ok", False), ("Te1/1/3", "member", "Po2", "failed", False),
                                ("Te1/1/4", "member", "Po2", "failed", False), ("Gi1/0/48", "port", "", "ok", False)])
        by = {r["port"]: r for r in data["ports"]}
        self.assertEqual((by["Po1"]["outPct"], by["Po1"]["bwMbps"]), (80.0, 2000))
        self.assertEqual(by["Po1"]["neighbor"], "dsw-test-01")
        self.assertEqual(by["Gi1/0/50"]["neighbor"], "dsw-test-01", "a member without its own CDP entry gets the bundle's")
        self.assertIn("read timed out", by["Te1/1/3"]["detail"])
        self.assertIn("Invalid input", by["Te1/1/4"]["detail"], "a rejected command is reported as failed")
        self.assertEqual(conn.sent[0], "show etherchannel summary", "bundles are read first")
        self.assertEqual(len(conn.sent), len(set(conn.sent)), "each port is read once")
        self.assertTrue(conn.closed)

    def test_nxos_bundles(self):
        core = dev("10.0.0.1", "core", "cisco_nxos", [nb("Eth1/49", "dsw-test-01", "10.0.1.1", peer="10.0.1.1")])
        self.snapshot([core], platforms={"10.0.0.1": "cisco_nxos"})
        conn = self.conns["10.0.0.1"] = FakeConn(lambda cmd: fx.NXOS_PORT_CHANNEL_SUMMARY if cmd == "show port-channel summary"
                                                 else iface("x", 5000000000, 20000000, "cisco_nxos") if cmd == "show interface Po10"
                                                 else fx.NXOS_SHOW_INTERFACE if cmd.startswith("show interface Eth") else None)
        _, data = self.poll()
        self.assertEqual([(r["port"], r["kind"], r["outPct"], r["alert"]) for r in data["ports"]],
                         [("Po10", "bundle", 25.0, False), ("Eth1/49", "member", 77.0, True), ("Eth1/50", "member", 77.0, True)])
        self.assertEqual(conn.sent, ["show port-channel summary", "show interface Po10", "show interface Eth1/49",
                                     "show interface Eth1/50"])

    def sx_poll(self, second_counters, status=fx.SX_STATUS_CBS350):
        """One round with an SG/CBS switch whose counters read fx.SX_COUNTERS_CBS350, then second_counters."""
        asw = dev("10.0.1.5", "access", "cisco_s300", [
            nb("Gi24", "dsw-test-01", "10.0.1.1", peer="10.0.1.1"),           # uplink, up at 1000 Mbps
            nb("Gi2", "asw-test-09", "10.0.1.9", platform="Cisco CBS350"),     # port down: no speed
            nb("Gi49", "asw-test-10", "10.0.1.10", platform="Cisco CBS350"),   # not in the counters
            nb("Gi50", "aruba-test", "10.0.1.11", platform="Aruba 2930F")])     # not Cisco: not an uplink
        dsw = dev("10.0.1.1", "distribution", "cisco_ios", [])
        self.snapshot([asw, dsw], platforms={"10.0.1.5": "cisco_s300"})
        readings = iter([fx.SX_COUNTERS_CBS350, second_counters])
        conn = self.conns["10.0.1.5"] = FakeConn(lambda cmd: status if cmd == "show interfaces status"
                                                 else next(readings) if cmd == "show interfaces counters" else None)
        now, slept = [100.0], []

        def sleep(seconds):
            slept.append(seconds)
            now[0] += seconds

        with mock.patch("netmon.uplinks._sleep", side_effect=sleep), mock.patch("netmon.uplinks._clock", side_effect=lambda: now[0]):
            code, data = self.poll()
        self.assertEqual(code, 0)
        return conn, slept, [r for r in data["ports"] if r["ip"] == "10.0.1.5"]

    def test_sx_load_from_two_readings_30_seconds_apart(self):
        """Network team choice A (2026-11-27): bytes sent between two readings 30 s apart, over the port speed."""
        # 3,000,000,000 bytes in 30 s = 800 Mbit/s = 80% of 1000 Mbps: over the 75% threshold.
        conn, slept, rows = self.sx_poll(fx.SX_COUNTERS_CBS350.replace("37884659241", "40884659241"))
        self.assertEqual(conn.sent, ["show interfaces status", "show interfaces counters", "show interfaces counters"])
        self.assertEqual(slept, [30])
        self.assertTrue(conn.closed)
        self.assertEqual([(r["port"], r["kind"], r["state"]) for r in rows],
                         [("Gi24", "port", "ok"), ("Gi2", "port", "not-read"), ("Gi49", "port", "not-read")])
        by = {r["port"]: r for r in rows}
        self.assertEqual({k: by["Gi24"][k] for k in ("bwMbps", "outBps", "outPct", "interval", "alert", "neighbor")},
                         {"bwMbps": 1000, "outBps": 800000000, "outPct": 80.0, "interval": "30 seconds", "alert": True,
                          "neighbor": "dsw-test-01"})
        self.assertIn("speed", by["Gi2"]["detail"])
        self.assertIn("counters", by["Gi49"]["detail"])
        self.assertEqual((by["Gi2"]["alert"], by["Gi2"]["outPct"]), (False, None))

    def test_sx_below_threshold(self):
        _, _, rows = self.sx_poll(fx.SX_COUNTERS_CBS350.replace("37884659241", "37984659241"))
        self.assertEqual((rows[0]["outPct"], rows[0]["alert"]), (2.7, False))  # 100,000,000 bytes in 30 s

    def test_sx_counter_reset_is_not_a_reading(self):
        _, _, rows = self.sx_poll(fx.SX_COUNTERS_CBS350.replace("37884659241", "1000"))
        self.assertEqual((rows[0]["state"], rows[0]["alert"], rows[0]["outPct"]), ("not-read", False, None))
        self.assertIn("reset", rows[0]["detail"])

    def test_sx_rejected_command_fails_the_switch_rows(self):
        conn, slept, rows = self.sx_poll(fx.SX_COUNTERS_CBS350, status="% Unrecognized command\n")
        self.assertEqual({(r["state"], r["detail"]) for r in rows}, {("failed", "% Unrecognized command")})
        self.assertEqual((conn.sent, slept), (["show interfaces status"], []), "stops at the first refused command")
        self.assertTrue(conn.closed)

    def test_sx_logs_in_with_the_remembered_set_and_sends_only_show(self):
        conn, _, _ = self.sx_poll(fx.SX_COUNTERS_CBS350)
        self.assertEqual(self.logins, [("cisco_s300", "10.0.1.5", "u1", "p1")])
        self.assertTrue(all(c.startswith("show ") for c in conn.sent))

    def test_only_remembered_credential_set_is_used(self):
        mk = lambda ip: dev(ip, "core", "cisco_ios", [nb("Gi1/0/48", "dsw-test-01", "10.0.1.1", peer="10.0.1.1")])
        devices_ = [mk("10.0.0.1"), mk("10.0.0.2"), mk("10.0.0.3"), mk("10.0.0.4"),
                    dict(mk("10.0.0.5"), status="failed"), dict(mk("10.0.0.6"), status="down")]
        creds = {"10.0.0.1": "set2", "10.0.0.3": "retired-set", "10.0.0.4": "set1", "10.0.0.5": "set1", "10.0.0.6": "set1"}
        self.snapshot(devices_, creds=creds)
        ok = lambda cmd: "" if cmd == "show etherchannel summary" else iface("Gi1/0/48", 1000)
        self.conns.update({"10.0.0.1": FakeConn(ok), "10.0.0.4": NetmikoAuthenticationException("Authentication failed.")})
        code, data = self.poll()
        self.assertEqual(code, 0)
        self.assertEqual(sorted(self.logins), [("cisco_ios", "10.0.0.1", "u2", "p2"), ("cisco_ios", "10.0.0.4", "u1", "p1")],
                         "only the set that worked last time; R2: only status ok switches")
        by = {r["ip"]: r for r in data["ports"]}
        self.assertEqual(set(by), {"10.0.0.1", "10.0.0.2", "10.0.0.3", "10.0.0.4"})
        self.assertEqual(by["10.0.0.1"]["state"], "ok")
        for ip in ("10.0.0.2", "10.0.0.3"):
            self.assertEqual((by[ip]["state"], by[ip]["detail"]), ("not-read", "No working login known yet"))
        self.assertEqual((by["10.0.0.4"]["state"], by["10.0.0.4"]["detail"]), ("failed", "SSH authentication failed"))
        self.assertEqual(self.store.read("state", "credentials.json"), creds, "the poller never changes the remembered sets")

    def test_rows_of_switches_not_due_are_carried_over(self):
        core = dev("10.0.0.1", "core", "cisco_ios", [nb("Gi1/0/48", "dsw-test-01", "10.0.1.1", peer="10.0.1.1")])
        asw = dev("10.0.1.2", "access", "cisco_ios", [nb("Gi1/0/49", "dsw-test-01", "10.0.1.1", peer="10.0.1.1")])
        dsw = dev("10.0.1.1", "distribution", "cisco_ios", [])
        self.snapshot([core, asw, dsw])
        rate = {"out": 100000000}
        answer = lambda cmd: "" if cmd == "show etherchannel summary" else iface("x", rate["out"])
        self.conns.update({"10.0.0.1": FakeConn(answer), "10.0.1.2": FakeConn(answer)})
        self.poll()
        self.assertEqual(sorted(ip for _, ip, *_ in self.logins), ["10.0.0.1", "10.0.1.2"])
        self.logins.clear()
        rate["out"] = 900000000
        _, data = self.poll(T0 + timedelta(minutes=10))
        self.assertEqual([ip for _, ip, *_ in self.logins], ["10.0.0.1"], "access switch not due after 10 minutes")
        by = {r["ip"]: r for r in data["ports"]}
        self.assertEqual((by["10.0.0.1"]["outPct"], by["10.0.0.1"]["readAt"]), (90.0, "2026-11-25T04:10:00Z"))
        self.assertEqual((by["10.0.1.2"]["outPct"], by["10.0.1.2"]["readAt"]), (10.0, "2026-11-25T04:00:00Z"), "carried over")
        self.logins.clear()
        _, data = self.poll(T0 + timedelta(minutes=30))
        self.assertEqual(sorted(ip for _, ip, *_ in self.logins), ["10.0.0.1", "10.0.1.2"])
        self.store.write({"devices": [core]}, "latest.json")
        _, data = self.poll(T0 + timedelta(minutes=40))
        self.assertEqual({r["ip"] for r in data["ports"]}, {"10.0.0.1"}, "switches gone from the snapshot are dropped")

    def test_lock_prevents_overlap(self):
        self.snapshot([dev("10.0.0.1", "core", "cisco_ios", [nb("Gi1/0/48", "dsw-test-01")])])
        self.assertTrue(self.store.acquire({"test": True}, "uplinks.lock"))
        code, data = self.poll()
        self.assertEqual((code, data, self.logins), (2, None, []))
        self.assertTrue(self.store.running("uplinks.lock"), "the other run's lock is left alone")
        self.assertTrue(self.store.acquire({"test": True}, "run.lock"), "independent of a full run's lock")
        old = time.time() - 11 * 60  # a poller killed at the 9-minute task limit leaves its lock behind
        os.utime(self.store.path("state", "uplinks.lock"), (old, old))
        self.conns["10.0.0.1"] = FakeConn(lambda cmd: "" if "etherchannel" in cmd else iface("x", 1))
        code, _ = self.poll()
        self.assertEqual(code, 0, "a stale lock is taken over")
        self.assertFalse(self.store.running("uplinks.lock"), "released after the round")
        self.assertTrue(self.store.running("run.lock"))

    def test_commands_are_read_only(self):
        for platform, cmds in uplinks.UPLINK_COMMANDS.items():
            for key, cmd in cmds.items():
                self.assertTrue(cmd.startswith("show "), (platform, key, cmd))
        self.assertEqual(set(uplinks.UPLINK_COMMANDS), {"cisco_ios", "cisco_nxos", "cisco_s300"})
        core = dev("10.0.0.1", "core", "cisco_ios", [nb("Gi1/0/49", "dsw-test-01"), nb("Gi1/0/1;reload", "x"),
                                                     nb("Te1/1/1", "dsw-test-02")])
        self.snapshot([core])
        # Member names come from the switch too: an unexpected one is never put into a command.
        conn = self.conns["10.0.0.1"] = FakeConn(lambda cmd: "" if "etherchannel" in cmd else iface("x", 1))
        with mock.patch.object(parse, "port_channels", return_value={"Po2": ["Te1/1/1", "Te1/1/4;x", "Te1/1/5 | x", ""]}):
            _, data = self.poll()
        self.assertEqual([r["port"] for r in data["ports"]], ["Gi1/0/49", "Po2", "Te1/1/1"])
        self.assertTrue(conn.sent)
        for cmd in conn.sent:
            self.assertTrue(cmd.startswith("show "), cmd)
            if cmd != "show etherchannel summary":
                self.assertRegex(cmd, r"^show interfaces " + SAFE.pattern + r"$")
                self.assertTrue(SAFE.fullmatch(cmd.split(" ", 2)[2]), cmd)
        self.assertNotIn("show interfaces Te1/1/5", conn.sent)

    def test_cli_command_runs_one_round(self):
        from netmon import __main__ as cli
        with mock.patch("netmon.config.load", return_value=self.cfg), mock.patch("logging.basicConfig"), \
                mock.patch("logging.FileHandler"), mock.patch("netmon.uplinks.poll", return_value=2) as poll:
            self.assertEqual(cli.main(["uplinks"]), 2)
        poll.assert_called_once_with(self.cfg)

    # ---- IMPORTANT 1: a long round is never lost ----
    def _switches(self, n, role="core"):
        devs = [dev(f"10.0.0.{i}", role, "cisco_ios", [nb("Gi1/0/48", "dsw-test-01", "10.0.1.1", peer="10.0.1.1")])
                for i in range(1, n + 1)]
        self.snapshot(devs)
        answer = lambda cmd: "" if cmd == "show etherchannel summary" else iface("Gi1/0/48", 100000000)
        for d in devs:
            self.conns[d["ip"]] = FakeConn(answer)
        return devs

    def test_round_over_budget_saves_what_is_done_and_stops(self):
        """New switches are not started once the time budget is used up; what was read is written and
        marked polled, and the rest is due first at the next round."""
        self._switches(3)
        self.cfg["collector"]["uplink_workers"] = 1
        clock = {"t": 0.0}
        connect = self.connect

        def slow(platform, ip, *a):
            clock["t"] += 400  # each switch takes 400 s; the budget is 420 s
            return connect(platform, ip, *a)
        with mock.patch("netmon.creds.switch_sets", return_value=self.sets), \
                mock.patch("netmon.devices._connect", side_effect=slow):
            code = uplinks.poll(self.cfg, now=T0, clock=lambda: clock["t"])
        self.assertEqual(code, 0)
        self.assertEqual([ip for _, ip, *_ in self.logins], ["10.0.0.1", "10.0.0.2"], "nothing started after 420 s")
        data = self.store.read("uplinks.json")
        self.assertEqual(sorted({r["ip"] for r in data["ports"]}), ["10.0.0.1", "10.0.0.2"])
        self.assertEqual(sorted(self.store.read("state", "uplinks-polled.json")), ["10.0.0.1", "10.0.0.2"])
        self.assertFalse(self.store.running("uplinks.lock"))
        self.logins.clear()
        clock["t"] = 0.0
        with mock.patch("netmon.creds.switch_sets", return_value=self.sets), \
                mock.patch("netmon.devices._connect", side_effect=self.connect):
            uplinks.poll(self.cfg, now=T0 + timedelta(minutes=10), clock=lambda: clock["t"])
        self.assertEqual(self.logins[0][1], "10.0.0.3", "the switch left over is read first next time")

    def test_progress_is_written_during_the_round(self):
        """A round killed at the 9-minute task limit keeps every switch already read."""
        self._switches(3)
        self.cfg["collector"]["uplink_workers"] = 1
        seen = {}
        connect = self.connect

        def spy(platform, ip, *a):
            seen[ip] = (sorted({r["ip"] for r in (self.store.read("uplinks.json") or {}).get("ports", [])}),
                        sorted(self.store.read("state", "uplinks-polled.json") or {}))
            return connect(platform, ip, *a)
        with mock.patch.object(uplinks, "SAVE_EVERY", 1), mock.patch("netmon.creds.switch_sets", return_value=self.sets), \
                mock.patch("netmon.devices._connect", side_effect=spy):
            self.assertEqual(uplinks.poll(self.cfg, now=T0), 0)
        self.assertEqual(seen["10.0.0.1"], ([], []))
        self.assertEqual(seen["10.0.0.3"], (["10.0.0.1", "10.0.0.2"], ["10.0.0.1", "10.0.0.2"]))

    def test_core_and_distribution_first_then_oldest_read(self):
        devs = [dev("10.0.1.5", "access", "cisco_ios", [nb("Gi1/0/48", "cisco-dsw", "10.0.1.1", peer="10.0.1.1")]),
                dev("10.0.0.1", "core", "cisco_ios", [nb("Gi1/0/48", "dsw-test-01", "10.0.1.1", peer="10.0.1.1")]),
                dev("10.0.1.6", "access", "cisco_ios", [nb("Gi1/0/48", "cisco-dsw", "10.0.1.1", peer="10.0.1.1")]),
                dev("10.0.1.1", "distribution", "cisco_ios", [nb("Gi1/0/48", "core-01", "10.0.0.1", peer="10.0.0.1")])]
        self.snapshot(devs)
        self.store.write({"10.0.0.1": "2026-11-25T03:30:00Z", "10.0.1.1": "2026-11-25T03:20:00Z",
                          "10.0.1.6": "2026-11-25T03:00:00Z"}, "state", "uplinks-polled.json")
        self.cfg["collector"]["uplink_workers"] = 1
        answer = lambda cmd: "" if cmd == "show etherchannel summary" else iface("Gi1/0/48", 1)
        for d in devs:
            self.conns[d["ip"]] = FakeConn(answer)
        self.poll()
        self.assertEqual([ip for _, ip, *_ in self.logins], ["10.0.1.1", "10.0.0.1", "10.0.1.5", "10.0.1.6"],
                         "core/distribution (oldest first), then the others: never read, then oldest")

    def test_stale_lock_boundary(self):
        """A round is stopped at 9 minutes; its lock is stale after 570 s, so the next start (600 s) runs."""
        self._switches(1)
        self.assertTrue(self.store.acquire({"test": True}, "uplinks.lock"))
        lock = self.store.path("state", "uplinks.lock")
        young = time.time() - 560
        os.utime(lock, (young, young))
        self.assertEqual(self.poll()[0], 2, "560 s: may still be running")
        old = time.time() - 580
        os.utime(lock, (old, old))
        self.assertEqual(self.poll()[0], 0, "580 s: left by a round the task limit stopped")

    def test_switch_without_uplinks_any_more_drops_its_old_rows(self):
        """MINOR 2: an ok switch that no longer has selected ports does not keep old rows (or alerts) forever."""
        core = dev("10.0.0.1", "core", "cisco_ios", [nb("Gi1/0/48", "dsw-test-01", "10.0.1.1", peer="10.0.1.1")])
        self.snapshot([core])
        self.conns["10.0.0.1"] = FakeConn(lambda cmd: "" if "etherchannel" in cmd else iface("Gi1/0/48", 900000000))
        _, data = self.poll()
        self.assertTrue(data["ports"][0]["alert"])
        self.snapshot([dict(core, neighbors=[])])
        _, data = self.poll(T0 + timedelta(minutes=10))
        self.assertEqual(data["ports"], [])


class StoreWrite(unittest.TestCase):
    def test_write_retries_when_the_target_is_open_elsewhere(self):
        """MINOR 6: on Windows os.replace fails (access denied) while another process reads the file."""
        with tempfile.TemporaryDirectory() as tmp:
            store = Store(tmp)
            real, calls = os.replace, []

            def flaky(src, dst):
                calls.append(dst)
                if len(calls) <= 2:
                    raise PermissionError(5, "Access is denied")
                return real(src, dst)
            with mock.patch("netmon.store.os.replace", side_effect=flaky), mock.patch("netmon.store.time.sleep") as sleep:
                store.write({"a": 1}, "latest.json")
            self.assertEqual(store.read("latest.json"), {"a": 1})
            self.assertEqual(len(calls), 3)
            self.assertEqual(sleep.call_count, 2)
            with mock.patch("netmon.store.os.replace", side_effect=PermissionError(5, "Access is denied")), \
                    mock.patch("netmon.store.time.sleep"):
                with self.assertRaises(PermissionError):
                    store.write({"a": 2}, "latest.json")
            self.assertEqual(store.read("latest.json"), {"a": 1})
            self.assertEqual([n for n in os.listdir(tmp) if n.endswith(".tmp")], [], "no temporary file left behind")


if __name__ == "__main__":
    unittest.main()
