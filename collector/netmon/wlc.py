"""Aruba AOS 8 controller collection (read-only show commands)."""
from . import parse
from .devices import _connect, _short_error, parse_rows

COMMANDS = [
    ("database", "show ap database long", True),
    ("active", "show ap active", False),
    ("lldp", "show ap lldp neighbors", False),
]
for _k, _cmd, _ in COMMANDS:
    assert _cmd.startswith("show "), _cmd


def collect(host, username, password, cfg, raw_sink=None):
    rec = {"host": host, "ok": False, "error": None, "warnings": [], "aps": [], "clients": {}, "apLldp": {}}
    timeout = cfg["collector"]["command_timeout"]
    try:
        conn = _connect("aruba_os", host, username, password, cfg)
    except Exception as exc:
        rec["error"] = _short_error(exc)
        return rec
    out = {}
    try:
        for key, cmd, textfsm in COMMANDS:
            try:
                text = conn.send_command(cmd, read_timeout=max(timeout, 120))
                out[key] = (parse_rows("aruba_os", cmd, text, rec["warnings"]) if textfsm else [], text)
                if raw_sink is not None:
                    raw_sink[cmd] = text
            except Exception as exc:
                rec["warnings"].append(f"{cmd}: {_short_error(exc)}")
    finally:
        try:
            conn.disconnect()
        except Exception:
            pass
    db_rows, db_text = out.get("database", ([], ""))
    rec["aps"] = parse.ap_database_best(db_rows, db_text)
    rec["clients"] = parse.ap_clients(parse.fixed_table(out.get("active", ([], ""))[1]))
    rec["apLldp"] = parse.ap_lldp(parse.fixed_table(out.get("lldp", ([], ""))[1]))
    rec["ok"] = bool(rec["aps"])
    if not rec["ok"]:
        rec["error"] = "No APs parsed from 'show ap database long'"
    return rec
