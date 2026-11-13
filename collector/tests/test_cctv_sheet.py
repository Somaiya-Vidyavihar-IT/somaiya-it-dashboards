"""CCTV sheet mode: parsing the CCTV sheet, service-account sign-in, checks and the collector's own down history.

All rows, names and addresses are invented (documentation ranges); no Google service or device is contacted.
"""
import base64
import json
import tempfile
import unittest
import urllib.parse
from datetime import datetime, timedelta, timezone

from netmon import cctv, cctv_sheet, config
from netmon.store import Store

NOW = datetime(2026, 11, 13, 6, 0, tzinfo=timezone.utc)
SHEET_ID = "1AbCdEfGhIjKlMnOpQrStUvWxYz_0123456789"
EMAIL_ROWS = [["Emails", "Off"], [], ["Institute Name", "IT SPOC"], ["Lab", "spoc@somaiya.edu"], ["Hall", "hall@somaiya.edu"]]

SIMPLE = [["Location", "Device Type", "IP"],
          ["Lab block - Dahua Non Poe", "NVR", "192.0.2.10"],
          ["Lab block - Class Room <b>1</b>", "CCTV Camera", "192.0.2.11"],
          ["Lab block - Lobby", "CCTV Camera", " 192.0.2.12 "],
          ["", "", ""],
          ["Lab block - Entrance", "CCTV Camera", "192.0.2.13"]]
WIDE = [["", "", "", "", "", "", "", "", "", ""],
        ["Location", "Particulars", "Model", "Category", "Serial No.", "IP", "Warranty", "Date of Expiry", "Status ", "NVR & CCTV Count"],
        ["Hall - data centre", "NVR-1", "Dahua NVR4216", "Non POE", "SN-1", "198.51.100.1", "5", "24.09.2029", "In Use", "2"],
        ["Hall - Gate", "CCTV Camera", "", "", "", "198.51.100.2", "", "", "", ""],
        ["", "HDD", "4TB", "", "", "", "", "", "", ""],
        ["Hall - Store", "Dome", "", "", "", "198.51.100.3 3", "", "", "", ""],
        ["Hall - Roof", "CCTV Camera", "", "", "", "192.0.2.11", "", "", "", ""],
        ["Hall - Corridor", "", "", "", "", "198.51.100.4", "", "", "", ""],
        ["Hall - Kiosk", "Monitor", "", "", "", "198.51.100.5", "", "", "", ""]]


def make_cfg(data_dir, **extra):
    cc = {"source": "sheet", "sheet_id": SHEET_ID, "sheet_tabs": ["Lab", "Hall"], "viewers": ["guard@somaiya.edu"]}
    cc.update(extra)
    cfg = config.validate({"collector": {"switch_username": "u", "data_dir": data_dir},
                           "subnets": [{"cidr": "192.0.2.0/24", "institute": "Engineering"}], "cctv": cc})
    cfg["_path"] = "unused"
    return cfg


class Parse(unittest.TestCase):
    def test_both_layouts_types_problems_and_nvr_link(self):
        devices, problems, skipped = cctv_sheet.parse({"Lab": SIMPLE, "Hall": WIDE})
        by_ip = {d["ip"]: d for d in devices}
        self.assertEqual(sorted(by_ip), ["192.0.2.10", "192.0.2.11", "192.0.2.12", "192.0.2.13", "198.51.100.1", "198.51.100.2"])
        self.assertEqual(by_ip["192.0.2.10"]["kind"], "nvr")
        self.assertEqual(by_ip["192.0.2.11"]["name"], "Lab block - Class Room <b>1</b>", "names pass through; the page escapes")
        self.assertEqual(by_ip["192.0.2.12"]["row"], 4, "sheet row numbers are 1-based")
        self.assertEqual(by_ip["192.0.2.13"]["nvr"], "Lab block - Dahua Non Poe (192.0.2.10)", "camera → NVR above it")
        self.assertEqual((by_ip["198.51.100.1"]["model"], by_ip["198.51.100.1"]["serial"], by_ip["198.51.100.1"]["expiry"]),
                         ("Dahua NVR4216", "SN-1", "24.09.2029"))
        self.assertEqual(by_ip["198.51.100.1"]["institute"], "Hall", "the tab is the site")
        self.assertEqual(skipped, 1, "HDD rows are skipped silently")
        issues = {(p["tab"], p["row"]): p["issue"] for p in problems}
        self.assertEqual(issues, {("Hall", 6): "IP missing or not valid", ("Hall", 7): "duplicate IP (also Lab row 3)",
                                  ("Hall", 8): "device type is blank", ("Hall", 9): "unknown device type"})

    def test_missing_header_or_type_column(self):
        _, problems, _ = cctv_sheet.parse({"Empty": [["just", "notes"]], "NoType": [["Location", "IP"], ["x", "192.0.2.1"]]})
        self.assertEqual([p["tab"] for p in problems], ["Empty", "NoType"])

    def test_range_names_are_quoted(self):
        self.assertEqual(cctv_sheet.a1_range("SVV- Security Campus"), "'SVV- Security Campus'!A1:Z3000")
        self.assertEqual(cctv_sheet.a1_range("Bob's"), "'Bob''s'!A1:Z3000")


class ServiceAccount(unittest.TestCase):
    def setUp(self):
        from cryptography.hazmat.primitives import serialization
        from cryptography.hazmat.primitives.asymmetric import rsa
        self.pk = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        pem = self.pk.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                    serialization.NoEncryption()).decode()
        self.key = {"type": "service_account", "client_email": "reader@example.iam.gserviceaccount.com",
                    "private_key": pem, "private_key_id": "kid1", "token_uri": "https://oauth2.googleapis.com/token"}

    def test_assertion_is_signed_and_read_only(self):
        from cryptography.hazmat.primitives import hashes
        from cryptography.hazmat.primitives.asymmetric import padding
        jwt = cctv_sheet.make_assertion(self.key, now=1_800_000_000)
        head, body, sig = jwt.split(".")
        dec = lambda s: base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))
        claims = json.loads(dec(body))
        self.assertEqual(json.loads(dec(head)), {"alg": "RS256", "typ": "JWT", "kid": "kid1"})
        self.assertEqual(claims["scope"], "https://www.googleapis.com/auth/spreadsheets.readonly")
        self.assertEqual((claims["iss"], claims["exp"] - claims["iat"]), (self.key["client_email"], 3600))
        self.pk.public_key().verify(dec(sig), f"{head}.{body}".encode(), padding.PKCS1v15(), hashes.SHA256())

    def test_token_then_batch_get(self):
        seen = []

        def http(req):
            seen.append(req)
            if req.full_url.endswith("/token"):
                form = urllib.parse.parse_qs(req.data.decode())
                self.assertEqual(form["grant_type"], ["urn:ietf:params:oauth:grant-type:jwt-bearer"])
                return {"access_token": "tok"}
            return {"valueRanges": [{"values": SIMPLE}, {}]}
        token = cctv_sheet.access_token(self.key, http=http)
        tabs = cctv_sheet.read_tabs(SHEET_ID, ["Lab", "Hall"], token, http=http)
        self.assertEqual(tabs, {"Lab": SIMPLE, "Hall": []})
        url = seen[1].full_url
        self.assertIn(urllib.parse.quote("'Lab'!A1:Z3000", safe=""), url)
        self.assertEqual(seen[1].get_header("Authorization"), "Bearer tok")

    def test_clear_errors(self):
        def refuse(req):
            raise cctv_sheet.SheetError("Google answered 403: The caller does not have permission")
        with self.assertRaisesRegex(cctv_sheet.SheetError, "Share it with the service account"):
            cctv_sheet.read_tabs(SHEET_ID, ["Lab"], "tok", http=refuse)
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaisesRegex(cctv_sheet.SheetError, "not found"):
                cctv_sheet.load_key(tmp + "/missing.json")
            path = tmp + "/oauth-client.json"
            with open(path, "w", encoding="utf-8") as fh:
                json.dump({"installed": {}}, fh)
            with self.assertRaisesRegex(cctv_sheet.SheetError, "not a Google service account"):
                cctv_sheet.load_key(path)


class Collect(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.cfg = make_cfg(self.tmp.name)
        self.store = Store(self.tmp.name)
        self.reads = 0
        self.down = {"192.0.2.12", "198.51.100.2"}

    def tearDown(self):
        self.tmp.cleanup()

    def reader(self):
        self.reads += 1
        return {"Lab": SIMPLE, "Hall": WIDE}

    def probe(self, ip):
        return None if ip in self.down else 3

    def collect(self, now):
        return cctv_sheet.collect_sheet(self.cfg, self.store, now=now, reader=self.reader, probe=self.probe,
                                        email_reader=lambda: EMAIL_ROWS)

    def test_status_history_escalation(self):
        snap = self.collect(NOW)
        d = {x["ip"]: x for x in snap["devices"]}
        self.assertEqual((snap["source"], snap["nodesRead"], snap["summary"]["sheetProblems"]), ("sheet", 6, 4))
        self.assertEqual((d["192.0.2.11"]["status"], d["192.0.2.11"]["rttMs"]), ("up", 3))
        self.assertEqual((d["192.0.2.12"]["status"], d["192.0.2.12"]["downSinceSource"]), ("down", "first-seen"),
                         "already down at the first check: only 'at least since'")
        self.assertEqual(snap["summary"]["cameras"]["down"], 2)
        self.assertEqual([i["name"] for i in snap["institutes"]], ["Hall", "Lab"])
        # An up camera stops answering: exact (observed) start; 49 h later it is escalated.
        self.down.add("192.0.2.13")
        later = NOW + timedelta(minutes=5)
        d = {x["ip"]: x for x in self.collect(later)["devices"]}
        self.assertEqual((d["192.0.2.13"]["downSinceSource"], d["192.0.2.13"]["downSince"]), ("observed", "2026-11-13T06:05:00Z"))
        self.assertEqual(d["192.0.2.12"]["downSince"], "2026-11-13T06:00:00Z", "kept, not reset")
        d = {x["ip"]: x for x in self.collect(later + timedelta(hours=49))["devices"]}
        self.assertTrue(d["192.0.2.13"]["escalated"])
        self.assertEqual(self.reads, 2, "the sheet is re-read only every sheet_minutes (15)")
        # Recovery clears the history.
        self.down.discard("192.0.2.13")
        self.collect(later + timedelta(hours=50))
        self.assertNotIn("192.0.2.13", self.store.read(*cctv_sheet.REACH_FILE)["down"])

    def test_sheet_failure_keeps_last_copy_or_fails_clearly(self):
        def broken():
            raise cctv_sheet.SheetError("Cannot reach Google")
        with self.assertRaisesRegex(cctv_sheet.SheetError, "no earlier copy"):
            cctv_sheet.collect_sheet(self.cfg, self.store, now=NOW, reader=broken, probe=self.probe)
        self.collect(NOW)
        snap = cctv_sheet.collect_sheet(self.cfg, self.store, now=NOW + timedelta(hours=1), reader=broken, probe=self.probe)
        self.assertEqual((snap["nodesRead"], snap["sheetReadAt"]), (6, "2026-11-13T06:00:00Z"))
        self.assertIn("Cannot reach Google", snap["sheetError"])

    def test_changed_tab_list_forces_a_reread(self):
        self.collect(NOW)
        self.cfg["cctv"]["sheet_tabs"] = ["Lab"]
        self.reader = lambda: {"Lab": SIMPLE}
        snap = cctv_sheet.collect_sheet(self.cfg, self.store, now=NOW + timedelta(minutes=1), reader=self.reader,
                                        probe=self.probe)
        self.assertEqual(snap["nodesRead"], 4)

    def test_tries_and_poll_dispatch(self):
        calls = []
        cc = dict(self.cfg["cctv"], check_tries=3)
        res = cctv_sheet.check_all(["192.0.2.1"], cc, probe=lambda ip: calls.append(ip))
        self.assertEqual((res, len(calls)), ({"192.0.2.1": None}, 3))
        orig = cctv_sheet.collect_sheet
        cctv_sheet.collect_sheet = lambda cfg, store, now=None: orig(cfg, store, now=now, reader=self.reader, probe=self.probe)
        try:
            st = cctv.poll_once(self.cfg, self.store, by="guard@somaiya.edu")
        finally:
            cctv_sheet.collect_sheet = orig
        self.assertEqual((st["ok"], st["cameras"], st["nvrs"]), (True, 4, 2))
        self.assertEqual(self.store.read("cctv.json")["source"], "sheet")


class ConfigRules(unittest.TestCase):
    def test_sheet_mode_validation(self):
        cc = config.validate_cctv({"source": "sheet", "sheet_id": SHEET_ID, "sheet_tabs": [" VTI ", "HPC"]})
        self.assertEqual((cc["enabled"], cc["sheet_tabs"], cc["check"], cc["tcp_ports"]), (True, ["VTI", "HPC"], "ping", [80, 554]))
        self.assertFalse(config.validate_cctv({"source": "sheet"})["enabled"], "off until sheet_id is set")
        bad = [{"source": "excel"}, {"source": "sheet", "sheet_id": "short"}, {"source": "sheet", "sheet_id": SHEET_ID},
               {"source": "sheet", "sheet_id": SHEET_ID, "sheet_tabs": ["VTI"], "check": "snmp"},
               {"source": "sheet", "sheet_id": SHEET_ID, "sheet_tabs": ["VTI"], "tcp_ports": [0]},
               {"source": "sheet", "sheet_id": SHEET_ID, "sheet_tabs": ["VTI"], "check_workers": 0}]
        for raw in bad:
            with self.assertRaises(config.ConfigError, msg=raw):
                config.validate_cctv(raw)


if __name__ == "__main__":
    unittest.main()
