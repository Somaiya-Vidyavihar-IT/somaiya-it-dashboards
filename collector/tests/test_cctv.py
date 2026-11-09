"""CCTV dashboard: SolarWinds classification, down-since, escalation, IPAM gap and the API's own allowlist.

All nodes, names and addresses below are invented (documentation ranges); no SolarWinds is contacted.
"""
import http.client
import json
import tempfile
import threading
import unittest
from datetime import datetime, timezone

from netmon import cctv, config
from netmon.server import NetmonServer, Verifier
from netmon.store import Store

CLIENT = "fixture.apps.googleusercontent.com"
ORIGIN = "https://somaiya-vidyavihar-it.github.io"
NOW = datetime(2026, 11, 10, 6, 0, tzinfo=timezone.utc)


def make_cfg(data_dir, **cctv_cfg):
    base = {"solarwinds_host": "sw.example.test", "solarwinds_username": "ro", "viewers": ["Guard@Somaiya.edu"],
            "camera_ranges": ["192.0.2.0/25"], "ipam_ranges": ["198.51.100.0/24"]}
    base.update(cctv_cfg)
    cfg = config.validate({"collector": {"switch_username": "u", "data_dir": data_dir},
                           "subnets": [{"cidr": "192.0.2.0/24", "institute": "Engineering"}],
                           "server": {"host": "127.0.0.1", "port": 0, "google_client_id": CLIENT,
                                      "network_team": ["net@somaiya.edu"]},
                           "cctv": base})
    cfg["_path"] = "unused"
    return cfg


def node(nid, caption, ip, status=1, tag=None, inst=None, unmanaged=False):
    return {"NodeID": nid, "Caption": caption, "IPAddress": ip, "Vendor": "Unknown", "MachineType": "Unknown",
            "Status": status, "StatusDescription": f"status {status}", "UnManaged": unmanaged, "Location": "",
            "DeviceType": tag, "InstituteName": inst}


NODES = [
    node(1, "GATE-NVR-1", "192.0.2.200", inst="Main Gate"),                    # name rule -> NVR
    node(2, "D.ed_NVR", "203.0.113.5", status=2),                               # name rule (underscore) -> NVR, down
    node(3, "host", "203.0.113.6", tag="Dahua NVR"),                            # tag -> NVR
    node(4, "192.0.2.10", "192.0.2.10"),                                        # range -> camera, up
    node(5, "192.0.2.11", "192.0.2.11", status=2),                              # range -> camera, down (event)
    node(6, "192.0.2.12", "192.0.2.12", status=12),                             # range -> camera, unreachable (no event)
    node(7, "Lift camera <b>1</b>", "203.0.113.9", tag="CCTV", status=3),       # tag -> camera, warning
    node(8, "CCTVSW", "192.0.2.20", tag="SWITCH"),                              # explicit other tag: never a camera
    node(9, "192.0.2.200", "192.0.2.201", status=9),                            # range? no: .201 outside /25
    node(10, "192.0.2.30", "192.0.2.30", unmanaged=True, status=9),             # range -> camera, unmonitored
    node(11, "office-pc", "203.0.113.50"),                                      # nothing matches
]


class FakeSwis:
    def __init__(self, nodes=NODES, events=None, oldest="2026-10-09T18:33:02.2430000", fail_events=False,
                 ipam=None):
        self.nodes, self.events, self.oldest, self.fail_events = nodes, events or {}, oldest, fail_events
        self.ipam = ipam or []
        self.queries = []

    def __call__(self, swql):
        self.queries.append(swql)
        assert swql.lstrip().upper().startswith("SELECT "), swql
        if "FROM Orion.Nodes" in swql:
            return self.nodes
        if "MAX(EventTime)" in swql:
            if self.fail_events:
                raise cctv.SwisError("events unavailable")
            ids = [int(x) for x in swql.split("IN (")[1].split(")")[0].split(",")]
            return [{"NetObjectID": i, "T": self.events[i]} for i in ids if i in self.events]
        if "MIN(EventTime)" in swql:
            return [{"Oldest": self.oldest}]
        if "FROM IPAM.GroupNode" in swql:
            return [{"GroupId": 7, "Address": "198.51.100.0", "CIDR": 24}, {"GroupId": 8, "Address": "10.0.0.0", "CIDR": 8}]
        if "FROM IPAM.IPNode" in swql:
            assert "SubnetId IN (7)" in swql, swql
            return self.ipam
        raise AssertionError(swql)


class Collect(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.cfg = make_cfg(self.tmp.name)
        self.store = Store(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def by_id(self, snap):
        return {d["id"]: d for d in snap["devices"]}

    def test_classification_rules(self):
        snap = cctv.collect(self.cfg, FakeSwis(events={5: "2026-11-09T20:00:00.0000000"}), self.store, now=NOW)
        d = self.by_id(snap)
        self.assertEqual({k: (v["kind"], v["matchedBy"]) for k, v in d.items()},
                         {1: ("nvr", "name"), 2: ("nvr", "name"), 3: ("nvr", "tag"), 4: ("camera", "range"),
                          5: ("camera", "range"), 6: ("camera", "range"), 7: ("camera", "tag"), 10: ("camera", "range")})
        self.assertNotIn(8, d, "a node tagged SWITCH is never a camera, even inside a camera range")
        self.assertEqual(d[7]["name"], "Lift camera <b>1</b>", "names are passed through; the page escapes them")
        self.assertEqual([d[i]["status"] for i in (4, 5, 6, 7, 10)], ["up", "down", "down", "warning", "unmonitored"])
        self.assertEqual(d[1]["institute"], "Main Gate", "InstituteName property wins")
        self.assertEqual(d[4]["institute"], "Engineering", "otherwise the netmon subnet's institute")
        self.assertEqual(d[2]["institute"], "Unassigned")
        self.assertEqual(snap["summary"]["cameras"], {"total": 5, "up": 1, "warning": 1, "down": 2, "unmonitored": 1,
                                                      "unknown": 0, "escalated": 1})
        self.assertEqual(snap["summary"]["nvrs"]["total"], 3)

    def test_down_since_from_events_then_kept(self):
        swis = FakeSwis(events={5: "2026-11-09T20:00:00.0000000", 2: "2026-11-01T06:00:00.0000000"})
        d = self.by_id(cctv.collect(self.cfg, swis, self.store, now=NOW))
        self.assertEqual((d[5]["downSince"], d[5]["downSinceExact"], d[5]["downHours"], d[5]["escalated"]),
                         ("2026-11-09T20:00:00Z", True, 10.0, False))
        self.assertEqual((d[2]["downHours"], d[2]["escalated"]), (216.0, True), "NVR down 9 days: escalated")
        self.assertEqual((d[6]["downSinceSource"], d[6]["downSince"], d[6]["escalated"]),
                         ("before-events", "2026-10-09T18:33:02Z", True), "no event kept: down before the oldest event")
        self.assertIsNone(d[4]["downSince"])
        # Next poll: nothing new is down, so SolarWinds' events are not asked again.
        swis.queries.clear()
        later = datetime(2026, 11, 11, 21, 0, tzinfo=timezone.utc)
        d = self.by_id(cctv.collect(self.cfg, swis, self.store, now=later))
        self.assertFalse(any("Orion.Events" in q for q in swis.queries))
        self.assertTrue(d[5]["escalated"], "49 hours later camera 5 passes 48 h")

    def test_recovered_device_is_forgotten_and_events_failure_uses_first_seen(self):
        cctv.collect(self.cfg, FakeSwis(fail_events=True), self.store, now=NOW)
        d = self.by_id(cctv.collect(self.cfg, FakeSwis(fail_events=True), self.store, now=NOW))
        self.assertEqual((d[5]["downSinceSource"], d[5]["downSince"]), ("first-seen", "2026-11-10T06:00:00Z"))
        up = [dict(n, Status=1) if n["NodeID"] == 5 else n for n in NODES]
        cctv.collect(self.cfg, FakeSwis(nodes=up), self.store, now=NOW)
        self.assertNotIn("5", self.store.read(*cctv.STATE_FILE)["down"])

    def test_ipam_gap_lists_live_ips_not_in_solarwinds(self):
        ipam = [{"IPAddress": "198.51.100.7", "MAC": "AA-BB", "Vendor": "Zhejiang Dahua", "DhcpClientName": "NVR.x",
                 "LastSync": "2026-11-09T22:40:47.4430000"},
                {"IPAddress": "198.51.100.9", "MAC": None, "Vendor": None, "DhcpClientName": None, "SysName": "cam9",
                 "LastSync": None}]
        nodes = NODES + [node(20, "known", "198.51.100.9", tag="CCTV")]
        swis = FakeSwis(nodes=nodes, ipam=ipam)
        snap = cctv.collect(self.cfg, swis, self.store, now=NOW)
        self.assertEqual([g["ip"] for g in snap["notInSolarWinds"]], ["198.51.100.7"])
        self.assertEqual(snap["notInSolarWinds"][0]["lastSeen"], "2026-11-09T22:40:47Z")
        self.assertEqual(snap["summary"]["notInSolarWinds"], 1)
        swis.queries.clear()
        cctv.collect(self.cfg, swis, self.store, now=NOW)
        self.assertFalse(any("IPAM" in q for q in swis.queries), "IPAM is read at most hourly")

    def test_poll_once_reports_failures_without_raising(self):
        def broken(_):
            raise cctv.SwisError("SolarWinds rejected the username or password (401).")
        st = cctv.poll_once(self.cfg, self.store, by="guard@somaiya.edu", query=broken)
        self.assertEqual((st["state"], st["ok"]), ("failed", False))
        self.assertIn("401", self.store.read("cctv-status.json")["error"])
        self.assertIsNone(self.store.read("cctv.json"))
        st = cctv.poll_once(self.cfg, self.store, query=FakeSwis())
        self.assertTrue(st["ok"])
        self.assertEqual(self.store.read("cctv.json")["schema"], 1)


class ReadOnlyAndPinning(unittest.TestCase):
    def test_only_select_is_sent(self):
        client = cctv.Swis("sw.example.test", 17774, "ro", "pw")
        for swql in ("DELETE FROM Orion.Nodes", "  update x", "Invoke Orion.Nodes", "SELECTX 1"):
            with self.assertRaises(cctv.SwisError):
                client.query(swql)

    def test_certificate_pin_trust_on_first_use(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = Store(tmp)
            client = cctv.Swis("sw.example.test", 17774, "ro", "pw", store=store)
            client._check_pin("a" * 64)
            client._check_pin("a" * 64)
            with self.assertRaisesRegex(cctv.SwisError, "changed"):
                client._check_pin("b" * 64)
            pinned = cctv.Swis("sw.example.test", 17774, "ro", "pw", store=store, pinned="c" * 64)
            with self.assertRaisesRegex(cctv.SwisError, "does not match"):
                pinned._check_pin("a" * 64)


class ConfigRules(unittest.TestCase):
    def test_disabled_by_default_and_validated(self):
        cfg = config.validate({"collector": {"switch_username": "u"}, "subnets": [{"cidr": "192.0.2.0/24"}]})
        self.assertFalse(cfg["cctv"]["enabled"])
        bad = [{"solarwinds_host": "<SOLARWINDS_HOST>"}, {"solarwinds_host": "h", "type_property": "Device Type"},
               {"solarwinds_host": "h", "camera_ranges": ["not-a-net"]}, {"solarwinds_host": "h", "poll_minutes": 0},
               {"solarwinds_host": "h", "nvr_name_pattern": "("}, {"solarwinds_host": "h", "solarwinds_cert_sha256": "xyz"}]
        for raw in bad:
            with self.assertRaises(config.ConfigError, msg=raw):
                config.validate_cctv(raw)
        cc = config.validate_cctv({"solarwinds_host": "h", "viewers": [" A@Somaiya.edu ", "a@somaiya.edu"],
                                   "solarwinds_cert_sha256": "AB:" * 31 + "AB"})
        self.assertEqual(cc["viewers"], ["a@somaiya.edu"])
        self.assertEqual(cc["solarwinds_cert_sha256"], "ab" * 32)


class FakePoller:
    def __init__(self):
        self.requests, self.busy = [], False

    def request(self, by):
        if self.busy:
            return False
        self.requests.append(by)
        return True

    def running(self):
        return self.busy


class CctvApi(unittest.TestCase):
    """The CCTV routes use their own list: network-team members are not let in, CCTV viewers see only CCTV."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        cfg = make_cfg(self.tmp.name)
        emails = {"net": "net@somaiya.edu", "guard": "guard@somaiya.edu", "outsider": "x@somaiya.edu"}

        def fetch(token):
            if token not in emails:
                return None
            return {"aud": CLIENT, "azp": CLIENT, "email": emails[token], "email_verified": "true", "expires_in": "3000"}
        self.poller = FakePoller()
        self.httpd = NetmonServer(cfg, verifier=Verifier(CLIENT, ["somaiya.edu"], cfg["server"]["network_team"], fetch=fetch),
                                  start_run=lambda e: None, cctv=self.poller)
        self.store = Store(self.tmp.name)
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    def tearDown(self):
        self.httpd.shutdown()
        self.httpd.server_close()
        self.tmp.cleanup()

    def req(self, method, path, token=None):
        conn = http.client.HTTPConnection("127.0.0.1", self.httpd.server_address[1], timeout=5)
        headers = {"Origin": ORIGIN}
        if token:
            headers["Authorization"] = "Bearer " + token
        conn.request(method, path, body=b"" if method == "POST" else None, headers=headers)
        resp = conn.getresponse()
        body = resp.read()
        conn.close()
        return resp.status, json.loads(body) if body else None

    def test_separate_allowlists(self):
        self.assertEqual(self.req("GET", "/api/v1/cctv/me")[0], 401)
        status, body = self.req("GET", "/api/v1/cctv/me", "net")
        self.assertEqual(status, 403)
        self.assertIn("CCTV dashboard list", body["error"])
        self.assertEqual(self.req("GET", "/api/v1/cctv/me", "guard"),
                         (200, {"email": "guard@somaiya.edu", "canRefresh": True, "apiVersion": 3}))
        self.assertEqual(self.req("GET", "/api/v1/me", "guard")[0], 403, "a CCTV viewer gets no network data")
        self.assertEqual(self.req("GET", "/api/v1/snapshot", "guard")[0], 403)
        self.assertEqual(self.req("GET", "/api/v1/me", "net")[0], 200)

    def test_snapshot_status_and_refresh(self):
        self.assertEqual(self.req("GET", "/api/v1/cctv/snapshot", "guard")[0], 404)
        self.store.write({"schema": 1, "devices": []}, "cctv.json")
        self.assertEqual(self.req("GET", "/api/v1/cctv/snapshot", "guard"), (200, {"schema": 1, "devices": []}))
        status, body = self.req("GET", "/api/v1/cctv/status", "guard")
        self.assertEqual((status, body["polling"], body["pollMinutes"]), (200, False, 5))
        self.assertEqual(self.req("POST", "/api/v1/cctv/refresh", "outsider")[0], 403)
        self.assertEqual(self.req("POST", "/api/v1/cctv/refresh", "guard")[0], 202)
        self.assertEqual(self.poller.requests, ["guard@somaiya.edu"])
        self.poller.busy = True
        self.assertEqual(self.req("POST", "/api/v1/cctv/refresh", "guard")[0], 409)
        with open(self.store.path("audit.log"), encoding="utf-8") as fh:
            self.assertIn("cctv-refresh-request by=guard@somaiya.edu", fh.read())

    def test_not_set_up(self):
        self.httpd.cctv = None
        status, body = self.req("GET", "/api/v1/cctv/me", "guard")
        self.assertEqual(status, 404)
        self.assertIn("not set up", body["error"])


class PollerWake(unittest.TestCase):
    def test_request_wakes_the_loop_and_refuses_while_busy(self):
        with tempfile.TemporaryDirectory() as tmp:
            cfg = make_cfg(tmp)
            seen, gate = [], threading.Event()

            def poll(cfg, store, by=""):
                seen.append(by)
                gate.wait(2)
            p = cctv.Poller(cfg, Store(tmp), poll=poll)
            stop = threading.Event()
            t = threading.Thread(target=p.loop, args=(stop,), daemon=True)
            t.start()
            for _ in range(100):
                if p.running():
                    break
                threading.Event().wait(0.01)
            self.assertFalse(p.request("guard@somaiya.edu"), "busy: refused")
            gate.set()
            for _ in range(100):
                if not p.running():
                    break
                threading.Event().wait(0.01)
            gate.clear()
            self.assertTrue(p.request("guard@somaiya.edu"))
            for _ in range(200):
                if len(seen) == 2:
                    break
                threading.Event().wait(0.01)
            self.assertEqual(seen, ["", "guard@somaiya.edu"])
            stop.set()
            gate.set()
            p.wake.set()


if __name__ == "__main__":
    unittest.main()
