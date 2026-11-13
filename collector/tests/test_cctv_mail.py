"""CCTV emails: the Email Config tab, the switch, each rule, recipients, test mode, outbox and limits.

All names and addresses are invented; no mail server is contacted (a fake sender records the messages).
"""
import tempfile
import unittest
from datetime import datetime, timedelta, timezone

from netmon import cctv, cctv_mail, config
from netmon.store import Store

T0 = datetime(2026, 11, 14, 4, 0, tzinfo=timezone.utc)  # 09:30 IST
SHEET_ID = "1AbCdEfGhIjKlMnOpQrStUvWxYz_0123456789"
EMAIL_ROWS = [["Emails", "On"], [],
              ["Institute Name", "IT SPOC", "CC", "CC", "Escalation"],
              ["Lab", "spoc@somaiya.edu", "cc1@somaiya.edu", "partner@example.com", "head@somaiya.edu"],
              ["Hall", "hall@somaiya.edu"]]


def make_cfg(data_dir, **extra):
    cc = {"source": "sheet", "sheet_id": SHEET_ID, "sheet_tabs": ["Lab", "Hall"], "email_team": ["net@somaiya.edu"]}
    cc.update(extra)
    return config.validate({"collector": {"switch_username": "u", "data_dir": data_dir},
                            "subnets": [{"cidr": "192.0.2.0/24"}], "cctv": cc})


def dev(ip, kind="camera", tab="Lab", nvr="Lab NVR (192.0.2.1)", name=None):
    return {"ip": ip, "kind": kind, "tab": tab, "name": name or f"{tab} {kind} {ip}", "nvr": None if kind == "nvr" else nvr}


class World:
    """Devices whose state the test changes round by round; builds the snapshot process() sees."""

    def __init__(self, devices):
        self.devs = {d["ip"]: dict(d) for d in devices}
        self.down = {}   # ip -> (since, fails)

    def set_down(self, ip, now):
        since, fails = self.down.get(ip, (now, 0))
        self.down[ip] = (since, fails + 1)

    def set_up(self, ip):
        self.down.pop(ip, None)

    def snap(self, now, rows=EMAIL_ROWS):
        out = []
        for ip, d in self.devs.items():
            x = dict(d)
            if ip in self.down:
                since, fails = self.down[ip]
                x.update(status="down", downSince=since.isoformat().replace("+00:00", "Z"), downSinceExact=True,
                         downHours=round((now - since).total_seconds() / 3600, 1), failedChecks=fails)
            else:
                x.update(status="up", downSince=None, downSinceExact=None, downHours=None, failedChecks=0)
            out.append(x)
        email_cfg = cctv_mail.parse_email_config(rows, "Email Config", None, ["Lab", "Hall"])
        return {"devices": out, "emailConfig": email_cfg, "sheetProblems": list(email_cfg["problems"]),
                "sheetError": None, "sheetReadAt": None}


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.cfg = make_cfg(self.tmp.name)
        self.store = Store(self.tmp.name)
        self.sent, self.fail = [], False
        self.world = World([dev("192.0.2.1", "nvr", name="Lab NVR")] + [dev(f"192.0.2.{i}") for i in range(10, 20)]
                           + [dev("198.51.100.1", "nvr", tab="Hall", name="Hall NVR"), dev("198.51.100.2", tab="Hall", nvr="Hall NVR (198.51.100.1)")])

    def tearDown(self):
        self.tmp.cleanup()

    def send(self, msgs):
        if self.fail:
            raise OSError("relay unreachable")
        self.sent.extend(msgs)

    def round(self, now, rows=EMAIL_ROWS):
        return cctv_mail.process(self.cfg, self.store, self.world.snap(now, rows), now=now, send=self.send)

    def kinds(self):
        return [m["X-Netmon-Kind"] for m in self.sent]


class EmailConfigTab(unittest.TestCase):
    def test_layout_switch_and_problems(self):
        rows = [["Emails", " test "], [], ["Institute Name", "IT SPOC", "CC", "CC", "Escalation", ""],
                ["Lab", "A@Somaiya.edu", "b@somaiya.edu", "two@x.com, three@x.com", "head@somaiya.edu", "orphan@x.com"],
                ["Lab", "dup@somaiya.edu"], ["Hall", "", "cc@somaiya.edu"]]
        out = cctv_mail.parse_email_config(rows, "Email Config", None, ["Lab", "Hall", "Gym"])
        self.assertEqual(out["mode"], "test")
        self.assertEqual(out["sites"]["Lab"], {"to": ["a@somaiya.edu"], "cc": ["b@somaiya.edu"], "escalation": ["head@somaiya.edu"]})
        issues = [p["issue"] for p in out["problems"]]
        self.assertIn("not one valid email address (one address per cell)", issues)
        self.assertIn("this column has no IT SPOC, CC or Escalation header", issues)
        self.assertTrue(any("listed twice" in i for i in issues))
        self.assertTrue(any(i.startswith("Hall is monitored but has no IT SPOC") for i in issues))
        self.assertTrue(any(i.startswith("Gym is monitored") for i in issues))

    def test_vendor_and_svv_spoc_columns(self):
        rows = [["Emails", "On"], ["Institute Name", "IT SPOC", "Vendor", "SVV IT SPOC", "Escalation 1", "escalation 2"],
                ["VTI", "a@somaiya.edu", "v@example.com", "b@somaiya.edu", "e1@somaiya.edu", "e2@somaiya.edu"]]
        out = cctv_mail.parse_email_config(rows, monitored=["VTI"])
        self.assertEqual(out["sites"]["VTI"], {"to": ["a@somaiya.edu", "b@somaiya.edu"], "cc": ["v@example.com"],
                                               "escalation": ["e1@somaiya.edu", "e2@somaiya.edu"]})
        self.assertEqual(out["problems"], [])

    def test_switch_defaults_to_off(self):
        for rows in (None, [["Institute Name", "IT SPOC"]], [["Emails", "Yes"], ["Institute Name", "IT SPOC"]],
                     [["Emails", ""], ["Institute Name", "IT SPOC"]]):
            self.assertEqual(cctv_mail.parse_email_config(rows)["mode"], "off", rows)


class Rules(Base):
    def test_first_run_is_quiet_then_nvr_down_after_two_checks_and_back_up(self):
        self.world.set_down("192.0.2.15", T0)  # already down before emails start: no urgent mail
        self.world.set_down("192.0.2.15", T0)
        self.round(T0)
        self.assertEqual(self.sent, [])
        t = T0 + timedelta(minutes=5)
        self.world.set_down("192.0.2.1", t)
        self.round(t)
        self.assertEqual(self.sent, [], "one failed check is not enough for an NVR")
        t += timedelta(minutes=5)
        self.world.set_down("192.0.2.1", t)
        self.round(t)
        self.assertEqual(self.kinds(), ["nvr-down"])
        m = self.sent[0]
        self.assertEqual(m["To"], "spoc@somaiya.edu")
        self.assertEqual(m["Cc"], "cc1@somaiya.edu, partner@example.com, net@somaiya.edu", "partner gets full mails")
        self.assertNotIn("head@somaiya.edu", m["Cc"], "escalation contacts only get escalations")
        self.assertIn("192.0.2.1", m.get_content())
        self.assertIn("10 listed under it", m.get_content())
        self.world.set_down("192.0.2.1", t + timedelta(minutes=5))
        self.round(t + timedelta(minutes=5))
        self.assertEqual(len(self.sent), 1, "never twice for the same outage")
        self.world.set_up("192.0.2.1")
        self.round(t + timedelta(minutes=80))
        self.assertEqual(self.kinds(), ["nvr-down", "nvr-up"])
        self.assertIn("back up after 1 h 25 min", self.sent[1]["Subject"])

    def test_mass_outage_one_mail_and_recovery(self):
        self.round(T0)
        t = T0 + timedelta(minutes=5)
        for i in range(10, 15):
            self.world.set_down(f"192.0.2.{i}", t)
        self.round(t)
        self.assertEqual(self.kinds(), ["mass-down"])
        self.assertIn("5 cameras down at once", self.sent[0]["Subject"])
        for i in range(10, 15):
            self.world.set_up(f"192.0.2.{i}")
        self.round(t + timedelta(minutes=40))
        self.assertEqual(self.kinds(), ["mass-down", "mass-up"])

    def test_single_camera_only_in_the_daily_report(self):
        self.round(T0)  # first run at 09:30 IST: the first report is tomorrow
        t = T0 + timedelta(hours=1)
        self.world.set_down("192.0.2.12", t)
        self.round(t)
        self.assertEqual(self.sent, [])
        morning = T0 + timedelta(hours=23, minutes=35)  # next day 09:05 IST
        self.round(morning)
        self.assertEqual(self.kinds(), ["daily"], "only Lab has something to report")
        body = self.sent[0].get_content()
        self.assertIn("1 down now, 1 went down", body)
        self.assertEqual(self.sent[0]["To"], "spoc@somaiya.edu")
        self.assertNotIn("net@somaiya.edu", self.sent[0]["Cc"] or "", "the team is not copied on daily reports")
        self.round(morning + timedelta(minutes=5))
        self.assertEqual(len(self.sent), 1, "one report a day")

    def test_escalation_48h_then_7_days(self):
        self.round(T0)
        t = T0 + timedelta(minutes=5)
        self.world.set_down("192.0.2.13", t)
        self.round(t)
        self.round(t + timedelta(hours=49))
        esc = [m for m in self.sent if m["X-Netmon-Kind"] == "escalation-48h"]
        self.assertEqual(len(esc), 1)
        self.assertIn("head@somaiya.edu", esc[0]["Cc"])
        self.assertIn("more than 2 days", esc[0]["Subject"])
        self.round(t + timedelta(hours=50))
        self.round(t + timedelta(days=7, hours=1))
        self.assertEqual([m["X-Netmon-Kind"] for m in self.sent if m["X-Netmon-Kind"].startswith("escalation")],
                         ["escalation-48h", "escalation-7d"])


class SwitchAndDelivery(Base):
    def test_test_mode_goes_only_to_the_team(self):
        rows = [["Emails", "Test"]] + EMAIL_ROWS[1:]
        self.round(T0, rows)
        t = T0 + timedelta(minutes=5)
        self.world.set_down("192.0.2.1", t)
        self.world.set_down("192.0.2.1", t)
        self.round(t, rows)
        m = self.sent[0]
        self.assertEqual((m["To"], m["Cc"]), ("net@somaiya.edu", None))
        self.assertTrue(m["Subject"].startswith("[TEST] [CCTV] Lab: NVR down"))
        self.assertIn("To: spoc@somaiya.edu", m.get_content())

    def test_off_sends_nothing_and_does_not_flood_when_switched_on(self):
        off = [["Emails", "Off"]] + EMAIL_ROWS[1:]
        self.round(T0, off)
        t = T0 + timedelta(minutes=5)
        self.world.set_down("192.0.2.1", t)
        self.world.set_down("192.0.2.1", t)
        status = self.round(t, off)
        self.assertEqual((self.sent, status["mode"]), ([], "off"))
        self.round(t + timedelta(minutes=5))  # switched On: that NVR outage was already handled while Off
        self.assertEqual(self.sent, [])

    def test_outbox_retries_after_a_failed_send(self):
        self.round(T0)
        self.fail = True
        t = T0 + timedelta(minutes=5)
        self.world.set_down("192.0.2.1", t)
        self.world.set_down("192.0.2.1", t)
        status = self.round(t)
        self.assertEqual((status["waiting"], "relay unreachable" in status["lastError"]), (1, True))
        self.fail = False
        status = self.round(t + timedelta(minutes=5))
        self.assertEqual((self.kinds(), status["waiting"], status["lastError"]), (["nvr-down"], 0, None))

    def test_hourly_limit_holds_extra_urgent_mails(self):
        self.cfg["cctv"]["urgent_per_site_hour"] = 1
        self.world.devs["192.0.2.2"] = dev("192.0.2.2", "nvr", name="Lab NVR 2")
        self.round(T0)
        t = T0 + timedelta(minutes=5)
        for ip in ("192.0.2.1", "192.0.2.2"):
            self.world.set_down(ip, t)
            self.world.set_down(ip, t)
        self.round(t)
        self.assertEqual(self.kinds(), ["nvr-down"])
        self.round(T0 + timedelta(hours=23, minutes=35))
        self.assertIn("held back", self.sent[-1].get_content())

    def test_subject_cannot_carry_extra_headers(self):
        self.world.devs["192.0.2.1"]["name"] = "Lab NVR\r\nBcc: attacker@example.com"
        self.round(T0)
        t = T0 + timedelta(minutes=5)
        self.world.set_down("192.0.2.1", t)
        self.world.set_down("192.0.2.1", t)
        self.round(t)
        self.assertNotIn("\n", self.sent[0]["Subject"])
        self.assertIsNone(self.sent[0]["Bcc"])

    def test_failure_mail_after_an_hour_once_a_day(self):
        self.round(T0, [["Emails", "Test"]] + EMAIL_ROWS[1:])
        for minutes in (5, 30, 70, 90):
            cctv_mail.on_failure(self.cfg, self.store, "SheetError: Cannot reach Google", now=T0 + timedelta(minutes=minutes),
                                 send=self.send)
        self.assertEqual(self.kinds(), ["system"])
        self.assertIn("stopped checking", self.sent[0]["Subject"])
        self.assertEqual(self.sent[0]["To"], "net@somaiya.edu")


class PollIntegration(Base):
    def test_poll_once_runs_the_rules_and_reports_status(self):
        world = self.world
        orig = cctv.__dict__.get("_emails")
        import netmon.cctv_sheet as cs
        real = cs.collect_sheet
        cs.collect_sheet = lambda cfg, store, now=None: dict(world.snap(now), schema=1, source="sheet", generatedAt="x",
                                                             nodesRead=13, summary={"cameras": {"total": 11}, "nvrs": {"total": 2}})
        try:
            st = cctv.poll_once(self.cfg, self.store, mailer=self.send, clock=lambda: T0)
        finally:
            cs.collect_sheet = real
        self.assertTrue(st["ok"])
        email = self.store.read("cctv.json")["email"]
        self.assertEqual((email["mode"], email["sites"]["Lab"]), ("on", {"to": 1, "cc": 2, "escalation": 1}))
        self.assertIsNotNone(orig)


if __name__ == "__main__":
    unittest.main()
