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
        return "none" if self.mode == "inband" else "password"

    def check_auth_none(self, username):
        return paramiko.AUTH_SUCCESSFUL if self.mode == "inband" else paramiko.AUTH_FAILED

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


def serve_one(sock, mode, host_key):
    client, _ = sock.accept()
    t = paramiko.Transport(client)
    t.add_server_key(host_key)
    server = FakeSwitch(mode)
    try:
        t.start_server(server=server)
        chan = t.accept(20)
        if chan is None or not server.event.wait(10):
            return
        if mode == "inband":
            while True:
                chan.send("\r\n\r\nUser Name:")
                user = readline(chan)
                chan.send("\r\nPassword:")
                pw = readline(chan, echo=False)
                if (user, pw) == ("fixture-user", "right"):
                    break
                chan.send("\r\n\r\nLogin invalid")
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

    def start(self, mode):
        sock = socket.socket()
        sock.bind(("127.0.0.1", 0))
        sock.listen(1)
        self.addCleanup(sock.close)
        threading.Thread(target=serve_one, args=(sock, mode, self.host_key), daemon=True).start()
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

    def test_wrong_password_ssh_layer_is_an_auth_failure(self):
        from netmiko.exceptions import NetmikoAuthenticationException
        with self.assertRaises(NetmikoAuthenticationException) as ctx:
            self.login("password", "wrong")
        self.assertEqual(devices._short_error(ctx.exception), devices.AUTH_FAILED)


if __name__ == "__main__":
    unittest.main()
