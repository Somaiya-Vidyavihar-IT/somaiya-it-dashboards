"""Per-device SSH collection. Sends only read-only 'show' commands; never enters config mode."""
import logging
import re

from . import parse

log = logging.getLogger("netmon.devices")
AUTH_FAILED = "SSH authentication failed"

# (key, command, use TextFSM). Every command must be a read-only show command.
COMMANDS = {
    "cisco_ios": [
        ("version", "show version", True),
        ("cdp", "show cdp neighbors detail", True),
        ("lldp", "show lldp neighbors detail", True),
        ("ports", "show interfaces status", True),
        ("errors", "show interfaces counters errors", False),
        ("poe", "show power inline", False),
        ("cpu", "show processes cpu | include CPU", False),
    ],
    "cisco_nxos": [
        ("version", "show version", True),
        ("cdp", "show cdp neighbors detail", True),
        ("lldp", "show lldp neighbors detail", True),
        ("ports", "show interface status", True),
        ("errors", "show interface counters errors", False),
        ("cpu", "show system resources", False),
    ],
    "cisco_s300": [
        ("version", "show version", True),
        ("system", "show system", True),
        ("system_id", "show system id", True),
        ("cdp", "show cdp neighbors detail", False),
        ("lldp", "show lldp neighbors", True),
        ("ports", "show interfaces status", True),
        ("poe", "show power inline", False),
        ("cpu", "show cpu utilization", False),
    ],
}

for _cmds in COMMANDS.values():
    for _key, _cmd, _ in _cmds:
        assert _cmd.startswith("show "), f"non read-only command in COMMANDS: {_cmd}"


def _connect(device_type, ip, username, password, cfg):
    col = cfg["collector"]
    kwargs = dict(host=ip, username=username, password=password, conn_timeout=15, auth_timeout=20,
                  banner_timeout=20, timeout=col["command_timeout"],
                  read_timeout_override=col["command_timeout"], fast_cli=False)
    if device_type == "cisco_s300":
        # SG/CBS/C1300: our driver also completes the in-band "User Name:" login (see sx.py).
        from .sx import SxSSH
        return SxSSH(device_type="cisco_s300", **kwargs)
    from netmiko import ConnectHandler
    return ConnectHandler(device_type=device_type, **kwargs)


def detect_platform(ip, username, password, cfg):
    """Log in as cisco_ios, read 'show version' and pick the driver. SG/CBS/C1300 switches without
    'ip ssh password-auth' refuse that login (or stall at an in-band "User Name:" prompt), so any
    failure is retried once with the SG/CBS driver; if that is refused too, the error is the
    SG/CBS one, so a wrong password is still reported as an authentication failure."""
    try:
        conn = _connect("cisco_ios", ip, username, password, cfg)
    except Exception:
        conn = _connect("cisco_s300", ip, username, password, cfg)
        conn.disconnect()
        return "cisco_s300"
    try:
        text = conn.send_command("show version", read_timeout=cfg["collector"]["command_timeout"])
    finally:
        conn.disconnect()
    return parse.classify_show_version(text) or "cisco_ios"


def _run(conn, cmd, textfsm, timeout, platform, warnings):
    """Send once, keep the raw text, then try the TextFSM template. A template that meets output it
    does not expect (e.g. NX-OS status 'inactive') raises; we keep the text for fallback parsers
    instead of losing the whole command."""
    text = conn.send_command(cmd, read_timeout=timeout)
    return parse_rows(platform, cmd, text, warnings) if textfsm else [], text


def parse_rows(platform, cmd, text, warnings):
    try:
        from ntc_templates.parse import parse_output
        rows = parse_output(platform=platform, command=cmd, data=text)
        return rows if isinstance(rows, list) else []
    except Exception as exc:
        first = str(exc).strip().splitlines()[0] if str(exc).strip() else exc.__class__.__name__
        warnings.append(f"{cmd}: template could not read the output ({exc.__class__.__name__}: {first[:120]}); used fallback")
        return []


def collect(ip, platform, username, password, cfg, raw_sink=None):
    """Return a normalised device record. Never raises: failures are recorded on the record."""
    rec = {"ip": ip, "ok": False, "error": None, "platform": platform, "warnings": [],
           "hostname": "", "model": "", "serial": "", "version": "", "uptime": "", "mac": "",
           "cdp": [], "lldp": [], "neighbors": [], "ports": [], "errors": None,
           "poe": None, "poePorts": [], "cpu": None}
    timeout = cfg["collector"]["command_timeout"]
    try:
        conn = _connect(platform, ip, username, password, cfg)
    except Exception as exc:
        rec["error"] = _short_error(exc)
        return rec
    results = {}
    try:
        for key, cmd, textfsm in COMMANDS[platform]:
            try:
                rows, text = _run(conn, cmd, textfsm, timeout, platform, rec["warnings"])
                results[key] = (rows, text)
                if raw_sink is not None:
                    raw_sink[cmd] = text
            except Exception as exc:
                rec["warnings"].append(f"{cmd}: {_short_error(exc)}")
                results[key] = ([], "")
    finally:
        try:
            conn.disconnect()
        except Exception:
            pass
    _normalise(rec, platform, results)
    rec["ok"] = bool(rec["hostname"]) or bool(rec["neighbors"]) or bool(rec["ports"])
    if not rec["ok"]:
        rec["error"] = "Logged in but no output could be parsed"
    return rec


def _normalise(rec, platform, results):
    get = lambda k: results.get(k, ([], ""))
    if platform == "cisco_s300":
        rec.update(parse.version_sx(get("version")[0], get("system")[0], get("system_id")[0]))
        rec["cdp"] = parse.cdp_detail_text(get("cdp")[1])
    else:
        rec.update((parse.version_nxos if platform == "cisco_nxos" else parse.version_ios)(get("version")[0]))
        # Text parser first: on the live Nexus it read all 33 entries with names and IPs, while the
        # ntc template left names or IPs blank on 18. The template only fills gaps.
        rec["cdp"] = parse.fill_missing_ips(parse.cdp_detail_text(get("cdp")[1]), parse.cdp_textfsm(get("cdp")[0]))
    rec["lldp"] = parse.lldp_textfsm(get("lldp")[0])
    rec["neighbors"] = parse.merge_neighbors(rec["cdp"], rec["lldp"])
    rec["ports"] = parse.ports_textfsm(get("ports")[0], platform) or parse.ports_status_text(get("ports")[1])
    if platform != "cisco_s300":
        rec["errors"] = parse.error_counters(get("errors")[1]) or None
    poe_text = get("poe")[1]
    if poe_text and not re.search(r"Invalid input|not supported|Unrecognized", poe_text, re.I):
        rec["poe"] = parse.poe_totals(poe_text)
        if platform == "cisco_s300":
            rec["poePorts"] = parse.poe_ports_sx(poe_text)
        else:
            try:
                from ntc_templates.parse import parse_output
                rec["poePorts"] = parse.poe_ports_ios(parse_output(platform="cisco_ios", command="show power inline", data=poe_text))
            except Exception:
                rec["poePorts"] = []
    rec["cpu"] = parse.cpu_percent(get("cpu")[1])
    if not rec["hostname"] and rec["neighbors"]:
        rec["warnings"].append("hostname not parsed")
    rec.pop("cdp")
    rec.pop("lldp")


def _short_error(exc):
    text = str(exc).strip().splitlines()
    msg = text[0] if text else exc.__class__.__name__
    name = exc.__class__.__name__
    if "Authentication" in name:
        return AUTH_FAILED
    if "Timeout" in name:
        return "SSH timed out"
    return f"{name}: {msg}"[:200]
