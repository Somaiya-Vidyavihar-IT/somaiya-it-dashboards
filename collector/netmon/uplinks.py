"""Uplink load poller: outgoing load of each switch's uplink ports, every 10 minutes (scheduled task).

Core and distribution switches are read every round (uplink_fast_minutes); other switches when their
last read is at least uplink_slow_minutes old. Uplinks are the ports with a switch neighbour (for
access and unlinked switches: only neighbours that are collected switches or say Cisco). Port-channel
bundles and their member ports are both reported. Read-only: one login per switch with the credential
set that worked last time, then only 'show' commands. Writes data/uplinks.json for the dashboard
during the round as well as at its end, so a round stopped at the 9-minute task limit loses nothing.
"""
import logging
import re
import time
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from datetime import datetime, timezone

from . import creds, devices, parse
from .store import Store

log = logging.getLogger("netmon.uplinks")

# Every command must be a read-only show command. {port} is filled only after PORT_RE accepts the name.
UPLINK_COMMANDS = {
    "cisco_ios": {"interface": "show interfaces {port}", "portchannel": "show etherchannel summary"},
    "cisco_nxos": {"interface": "show interface {port}", "portchannel": "show port-channel summary"},
    # SG/CBS/C1300 give byte totals, not a rate: read twice SX_SAMPLE_SECONDS apart (network team choice A).
    "cisco_s300": {"status": "show interfaces status", "counters": "show interfaces counters"},
}

for _cmds in UPLINK_COMMANDS.values():
    for _cmd in _cmds.values():
        assert _cmd.startswith("show "), f"non read-only command in UPLINK_COMMANDS: {_cmd}"

# A port name that may go into a command: letters, then numbers separated by '/', optional '.sub'.
PORT_RE = re.compile(r"^[A-Za-z][A-Za-z\-]*\d+(/\d+){0,3}(\.\d+)?$")
SX_SAMPLE_SECONDS = 30  # SG/CBS/C1300: seconds between the two counter readings
# Indirections so tests can run the 30-second wait instantly.
_sleep = time.sleep
_clock = time.monotonic
NO_LOGIN = "No working login known yet"
LOCK = "uplinks.lock"
GRACE_SECONDS = 30  # a scheduled start a few seconds early must not skip a 10-minute round
# Task Scheduler stops the task at 9 minutes: no switch is started after 7, so the ones in progress
# can finish and be written. Results are also written every SAVE_EVERY switches during the round.
BUDGET_SECONDS = 7 * 60
SAVE_EVERY = 10


def safe_port(port):
    return isinstance(port, str) and PORT_RE.fullmatch(port) is not None


def command(platform, key, port=None):
    """The show command for this platform; a port name is checked before it is put in."""
    cmd = UPLINK_COMMANDS[platform][key]
    if port is not None:
        if not safe_port(port):
            raise ValueError(f"unexpected port name {port!r}")
        cmd = cmd.format(port=port)
    assert cmd.startswith("show ")
    return cmd


def select_ports(device, collected=None):
    """Uplink ports of one switch from its CDP/LLDP neighbours: [{port, neighbor, neighborIp}].

    Core/distribution: every switch neighbour. Other roles: switch neighbours that are collected
    switches (peer; `collected` = IPs read on the last run, None = any resolved peer) or whose
    name/platform/description says Cisco. Port names that fail PORT_RE are skipped."""
    fast = device.get("role") in ("core", "distribution")
    out, seen = [], set()
    for n in device.get("neighbors") or []:
        if n.get("kind") != "switch":
            continue
        if not fast:
            peer = n.get("peer") or ""
            known = bool(peer) and (collected is None or peer in collected)
            text = " ".join(str(n.get(k) or "") for k in ("name", "platform", "desc")).lower()
            if not known and "cisco" not in text:
                continue
        port = n.get("localPort")
        if not safe_port(port):
            if port:
                log.debug("%s: neighbour port name %r skipped (unexpected format)", device.get("ip"), port)
            continue
        if port in seen:
            continue
        seen.add(port)
        out.append({"port": port, "neighbor": n.get("name") or n.get("ip") or "", "neighborIp": n.get("ip") or ""})
    return out


def _parse_time(value):
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None


def due(device, state, now, cfg):
    """state: {ip: ISO time of the last read}. Core/distribution every uplink_fast_minutes, others uplink_slow_minutes."""
    col = cfg["collector"]
    last = _parse_time(state.get(device["ip"])) if state.get(device["ip"]) else None
    if last is None:
        return True
    minutes = col["uplink_fast_minutes"] if device.get("role") in ("core", "distribution") else col["uplink_slow_minutes"]
    return (now - last).total_seconds() >= minutes * 60 - GRACE_SECONDS


def reading(rate, threshold):
    """One port's load from parse.interface_rate: bwMbps, outBps, outPct, interval, state, detail, alert."""
    bw, out = rate.get("bwKbps"), rate.get("outBps")
    row = {"bwMbps": None if bw is None else _mbps(bw), "outBps": out, "outPct": None,
           "interval": rate.get("interval") or "", "state": "not-read", "detail": "", "alert": False}
    if out is None:
        row["detail"] = "No output rate in the switch's answer"
    elif not bw:
        row["detail"] = "No link speed (BW) in the switch's answer, so no load percentage"
    else:
        row["outPct"] = round(out / (bw * 1000) * 100, 1)
        row.update(state="ok", alert=row["outPct"] >= threshold)
    return row


def _mbps(kbps):
    value = round(kbps / 1000, 1)
    return int(value) if value == int(value) else value


def _row(d, port, kind, bundle, neighbor, at, **fields):
    row = {"ip": d["ip"], "hostname": d.get("hostname") or "", "institute": d.get("institute") or "",
           "role": d.get("role") or "", "port": port, "kind": kind, "bundle": bundle, "neighbor": neighbor,
           "bwMbps": None, "outBps": None, "outPct": None, "interval": "", "readAt": at, "state": "not-read",
           "detail": "", "alert": False}
    row.update(fields)
    return row


def _plan(selected, bundles):
    """[(port, kind, bundle, neighbour)]: each uplink port, or its bundle followed by every member."""
    member_of = {m: b for b, members in bundles.items() for m in members}
    names = {p["port"]: p["neighbor"] for p in selected}
    out, done = [], set()
    for p in selected:
        port = p["port"]
        bundle = port if port.startswith("Po") else member_of.get(port)
        if bundle is None:
            if port not in done:
                done.add(port)
                out.append((port, "port", "", p["neighbor"]))
            continue
        if bundle in done:
            continue
        done.add(bundle)
        group = [bundle] + bundles.get(bundle, [])
        bundle_name = ", ".join(dict.fromkeys(names[x] for x in group if names.get(x)))
        out.append((bundle, "bundle", bundle, bundle_name))
        for m in bundles.get(bundle, []):
            if m not in done:
                done.add(m)
                out.append((m, "member", bundle, names.get(m) or bundle_name))
    kept = [t for t in out if safe_port(t[0])]
    for t in out:
        if t not in kept:
            log.warning("port name %r from the switch skipped (unexpected format)", t[0])
    return kept


def _rejected(text):
    m = devices.REJECTED.search("\n".join((text or "").splitlines()[:3]))
    return m.group(0).strip()[:200] if m else ""


def read_ip(d):
    """The IP a device row was read on (readIp, Ruling 10); rows without it were read on their ip."""
    return d.get("readIp") or d["ip"]


def poll_switch(d, platform, login, cfg, collected, at):
    """Rows for one switch, read on read_ip(d); the rows keep ip = d["ip"] (the main IP).
    login: (username, password) or None. Never raises."""
    selected = select_ports(d, collected)
    plain = lambda **kw: [_row(d, p["port"], "port", "", p["neighbor"], at, **kw) for p in selected]
    if not UPLINK_COMMANDS.get(platform):
        return plain(detail=f"Driver not known yet ({platform or 'none'})")
    if login is None:
        return plain(detail=NO_LOGIN)
    threshold, timeout = cfg["collector"]["uplink_threshold"], cfg["collector"]["command_timeout"]
    try:
        conn = devices._connect(platform, read_ip(d), login[0], login[1], cfg)
    except Exception as exc:
        return plain(state="failed", detail=devices._short_error(exc))
    if platform == "cisco_s300":
        try:
            return _read_sx(conn, d, selected, at, threshold, timeout)
        except Exception as exc:  # never expected: keep the round going
            log.exception("%s: uplink read failed", d["ip"])
            return plain(state="failed", detail=devices._short_error(exc))
        finally:
            try:
                conn.disconnect()
            except Exception:
                pass
    rows = []
    try:
        bundles = {}
        try:
            text = conn.send_command(command(platform, "portchannel"), read_timeout=timeout)
            bundles = {} if _rejected(text) else parse.port_channels(platform, text)
        except Exception as exc:
            log.warning("%s: port-channel summary not read: %s", d["ip"], devices._short_error(exc))
        for port, kind, bundle, neighbor in _plan(selected, bundles):
            row = _row(d, port, kind, bundle, neighbor, at)
            try:
                text = conn.send_command(command(platform, "interface", port), read_timeout=timeout)
            except Exception as exc:
                row.update(state="failed", detail=devices._short_error(exc))
            else:
                refused = _rejected(text)
                if refused:
                    row.update(state="failed", detail=refused)
                else:
                    row.update(reading(parse.interface_rate(platform, text), threshold))
            rows.append(row)
    except Exception as exc:  # never expected: keep the round going
        log.exception("%s: uplink read failed", d["ip"])
        rows = plain(state="failed", detail=devices._short_error(exc))
    finally:
        try:
            conn.disconnect()
        except Exception:
            pass
    return rows


def _read_sx(conn, d, selected, at, threshold, timeout):
    """SG/CBS/C1300 rows: link speeds, then the byte counters twice, SX_SAMPLE_SECONDS apart.

    Outgoing rate = bytes sent between the readings x 8 / seconds measured. Bundles are not known on
    these switches yet, so every uplink is a plain port. A refused or failed command fails every row."""
    plain = lambda **kw: [_row(d, p["port"], "port", "", p["neighbor"], at, **kw) for p in selected]
    texts = {}

    def ask(key):
        try:
            text = conn.send_command(command("cisco_s300", key), read_timeout=timeout)
        except Exception as exc:
            return devices._short_error(exc)
        texts[key] = text
        return _rejected(text)

    problem = ask("status")
    if problem:
        return plain(state="failed", detail=problem)
    speeds = parse.port_speeds(texts["status"])
    readings = []
    for i in range(2):
        if i:
            _sleep(SX_SAMPLE_SECONDS)
        before = _clock()
        problem = ask("counters")
        if problem:
            return plain(state="failed", detail=problem)
        readings.append(((before + _clock()) / 2, parse.counter_octets(texts["counters"])))
    (t1, first), (t2, second) = readings
    seconds = t2 - t1
    rows = []
    for p in selected:
        port = parse.short_if(p["port"])
        row = _row(d, p["port"], "port", "", p["neighbor"], at)
        a, b = first.get(port, {}).get("out"), second.get(port, {}).get("out")
        if a is None or b is None:
            row["detail"] = "Port not in the switch's counters"
        elif b < a:
            row["detail"] = "Counters were reset between the two readings; read again next time"
        elif seconds <= 0:
            row["detail"] = "No time between the two readings"
        else:
            mbps = speeds.get(port)
            rate = {"outBps": round((b - a) * 8 / seconds), "bwKbps": mbps * 1000 if mbps else None,
                    "interval": f"{SX_SAMPLE_SECONDS} seconds"}
            row.update(reading(rate, threshold))
            if not mbps:
                row["detail"] = "No link speed in 'show interfaces status' (port down?), so no load percentage"
        rows.append(row)
    return rows


def _iso(dt):
    return dt.astimezone(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def order(todo, polled):
    """Core and distribution first, then the others; within each, never read first, then the oldest read."""
    return sorted(todo, key=lambda d: (d.get("role") not in ("core", "distribution"), polled.get(d["ip"]) or ""))


def poll(cfg, now=None, clock=time.monotonic, budget=None):
    """One round: read the switches that are due and write uplinks.json. 0 done, 1 failed, 2 another round running.

    Task Scheduler stops the task after 9 minutes, so a round never relies on reaching its end: results
    are written every SAVE_EVERY switches, no new switch is started after `budget` seconds (BUDGET_SECONDS),
    and the switches left over are the oldest read, so they come first in the next round."""
    col = cfg["collector"]
    store = Store(col["data_dir"])
    now = now or datetime.now(timezone.utc)
    budget = BUDGET_SECONDS if budget is None else budget
    t0 = clock()
    at = _iso(now)
    if not store.acquire({"startedAt": at}, LOCK):
        log.warning("Another uplink round is in progress; exiting")
        return 2
    try:
        latest = store.read("latest.json", default={}) or {}
        cred_of = store.read("state", "credentials.json", default={}) or {}
        platforms = store.read("state", "platforms.json", default={}) or {}
        polled = store.read("state", "uplinks-polled.json", default={}) or {}
        previous = store.read("uplinks.json", default={}) or {}
        # R2: only switches the last run read (status ok); Down, failed and no-SSH rows are not logged in to.
        switches = [d for d in latest.get("devices", []) if d.get("status") == "ok" and d.get("ip")]
        collected = {d["ip"] for d in switches}
        # Switches with uplinks to read; one with none any more lists no rows (its old ones are not carried).
        with_ports = {d["ip"] for d in switches if select_ports(d, collected)}
        todo = order([d for d in switches if d["ip"] in with_ports and due(d, polled, now, cfg)], polled)
        # A switch read on another IP (readIp) is logged in to there, with that IP's remembered set and driver,
        # else its main IP's (an IP of the switch logs in with the main's set first, as in a full run).
        def cred_set(d):
            return cred_of.get(read_ip(d)) or cred_of.get(d["ip"])

        logins = {}
        if any(cred_set(d) for d in todo):
            logins = {name: (user, pw) for name, user, pw in creds.switch_sets(cfg)}
        try:  # load netmiko once in this thread (parallel first imports deadlock; see run.full_run)
            import netmiko  # noqa: F401
            import netmiko.exceptions  # noqa: F401
        except ImportError:
            pass

        def work(d):
            platform = platforms.get(read_ip(d)) or platforms.get(d["ip"]) or d.get("platform") or ""
            # Only the set that worked last time: never try the others (no failed logins against TACACS+).
            return d["ip"], poll_switch(d, platform, logins.get(cred_set(d)), cfg, collected, at)

        carried = {}
        for r in previous.get("ports", []):
            carried.setdefault(r.get("ip"), []).append(r)
        polled = {ip: t for ip, t in polled.items() if ip in collected}
        fresh = {}

        def save():
            ports = []
            for d in switches:
                if d["ip"] in fresh:
                    ports += fresh[d["ip"]]
                elif d["ip"] in with_ports:
                    ports += carried.get(d["ip"], [])
            store.write({"checkedAt": at, "threshold": col["uplink_threshold"], "ports": ports}, "uplinks.json")
            store.write(dict(polled, **{ip: at for ip in fresh}), "state", "uplinks-polled.json")
            return ports

        # Switches are started one by one (never queued all at once) so none starts after the budget.
        queue, pending, unsaved, out_of_time = iter(todo), set(), 0, False
        with ThreadPoolExecutor(max_workers=col["uplink_workers"]) as pool:
            while True:
                while not out_of_time and len(pending) < col["uplink_workers"]:
                    if clock() - t0 >= budget:
                        out_of_time = True
                        break
                    d = next(queue, None)
                    if d is None:
                        break
                    pending.add(pool.submit(work, d))
                if not pending:
                    break
                done, pending = wait(pending, return_when=FIRST_COMPLETED)
                for fut in done:
                    ip, rows = fut.result()  # poll_switch never raises
                    fresh[ip] = rows
                    unsaved += 1
                if unsaved >= SAVE_EVERY:
                    save()
                    unsaved = 0
        ports = save()
        if len(fresh) < len(todo):
            log.warning("Uplinks: time budget of %d s used up; %d switches left for the next round (read first then)",
                        budget, len(todo) - len(fresh))
        log.info("Uplinks: %d switches read, %d ports listed, %d at or over %s%% out", len(fresh), len(ports),
                 sum(bool(p.get("alert")) for p in ports), col["uplink_threshold"])
        return 0
    except Exception:
        log.exception("Uplink round failed")
        return 1
    finally:
        store.release(LOCK)
