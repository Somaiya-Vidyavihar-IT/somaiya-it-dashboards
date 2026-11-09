"""Orchestration: full discovery run (LLD workflow), single-switch refresh and the 15-minute reachability check."""
import logging
import os
import threading
import time
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait

from . import build, config as config_mod, creds, devices, parse, reports, scan, wlc
from .store import Store, stamp

log = logging.getLogger("netmon.run")


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

    # 1. IP range scanner: TCP 22 over sweep-enabled subnets, plus seeds and last run's devices.
    targets = list(scan.sweep_targets(cfg))
    previous = store.read("latest.json", default={}) or {}
    phone_re = col.get("phone_re")
    # Phones that an earlier run took for switches (configured phone models) are not probed again.
    phones = {n["ip"] for d in previous.get("devices", []) for n in d.get("neighbors", [])
              if n.get("ip") and (n.get("kind") == "phone" or build.is_phone_model(n, phone_re))}
    phones |= {d["ip"] for d in previous.get("devices", []) if build.is_phone_model({"name": d.get("hostname") or ""}, phone_re)}
    known = [d["ip"] for d in previous.get("devices", []) if d["ip"] not in phones]
    hints = {}
    for d in previous.get("devices", []):
        _add_hints(hints, d.get("neighbors"))
    extra = [ip for ip in list(col["seeds"]) + known if config_mod.in_scope(cfg, ip)]
    log.info("Sweeping %d addresses for tcp/22", len(targets) + len(extra))
    probes = scan.probe(targets + extra, 22, col["connect_timeout"], col["scan_concurrency"])
    open_ips = [ip for ip, lat in probes.items() if lat is not None]
    closed = {ip for ip, lat in probes.items() if lat is None}
    progress = Progress(store, started, trigger, by)
    seeds, last_run = set(col["seeds"]), set(known)
    for ip in probes:
        if ip in seeds or ip in last_run:
            progress.mark(ip, source="core seed" if ip in seeds else "last run",
                          state="waiting" if ip not in closed else "no-ssh",
                          error=None if ip not in closed else "No answer on SSH (tcp/22)")
        elif ip not in closed:
            progress.mark(ip, source="subnet sweep")
    progress.write(force=True, phase="ssh")
    scan_info = {"subnets": len(cfg["subnets"]), "addresses": len(probes), "sshOpen": len(open_ips)}

    # 2-5. SSH authentication, device information, CDP + LLDP, recursive discovery.
    records, scanned = [], set()
    store.status(phase="ssh", done=0, total=len(open_ips))
    # Load netmiko once in this thread. Worker threads importing it for the first time at the same
    # moment raise "_DeadlockError ... netmiko.exceptions" (seen live: both Nexus seeds failed).
    import netmiko  # noqa: F401
    import netmiko.exceptions  # noqa: F401

    def work(ip):
        progress.mark(ip, state="running")
        return _login_collect(cfg, ip, sets, platforms, cred_of, hints)

    with ThreadPoolExecutor(max_workers=col["ssh_workers"]) as pool:
        pending = set()
        for ip in open_ips:
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
                new = []
                for n in rec.get("neighbors", []):
                    ip = n.get("ip")
                    if ip and ip not in scanned and config_mod.in_scope(cfg, ip) \
                            and build.neighbor_kind(n, phone_re=phone_re) == "switch":
                        scanned.add(ip)
                        new.append(ip)
                        progress.mark(ip, hostname=n.get("name", "")[:80],
                                      source=f"neighbour of {rec.get('hostname') or rec['ip']} {n.get('localPort', '')}".strip())
                if new:
                    reach = scan.probe(new, 22, col["connect_timeout"], 64)
                    for ip in new:
                        if reach.get(ip) is None:
                            closed.add(ip)
                            progress.mark(ip, state="no-ssh", error="No answer on SSH (tcp/22)")
                        else:
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
    snapshot, baseline, counters = build.build(
        cfg, records, wlcs, store.read("state", "links-baseline.json", default={}),
        prev_counters, scan_info,
        {"trigger": trigger, "triggeredBy": by, "startedAt": started}, ssh_closed=frozenset(closed))
    snapshot["finishedAt"] = build.iso_now()
    progress.write(force=True, phase="done", finishedAt=snapshot["finishedAt"])
    store.write(baseline, "state", "links-baseline.json")
    store.write(counters, "state", "counters.json")
    # Everything build() needs, so one switch can later be re-read and the snapshot rebuilt without a
    # full run (refresh_switch). The same data as latest.json in raw form; never passwords.
    store.write({"generatedAt": snapshot["generatedAt"], "finishedAt": snapshot["finishedAt"],
                 "meta": {"trigger": trigger, "triggeredBy": by, "startedAt": started}, "scan": scan_info,
                 "records": records, "wlcs": wlcs, "closed": sorted(closed), "countersBase": prev_counters},
                "state", "last-run.json")
    return snapshot


# Driver hints from what neighbours report over CDP/LLDP (e.g. "Cisco CBS350-24P-4G"), so SG/CBS/
# C1300 switches go straight to the in-band-login driver instead of failing a Catalyst login first.
def _add_hints(hints, neighbors):
    for n in neighbors or []:
        # CDP gives a platform; SG/CBS per-port LLDP gives only a system description.
        hint = parse.platform_hint(n.get("platform", "")) or parse.platform_hint(n.get("desc", ""))
        if n.get("ip") and hint:
            hints.setdefault(n["ip"], hint)


def _login_collect(cfg, ip, sets, platforms, cred_of, hints):
    """Log in with each credential set in turn (the one that worked last time first) and collect one switch."""
    by_name = {s[0]: s for s in sets}
    known_set = cred_of.get(ip)
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


def refresh_switch(cfg, ip, by="", locked=False):
    """Re-read one switch and rebuild the snapshot around it (dashboard: Refresh switch / Reconnect).

    Every other switch and the WLCs come from the last full run's raw records (state/last-run.json),
    so links, roles, findings and APs are rebuilt exactly as a full run would, without logging in to
    anything else. New neighbours this switch reveals are crawled by the next full run, not here.
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
        no_ssh = ip not in {r["ip"] for r in last["records"]}
        if no_ssh and ip not in set(last.get("closed", [])):
            raise RefreshRefused(f"{ip} is not a switch from the last full discovery run.")
        if no_ssh:
            # Listed as "no SSH from the collector": check tcp/22 again before any login.
            col = cfg["collector"]
            if scan.probe([ip], 22, max(col["connect_timeout"], 2.0), 1).get(ip) is None:
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
        rec = _login_collect(cfg, ip, sets, platforms, cred_of, hints)
        now = build.iso_now()
        rec.update(refreshedAt=now, refreshedBy=by)
        # Merge into the newest saved state: a full run may have finished while we were logged in.
        # (One finishing in the milliseconds between this read and the writes below would be
        # overwritten until the next run; accepted, since both carry almost the same data.)
        last = store.read("state", "last-run.json", default=None) or last
        if ip in {r["ip"] for r in last["records"]}:
            records = [rec if r["ip"] == ip else r for r in last["records"]]
        elif ip in set(last.get("closed", [])):
            # It answers SSH now: it becomes a normal record. Switches behind it wait for the next full run.
            records = last["records"] + [rec]
            last = dict(last, closed=[c for c in last["closed"] if c != ip])
        else:
            raise RefreshRefused(f"{ip} is no longer in the last full discovery run.")
        # Error deltas: other switches keep the base their full run used; this one counts from its last read.
        counters = store.read("state", "counters.json", default={}) or {}
        base = dict(last.get("countersBase") or {})
        if ip in counters:
            base[ip] = counters[ip]
        else:
            base.pop(ip, None)
        snapshot, baseline, new_counters = build.build(
            cfg, records, last["wlcs"], store.read("state", "links-baseline.json", default={}), base,
            last["scan"], last["meta"], ssh_closed=frozenset(last.get("closed", [])), now=now)
        snapshot.update(generatedAt=last["generatedAt"], finishedAt=last.get("finishedAt"), refreshedAt=now)
        store.write(platforms, "state", "platforms.json")
        store.write(cred_of, "state", "credentials.json")
        store.write(baseline, "state", "links-baseline.json")
        if ip in new_counters:
            counters[ip] = new_counters[ip]
            store.write(counters, "state", "counters.json")
        store.write(dict(last, records=records), "state", "last-run.json")
        _publish(store, snapshot)
        store.write({"state": "done", "ip": ip, "by": by, "startedAt": started, "finishedAt": build.iso_now(),
                     "ok": rec["ok"], "error": None if rec["ok"] else rec.get("error"),
                     "hostname": rec.get("hostname", ""), "snapshotRefreshedAt": now}, "refresh-status.json")
        store.audit(f"refresh-done ip={ip} ok={rec['ok']}")
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


def reach_run(cfg):
    """Cheap up/down: TCP 22 to every switch in the latest snapshot plus the WLCs. No login."""
    col = cfg["collector"]
    store = Store(col["data_dir"])
    latest = store.read("latest.json", default={}) or {}
    # "no-ssh" switches are known not to answer tcp/22; Reconnect or the next full run re-checks them.
    ips = [d["ip"] for d in latest.get("devices", []) if d.get("status") != "no-ssh"] + list(cfg["wlc"]["hosts"])
    results = scan.probe(ips, 22, max(col["connect_timeout"], 2.0), 256)
    prev = (store.read("reachability.json", default={}) or {}).get("results", {})
    now = build.iso_now()
    out = {}
    for ip, latency in results.items():
        up = latency is not None
        before = prev.get(ip, {})
        since = before.get("since") if before.get("up") == up and before.get("since") else now
        out[ip] = {"up": up, "since": since, "latencyMs": latency}
    store.write({"checkedAt": now, "results": out}, "reachability.json")
    down = sum(not v["up"] for v in out.values())
    log.info("Reachability: %d checked, %d down", len(out), down)
    return 0
