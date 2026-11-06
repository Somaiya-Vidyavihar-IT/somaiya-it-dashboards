"""End-to-end SG/CBS login against a local fake SSH switch, using the real netmiko + paramiko stack.

The fake switch behaves like the campus SG/CBS/C1300 boxes: in "in-band" mode it accepts SSH
auth 'none' and then asks "User Name:" / "Password:" in the CLI; in "password-auth" mode it checks
the password at the SSH layer. No real device is contacted."""
import socket
import threading
import unittest

import paramiko

from netmon import devices
from netmon.config import validate

PROMPT = "asw-test-02#"
OUTPUTS = {"show version": "Active-image: flash://system/images/image.bin\r\n  Version: 3.2.1.1\r\n",
           "show system": "System Description:  CBS350-24P-4G 24-Port Gigabit PoE Managed Switch\r\n"
                          "System Up Time (days,hour:min:sec): 10,01:02:03\r\nSystem Name:  asw-test-02\r\n"}


class FakeSwitch(paramiko.ServerInterface):
    def __init__(self, mode):
        self.mode = mode
        self.event = threading.Event()

    def check_channel_request(self, kind, chanid):
        return paramiko.OPEN_SUCCEEDED if kind == "session" else paramiko.OPEN_FAILED_ADMINISTRATIVELY_PROHIBITED

    def get_allowed_auths(self, username):
        return "none" if self.mode in ("inband", "expired") else "password"

    def check_auth_none(self, username):
        return paramiko.AUTH_SUCCESSFUL if self.mode in ("inband", "expired") else paramiko.AUTH_FAILED

    def check_auth_password(self, username, password):
        ok = self.mode == "password" and (username, password) == ("fixture-user", "right")
        return paramiko.AUTH_SUCCESSFUL if ok else paramiko.AUTH_FAILED

    def check_channel_pty_request(self, *args):
        return True

    def check_channel_shell_request(self, channel):
        self.event.set()
        return True


def readline(chan, echo=True):
    buf = ""
    while True:
        ch = chan.recv(1).decode(errors="ignore")
        if not ch:
            return None
        if ch in "\r\n":
            if buf or ch == "\n":
                return buf
            continue
        buf += ch
        if echo:
            chan.send(ch)


EXPIRED = ("\r\n\r\nYour password has exceeded the maximum lifetime. Please change the password for better "
           "protection of your network.\r\nEnter old password: ")


def serve_one(sock, mode, host_key, state=None):
    state = state if state is not None else {}
    client, _ = sock.accept()
    t = paramiko.Transport(client)
    t.add_server_key(host_key)
    server = FakeSwitch(mode)
    try:
        t.start_server(server=server)
        chan = t.accept(20)
        if chan is None or not server.event.wait(10):
            return
        if mode in ("inband", "expired"):
            while True:
                chan.send("\r\n\r\nUser Name:")
                user = readline(chan)
                chan.send("\r\nPassword:")
                pw = readline(chan, echo=False)
                if (user, pw) == ("fixture-user", "right"):
                    break
                chan.send("\r\n\r\nLogin invalid")
        if mode == "expired":  # forced password change, as seen live on CBS350 fw 3.5.0.26
            chan.send(EXPIRED)
            old = readline(chan, echo=False)
            state["old_password_sent"] = old
            if old:  # a real answer leads into the change dialog: the collector must never get here
                chan.send("\r\n\r\nDo you want to auto generate new password (Y/N)[N] ?")
                state["change_dialog"] = True
                readline(chan)
                return
            chan.send("\r\n\r\nIncorrect old password.\r\n")
        chan.send("\r\n" + PROMPT)
        while True:
            line = readline(chan)
            if line is None:
                return
            cmd = line.strip()
            body = OUTPUTS.get(cmd, "")
            if cmd and not body and not cmd.startswith("terminal"):
                body = "% Unrecognized command\r\n"
            chan.send("\r\n" + body + PROMPT)
    except (EOFError, OSError, paramiko.SSHException):
        pass
    finally:
        t.close()


class LiveSxLogin(unittest.TestCase):
    host_key = None

    @classmethod
    def setUpClass(cls):
        cls.host_key = paramiko.RSAKey.generate(2048)

    def start(self, mode, state=None):
        sock = socket.socket()
        sock.bind(("127.0.0.1", 0))
        sock.listen(1)
        self.addCleanup(sock.close)
        threading.Thread(target=serve_one, args=(sock, mode, self.host_key, state), daemon=True).start()
        return sock.getsockname()[1]

    def login(self, mode, password):
        from netmon.sx import SxSSH
        port = self.start(mode)
        conn = SxSSH(device_type="cisco_s300", host="127.0.0.1", port=port, username="fixture-user", password=password,
                     conn_timeout=10, auth_timeout=10, banner_timeout=10, timeout=20, read_timeout_override=20,
                     fast_cli=False)
        self.addCleanup(conn.disconnect)
        return conn

    def test_inband_user_name_prompt(self):
        conn = self.login("inband", "right")
        self.assertIn("CBS350-24P-4G", conn.send_command("show system"))
        self.assertEqual(conn.base_prompt, "asw-test-02")

    def test_password_auth_mode(self):
        conn = self.login("password", "right")
        self.assertIn("Version: 3.2.1.1", conn.send_command("show version"))

    def test_wrong_password_inband_is_an_auth_failure(self):
        from netmiko.exceptions import NetmikoAuthenticationException
        with self.assertRaises(NetmikoAuthenticationException):
            self.login("inband", "wrong")

    def detect(self, mode, password="right"):
        from netmon.config import validate
        port = self.start(mode)
        cfg = validate({"collector": {"switch_username": "u", "ssh_port": port, "command_timeout": 20},
                        "subnets": [{"cidr": "10.0.0.0/24"}]})
        return devices.detect_platform("127.0.0.1", "fixture-user", password, cfg)

    def test_detection_end_to_end_both_login_styles(self):
        """The real probe (SG/CBS driver + quiet-period reads) against the fake switch."""
        self.assertEqual(self.detect("inband"), "cisco_s300")
        self.assertEqual(self.detect("password"), "cisco_s300")

    def test_expired_password_is_declined_without_changing_anything(self):
        """Regression (live CBS350): set2's password had expired; the switch forced a change, the old
        handler sent the password as the 'old password' and the login stalled at a Y/N question."""
        from netmon.config import validate
        state = {}
        port = self.start("expired", state)
        cfg = validate({"collector": {"switch_username": "u", "ssh_port": port, "command_timeout": 20},
                        "subnets": [{"cidr": "10.0.0.0/24"}]})
        conn = devices._connect("cisco_s300", "127.0.0.1", "fixture-user", "right", cfg)
        self.addCleanup(conn.disconnect)
        self.assertTrue(conn.password_expired)
        self.assertEqual(state.get("old_password_sent"), "", "the password is never sent to 'Enter old password'")
        self.assertNotIn("change_dialog", state)
        self.assertIn("CBS350-24P-4G", conn.send_command("show system"), "the session still works after declining")

    def test_login_trace_records_the_switch_but_never_the_password(self):
        import os
        import tempfile
        from netmon.config import validate
        port = self.start("inband")
        fd, log = tempfile.mkstemp(suffix=".log")
        os.close(fd)
        self.addCleanup(os.remove, log)
        cfg = validate({"collector": {"switch_username": "u", "ssh_port": port, "command_timeout": 20, "_session_log": log},
                        "subnets": [{"cidr": "10.0.0.0/24"}]})
        conn = devices._connect("cisco_s300", "127.0.0.1", "fixture-user", "right", cfg)
        conn.disconnect()
        with open(log, encoding="utf-8", errors="replace") as fh:
            text = fh.read()
        self.assertIn("User Name:", text)
        self.assertNotIn("right", text)

    def test_detection_wrong_password_is_an_auth_failure(self):
        from netmiko.exceptions import NetmikoAuthenticationException
        with self.assertRaises(NetmikoAuthenticationException):
            self.detect("inband", "wrong")

    def test_wrong_password_ssh_layer_is_an_auth_failure(self):
        from netmiko.exceptions import NetmikoAuthenticationException
        with self.assertRaises(NetmikoAuthenticationException) as ctx:
            self.login("password", "wrong")
        self.assertEqual(devices._short_error(ctx.exception), devices.AUTH_FAILED)


if __name__ == "__main__":
    unittest.main()
