"""Assemble a dashboard snapshot from collected device and WLC records. Pure: no I/O."""
import re
from collections import defaultdict, deque
from datetime import datetime, timezone

from . import config as config_mod
from .parse import base_name, norm_mac

SCHEMA = 1
# Common Aruba/HPE AP OUIs. Shortcut: partial list, used only when LLDP/CDP text does not say
# "Aruba"; extend via collector.aruba_ouis in config.toml when an AP model is missed.
ARUBA_OUIS = {"00:0b:86", "00:1a:1e", "00:24:6c", "04:bd:88", "18:64:72", "20:4c:03", "24:de:c6",
              "40:e3:d6", "6c:f3:7f", "70:3a:0e", "84:d4:7e", "94:b4:0f", "9c:1c:12", "ac:a3:1e",
              "d8:c7:c8", "f0:5c:19"}


def iso_now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def neighbor_kind(n, extra_ouis=(), ap_names=frozenset(), ap_macs=frozenset(), phone_re=None):
    text = " ".join((n.get("platform", ""), n.get("desc", ""))).lower()
    caps = n.get("caps", "").lower()
    oui = n.get("chassis", "")[:8]
    port_mac = norm_mac(n.get("remotePort", ""))
    wlan_flag = re.search(r"(^|[\s,])W($|[\s,])", n.get("caps", ""))  # LLDP abbreviated caps, e.g. "B,W"
    # SG/CBS 'show lldp neighbors' gives no capabilities or description, so also recognise APs the
    # WLCs know by name or wired MAC (seen live: 14 of 18 unclassified neighbours were WLC APs).
    known_ap = n.get("name", "").lower() in ap_names or (n.get("chassis") or port_mac or "-") in ap_macs \
        or (port_mac and port_mac in ap_macs)
    if known_ap or "aruba" in text or "wlan" in caps or "access point" in caps or wlan_flag \
            or oui in ARUBA_OUIS or oui in extra_ouis or (port_mac and port_mac[:8] in extra_ouis):
        return "ap"
    if "phone" in caps or "phone" in text or is_phone_model(n, phone_re):
        return "phone"
    if re.search(r"switch|bridge|router", caps) or re.search(r"(^|[\s,])[BRS]($|[\s,])", n.get("caps", "")):
        return "switch"
    return "other"


def is_phone_model(n, phone_re):
    """Neighbour named or described as a configured phone model (collector.phone_models)."""
    return bool(phone_re) and bool(phone_re.search(" ".join((n.get("name", ""), n.get("platform", ""), n.get("desc", "")))))


def _ap_model(n):
    m = re.search(r"MODEL:\s*([\w-]+)", n.get("desc", "") + " " + n.get("platform", ""))
    return m.group(1) if m else ""


def build(cfg, records, wlcs=(), prev_baseline=None, prev_counters=None, scan_info=None, meta=None,
          ssh_closed=frozenset(), now=None):
    """Return (snapshot, links_baseline, counters)."""
    col = cfg["collector"]
    now = now or iso_now()
    extra_ouis = {norm_mac(o + "000000")[:8] for o in col.get("aruba_ouis", [])}
    baseline = {k: dict(v) for k, v in (prev_baseline or {}).items()}
    prev_counters = prev_counters or {}
    by_ip = {r["ip"]: r for r in records}
    ok = [r for r in records if r["ok"]]
    institute = lambda ip: (config_mod.subnet_for(cfg, ip) or {}).get("institute", "Unassigned")

    name_idx, mac_idx = {}, {}
    for r in ok:
        if r["hostname"]:
            name_idx.setdefault(base_name(r["hostname"]), r["ip"])
        if r.get("mac"):
            mac_idx[r["mac"]] = r["ip"]

    def resolve(n):
        if n["ip"] and n["ip"] in by_ip:
            return n["ip"]
        b = base_name(n["name"])
        if b and b in name_idx:
            return name_idx[b]
        if n.get("chassis") and n["chassis"] in mac_idx:
            return mac_idx[n["chassis"]]
        return None

    findings = []

    def finding(severity, kind, message, ip="", hostname=""):
        findings.append({"severity": severity, "type": kind, "ip": ip, "hostname": hostname, "message": message})

    # ---- neighbour observations -> links ----
    obs = defaultdict(list)  # (A, B) -> [(portA, reportedPortB, via)]
    unresolved = {}
    # Neighbour switches whose management IP is in scope but did not answer SSH (tcp/22) from the
    # collector. They get their own row ("no-ssh") linked to the switch that sees them, so a blocked
    # distribution switch (seen live: the hostel dists under the core) is visible, not just a finding.
    no_ssh = {}  # ip -> [(record that sees it, neighbour entry)]
    wlc_aps = [a for w in wlcs if w.get("ok") for a in w.get("aps", [])]
    ap_names = {a["name"].lower() for a in wlc_aps if a.get("name")}
    ap_macs = {a["mac"] for a in wlc_aps if a.get("mac")}
    ap_ouis = extra_ouis | {m[:8] for m in ap_macs}  # vendor prefixes of the APs this campus really has
    for r in ok:
        for n in r["neighbors"]:
            n["kind"] = neighbor_kind(n, ap_ouis, ap_names, ap_macs, col.get("phone_re"))
            if n["kind"] in ("ap", "phone"):
                continue
            peer = resolve(n)
            if peer is None and n["kind"] == "switch" and n["ip"] and n["ip"] in ssh_closed \
                    and config_mod.in_scope(cfg, n["ip"]):
                peer = n["ip"]
                no_ssh.setdefault(peer, []).append((r, n))
            n["peer"] = peer or ""
            if peer is None or peer == r["ip"]:
                if peer is None and n["kind"] == "switch":
                    unresolved.setdefault(base_name(n["name"]) or n["ip"], (r, n))
                continue
            obs[(r["ip"], peer)].append((n["localPort"], n["remotePort"], n["via"]))

    links = {}
    done_pairs = set()
    for (a, b) in list(obs):
        if (a, b) in done_pairs:
            continue
        done_pairs.update({(a, b), (b, a)})
        side_a, side_b = list(obs.get((a, b), [])), list(obs.get((b, a), []))
        for pa, pb_rep, via in side_a:
            match = next((x for x in side_b if x[0] == pb_rep or x[1] == pa), None)
            if match:
                side_b.remove(match)
                _add_link(links, a, pa, b, match[0], sorted(set(via) | set(match[2])), both=True)
            else:
                _add_link(links, a, pa, b, pb_rep, via, both=False)
        for pb, pa_rep, via in side_b:
            _add_link(links, b, pb, a, pa_rep, via, both=False)

    port_status = {}
    for r in ok:
        for p in r["ports"]:
            port_status[(r["ip"], p["port"])] = p["status"]
    for key, link in links.items():
        sa = port_status.get((link["a"], link["aPort"]))
        sb = port_status.get((link["b"], link["bPort"]))
        down = [s for s in (sa, sb) if s and s not in ("connected", "up", "monitoring")]
        link["status"] = "down" if down else "up"
        old = baseline.get(key, {})
        baseline[key] = {"a": link["a"], "aPort": link["aPort"], "b": link["b"], "bPort": link["bPort"],
                         "firstSeen": old.get("firstSeen", now), "lastSeen": now}
        link["firstSeen"] = baseline[key]["firstSeen"]
        link["lastSeen"] = now

    ok_ips = {r["ip"] for r in ok}
    keep_days = col["keep_days"]
    for key, old in list(baseline.items()):
        if key in links:
            continue
        if _age_days(old["lastSeen"], now) > keep_days:
            del baseline[key]
            continue
        both_ok = old["a"] in ok_ips and old["b"] in ok_ips
        links[key] = dict(old, via=[], both=False, status="lost" if both_ok else "unknown")

    # ---- roles by hops from the core ----
    pattern = col.get("core_hostname_pattern") or ""
    core = {r["ip"] for r in ok if (re.search(pattern, r["hostname"] or "", re.I) if pattern else r["platform"] == "cisco_nxos")}
    adj = defaultdict(set)
    for link in links.values():
        if link["status"] in ("up", "down"):
            adj[link["a"]].add(link["b"])
            adj[link["b"]].add(link["a"])
    depth = {ip: 0 for ip in core}
    queue = deque(core)
    while queue:
        cur = queue.popleft()
        for nxt in adj[cur]:
            if nxt not in depth:
                depth[nxt] = depth[cur] + 1
                queue.append(nxt)
    role_of = lambda ip: {None: "unknown", 0: "core", 1: "distribution"}.get(depth.get(ip), "access")

    far = lambda ip: depth.get(ip, 10 ** 6)  # not reachable from the core counts as furthest
    for link in links.values():
        da, db = depth.get(link["a"]), depth.get(link["b"])
        if link["status"] == "lost":
            # A lost uplink usually leaves the downstream switch with no path to the core.
            link["uplink"] = (da is not None or db is not None) and da != db
        else:
            link["uplink"] = da is not None and db is not None and da != db
        link["downstream"] = (link["a"] if far(link["a"]) > far(link["b"]) else link["b"]) if link["uplink"] else ""
        if link["status"] == "lost":
            finding("critical", "link-lost", f"Link {link['aPort']} ↔ {link['bPort']} seen on {link['lastSeen']} is missing now",
                    link["a"], (by_ip.get(link["a"]) or {}).get("hostname", ""))

    # ---- devices ----
    new_counters = {}
    devices, problem_ports = [], []
    thr_err, thr_cpu, thr_poe = col["error_delta_threshold"], col["cpu_threshold"], col["poe_threshold"]
    for r in records:
        ip = r["ip"]
        d = {"ip": ip, "hostname": r.get("hostname") or "", "institute": institute(ip), "platform": r.get("platform", ""),
             "model": r.get("model", ""), "serial": r.get("serial", ""), "version": r.get("version", ""),
             "uptime": r.get("uptime", ""), "role": role_of(ip), "depth": depth.get(ip),
             "status": "ok" if r["ok"] else "failed", "error": r.get("error"), "warnings": r.get("warnings", [])[:10],
             "cpu": r.get("cpu"), "poe": None, "ports": None, "errDelta": None, "flags": [], "neighbors": [],
             "commands": r.get("commands", [])[:80]}
        if r.get("refreshedAt"):  # re-read on its own from the dashboard after the full run
            d.update(refreshedAt=r["refreshedAt"], refreshedBy=r.get("refreshedBy", ""))
        if not r["ok"]:
            d["flags"].append("ssh-failed")
            finding("warning", "ssh-failed", f"SSH collection failed: {r.get('error')}", ip)
            if ip in prev_counters:
                new_counters[ip] = prev_counters[ip]
            devices.append(d)
            continue
        hn = d["hostname"]
        statuses = [p["status"] for p in r["ports"]]
        d["ports"] = {"total": len(statuses), "connected": sum(s in ("connected", "up") for s in statuses),
                      "errDisabled": sum(s == "err-disabled" for s in statuses)}
        for p in r["ports"]:
            if p["status"] == "err-disabled":
                problem_ports.append(_pp(d, p["port"], "err-disabled", "Port is err-disabled", p))
        if r.get("poe"):
            avail, used = r["poe"]
            pct = round(used * 100 / avail) if avail else None
            d["poe"] = {"availableW": avail, "usedW": used, "pct": pct}
            if pct is not None and pct >= thr_poe:
                d["flags"].append("poe")
                finding("warning", "poe", f"PoE budget {pct}% used ({used} of {avail} W)", ip, hn)
        if d["cpu"] is not None and d["cpu"] >= thr_cpu:
            d["flags"].append("cpu")
            finding("warning", "cpu", f"CPU {d['cpu']}% (1-minute average)", ip, hn)
        if r.get("passwordExpired"):
            d["flags"].append("password-expired")
            finding("warning", "password-expired",
                    f"The read-only account's password has expired here (credential set {r.get('credentialSet') or '?'}); "
                    "the switch asked for a change, which the collector declined. Reset it or disable password aging.", ip, hn)
        if r.get("errors") is not None:
            new_counters[ip] = r["errors"]
            prev = prev_counters.get(ip)
            if prev is not None:
                total = 0
                for port, cur in r["errors"].items():
                    before = prev.get(port, 0)
                    delta = cur - before if cur >= before else cur
                    total += delta
                    if delta >= thr_err:
                        problem_ports.append(_pp(d, port, "errors", f"{delta} receive errors since last run", None))
                d["errDelta"] = total
        if d["ports"]["errDisabled"]:
            d["flags"].append("err-disabled")
        d["neighbors"] = [{"localPort": n["localPort"], "name": n["name"][:80], "ip": n["ip"], "remotePort": n["remotePort"],
                           "platform": n["platform"][:60], "via": n["via"], "kind": n.get("kind", "other"),
                           "peer": n.get("peer", "")} for n in r["neighbors"]]
        devices.append(d)

    for ip, seen in no_ssh.items():
        name = next((n["name"] for _, n in seen if n["name"]), "")[:80]
        platform = next((n["platform"] for _, n in seen if n["platform"]), "")
        seen_from = [{"ip": r["ip"], "hostname": r["hostname"], "port": n["localPort"], "remotePort": n["remotePort"]}
                     for r, n in seen]
        devices.append({"ip": ip, "hostname": name, "institute": institute(ip), "platform": "",
                        "model": re.sub(r"^cisco\s+", "", platform, flags=re.I)[:60], "serial": "", "version": "",
                        "uptime": "", "role": role_of(ip), "depth": depth.get(ip), "status": "no-ssh",
                        "error": "Does not answer SSH (tcp/22) from the collector", "warnings": [], "cpu": None,
                        "poe": None, "ports": None, "errDelta": None, "flags": ["no-ssh"], "neighbors": [],
                        "commands": [], "seenFrom": seen_from})
        where = ", ".join(f"{s['hostname'] or s['ip']} {s['port']}" for s in seen_from)
        finding("warning", "no-ssh", f"Switch {name or ip} ({ip}), seen on {where}, does not answer SSH (tcp/22) from "
                "the collector, so it and the switches behind it are not read. Allow SSH from the collector "
                "(VTY access list or firewall), then use Reconnect.", ip, name)

    host_of = {d["ip"]: d["hostname"] for d in devices}
    dev_by_ip = {d["ip"]: d for d in devices}
    for link in links.values():
        link["aName"], link["bName"] = host_of.get(link["a"], ""), host_of.get(link["b"], "")
        if link["status"] in ("down", "lost"):
            dev = dev_by_ip.get(link["downstream"])
            if dev and "uplink" not in dev["flags"]:
                dev["flags"].append("uplink")
            problem_ports.append({"ip": link["a"], "hostname": link["aName"], "institute": institute(link["a"]),
                                  "port": link["aPort"], "issue": f"link-{link['status']}",
                                  "detail": f"to {link['bName'] or link['b']} {link['bPort']}", "neighbor": link["bName"]})

    for key, (r, n) in unresolved.items():
        if not n["ip"]:
            msg = "advertised no management IP"
        elif not config_mod.in_scope(cfg, n["ip"]):
            msg = f"has management IP {n['ip']} outside the configured subnets"
        elif n["ip"] in ssh_closed:
            msg = f"at {n['ip']} does not answer SSH (tcp/22)"
        else:
            msg = f"at {n['ip']} was not collected"
        finding("info", "neighbor-not-collected", f"Neighbour {n['name']} on {n['localPort']} {msg}", r["ip"], r["hostname"])

    aps = _build_aps(cfg, ok, wlcs, name_idx, host_of, institute, finding, extra_ouis)

    for w in wlcs:
        if not w["ok"]:
            finding("warning", "wlc-failed", f"WLC {w['host']} collection failed: {w['error']}", w["host"])

    summary = {
        "switches": len(devices), "collected": len(ok), "failed": sum(d["status"] == "failed" for d in devices),
        "noSsh": len(no_ssh),
        "links": sum(l["status"] in ("up", "down") for l in links.values()),
        "linksLost": sum(l["status"] == "lost" for l in links.values()),
        "uplinksDown": sum(l["uplink"] and l["status"] in ("down", "lost") for l in links.values()),
        "problemPorts": len(problem_ports), "poeHigh": sum("poe" in d["flags"] for d in devices),
        "cpuHigh": sum("cpu" in d["flags"] for d in devices), "aps": len(aps),
        "apsUp": sum(a["status"].lower().startswith("up") for a in aps), "findings": len(findings),
    }
    institutes = defaultdict(lambda: {"switches": 0, "failed": 0, "aps": 0, "apsDown": 0, "problems": 0})
    for d in devices:
        i = institutes[d["institute"]]
        i["switches"] += 1
        i["failed"] += d["status"] != "ok"
        i["problems"] += bool(d["flags"])
    for a in aps:
        i = institutes[a["institute"]]
        i["aps"] += 1
        i["apsDown"] += not a["status"].lower().startswith("up")

    snapshot = {
        "schema": SCHEMA, "generatedAt": now, **(meta or {}), "scan": scan_info or {}, "summary": summary,
        "institutes": [dict(name=k, **v) for k, v in sorted(institutes.items())],
        "devices": sorted(devices, key=lambda d: (d["institute"], d["depth"] if d["depth"] is not None else 99, d["hostname"] or d["ip"])),
        "links": sorted(links.values(), key=lambda l: (l["aName"], l["aPort"])),
        "problemPorts": problem_ports, "aps": aps, "findings": findings,
    }
    return snapshot, baseline, new_counters


def _add_link(links, a, pa, b, pb, via, both):
    (x, px), (y, py) = sorted([(a, pa), (b, pb)])
    key = f"{x}#{px}|{y}#{py}"
    cur = links.get(key)
    if cur:
        cur["via"] = sorted(set(cur["via"]) | set(via))
        cur["both"] = cur["both"] or both
    else:
        links[key] = {"a": x, "aPort": px, "b": y, "bPort": py, "via": sorted(set(via)), "both": both}


def _pp(d, port, issue, detail, p):
    return {"ip": d["ip"], "hostname": d["hostname"], "institute": d["institute"], "port": port, "issue": issue,
            "detail": detail, "neighbor": (p or {}).get("name", "")}


def _age_days(then, now):
    try:
        a = datetime.fromisoformat(then.replace("Z", "+00:00"))
        b = datetime.fromisoformat(now.replace("Z", "+00:00"))
        return (b - a).total_seconds() / 86400
    except ValueError:
        return 0


def _build_aps(cfg, ok, wlcs, name_idx, host_of, institute, finding, extra_ouis):
    wlc_aps, clients, ap_lldp = {}, {}, {}
    for w in wlcs:
        if not w["ok"]:
            continue
        for ap in w["aps"]:
            cur = wlc_aps.get(ap["name"])
            if cur is None or (not cur["status"].lower().startswith("up") and ap["status"].lower().startswith("up")):
                wlc_aps[ap["name"]] = dict(ap, wlc=w["host"])
        for k, v in w["clients"].items():
            clients[k] = clients.get(k, 0) + v
        ap_lldp.update({k: v for k, v in w["apLldp"].items() if k not in ap_lldp})

    switch_side = []
    for r in ok:
        poe = {p["port"]: p for p in r.get("poePorts", [])}
        for n in r["neighbors"]:
            if n.get("kind") != "ap":
                continue
            p = poe.get(n["localPort"], {})
            mac = n.get("chassis") or norm_mac(n.get("remotePort", ""))  # SG/CBS LLDP: port ID is the AP's MAC
            switch_side.append({"name": n["name"], "mac": mac, "ip": n["ip"], "switchIp": r["ip"],
                                "switch": r["hostname"], "port": n["localPort"], "via": n["via"],
                                "model": _ap_model(n), "poeW": p.get("powerW"), "poeClass": p.get("class", "")})

    by_mac = {a["mac"]: a for a in wlc_aps.values() if a["mac"]}
    by_name = {a["name"].lower(): a for a in wlc_aps.values()}
    by_ip = {a["ip"]: a for a in wlc_aps.values() if a["ip"]}
    out, matched = {}, set()
    for s in switch_side:
        ap = by_mac.get(s["mac"]) or by_name.get(s["name"].lower()) or by_ip.get(s["ip"])
        key = ap["name"] if ap else f"switch:{s['switchIp']}#{s['port']}"
        if key in out:
            out[key]["seenBy"] = sorted(set(out[key]["seenBy"]) | set(s["via"]))
            continue
        rec = _ap_rec(ap) if ap else {"name": s["name"], "model": s["model"], "serial": "", "mac": s["mac"], "ip": s["ip"],
                                     "group": "", "status": "Not in WLC", "clients": None, "wlc": "", "seenBy": []}
        rec.update(switch=s["switch"], switchIp=s["switchIp"], port=s["port"], poeW=s["poeW"], poeClass=s["poeClass"])
        rec["seenBy"] = sorted(set(rec["seenBy"]) | set(s["via"]))
        if ap:
            matched.add(ap["name"])
        else:
            finding("warning", "ap-not-in-wlc", f"AP {s['name'] or s['mac']} on {s['port']} is not in any WLC AP database",
                    s["switchIp"], s["switch"])
        out[key] = rec

    for name, ap in wlc_aps.items():
        if name in matched:
            continue
        rec = _ap_rec(ap)
        rec.update(switch="", switchIp="", port="", poeW=None, poeClass="")
        out[name] = rec

    for rec in out.values():
        if rec["name"] in clients:
            rec["clients"] = clients[rec["name"]]
        side = ap_lldp.get(rec["name"])
        if side:
            rec["seenBy"] = sorted(set(rec["seenBy"]) | {"ap-lldp"})
            if not rec["switchIp"]:
                rec["switch"] = side["switch"]
                rec["switchIp"] = name_idx.get(base_name(side["switch"])) or side["switchIp"]
                rec["port"] = side["port"]
        rec["institute"] = institute(rec["switchIp"]) if rec["switchIp"] else institute(rec["ip"]) if rec["ip"] else "Unassigned"
        if rec["wlc"] and not rec["status"].lower().startswith("up"):
            finding("warning", "ap-down", f"AP {rec['name']} is {rec['status']} on the WLC", rec["switchIp"], rec["switch"])
        elif rec["wlc"] and not rec["switchIp"]:
            finding("info", "ap-no-switch-port", f"AP {rec['name']} is up on the WLC but no switch port was found", rec["ip"])
    return sorted(out.values(), key=lambda a: (a["institute"], a["name"]))


def _ap_rec(ap):
    return {"name": ap["name"], "model": ap["model"], "serial": ap["serial"], "mac": ap["mac"], "ip": ap["ip"],
            "group": ap["group"], "status": ap["status"], "clients": None, "wlc": ap["wlc"], "seenBy": ["wlc"]}
