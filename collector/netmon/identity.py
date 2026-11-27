"""One switch = one serial set: the identity map (state/identity.json).

A switch is known by its member serials, not by an address. Every IP whose login returns a serial of a
known switch is that switch; one of its IPs is the main IP (shown and logged in to), the others get a login
only for the daily re-check. Entry per switch, keyed by the first real serial seen (never changed):
{"serials", "main", "hostname", "ips": {ip: {"firstSeen", "lastLogin", "lastTry"}}, "firstSeen", "lastRead",
 "replacedBy", "last": {hostname, model, version, platform, serial; ip once replaced}, "mainChangedFrom",
 "mainChangedAt"}.
Pure logic plus load/save; no SSH or network I/O.
"""
import ipaddress
import re
from datetime import datetime, timezone

from . import config as config_mod
from .parse import base_name

FILE = "identity.json"
RECHECK_HOURS = 20
PLACEHOLDERS = frozenset({"UNKNOWN", "NA", "NONE", "NOTAVAILABLE"})
SERIAL_RE = re.compile(r"[A-Z0-9]{6,}")
LAST_FIELDS = ("hostname", "model", "version", "platform", "serial")


def load(store):
    ident = store.read("state", FILE, default=None)
    if not isinstance(ident, dict) or not isinstance(ident.get("switches"), dict):
        return {"switches": {}}
    return ident


def save(store, ident):
    store.write(ident, "state", FILE)


def real_serials(values):
    """Serials that can identify a switch: upper-cased, de-duplicated, 6+ letters/digits, not a placeholder."""
    out = []
    for value in values or ():
        s = str(value or "").strip().upper()
        if SERIAL_RE.fullmatch(s) and s.strip("0") and s not in PLACEHOLDERS and s not in out:
            out.append(s)
    return out


def _owners(ident):
    return {ip: key for key, sw in ident.get("switches", {}).items() for ip in sw.get("ips") or {}}


def alias(ident):
    """Every IP of every switch -> that switch's main IP."""
    switches = ident.get("switches", {})
    return {ip: switches[key]["main"] for ip, key in _owners(ident).items()}


def owner(ident, ip):
    return _owners(ident).get(ip)


def _ip_key(ip):
    try:
        return 0, int(ipaddress.IPv4Address(ip)), ""
    except ValueError:
        return 1, 0, str(ip)


def other_ips(ident, key):
    """The switch's IPs except its main, oldest first."""
    sw = ident.get("switches", {}).get(key) or {}
    ips = sw.get("ips") or {}
    return sorted((ip for ip in ips if ip != sw.get("main")),
                  key=lambda ip: (not ips[ip].get("firstSeen"), ips[ip].get("firstSeen") or "", _ip_key(ip)))


def _time(value):
    try:
        t = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    return t if t.tzinfo else t.replace(tzinfo=timezone.utc)


def needs_login(ident, ip, now):
    """False only for an other IP of a known switch tried (or logged in to) less than RECHECK_HOURS ago."""
    key = owner(ident, ip)
    if key is None:
        return True
    sw = ident["switches"][key]
    if sw.get("main") == ip:
        return True
    seen = sw["ips"][ip]
    times = [t for t in (_time(seen.get("lastLogin")), _time(seen.get("lastTry"))) if t]
    current = _time(now)
    if not times or current is None:
        return True
    return (current - max(times)).total_seconds() >= RECHECK_HOURS * 3600


def replacement_rechecks(ident, rec):
    """A's other IPs to log in to once now, when rec (an IP of switch A) returned serials A does not have."""
    serials = real_serials(rec.get("serials")) if rec.get("ok") else []
    key = owner(ident, rec.get("ip"))
    if not serials or key is None or set(serials) & set(ident["switches"][key].get("serials") or []):
        return []
    return [ip for ip in other_ips(ident, key) if ip != rec["ip"]]


def row_fields(ident, ip):
    """Identity fields of a device row for ip: its switch (id = key, ips main first, otherIps, serials,
    mainIpChanged when the main IP changed), or for an IP no switch owns id "ip:<ip>" and ips [ip]."""
    key = owner(ident, ip)
    sw = ident["switches"][key] if key is not None else None
    if not sw or not sw.get("main"):
        return {"id": "ip:" + ip, "ips": [ip], "otherIps": [], "serials": []}
    others = other_ips(ident, key)
    out = {"id": key, "ips": [sw["main"]] + others, "otherIps": others, "serials": list(sw.get("serials") or [])}
    if sw.get("mainChangedFrom"):
        out["mainIpChanged"] = {"from": sw["mainChangedFrom"], "at": sw.get("mainChangedAt")}
    return out


def fold(records, ident, reach=None):
    """One record per switch, for build. Returns (records, alias {any IP of a switch: its main IP}).

    A switch's records become one copy with ip = the main IP and the row_fields, chosen in this order: the
    main's ok record, the newest ok record (refreshedAt; tie: main first, then other_ips order), the main's
    failed record, any. A record read on another IP gets ipWarning. Records of an IP no switch owns pass
    through (copied) with id "ip:<ip>". reach is accepted for the build call shape; the choice does not use it.
    """
    ident = ident or {}
    switches = ident.get("switches") or {}
    owners = {ip: key for ip, key in _owners(ident).items() if switches[key].get("main")}
    alias = {ip: switches[key]["main"] for ip, key in owners.items()}
    groups, order = {}, []
    for r in records:
        key = owners.get(r.get("ip"))
        if key is None:
            order.append((None, r))
        else:
            if key not in groups:
                order.append((key, None))
            groups.setdefault(key, []).append(r)
    out = []
    for key, r in order:
        if key is None:
            c = dict(r)
            if r.get("ip"):
                c.update(row_fields({}, r["ip"]), serials=list(r.get("serials") or []))
            out.append(c)
            continue
        main = switches[key]["main"]
        rank = {ip: i for i, ip in enumerate([main] + other_ips(ident, key))}
        recs = groups[key]
        ok = [x for x in recs if x.get("ok")]
        chosen = (next((x for x in ok if x["ip"] == main), None)
                  or (max(ok, key=lambda x: (x.get("refreshedAt") or "", -rank[x["ip"]])) if ok else None)
                  or next((x for x in recs if x["ip"] == main), None) or recs[0])
        c = dict(chosen, **row_fields(ident, main))
        c["ip"] = main
        if chosen["ip"] != main:
            c["ipWarning"] = f"Read on {chosen['ip']}; main IP {main} did not answer"
        out.append(c)
    return out, alias


def forget(ident, key):
    """Remove one switch with all its IPs (dashboard Remove). True if it was in the map."""
    return ident.get("switches", {}).pop(key, None) is not None


def _groups(reads):
    """This call's reads, grouped when their serials intersect (union-find), in first-record order."""
    parent = list(range(len(reads)))

    def find(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    by_serial = {}
    for i, (_, serials) in enumerate(reads):
        for s in serials:
            if s in by_serial:
                a, b = find(i), find(by_serial[s])
                parent[max(a, b)] = min(a, b)
            else:
                by_serial[s] = i
    groups = {}
    for i, (r, serials) in enumerate(reads):
        g = groups.setdefault(find(i), {"records": [], "serials": [], "hostname": base_name(r.get("hostname"))})
        g["records"].append(r)
        g["serials"] += [s for s in serials if s not in g["serials"]]
    return list(groups.values())


def _match(g, switches, owners, fresh, log):
    """The existing switch this group is, or None (a new switch). fresh: switches read this call -> new serials.

    A switch not read this call matches on its old serial list only through one of its own IPs or the same
    hostname. More than one match: most shared serials, tie: the one holding the group's first serial."""
    mine, ips = set(g["serials"]), {r["ip"] for r in g["records"]}
    cands, skipped = [], []
    for key, sw in switches.items():
        shared = len(mine & set(fresh.get(key, sw.get("serials") or [])))
        if not shared:
            continue
        if key in fresh or any(owners.get(ip) == key for ip in ips) or (sw.get("hostname") and sw["hostname"] == g["hostname"]):
            cands.append((shared, key))
        else:
            skipped.append(key)
    where = ", ".join(sorted(ips, key=_ip_key))
    if not cands:
        for key in skipped:
            log.append(f"{where} shares serials with {key}'s old member list but its hostname {g['hostname'] or '(none)'} "
                       f"is not {switches[key].get('hostname') or '(none)'}; kept as its own switch")
        return None
    first = g["serials"][0]
    cands.sort(key=lambda c: (-c[0], first not in fresh.get(c[1], switches[c[1]].get("serials") or []), c[1]))
    if len(cands) > 1:
        log.append(f"{where}: serials match {len(cands)} switches ({', '.join(k for _, k in cands)}); "
                   f"ambiguous, joined {cands[0][1]} (most shared serials)")
    return cands[0][1]


def _new_key(switches, serial):
    key, n = serial, 2
    while key in switches:
        key, n = f"{serial}-{n}", n + 1
    return key


def _choose_main(key, sw, cfg, now, logged_in, answered, first_seen, log):
    """Candidates: IPs that logged in this call, else that answer tcp/22, else all. Inside the management ranges
    wins; the current main is kept while it qualifies; else the earliest firstSeen, tie: the lowest IP."""
    ips = sw["ips"]
    cands = [ip for ip in ips if ip in logged_in] or [ip for ip in ips if ip in answered] or list(ips)
    pool = [ip for ip in cands if config_mod.in_management(cfg, ip)] or cands
    old = sw.get("main")
    if old in pool:
        return

    def seen(ip):
        fs = first_seen.get(ip) or ips[ip].get("firstSeen")
        return not fs, fs or "", _ip_key(ip)

    sw["main"] = min(pool, key=seen) if pool else None
    if old and sw["main"]:
        sw["mainChangedFrom"], sw["mainChangedAt"] = old, now
        log.append(f"{key}: main IP changed from {old} to {sw['main']}")


def update(ident, records, cfg, now, answered, first_seen):
    """Apply one run's (or one refresh's) login records to the map in place; return plain-English log lines.

    Only ok records with real serials create, join, move or merge; any other record of an IP in the map only
    sets its lastTry. answered: IPs that answered tcp/22 this call; first_seen: known-switches firstSeen per IP.
    """
    switches = ident.setdefault("switches", {})
    first_seen = first_seen or {}
    log, reads = [], []
    owners = _owners(ident)
    for r in records:
        serials = real_serials(r.get("serials")) if r.get("ok") and r.get("ip") else []
        if serials:
            reads.append((r, serials))
        elif r.get("ip") in owners:
            switches[owners[r["ip"]]]["ips"][r["ip"]]["lastTry"] = now
    logged_in = {r["ip"] for r in records if r.get("ok") and r.get("ip")}

    # 1. match: groups holding one of a switch's own IPs first, so later groups see who was read this call
    groups = _groups(reads)

    def direct(g):
        return any(set(g["serials"]) & set(switches[owners[r["ip"]]].get("serials") or [])
                   for r in g["records"] if r["ip"] in owners)

    groups = [g for g in groups if direct(g)] + [g for g in groups if not direct(g)]
    fresh = {}
    for g in groups:
        g["target"] = _match(g, switches, owners, fresh, log)
        if g["target"]:
            fresh[g["target"]] = g["serials"]

    # 2. apply: join, add new IPs, move IPs. An IP whose old switch was read on another IP this call also
    # moves, but that is not a replacement: the old switch keeps its other IPs and a warning is logged.
    touched, moved_to = set(), {}
    for g in groups:
        target, take = g["target"], g["records"]
        if target is None:
            target = _new_key(switches, g["serials"][0])
            switches[target] = {"serials": [], "main": None, "hostname": "", "ips": {}, "firstSeen": now,
                                "lastRead": None, "replacedBy": None}
        sw = switches[target]
        for r in take:
            ip, cur = r["ip"], owners.get(r["ip"])
            if ip not in sw["ips"]:
                if cur is not None:
                    old = switches[cur]
                    sw["ips"][ip] = old["ips"].pop(ip)
                    touched.add(cur)
                    if cur in fresh:
                        log.append(f"{ip} answers for more than one switch ({cur} and {target}); "
                                   f"it moves to {target}, {cur} keeps its other IPs")
                    else:
                        moved_to[cur] = (target, old.get("main") or ip)
                        log.append(f"{ip} moved from {cur} to {target} (hardware replaced?)")
                else:
                    sw["ips"][ip] = {"firstSeen": first_seen.get(ip) or now}
                owners[ip] = target
            sw["ips"][ip]["lastLogin"] = now
        sw.update(serials=list(g["serials"]), hostname=g["hostname"], lastRead=now, replacedBy=None,
                  last={k: take[0].get(k) or "" for k in LAST_FIELDS})
        touched.add(target)

    # 3. a switch left without IPs: merged (it shares serials with where its IPs went) or replaced; a
    # replaced one keeps the main IP it had in last["ip"] ("its IP X now answers as <serial>")
    for key, (dest, last_ip) in moved_to.items():
        old = switches.get(key)
        if old is None or old["ips"]:
            continue
        if set(old.get("serials") or []) & set(switches[dest]["serials"]):
            del switches[key]
            touched.discard(key)
            log.append(f"{key} merged into {dest}")
        else:
            old["replacedBy"] = dest
            old["last"] = dict(old.get("last") or {}, ip=last_ip)
            log.append(f"{key} has no IPs left; replaced by {dest} (shown Down until removed)")

    # 4. main IP of every switch read or changed
    for key in sorted(touched):
        _choose_main(key, switches[key], cfg, now, logged_in, answered, first_seen, log)
    return log
