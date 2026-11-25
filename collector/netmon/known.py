"""Switches the dashboard has read before (state/known-switches.json) and the up/down miss counter.

A known switch that stops answering tcp/22 stays on the dashboard as "Down" with its last-known
inventory and uplinks until a team member removes it (forget). Pure helpers plus load/save.
"""

FILE = ("state", "known-switches.json")
INVENTORY = ("hostname", "model", "serial", "version", "institute", "role", "platform")


def load(store):
    return store.read(*FILE, default={}) or {}


def save(store, known):
    store.write(known, *FILE)


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
            entry["uplinks"] = [{"localPort": n.get("localPort", ""), "name": n.get("name", ""), "ip": n.get("ip", ""),
                                 "remotePort": n.get("remotePort", "")}
                                for n in d.get("neighbors") or [] if n.get("kind") == "switch"]
            entry.update(firstSeen=(old or {}).get("firstSeen") or now, lastSeenOk=now, lastSeen=now)
            out[ip] = entry
        elif old:
            out[ip] = dict(old, lastSeen=now)
        else:
            out[ip] = {"ip": ip, **{k: d.get(k) or "" for k in INVENTORY}, "uplinks": [], "firstSeen": now,
                       "lastSeenOk": None, "lastSeen": now}
    return out


def forget(known, ip):
    """Remove one switch (dashboard: Remove on a Down row). True if it was known."""
    return known.pop(ip, None) is not None


def is_down(status, reach_entry):
    """Down as the dashboard shows it now, for a snapshot row of a switch not read on the last run
    (status "down" or "unreachable"): the live up/down check decides (2+ misses); with no live
    result the snapshot's own status stands. Collected rows are never removable."""
    if status not in ("down", "unreachable"):
        return False
    if reach_entry is None:
        return status == "down"
    return not reach_entry.get("up") and int(reach_entry.get("misses") or 0) >= 2


def apply_probe(prev_results, probe_results, now):
    """Up/down results for the probed addresses: {ip: {up, since, latencyMs, misses}}.

    misses counts consecutive failed tcp/22 checks (0 when up). Files written before misses
    existed have no key; that counts as 0. Shared by the 15-minute check and the full run.
    """
    prev_results = prev_results or {}
    out = {}
    for ip, latency in probe_results.items():
        up = latency is not None
        before = prev_results.get(ip) or {}
        since = before.get("since") if before.get("up") == up and before.get("since") else now
        misses = 0 if up else (0 if before.get("up") else int(before.get("misses") or 0)) + 1
        out[ip] = {"up": up, "since": since, "latencyMs": latency, "misses": misses}
    return out
