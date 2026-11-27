"""Orchestration: full discovery run (LLD workflow), single-switch refresh and the 15-minute reachability check."""
import logging
import os
import re
import threading
import time
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait

from . import build, config as config_mod, creds, devices, identify, identity as identity_mod, known as known_mod, parse, \
    reports, scan, wlc
from .store import Store, stamp

log = logging.getLogger("netmon.run")
# A switch key as forget --id takes it: an identity key (first serial seen) or "ip:<address>".
KEY_RE = re.compile(r"^[A-Za-z0-9:.\-]+$")


def full_run(cfg, trigger="scheduled", by=""):
    col = cfg["collector"]
    store = Store(col["data_dir"])
    started = build.iso_now()
    if not store.acquire({"trigger": trigger, "by": by, "startedAt": started}):
        log.warning("Another run is in progress; exiting")
        return 2
    store.status(state="running", trigger=trigger, by=by, startedAt=started, finishedAt=None, error=None,
                 phase="sweep", done=0, total=0)
    store.audit(f"run-start trigger={trigger} by={by or '-'}")
    t0 = time.monotonic()
    try:
        snapshot = _discover(cfg, store, trigger, by, started)
        name = stamp()
        store.write(snapshot, "snapshots", f"snapshot-{name}.json")
        _publish(store, snapshot, name)
        store.prune(col["keep_days"])
        store.status(state="idle", finishedAt=build.iso_now(), phase="done", lastSuccessAt=snapshot["generatedAt"],
                     durationSec=round(time.monotonic() - t0))
        store.audit(f"run-done trigger={trigger} devices={snapshot['summary']['switches']} seconds={round(time.monotonic() - t0)}")
        return 0
    except Exception as exc:
        log.exception("Run failed")
        store.status(state="failed", finishedAt=build.iso_now(), error=str(exc)[:300])
        store.audit(f"run-failed trigger={trigger} error={str(exc)[:200]!r}")
        return 1
    finally:
        store.release()


def _publish(store, snapshot, name=None):
    """latest.json + the Excel and DOT downloads. A full run also keeps dated copies under reports/."""
    store.write(snapshot, "latest.json")
    for kind, ext, write in (("inventory", "xlsx", reports.write_excel), ("topology", "dot", reports.write_dot)):
        latest = store.path(f"{kind}-latest.{ext}")
        target = store.path("reports", f"{kind}-{name}.{ext}") if name else f"{latest}.{os.getpid()}.tmp"
        try:
            write(snapshot, target)
        except ImportError:
            log.warning("openpyxl not installed; Excel report skipped")
            continue
        if name:
            _copy(target, latest)
        else:
            os.replace(target, latest)


def _copy(src, dst):
    tmp = dst + ".tmp"
    with open(src, "rb") as a, open(tmp, "wb") as b:
        b.write(a.read())
    os.replace(tmp, dst)


class Progress:
    """Per-switch progress of a full run, for the dashboard's live list (data/run-progress.json).

    Worker threads only mark items; the main thread writes the file (at most about once a second).
    States: waiting, running, ok, failed, no-ssh (does not answer tcp/22). WLCs are items too.
    """

    def __init__(self, store, started, trigger, by):
        self.store, self.lock, self.items, self.t0, self.written = store, threading.Lock(), {}, {}, 0.0
        self.head = {"startedAt": started, "trigger": trigger, "by": by, "finishedAt": None}

    def mark(self, key, **fields):
        with self.lock:
            item = self.items.setdefault(key, {"ip": key, "kind": "switch", "hostname": "", "state": "waiting",
                                               "error": None, "seconds": None, "source": "", "at": None})
            if fields.get("state") == "running":
                self.t0[key] = time.monotonic()
            elif fields.get("state") in ("ok", "failed") and key in self.t0:
                fields["seconds"] = round(time.monotonic() - self.t0[key], 1)
            item.update(fields, at=build.iso_now())

    def write(self, force=False, **head):
        self.head.update(head)
        if not force and time.monotonic() - self.written < 1.0:
            return
        with self.lock:
            data = dict(self.head, updatedAt=build.iso_now(), items=[dict(v) for v in self.items.values()])
        self.store.write(data, "run-progress.json")
        self.written = time.monotonic()


def _discover(cfg, store, trigger, by, started):
    col = cfg["collector"]
    sets = creds.switch_sets(cfg)
    platforms = store.read("state", "platforms.json", default={}) or {}
    # Which credential set worked per switch (set names only, never passwords), so later runs
    # log in first time and do not generate repeated failed logins against TACACS+.
    cred_of = store.read("state", "credentials.json", default={}) or {}

    # 1. Campus IP scan. First tcp/22 to the seeds, last run's devices and known switches (also this run's
    #    up/down check, written at once); then tcp/22 over the rest of the sweep-enabled subnets and the
    #    web ports (collector.scan_ports: 443, 80) over the scan-only addresses.
    progress = Progress(store, started, trigger, by)
    targets = list(scan.sweep_targets(cfg))
    previous = store.read("latest.json", default={}) or {}
    phone_re = col.get("phone_re")
    # Phones that an earlier run took for switches (configured phone models) are not probed again.
    phones = {n["ip"] for d in previous.get("devices", []) for n in d.get("neighbors", [])
              if n.get("ip") and (n.get("kind") == "phone" or build.is_phone_model(n, phone_re))}
    phones |= {d["ip"] for d in previous.get("devices", []) if build.is_phone_model({"name": d.get("hostname") or ""}, phone_re)}
    known = [d["ip"] for d in previous.get("devices", []) if d["ip"] not in phones]
    # Switches read on any earlier run (Down ones included) are probed too, until a team member removes them.
    if known_mod.exists(store):
        remembered = known_mod.load(store)
    else:  # first run with this collector: remember what the previous run listed as read or SSH failed
        remembered = known_mod.seed_from_snapshot(
            dict(previous, devices=[d for d in previous.get("devices", []) if d.get("ip") not in phones]))
        if remembered:
            known_mod.save(store, remembered)
    known += [ip for ip in remembered if ip not in phones and ip not in known]
    hints = {}
    for d in previous.get("devices", []):
        _add_hints(hints, d.get("neighbors"))
    # Out of scope or excluded addresses are never probed (MINOR 3), known switches included.
    extra = list(dict.fromkeys(ip for ip in list(col["seeds"]) + known if config_mod.in_scope(cfg, ip)))
    # Seeds, last run's devices and known switches are switches already: no Cisco check for them.
    # Every other address is "scan-only" and gets the switch passwords only if it identifies as Cisco.
    trusted = set(extra)
    scan_only = [ip for ip in targets if ip not in trusted]
    scan_only_set = set(scan_only)
    rest = [ip for ip in dict.fromkeys(targets) if ip not in trusted]
    web_ports = [p for p in col["scan_ports"] if p != 22]
    sweep = {"sweepDone": 0, "sweepTotal": len(extra) + len(rest) + len(scan_only) * len(web_ports)}
    progress.write(force=True, phase="sweep", **sweep)
    log.info("Checking %d known switches, then scanning %d addresses on tcp/22 and %d on %s",
             len(extra), len(rest), len(scan_only), web_ports)
    # 1a. Up/down check of the switches the 15-minute check watches, applied and written before the
    #     sweep (which takes minutes), so a switch that missed the last check and this one is Down now
    #     and a 15-minute check made during the sweep is never overwritten by an older result.
    probes = _probe_steps(extra, 22, col, progress, sweep)
    watched = {d["ip"] for d in previous.get("devices", []) if d.get("status") != "no-ssh"} | set(remembered)
    _apply_reach(store, {ip: probes[ip] for ip in watched if ip in probes}, keep_others=True)
    # 1b. The rest of the campus.
    probes.update(_probe_steps(rest, 22, col, progress, sweep))
    web_open = {}  # scan-only ip -> web ports that answered
    for port in web_ports:
        for ip, lat in _probe_steps(scan_only, port, col, progress, sweep).items():
            if lat is not None:
                web_open.setdefault(ip, []).append(port)
    open_ips = [ip for ip, lat in probes.items() if lat is not None]
    # Only addresses that matter later (Reconnect, no-SSH rows): never the scan-only ones. Neighbours
    # that do not answer are added by the crawl below.
    closed = {ip for ip, lat in probes.items() if lat is None and ip not in scan_only_set}

    # Cisco check (identify): banner / web page of the scan-only hosts, read without logging in.
    cisco_ssh, web_only, skipped = _cisco_gate(cfg, [ip for ip in open_ips if ip in scan_only_set], web_open)
    seeds, last_run = set(col["seeds"]), set(known)
    for ip in probes:
        if ip in seeds or ip in last_run:
            progress.mark(ip, source="core seed" if ip in seeds else "last run",
                          state="waiting" if ip not in closed else "no-ssh",
                          error=None if ip not in closed else "No answer on SSH (tcp/22)")
    for ip in cisco_ssh:
        progress.mark(ip, source="IP scan (Cisco)")
    scan_info = {"subnets": len(cfg["subnets"]), "addresses": len(probes), "sshOpen": len(open_ips),
                 "webOpen": len(web_open), "ciscoSsh": len(cisco_ssh), "ciscoWebOnly": len(web_only),
                 "nonCiscoSkipped": skipped}
    progress.write(force=True, phase="ssh", scan=scan_info)
    # The only addresses the switch passwords may go to: seeds, last run's devices and known switches
    # that answer tcp/22, scan-only hosts that identified as Cisco, and (below) CDP/LLDP switch neighbours.
    login_ips = [ip for ip in open_ips if ip not in scan_only_set] + cisco_ssh
    # Built separately from login_ips on purpose: work() checks every login against it.
    allowed = {ip for ip in open_ips if ip in trusted} | set(cisco_ssh)

    # Identity map (one switch = one serial set; design 7.3). An other IP of a known switch gets no login until
    # its daily re-check (identity.needs_login: 20 h since its last try), except that when the switch's main IP
    # does not answer tcp/22 its first answering other IP is logged in to instead. No identity.json yet (first
    # run with this code): the map is empty and every IP is logged in to, as before.
    ident = identity_mod.load(store)
    alias = identity_mod.alias(ident)  # any IP of a known switch -> its main IP
    switches = ident["switches"]
    open_set, login_set = set(open_ips), set(login_ips)
    fell_back, rechecked = set(), set()  # switches given their one fallback login / replacement re-check this run
    kept = set()
    for key, sw in switches.items():
        if sw.get("ips") and sw.get("main") not in open_set:
            first = next((o for o in identity_mod.other_ips(ident, key) if o in login_set), None)
            if first:
                fell_back.add(key)
                kept.add(first)
                progress.mark(first, source=f"other IP of {_switch_name(sw)}; main IP {sw['main']} does not answer SSH")
    check_at = build.iso_now()
    skipped = {ip for ip in login_ips if ip not in kept and not identity_mod.needs_login(ident, ip, check_at)}
    for ip in skipped:
        sw = switches[identity_mod.owner(ident, ip)]
        progress.mark(ip, state="skipped", hostname=sw.get("hostname") or "",
                      error=f"Other IP of {_switch_name(sw)}; re-checked daily")
    login_ips = [ip for ip in login_ips if ip not in skipped]

    # 2-5. SSH authentication, device information, CDP + LLDP, recursive discovery.
    records, scanned = [], set()
    store.status(phase="ssh", done=0, total=len(login_ips))
    # Load netmiko once in this thread. Worker threads importing it for the first time at the same
    # moment raise "_DeadlockError ... netmiko.exceptions" (seen live: both Nexus seeds failed).
    import netmiko  # noqa: F401
    import netmiko.exceptions  # noqa: F401

    def work(ip):
        if ip not in allowed:  # last check before any password leaves the collector; never expected
            raise RuntimeError(f"{ip} was not cleared for login (not a known switch, neighbour or Cisco scan host)")
        progress.mark(ip, state="running")
        # Any IP of a known switch tries the switch's working set first (its main IP's credentials.json entry).
        return _login_collect(cfg, ip, sets, platforms, cred_of, hints, alias.get(ip))

    def followups(rec):
        """Other IPs of rec's switch to log in to now, each at most once per switch per run (design 7.3):
        the fallback when its main IP's login failed for a reason other than authentication (an
        authentication failure would fail the same way on another IP), and the replacement re-check when
        an IP returned serials its switch does not have."""
        key = identity_mod.owner(ident, rec["ip"])
        if key is None:
            return []
        sw, out = switches[key], []
        if not rec["ok"] and rec["ip"] == sw.get("main") and key not in fell_back \
                and devices.AUTH_FAILED not in (rec.get("error") or ""):
            fell_back.add(key)
            first = next((o for o in identity_mod.other_ips(ident, key) if o in allowed), None)
            if first and first not in scanned:
                out.append((first, f"other IP of {_switch_name(sw)}; login on main IP {sw['main']} failed"))
        if key not in rechecked:
            again = identity_mod.replacement_rechecks(ident, rec)
            if again:
                rechecked.add(key)
                out += [(o, f"other IP of {_switch_name(sw)}; {rec['ip']} now answers as another switch")
                        for o in again if o in allowed and o not in scanned]
        return out

    with ThreadPoolExecutor(max_workers=col["ssh_workers"]) as pool:
        pending = set()
        for ip in login_ips:
            scanned.add(ip)
            pending.add(pool.submit(work, ip))
        while pending:
            # The timeout only refreshes the progress file (switches still logging in).
            done, pending = wait(pending, timeout=2, return_when=FIRST_COMPLETED)
            for fut in done:
                rec = fut.result()
                records.append(rec)
                progress.mark(rec["ip"], state="ok" if rec["ok"] else "failed", hostname=rec.get("hostname") or "",
                              error=None if rec["ok"] else rec.get("error"))
                _add_hints(hints, rec.get("neighbors"))
                for ip, why in followups(rec):  # known IPs that answer tcp/22, so already in allowed
                    if ip not in scanned:
                        scanned.add(ip)
                        progress.mark(ip, state="waiting", error=None, source=why)
                        pending.add(pool.submit(work, ip))
                new = []
                for n in rec.get("neighbors", []):
                    ip = n.get("ip")
                    # An IP of a known switch is that switch, not a new one: not probed or logged in to here.
                    if ip and ip not in scanned and ip not in alias and config_mod.in_scope(cfg, ip) \
                            and build.neighbor_kind(n, phone_re=phone_re) == "switch":
                        scanned.add(ip)
                        new.append(ip)
                        progress.mark(ip, hostname=n.get("name", "")[:80],
                                      source=f"neighbour of {rec.get('hostname') or rec['ip']} {n.get('localPort', '')}".strip())
                if new:
                    # Not `reach`: that name holds this run's up/down results, which build() needs.
                    nb_probe = scan.probe(new, 22, col["connect_timeout"], 64)
                    for ip in new:
                        if nb_probe.get(ip) is None:
                            closed.add(ip)
                            progress.mark(ip, state="no-ssh", error="No answer on SSH (tcp/22)")
                        else:
                            allowed.add(ip)  # reported as a switch over CDP/LLDP: not gated
                            pending.add(pool.submit(work, ip))
            if done:
                store.status(done=len(records), total=len(records) + len(pending))
            progress.write(force=not pending)
    store.write(platforms, "state", "platforms.json")
    store.write(cred_of, "state", "credentials.json")
    scan_info["collected"] = sum(r["ok"] for r in records)

    # Aruba WLCs.
    wlcs = []
    if cfg["wlc"]["hosts"]:
        store.status(phase="wlc")
        for h in cfg["wlc"]["hosts"]:
            progress.mark(h, kind="wlc", source="WLC list in config")
        progress.write(force=True, phase="wlc")
        wuser, wpass = creds.get(cfg)
        for h in cfg["wlc"]["hosts"]:
            progress.mark(h, state="running")
            progress.write(force=True)
            w = wlc.collect(h, wuser, wpass, cfg)
            progress.mark(h, state="ok" if w["ok"] else "failed", error=None if w["ok"] else w.get("error"),
                          hostname=f"{len(w.get('aps', []))} APs" if w["ok"] else "")
            wlcs.append(w)

    # 6. Report generation.
    store.status(phase="report")
    progress.write(force=True, phase="report")
    prev_counters = store.read("state", "counters.json", default={}) or {}
    # Read again just before use: a Remove that finished as this run was starting must not bring the switch back.
    remembered = known_mod.load(store)
    # The identity map gets this run's logins (joins, moves, replacements, main IP choice). Read again too, so a
    # Remove that finished as this run was starting stays removed unless this run logged in to the switch.
    ident = identity_mod.load(store)
    first_seen = {ip: k.get("firstSeen") for ip, k in remembered.items() if k.get("firstSeen")}
    for line in identity_mod.update(ident, records, cfg, build.iso_now(), set(open_ips), first_seen):
        log.info("Identity map: %s", line)
    identity_mod.save(store, ident)
    # Up/down results as they are now: this run's own check (step 1a) and any 15-minute check since.
    # (Never the crawl's neighbour probe: that is nb_probe above.)
    reach = _reach_results(store)
    snapshot, baseline, counters = build.build(
        cfg, records, wlcs, store.read("state", "links-baseline.json", default={}),
        prev_counters, scan_info,
        {"trigger": trigger, "triggeredBy": by, "startedAt": started}, ssh_closed=frozenset(closed),
        known=remembered, reach=reach, web_only=web_only, identity=ident)
    snapshot["finishedAt"] = build.iso_now()
    progress.write(force=True, phase="done", finishedAt=snapshot["finishedAt"])
    store.write(baseline, "state", "links-baseline.json")
    store.write(counters, "state", "counters.json")
    known_mod.save(store, known_mod.update_from_records(remembered, records, snapshot["devices"], snapshot["finishedAt"]))
    # Everything build() needs, so one switch can later be re-read and the snapshot rebuilt without a
    # full run (refresh_switch, forget_switch). The same data as latest.json in raw form; never passwords.
    store.write({"generatedAt": snapshot["generatedAt"], "finishedAt": snapshot["finishedAt"],
                 "meta": {"trigger": trigger, "triggeredBy": by, "startedAt": started}, "scan": scan_info,
                 "records": records, "wlcs": wlcs, "closed": sorted(closed), "countersBase": prev_counters,
                 "webOnly": web_only}, "state", "last-run.json")
    return snapshot


def _apply_reach(store, probe_results, keep_others):
    """Apply one tcp/22 check to reachability.json and write it. The file is read right before the
    write (after the probe), so a check that finished meanwhile is counted, not overwritten.
    keep_others: keep results of addresses this check did not probe (full run: WLCs, no-SSH rows)."""
    cur = store.read("reachability.json", default={}) or {}
    prev = cur.get("results") or {}
    now = build.iso_now()
    out = known_mod.apply_probe(prev, probe_results, now, prev_checked_at=cur.get("checkedAt"))
    if keep_others:
        out = dict(prev, **out)
    store.write({"checkedAt": now, "results": out}, "reachability.json")
    return out


def _probe_steps(ips, port, col, progress, sweep):
    """scan.probe in steps, so the Discovery tab can show how far the campus scan has got."""
    out, step = {}, max(col["scan_concurrency"] * 16, 1024)
    for i in range(0, len(ips), step):
        part = ips[i:i + step]
        out.update(scan.probe(part, port, col["connect_timeout"], col["scan_concurrency"]))
        sweep["sweepDone"] += len(part)
        progress.write(**sweep)  # at most about once a second
    return out


def _cisco_gate(cfg, ssh_open, web_open):
    """Decide which scan-only hosts may get the switch passwords. Returns (cisco_ssh, web_only, skipped).

    ssh_open: scan-only hosts answering tcp/22; web_open: {scan-only ip: web ports that answered}.
    Cisco + SSH -> cisco_ssh (logged in to). Cisco + web only -> web_only {ip, ports, hint} (listed,
    never logged in to). Not Cisco -> only counted in skipped (not logged in to, not listed, not stored).
    """
    banners = scan.ssh_banners(ssh_open) if ssh_open else {}
    # The web page is read only where the SSH banner did not already say Cisco.
    need_web = {ip: ports for ip, ports in web_open.items() if not identify.is_cisco(banners.get(ip), "", cfg)}
    pages = identify.web_hints(need_web)
    cisco_ssh, web_only, skipped = [], [], 0
    for ip in ssh_open:
        if identify.is_cisco(banners.get(ip), pages.get(ip, ""), cfg):
            cisco_ssh.append(ip)
        else:
            skipped += 1
    ssh_set = set(ssh_open)
    for ip, ports in web_open.items():
        if ip in ssh_set:
            continue
        if identify.is_cisco(None, pages.get(ip, ""), cfg):
            web_only.append({"ip": ip, "ports": ports, "hint": pages[ip]})
        else:
            skipped += 1
    log.info("Cisco check: %d with SSH, %d web UI only, %d other hosts skipped", len(cisco_ssh), len(web_only), skipped)
    return cisco_ssh, sorted(web_only, key=lambda w: tuple(int(x) for x in w["ip"].split("."))), skipped


# Driver hints from what neighbours report over CDP/LLDP (e.g. "Cisco CBS350-24P-4G"), so SG/CBS/
# C1300 switches go straight to the in-band-login driver instead of failing a Catalyst login first.
def _add_hints(hints, neighbors):
    for n in neighbors or []:
        # CDP gives a platform; SG/CBS per-port LLDP gives only a system description.
        hint = parse.platform_hint(n.get("platform", "")) or parse.platform_hint(n.get("desc", ""))
        if n.get("ip") and hint:
            hints.setdefault(n["ip"], hint)


def _switch_name(sw):
    """An identity map entry's name for messages: its hostname, else its main IP."""
    return sw.get("hostname") or sw.get("main") or ""


def _login_collect(cfg, ip, sets, platforms, cred_of, hints, cred_ip=None):
    """Log in with each credential set in turn (the one that worked last time first) and collect one switch.

    cred_ip: the switch's main IP when ip is one of its IPs; its working set is tried first (design 7.3)."""
    by_name = {s[0]: s for s in sets}
    known_set = cred_of.get(cred_ip) if cred_ip in cred_of else cred_of.get(ip)
    order = ([by_name[known_set]] if known_set in by_name else []) + [s for s in sets if s[0] != known_set]
    rec = None
    failures = []  # (set name, error) per attempt
    # A login that times out with one account may still work with another (seen live: a switch
    # refused one account cleanly but stalled on another), so both outcomes try the next set.
    retryable = (devices.AUTH_FAILED, "SSH timed out")
    subnet_platform = (config_mod.subnet_for(cfg, ip) or {}).get("platform", "auto")
    for name, username, password in order:
        # Learned driver > driver forced for the subnet > neighbour hint > detect.
        platform = platforms.get(ip) or (subnet_platform if subnet_platform != "auto" else hints.get(ip, "auto"))
        if platform == "auto":
            try:
                platform = devices.detect_platform(ip, username, password, cfg)
            except Exception as exc:
                rec = {"ip": ip, "ok": False, "error": devices._short_error(exc), "platform": "", "neighbors": [],
                       "ports": [], "warnings": []}
                failures.append((name, rec["error"]))
                if rec["error"] in retryable:
                    continue  # try the next credential set
                return rec
        rec = devices.collect(ip, platform, username, password, cfg)
        if rec["ok"]:
            platforms[ip] = platform
            cred_of[ip] = name
            rec["credentialSet"] = name
            return rec
        failures.append((name, rec["error"]))
        if rec["error"] not in retryable:
            return rec
    if rec is None:
        return {"ip": ip, "ok": False, "error": "No switch credential set has a stored password", "platform": "",
                "neighbors": [], "ports": [], "warnings": []}
    if all(err == devices.AUTH_FAILED for _, err in failures):
        rec["error"] = f"SSH authentication failed with all {len(order)} credential sets"
    else:
        rec["error"] = "Login failed with every credential set: " + "; ".join(f"{n}: {e}" for n, e in failures)
    cred_of.pop(ip, None)
    return rec


class RefreshRefused(Exception):
    """A refresh that cannot go ahead; the message is shown on the dashboard."""


def _reach_results(store):
    """Up/down results of the latest check, {ip: {up, since, latencyMs, misses}}."""
    return (store.read("reachability.json", default={}) or {}).get("results", {}) or {}


def _rebuild_from_last(store, cfg, records, last, now, refreshed=None):
    """Rebuild latest.json from the last full run's raw records (state/last-run.json) without logging in.

    Shared by refresh_switch (`refreshed`: the switch just re-read) and forget_switch. Known switches
    come from state/known-switches.json and up/down misses from reachability.json, both as they are
    now (R6): a removed switch is gone, and Down / Not answering follows the latest 15-minute check. The
    identity map too (state/identity.json): one row per switch, as the full run builds it.
    """
    # Error deltas: other switches keep the base their full run used; a re-read one counts from its last read.
    counters = store.read("state", "counters.json", default={}) or {}
    base = dict(last.get("countersBase") or {})
    if refreshed:
        if refreshed in counters:
            base[refreshed] = counters[refreshed]
        else:
            base.pop(refreshed, None)
    snapshot, baseline, new_counters = build.build(
        cfg, records, last["wlcs"], store.read("state", "links-baseline.json", default={}), base,
        last["scan"], last["meta"], ssh_closed=frozenset(last.get("closed", [])), now=now,
        known=known_mod.load(store), reach=_reach_results(store), web_only=last.get("webOnly"),
        identity=identity_mod.load(store))
    snapshot.update(generatedAt=last["generatedAt"], finishedAt=last.get("finishedAt"), refreshedAt=now)
    store.write(baseline, "state", "links-baseline.json")
    if refreshed and refreshed in new_counters:
        counters[refreshed] = new_counters[refreshed]
        store.write(counters, "state", "counters.json")
    store.write(dict(last, records=records), "state", "last-run.json")
    _publish(store, snapshot)
    return snapshot


def refresh_switch(cfg, ip, by="", locked=False):
    """Re-read one switch and rebuild the snapshot around it (dashboard: Refresh switch / Reconnect).

    Every other switch and the WLCs come from the last full run's raw records (state/last-run.json),
    so links, roles, findings and APs are rebuilt exactly as a full run would, without logging in to
    anything else. New neighbours this switch reveals are crawled by the next full run, not here.
    Any IP of a switch may be posted (design 7.3a): the switch's main IP is logged in to, or when it does
    not answer tcp/22 its first answering other IP; the identity map is updated from the login.
    `locked`: the API already took refresh.lock for this request (check-and-take is atomic there).
    """
    store = Store(cfg["collector"]["data_dir"])
    if not locked and not store.acquire({"ip": ip, "by": by}, "refresh.lock"):
        log.warning("Another switch refresh is in progress; exiting")
        return 2
    started = build.iso_now()
    store.write({"state": "running", "ip": ip, "by": by, "startedAt": started, "finishedAt": None, "ok": None,
                 "error": None}, "refresh-status.json")
    store.audit(f"refresh-start ip={ip} by={by or '-'}")
    try:
        if store.running():
            raise RefreshRefused("A full discovery run is in progress; it reads this switch too.")
        last = store.read("state", "last-run.json", default=None)
        if not last:
            raise RefreshRefused("Switch refresh needs one full discovery run with the updated collector first.")
        ident = identity_mod.load(store)
        key = identity_mod.owner(ident, ip)
        main = ident["switches"][key]["main"] if key is not None else ip
        # Main first, then the other IPs oldest first; an other IP now out of scope or excluded is never used.
        ips = [ip] if key is None else [main] + [o for o in identity_mod.other_ips(ident, key)
                                                 if config_mod.in_scope(cfg, o)]
        read = {r["ip"] for r in last["records"]}
        if not (read | set(last.get("closed", []))) & set(ips):
            raise RefreshRefused(f"{ip} is not a switch from the last full discovery run.")
        answered = set()
        target = main
        if len(ips) > 1 or main not in read:
            # Listed as "no SSH from the collector", or a switch with more than one IP: check tcp/22 before any login.
            col = cfg["collector"]
            answers = scan.probe(ips, 22, max(col["connect_timeout"], 2.0), len(ips))
            answered = {i for i in ips if answers.get(i) is not None}
            target = next((i for i in ips if i in answered), None)
            if target is None:
                store.write({"state": "done", "ip": ip, "by": by, "startedAt": started, "finishedAt": build.iso_now(),
                             "ok": False, "error": "Still no answer on SSH (tcp/22) from the collector."},
                            "refresh-status.json")
                store.audit(f"refresh-done ip={ip} ok=False no-ssh")
                return 1
        sets = creds.switch_sets(cfg)
        platforms = store.read("state", "platforms.json", default={}) or {}
        cred_of = store.read("state", "credentials.json", default={}) or {}
        hints = {}
        for r in last["records"]:
            _add_hints(hints, r.get("neighbors"))
        import netmiko  # noqa: F401  (same warm import as the full run)
        rec = _login_collect(cfg, target, sets, platforms, cred_of, hints, main)
        now = build.iso_now()
        rec.update(refreshedAt=now, refreshedBy=by)
        # Merge into the newest saved state: a full run may have finished while we were logged in.
        # (One finishing in the milliseconds between this read and the writes below would be
        # overwritten until the next run; accepted, since both carry almost the same data.)
        last = store.read("state", "last-run.json", default=None) or last
        read, closed = {r["ip"] for r in last["records"]}, set(last.get("closed", []))
        if target in read:
            records = [rec if r["ip"] == target else r for r in last["records"]]
        elif target in closed:
            # It answers SSH now: it becomes a normal record. Switches behind it wait for the next full run.
            records = last["records"] + [rec]
            last = dict(last, closed=[c for c in last["closed"] if c != target])
        elif key is not None and (read | closed) & set(ips):
            records = last["records"] + [rec]  # an other IP the last run did not log in to (daily re-check only)
        else:
            raise RefreshRefused(f"{ip} is no longer in the last full discovery run.")
        if target != main:
            # The main did not answer tcp/22: as in a full run, it has no record and is listed as closed, so its
            # older read does not hide this one (the row stays on the main with ipWarning while the main is kept).
            records = [r for r in records if r["ip"] != main]
            last = dict(last, closed=sorted(set(last.get("closed", [])) | {main}))
        store.write(platforms, "state", "platforms.json")
        store.write(cred_of, "state", "credentials.json")
        # Newest map (a full run may have finished meanwhile); joins, moves and the main IP choice as in a full run.
        ident = identity_mod.load(store)
        first_seen = {i: k.get("firstSeen") for i, k in known_mod.load(store).items() if k.get("firstSeen")}
        for line in identity_mod.update(ident, [rec], cfg, now, answered, first_seen):
            log.info("Identity map: %s", line)
        identity_mod.save(store, ident)
        # Error counters are kept on the switch's main IP (its device row).
        _rebuild_from_last(store, cfg, records, last, now, refreshed=identity_mod.alias(ident).get(target, target))
        store.write({"state": "done", "ip": ip, "by": by, "startedAt": started, "finishedAt": build.iso_now(),
                     "ok": rec["ok"], "error": None if rec["ok"] else rec.get("error"),
                     "hostname": rec.get("hostname", ""), "snapshotRefreshedAt": now, "loginIp": target},
                    "refresh-status.json")
        store.audit(f"refresh-done ip={ip} ok={rec['ok']}" + (f" login-ip={target}" if target != ip else ""))
        return 0 if rec["ok"] else 1
    except Exception as exc:
        if not isinstance(exc, RefreshRefused):
            log.exception("Refresh of %s failed", ip)
        store.write({"state": "failed", "ip": ip, "by": by, "startedAt": started, "finishedAt": build.iso_now(),
                     "ok": False, "error": str(exc)[:300]}, "refresh-status.json")
        store.audit(f"refresh-failed ip={ip} error={str(exc)[:200]!r}")
        return 1
    finally:
        store.release("refresh.lock")


def forget_switch(cfg, ip=None, by="", locked=False, key=None):
    """Remove a switch from the dashboard (Remove): forget it and rebuild the snapshot without it.

    The switch is given by any of its IPs or by its key (`key`: identity key, or a row id "ip:<address>"
    for a switch without a serial); exactly one of the two. Rules per switch (design 7.6), through
    known.forget_refusal: a row the snapshot lists as Down (R7/R8) while no IP of the switch answers, a
    row whose SSH login fails (R10), or a known switch now out of scope or excluded; never a switch with
    a core seed among its IPs. A replaced switch (no IPs left in the identity map) can always be removed.
    The switch is dropped from identity.json, and every IP of it from known-switches.json and from the
    last run's records, so the next full run no longer logs in to those addresses unless it finds them
    again as CDP/LLDP neighbours or as Cisco hosts in the subnet scan. No login and no probe; the snapshot
    is rebuilt from state/last-run.json like refresh_switch. Shares refresh.lock with refresh_switch.
    """
    store = Store(cfg["collector"]["data_dir"])
    valid_key = bool(key) and KEY_RE.fullmatch(key) is not None
    who = f"ip={ip}" if ip else f"id={key}" if valid_key else f"id={key!r}"
    if not locked and not store.acquire(dict({"ip": ip or "", "by": by, "action": "forget"}, **({"id": key} if key else {})),
                                        "refresh.lock"):
        log.warning("A switch refresh or removal is in progress; exiting")
        return 2
    started = build.iso_now()
    head = {"action": "forget", "ip": ip or "", "id": key if valid_key else "", "by": by, "startedAt": started}
    store.write(dict(head, state="running", finishedAt=None, ok=None, error=None), "refresh-status.json")
    try:
        if store.running():
            raise RefreshRefused("A full discovery run is in progress. Remove the switch when it finishes.")
        if bool(ip) == bool(key):
            raise RefreshRefused("Give the switch's IP or its id (exactly one).")
        if key and not valid_key:
            raise RefreshRefused("That is not a valid switch id.")
        ident = identity_mod.load(store)
        if key and key not in ident["switches"]:
            if not key.startswith("ip:"):
                raise RefreshRefused(f"{key} is not a switch the collector knows.")
            ip, key = key[3:], None  # a switch without a serial is known by its one IP
        if ip:
            key = identity_mod.owner(ident, ip)
        head["id"] = key or ""
        sw = ident["switches"].get(key) if key is not None else None
        # Every IP of the switch, main first; [] for a replaced switch (its old IP now belongs to another one).
        if sw is None:
            ips = [ip]
        elif sw.get("ips"):
            ips = [sw["main"]] + identity_mod.other_ips(ident, key)
        else:
            ips = []
        latest = store.read("latest.json", default={}) or {}
        rows = latest.get("devices", [])
        # The switch's row: by its id, else by any of its IPs (rows from a collector without the identity map).
        row = next((d for d in rows if key is not None and d.get("id") == key), None) \
            or next((d for d in rows if d.get("ip") and (d["ip"] in ips or set(d.get("ips") or ()) & set(ips))), None) or {}
        remembered = known_mod.load(store)
        status = row.get("status")
        refusal = None
        if ips:
            seeds = cfg["collector"]["seeds"]
            reach = _reach_results(store)
            live = reach.get(ips[0])
            if len(ips) > 1:  # Down only when no IP of the switch answers; the main IP's misses count
                entries = [reach[i] for i in ips if i in reach]
                live = {"up": any(e.get("up") for e in entries), "misses": (live or {}).get("misses", 0)} if entries else None
            subject = next((i for i in ips if i in set(seeds)), ips[0])  # any IP of it a seed: refused
            refusal = known_mod.forget_refusal(subject, status, live, remembered, seeds, config_mod.in_scope(cfg, subject))
        if refusal:
            raise RefreshRefused(refusal)
        last = store.read("state", "last-run.json", default=None)
        if not last:
            raise RefreshRefused("Removing a switch needs one full discovery run with the updated collector first.")
        for i in ips:
            known_mod.forget(remembered, i)
        known_mod.save(store, remembered)
        if key is not None:
            identity_mod.forget(ident, key)
            identity_mod.save(store, ident)
        # An SSH-failed switch is a record of the last run: drop it, so the rebuilt latest.json (which the
        # next run trusts) no longer lists it.
        records = [r for r in last["records"] if r["ip"] not in ips]
        last = dict(last, closed=[c for c in last.get("closed", []) if c not in ips])
        now = build.iso_now()
        _rebuild_from_last(store, cfg, records, last, now)
        name = row.get("hostname") or (sw or {}).get("hostname") or ""
        store.write(dict(head, state="done", finishedAt=build.iso_now(), ok=True, error=None,
                         hostname=name, snapshotRefreshedAt=now), "refresh-status.json")
        extra = ((f" id={key}" if who.startswith("ip=") else "") + f" ips={','.join(ips) or '-'}") if key else ""
        store.audit(f"forget {who} by={by or '-'} status={status or 'not-listed'}{extra}")
        return 0
    except Exception as exc:
        if not isinstance(exc, RefreshRefused):
            log.exception("Removing %s failed", who)
        store.write(dict(head, state="failed", finishedAt=build.iso_now(), ok=False, error=str(exc)[:300]),
                    "refresh-status.json")
        store.audit(f"forget-failed {who} by={by or '-'} error={str(exc)[:200]!r}")
        return 1
    finally:
        store.release("refresh.lock")


def reach_run(cfg):
    """Cheap up/down: TCP 22 to every switch in the latest snapshot, known switches missing from it (every
    other IP of a switch among them), and the WLCs. No login. Each address keeps a count of consecutive
    misses (2 = Down)."""
    col = cfg["collector"]
    store = Store(col["data_dir"])
    latest = store.read("latest.json", default={}) or {}
    # A replaced switch's row has ip "" (no IP left); every other IP of a switch is in known-switches.json.
    rows = [d for d in latest.get("devices", []) if d.get("ip")]
    listed = {d["ip"] for d in rows}
    # "no-ssh" switches are known not to answer tcp/22; Reconnect or the next full run re-checks them.
    ips = [d["ip"] for d in rows if d.get("status") != "no-ssh"]
    ips += [ip for ip in known_mod.load(store) if ip not in listed]
    # Never an address that is now out of scope or excluded (MINOR 3); the WLCs are configured by hand.
    ips = [ip for ip in dict.fromkeys(ips) if config_mod.in_scope(cfg, ip)]
    ips += [h for h in cfg["wlc"]["hosts"] if h not in ips]
    results = scan.probe(ips, 22, max(col["connect_timeout"], 2.0), 256)
    out = _apply_reach(store, results, keep_others=False)
    down = sum(not v["up"] for v in out.values())
    log.info("Reachability: %d checked, %d down", len(out), down)
    return 0
