"""SG/CBS/C1300 login handling. Regression for the 2026-11-05 campus run, where ~70 such switches
failed as "authentication failed with all 3 credential sets" or "timed out" because they ask
"User Name:" / "Password:" inside the CLI unless `ip ssh password-auth` is configured."""
import unittest
from unittest import mock

import paramiko

from netmon import devices, parse, sx
from netmon.config import validate

CFG = validate({"collector": {"switch_username": "u"}, "subnets": [{"cidr": "10.0.0.0/24"}]})


class InbandSteps(unittest.TestCase):
    def test_reads_the_last_line_only(self):
        self.assertEqual(sx.inband_step("\r\n\r\nUser Name:"), "user")
        self.assertEqual(sx.inband_step("User Name:fixture-user\r\nPassword:"), "password")
        self.assertEqual(sx.inband_step("Password:********\r\n\r\nasw-test-02#"), "done")
        self.assertEqual(sx.inband_step("asw-test-02>"), "done")
        self.assertEqual(sx.inband_step("Banner #1 mentions User Name: below\r\nWelcome"), "")


class FakeTransport:
    def __init__(self, allow, accept):
        self.allow, self.accept, self.calls = allow, accept, []

    def auth_none(self, user):
        self.calls.append("none")
        if "none" in self.accept:
            return []
        raise paramiko.BadAuthenticationType("not allowed", [a for a in self.allow if a != "none"])

    def auth_password(self, user, pw, fallback=True):
        self.calls.append("password")
        if "password" not in self.accept:
            raise paramiko.AuthenticationException("bad password")

    def auth_interactive(self, user, handler):
        self.calls.append("keyboard-interactive")
        self.answers = handler("", "", [("User Name: ", True), ("Password: ", False)])
        if "keyboard-interactive" not in self.accept:
            raise paramiko.AuthenticationException("bad")


class SshLayerAuth(unittest.TestCase):
    def run_auth(self, allow, accept):
        t = FakeTransport(allow, accept)
        client = sx.FlexibleAuthClient()
        client.get_transport = lambda: t
        return client, t

    def test_inband_switch_never_gets_the_password_at_ssh_layer(self):
        client, t = self.run_auth(["none"], ["none"])
        client._auth("u", "secret", None, None, False, False, False, False, False, None, None)
        self.assertEqual(t.calls, ["none"])

    def test_password_auth_switch(self):
        client, t = self.run_auth(["password"], ["password"])
        client._auth("u", "secret", None, None, False, False, False, False, False, None, None)
        self.assertEqual(t.calls, ["none", "password"])

    def test_keyboard_interactive_answers_user_and_password(self):
        client, t = self.run_auth(["keyboard-interactive"], ["keyboard-interactive"])
        client._auth("u", "secret", None, None, False, False, False, False, False, None, None)
        self.assertEqual(t.answers, ["u", "secret"])

    def test_wrong_password_is_an_authentication_failure(self):
        client, t = self.run_auth(["password", "keyboard-interactive"], [])
        with self.assertRaises(paramiko.AuthenticationException):
            client._auth("u", "bad", None, None, False, False, False, False, False, None, None)
        self.assertEqual(t.calls, ["none", "password", "keyboard-interactive"])


class InbandLogin(unittest.TestCase):
    def conn(self, screens):
        c = object.__new__(sx.SxSSH)
        c.host, c.username, c.password, c.RETURN = "10.0.0.7", "u", "secret", "\n"
        c.sent = []
        feed = iter(screens)
        c.read_until_pattern = lambda pattern, re_flags=0, read_timeout=None: next(feed)
        c.write_channel = c.sent.append
        return c

    def test_completes_user_name_and_password_prompts(self):
        c = self.conn(["\r\nUser Name:", "u\r\nPassword:", "******\r\nasw-test-02#"])
        c.special_login_handler()
        self.assertEqual(c.sent, ["u\n", "secret\n"])

    def test_password_auth_switch_goes_straight_to_prompt(self):
        c = self.conn(["\r\nasw-test-02#"])
        c.special_login_handler()
        self.assertEqual(c.sent, [])

    def test_rejected_credentials_raise_auth_error_so_next_set_is_tried(self):
        from netmiko.exceptions import NetmikoAuthenticationException
        c = self.conn(["User Name:", "Password:", "\r\nUser Name:"])
        c.paramiko_cleanup = mock.Mock()
        with self.assertRaises(NetmikoAuthenticationException):
            c.special_login_handler()
        self.assertEqual(devices._short_error(NetmikoAuthenticationException("x")), devices.AUTH_FAILED)
        c.paramiko_cleanup.assert_called_once()  # a refused login must not leave a session on the switch


class Detection(unittest.TestCase):
    def test_hints_from_neighbour_platform_strings(self):
        cases = {"Cisco CBS350-24P-4G (PID:CBS350-24P-4G)-VSD": "cisco_s300",
                 "Cisco C1300-48P-4X (PID:C1300-48P-4X)-V": "cisco_s300",
                 "Cisco SG300-28 (PID:SRW2024-K9)-VSD": "cisco_s300",
                 "Cisco SG350X-24P (PID:SG350X-24P-K9)-VSD": "cisco_s300",
                 "cisco C9300X-12Y": "cisco_ios", "cisco WS-C3650-24TD": "cisco_ios",
                 "cisco C1000-24FP-4X-L": "cisco_ios", "N9K-C93360YC-FX2": "cisco_nxos",
                 "ArubaOS (MODEL: 515)": ""}
        for text, want in cases.items():
            self.assertEqual(parse.platform_hint(text), want, text)

    def probe(self, outcomes):
        """outcomes: {device_type: Exception or 'show version' text}. Returns (result, tried)."""
        tried = []

        def connect(device_type, *a):
            tried.append(device_type)
            out = outcomes[device_type]
            if isinstance(out, Exception):
                raise out
            conn = mock.Mock()
            conn.send_command_timing.side_effect = lambda cmd, **kw: out if cmd == "show version" else ""
            conn.send_command.side_effect = lambda cmd, **kw: out
            return conn
        with mock.patch.object(devices, "_connect", side_effect=connect):
            return devices.detect_platform("10.0.0.7", "u", "p", CFG), tried

    def test_probe_uses_the_sg_cbs_driver_first_and_classifies_any_cisco(self):
        """Regression (live): probing with the Catalyst driver first hung on SG/CBS switches."""
        from tests import fixtures as fx
        self.assertEqual(self.probe({"cisco_s300": "Active-image: flash://x\n  Version: 3.2.1.1"}), ("cisco_s300", ["cisco_s300"]))
        self.assertEqual(self.probe({"cisco_s300": fx.IOS_VERSION}), ("cisco_ios", ["cisco_s300"]))
        self.assertEqual(self.probe({"cisco_s300": "Cisco Nexus Operating System (NX-OS) Software"}), ("cisco_nxos", ["cisco_s300"]))

    def test_catalyst_driver_only_when_the_probe_cannot_log_in(self):
        from tests import fixtures as fx
        timeout = type("NetmikoTimeoutException", (Exception,), {})("stalled")
        self.assertEqual(self.probe({"cisco_s300": timeout, "cisco_ios": fx.IOS_VERSION}), ("cisco_ios", ["cisco_s300", "cisco_ios"]))

    def test_wrong_password_is_one_failed_login_not_two(self):
        from netmiko.exceptions import NetmikoAuthenticationException
        with self.assertRaises(NetmikoAuthenticationException):
            self.probe({"cisco_s300": NetmikoAuthenticationException("no"), "cisco_ios": "unused"})

    def test_connect_uses_the_inband_driver_for_sx(self):
        with mock.patch("netmon.sx.SxSSH") as drv:
            devices._connect("cisco_s300", "10.0.0.7", "u", "p", CFG)
        self.assertEqual(drv.call_args.kwargs["device_type"], "cisco_s300")
        self.assertEqual(drv.call_args.kwargs["host"], "10.0.0.7")


if __name__ == "__main__":
    unittest.main()
