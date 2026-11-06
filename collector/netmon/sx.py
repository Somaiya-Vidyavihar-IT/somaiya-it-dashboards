"""SSH login for Cisco SG / CBS / C1300 ("Sx") switches.

These switches only check the password at the SSH layer when `ip ssh password-auth` is configured.
Without it they accept the SSH session and then ask "User Name:" / "Password:" inside the CLI.
Netmiko's cisco_s300 driver does not answer those prompts (its docs require password-auth), so on
campus it failed as "authentication failed" or "timed out" on ~70 switches. This driver handles
both styles without any change on the switches.

When the account's password has expired (password aging), the switch forces a password change
at login ("Enter old password:"). The collector never changes anything: it declines with an
empty answer, which the switch accepts by letting the session continue (seen live), and the
switch is flagged so the network team can reset the password or disable aging.
"""
import re

import paramiko
from netmiko.cisco.cisco_s300 import CiscoS300SSH
from netmiko.exceptions import NetmikoAuthenticationException

USER_RE = re.compile(r"(User ?Name|Username|login as|User)\s*:\s*$", re.I)
PASS_RE = re.compile(r"Password\s*:\s*$", re.I)
OLD_PASS_RE = re.compile(r"old password\s*:\s*$", re.I)
YES_NO_RE = re.compile(r"[(\[]\s*Y\s*/\s*N\s*[)\]]", re.I)
PROMPT_RE = re.compile(r"[>#]\s*$")
# No inline (?i): netmiko wraps the pattern in a group, so case-insensitivity goes in re_flags.
# "\?" stops on questions such as "auto generate new password (Y/N)[N] ?".
LOGIN_PATTERN = r"(?:User ?Name\s*:|Username\s*:|login as\s*:|User\s*:|Password\s*:|\?|[>#])\s*$"


class PasswordExpired(Exception):
    """The switch insists on a password change before allowing a session. Nothing was changed."""


def inband_step(text):
    """What the last line of the login output asks for:
    'old-password', 'question', 'password', 'user', 'done' or ''."""
    lines = [l for l in (text or "").replace("\r", "\n").split("\n") if l.strip()]
    tail = lines[-1].strip() if lines else ""
    if OLD_PASS_RE.search(tail):
        return "old-password"
    if YES_NO_RE.search(tail):
        return "question"
    if PASS_RE.search(tail):
        return "password"
    if USER_RE.search(tail):
        return "user"
    if PROMPT_RE.search(tail):
        return "done"
    return ""


class FlexibleAuthClient(paramiko.SSHClient):
    """SSH-layer authentication: 'none' (in-band login), then password, then keyboard-interactive."""

    def _auth(self, username, password, *args, **kwargs):
        transport = self.get_transport()
        try:
            transport.auth_none(username)
            return
        except paramiko.BadAuthenticationType as exc:
            allowed = list(getattr(exc, "allowed_types", None) or [])
        except paramiko.AuthenticationException:
            allowed = ["password", "keyboard-interactive"]
        last = None
        if "password" in allowed:
            try:
                transport.auth_password(username, password, fallback=False)
                return
            except paramiko.AuthenticationException as exc:
                last = exc
        if "keyboard-interactive" in allowed:
            def answer(title, instructions, prompts):
                return [username if re.search(r"user|name|login", p, re.I) else password for p, _echo in prompts]
            try:
                transport.auth_interactive(username, answer)
                return
            except paramiko.AuthenticationException as exc:
                last = exc
        raise last or paramiko.AuthenticationException(f"No usable SSH auth method (switch allows {allowed})")


class SxSSH(CiscoS300SSH):
    """cisco_s300 driver that also completes the in-band "User Name:" / "Password:" login."""

    password_expired = False

    def _get_ssh_client_instance(self):
        return FlexibleAuthClient()

    def special_login_handler(self, delay_factor: float = 1.0) -> None:
        # Netmiko only cleans up after failures in session preparation, not in the login step, so a
        # refused in-band login would otherwise leave an SSH session open on the switch.
        try:
            self._inband_login()
        except Exception:
            self.paramiko_cleanup()
            raise

    def _inband_login(self) -> None:
        sent_password = False
        declined = 0
        for _ in range(10):
            data = self.read_until_pattern(pattern=LOGIN_PATTERN, re_flags=re.I, read_timeout=20)
            step = inband_step(data)
            if step == "done":
                return
            if step == "old-password":
                # Forced password change (expired password). Decline with an empty answer: never
                # send the password here and never enter a new one.
                declined += 1
                if declined > 2:
                    raise PasswordExpired(f"Password expired on {self.host}: the switch insists on a change (none made)")
                self.password_expired = True
                self.write_channel(self.RETURN)
            elif step == "question":
                # e.g. "auto generate new password (Y/N)": any answer could lead to a change.
                raise PasswordExpired(f"Password expired on {self.host}: the switch asks to change it (declined, none made)")
            elif step == "user":
                if sent_password:  # asked to log in again: the switch rejected these credentials
                    raise NetmikoAuthenticationException(f"Switch rejected the credentials (in-band login) on {self.host}")
                self.write_channel(self.username + self.RETURN)
            elif step == "password":
                if sent_password:
                    raise NetmikoAuthenticationException(f"Switch rejected the password (in-band login) on {self.host}")
                self.write_channel(self.password + self.RETURN)
                sent_password = True
        raise NetmikoAuthenticationException(f"In-band login did not reach a prompt on {self.host}")
