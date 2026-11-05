"""Orchestration: full discovery run (LLD workflow) and the 15-minute reachability check."""
import logging
import os
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
        store.write(snapshot, "latest.json")
        try:
            reports.write_excel(snapshot, store.path("reports", f"inventory-{name}.xlsx"))
            _copy(store.path("reports", f"inventory-{name}.xlsx"), store.path("inventory-latest.xlsx"))
        except ImportError:
            log.warning("openpyxl not installed; Excel report skipped")
        reports.write_dot(snapshot, store.path("reports", f"topology-{name}.dot"))
        _copy(store.path("reports", f"topology-{name}.dot"), store.path("topology-latest.dot"))
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


def _copy(src, dst):
    tmp = dst + ".tmp"
    with open(src, "rb") as a, open(tmp, "wb") as b:
        b.write(a.read())
    os.replace(tmp, dst)


def _discover(cfg, store, trigger, by, started):
    col = cfg["collector"]
    sets = creds.switch_sets(cfg)
    by_name = {s[0]: s for s in sets}
    platforms = store.read("state", "platforms.json", default={}) or {}
    # Which credential set worked per switch (set names only, never passwords), so later runs
    # log in first time and do not generate repeated failed logins against TACACS+.
    cred_of = store.read("state", "credentials.json", default={}) or {}

    # 1. IP range scanner: TCP 22 over sweep-enabled subnets, plus seeds and last run's devices.
    targets = list(scan.sweep_targets(cfg))
    previous = store.read("latest.json", default={}) or {}
    known = [d["ip"] for d in previous.get("devices", [])]
    # Driver hints from what neighbours report over CDP/LLDP (e.g. "Cisco CBS350-24P-4G"), so SG/CBS/
    # C1300 switches go straight to the in-band-login driver instead of failing a Catalyst login first.
    hints = {}

    def add_hints(neighbors):
        for n in neighbors or []:
            hint = parse.platform_hint(n.get("platform", ""))
            if n.get("ip") and hint:
                hints.setdefault(n["ip"], hint)

    for d in previous.get("devices", []):
        add_hints(d.get("neighbors"))
    extra = [ip for ip in list(col["seeds"]) + known if config_mod.in_scope(cfg, ip)]
    log.info("Sweeping %d addresses for tcp/22", len(targets) + len(extra))
    probes = scan.probe(targets + extra, 22, col["connect_timeout"], col["scan_concurrency"])
    open_ips = [ip for ip, lat in probes.items() if lat is not None]
    closed = {ip for ip, lat in probes.items() if lat is None}
    scan_info = {"subnets": len(cfg["subnets"]), "addresses": len(probes), "sshOpen": len(open_ips)}

    # 2-5. SSH authentication, device information, CDP + LLDP, recursive discovery.
    records, scanned = [], set()
    store.status(phase="ssh", done=0, total=len(open_ips))
    # Load netmiko once in this thread. Worker threads importing it for the first time at the same
    # moment raise "_DeadlockError ... netmiko.exceptions" (seen live: both Nexus seeds failed).
    import netmiko  # noqa: F401
    import netmiko.exceptions  # noqa: F401

    def work(ip):
        known_set = cred_of.get(ip)
        order = ([by_name[known_set]] if known_set in by_name else []) + [s for s in sets if s[0] != known_set]
        rec = None
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
                    if rec["error"] == devices.AUTH_FAILED:
                        continue  # try the next credential set
                    return rec
            rec = devices.collect(ip, platform, username, password, cfg)
            if rec["ok"]:
                platforms[ip] = platform
                cred_of[ip] = name
                rec["credentialSet"] = name
                return rec
            if rec["error"] != devices.AUTH_FAILED:
                return rec
        rec["error"] = f"SSH authentication failed with all {len(order)} credential sets"
        cred_of.pop(ip, None)
        return rec

    with ThreadPoolExecutor(max_workers=col["ssh_workers"]) as pool:
        pending = set()
        for ip in open_ips:
            scanned.add(ip)
            pending.add(pool.submit(work, ip))
        while pending:
            done, pending = wait(pending, return_when=FIRST_COMPLETED)
            for fut in done:
                rec = fut.result()
                records.append(rec)
                add_hints(rec.get("neighbors"))
                new = []
                for n in rec.get("neighbors", []):
                    ip = n.get("ip")
                    if ip and ip not in scanned and config_mod.in_scope(cfg, ip) and build.neighbor_kind(n) == "switch":
                        scanned.add(ip)
                        new.append(ip)
                if new:
                    reach = scan.probe(new, 22, col["connect_timeout"], 64)
                    for ip in new:
                        if reach.get(ip) is None:
                            closed.add(ip)
                        else:
                            pending.add(pool.submit(work, ip))
            store.status(done=len(records), total=len(records) + len(pending))
    store.write(platforms, "state", "platforms.json")
    store.write(cred_of, "state", "credentials.json")
    scan_info["collected"] = sum(r["ok"] for r in records)

    # Aruba WLCs.
    wlcs = []
    if cfg["wlc"]["hosts"]:
        store.status(phase="wlc")
        wuser, wpass = creds.get(cfg)
        wlcs = [wlc.collect(h, wuser, wpass, cfg) for h in cfg["wlc"]["hosts"]]

    # 6. Report generation.
    store.status(phase="report")
    snapshot, baseline, counters = build.build(
        cfg, records, wlcs, store.read("state", "links-baseline.json", default={}),
        store.read("state", "counters.json", default={}), scan_info,
        {"trigger": trigger, "triggeredBy": by, "startedAt": started}, ssh_closed=frozenset(closed))
    snapshot["finishedAt"] = build.iso_now()
    store.write(baseline, "state", "links-baseline.json")
    store.write(counters, "state", "counters.json")
    return snapshot


def reach_run(cfg):
    """Cheap up/down: TCP 22 to every switch in the latest snapshot plus the WLCs. No login."""
    col = cfg["collector"]
    store = Store(col["data_dir"])
    latest = store.read("latest.json", default={}) or {}
    ips = [d["ip"] for d in latest.get("devices", [])] + list(cfg["wlc"]["hosts"])
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
