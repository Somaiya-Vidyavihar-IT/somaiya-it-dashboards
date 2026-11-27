"""Switches the dashboard has read before (state/known-switches.json) and the up/down miss counter.

A known switch that stops answering tcp/22 stays on the dashboard as "Down" with its last-known
inventory and uplinks until a team member removes it (forget). Pure helpers plus load/save.
"""
import os
from datetime import datetime

FILE = ("state", "known-switches.json")
INVENTORY = ("hostname", "model", "serial", "version", "institute", "role", "platform")
# A check this soon after the last one does not add a second miss: "2 misses in a row" must be two
# checks minutes apart, not e.g. the 08:00 full run and the 08:00 15-minute check seconds apart.
MIN_CHECK_GAP_SECONDS = 5 * 60
NOT_REMOVABLE = "only a Down switch or one whose SSH login fails can be removed"


def load(store):
    return store.read(*FILE, default={}) or {}


def exists(store):
    return os.path.exists(store.path(*FILE))


def save(store, known):
    store.write(known, *FILE)


def _uplinks(neighbors):
    return [{"localPort": n.get("localPort", ""), "name": n.get("name", ""), "ip": n.get("ip", ""),
             "remotePort": n.get("remotePort", "")}
            for n in neighbors or [] if n.get("kind") == "switch"]


def update_from_records(known, records, snapshot_devices, now):
    """Return the known switches after a full run.

    Every collected or SSH-failed record (both answered tcp/22) is remembered. A collected switch
    stores its inventory and switch neighbours; a failed one keeps what an earlier read stored and
    only gets a new lastSeen. firstSeen never changes.
    """
    devices = {d["ip"]: d for d in snapshot_devices}
    out = {ip: dict(e) for ip, e in (known or {}).items()}
    for r in records:
        ip = r["ip"]
        d = devices.get(ip) or {}
        old = out.get(ip)
        if r.get("ok"):
            entry = {"ip": ip, **{k: d.get(k, r.get(k)) or "" for k in INVENTORY}}
            entry["uplinks"] = _uplinks(d.get("neighbors"))
            entry.update(firstSeen=(old or {}).get("firstSeen") or now, lastSeenOk=now, lastSeen=now)
            out[ip] = entry
        elif old:
            out[ip] = dict(old, lastSeen=now)
        else:
            out[ip] = {"ip": ip, **{k: d.get(k) or "" for k in INVENTORY}, "uplinks": [], "firstSeen": now,
                       "lastSeenOk": None, "lastSeen": now}
    return out


def seed_from_snapshot(snapshot):
    """Known switches from an older latest.json (first run with this collector: no known-switches.json yet).

    Every row the previous run read (ok) or logged in to without success (failed) is remembered, so a
    switch that is off during the first run is still listed. Switch neighbours become its uplinks."""
    at = (snapshot or {}).get("generatedAt")
    out = {}
    for d in (snapshot or {}).get("devices", []):
        if d.get("status") not in ("ok", "failed") or not d.get("ip"):
            continue
        ok = d["status"] == "ok"
        out[d["ip"]] = {"ip": d["ip"], **{k: d.get(k) or "" for k in INVENTORY},
                        "uplinks": _uplinks(d.get("neighbors")) if ok else [], "firstSeen": at,
                        "lastSeenOk": at if ok else None, "lastSeen": at}
    return out


def forget(known, ip):
    """Remove one switch (dashboard: Remove on a Down or SSH-failed row). True if it was known."""
    return known.pop(ip, None) is not None


def is_down(status, reach_entry):
    """The snapshot row is "down" (R7) and the latest up/down check has not seen it answer since
    (R8: not up, 2+ misses; no live result: the snapshot status stands). "unreachable" (Not
    answering) and collected rows are never Down here."""
    if status != "down":
        return False
    if reach_entry is None:
        return True
    return not reach_entry.get("up") and int(reach_entry.get("misses") or 0) >= 2


def forget_refusal(ip, status, reach_entry, known, seeds, in_scope):
    """Why this address cannot be removed (dashboard Remove), or None if it can.

    status: its row in latest.json (None: not listed); reach_entry: its latest up/down result;
    known: known-switches.json; in_scope: config.in_scope (honours exclude).
    - A core seed is never removed (every run logs in to it; take it out of config.toml instead).
    - A known switch that is now out of scope or excluded can always be removed (it is not listed).
    - A Down row (is_down, R7/R8).
    - An SSH-failed row (R10): every run logs in to it with the switch passwords, so the team must be
      able to stop that, e.g. when the address now belongs to another host.
    """
    if ip in set(seeds or ()):
        return f"{ip} is a core seed in config.toml, so it cannot be removed here. Take it out of the seeds first."
    if not in_scope and ip in (known or {}):
        return None
    if status == "failed" or is_down(status, reach_entry):
        return None
    return f"{ip} is not listed as Down or SSH failed; {NOT_REMOVABLE}."


def switch_forget_refusal(ips, status, reach, known, seeds, in_scope_of):
    """forget_refusal for a whole switch (one row, maybe several IPs); shared by run.forget_switch and the API.

    ips: every IP of the switch, main first ([] for a replaced switch, which can always be removed);
    status: its row's status (None: not listed); reach: reachability.json "results", {ip: entry};
    in_scope_of: callable ip -> bool. Down only when no IP of the switch answers (the main IP's misses
    count); refused when any IP of it is a core seed.
    """
    if not ips:
        return None
    live = reach.get(ips[0])
    if len(ips) > 1:
        entries = [reach[i] for i in ips if i in reach]
        live = {"up": any(e.get("up") for e in entries), "misses": (live or {}).get("misses", 0)} if entries else None
    seed_set = set(seeds or ())
    subject = next((i for i in ips if i in seed_set), ips[0])
    return forget_refusal(subject, status, live, known, seeds, in_scope_of(subject))


def _time(value):
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None


def apply_probe(prev_results, probe_results, now, prev_checked_at=None):
    """Up/down results for the probed addresses: {ip: {up, since, latencyMs, misses}}.

    misses counts consecutive failed tcp/22 checks (0 when up). Files written before misses
    existed have no key; that counts as 0. Shared by the 15-minute check and the full run.
    prev_checked_at: checkedAt of the results in prev_results; when that check was less than
    MIN_CHECK_GAP_SECONDS ago, a switch already missing keeps its count (a first miss still counts).
    """
    prev_results = prev_results or {}
    last, current = _time(prev_checked_at), _time(now)
    recent = last is not None and current is not None and (current - last).total_seconds() < MIN_CHECK_GAP_SECONDS
    out = {}
    for ip, latency in probe_results.items():
        up = latency is not None
        before = prev_results.get(ip) or {}
        since = before.get("since") if before.get("up") == up and before.get("since") else now
        if up:
            misses = 0
        else:
            misses = 0 if before.get("up") else int(before.get("misses") or 0)
            if not (recent and misses):
                misses += 1
        out[ip] = {"up": up, "since": since, "latencyMs": latency, "misses": misses}
    return out
