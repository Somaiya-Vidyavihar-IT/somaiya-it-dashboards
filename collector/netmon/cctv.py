"""CCTV dashboard: cameras and NVRs, read from SolarWinds (SWIS REST API, SWQL SELECT only).

SolarWinds already polls the cameras and NVRs; this module only reads its results with a read-only account.
Which nodes count as cameras and NVRs is set in [cctv] (config.validate_cctv):

1. the node custom property `type_property` (Device_Type) is one of `nvr_types` / `camera_types`;
2. otherwise, with no Device_Type tag, a node whose name matches `nvr_name_pattern` is an NVR;
3. otherwise, with no tag, a node inside `camera_ranges` (CIDRs confirmed by the network team) is a camera.

An explicit other tag (e.g. SWITCH) always wins: such a node is never a camera.

"Down since" comes from SolarWinds node-down events (EventType 1, times in UTC); when none is kept
(SolarWinds keeps ~30 days) it is "before <oldest event>"; when events cannot be read it is the first poll
that saw the node down. It is kept in state/cctv-state.json so later polls do not ask again.
"""
import base64
import hashlib
import http.client
import ipaddress
import json
import logging
import re
import ssl
import threading
import time
from datetime import datetime, timedelta, timezone

from . import config as config_mod

log = logging.getLogger("netmon.cctv")
SWIS_PATH = "/SolarWinds/InformationService/v3/Json/Query"
SELECT_ONLY = re.compile(r"\s*SELECT\s", re.I)
STATE_FILE = ("state", "cctv-state.json")
PIN_FILE = ("state", "solarwinds-cert.json")
GAP_MAX_AGE = 3600  # IPAM changes slowly: check "not in SolarWinds" at most hourly
# Orion node status codes.
UP, DOWN, WARNING, UNMANAGED, UNREACHABLE, CRITICAL = 1, 2, 3, 9, 12, 14


class SwisError(RuntimeError):
    pass


def _utc(value):
    """SWIS datetimes ('2026-11-09T17:45:08.4530000', UTC without a zone) -> aware datetime."""
    if not value:
        return None
    text = str(value).rstrip("Z")
    text = re.sub(r"(\.\d{6})\d+", r"\1", text)
    try:
        dt = datetime.fromisoformat(text)
    except ValueError:
        return None
    return dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt.astimezone(timezone.utc)


def _iso(dt):
    return dt.astimezone(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z") if dt else None


class Swis:
    """Minimal SWIS client: POST /Query with HTTP Basic auth. Refuses anything but SELECT.

    SWIS normally has a self-signed certificate. We pin its SHA-256 fingerprint: the configured one,
    or else the one seen on the first successful connection (stored in state/solarwinds-cert.json).
    """

    def __init__(self, host, port, username, password, store=None, pinned="", timeout=60):
        self.host, self.port, self.timeout = host, port, timeout
        self.auth = "Basic " + base64.b64encode(f"{username}:{password}".encode()).decode()
        self.store, self.pinned = store, pinned

    def _check_pin(self, fingerprint):
        if self.pinned:
            if fingerprint != self.pinned:
                raise SwisError("The SolarWinds certificate does not match cctv.solarwinds_cert_sha256 in config.toml.")
            return
        if self.store is None:
            return
        seen = (self.store.read(*PIN_FILE, default={}) or {}).get("sha256")
        if not seen:
            self.store.write({"sha256": fingerprint, "host": self.host, "firstSeen": _iso(datetime.now(timezone.utc))},
                             *PIN_FILE)
        elif seen != fingerprint:
            raise SwisError("The SolarWinds certificate changed since it was first trusted. If SolarWinds renewed it, "
                            "delete data\\state\\solarwinds-cert.json on the collector host; otherwise investigate.")

    def query(self, swql):
        if not SELECT_ONLY.match(swql):
            raise SwisError("Only SWQL SELECT statements are allowed (read-only).")
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE  # self-signed; checked by fingerprint pin below
        conn = http.client.HTTPSConnection(self.host, self.port, context=ctx, timeout=self.timeout)
        try:
            try:
                conn.connect()
            except OSError as exc:
                raise SwisError(f"Cannot reach SolarWinds at {self.host}:{self.port} ({exc.__class__.__name__}).")
            self._check_pin(hashlib.sha256(conn.sock.getpeercert(binary_form=True)).hexdigest())
            body = json.dumps({"query": swql}).encode()
            conn.request("POST", SWIS_PATH, body=body, headers={"Authorization": self.auth,
                                                                 "Content-Type": "application/json"})
            resp = conn.getresponse()
            data = resp.read()
        except (OSError, http.client.HTTPException) as exc:
            raise SwisError(f"SolarWinds request failed ({exc.__class__.__name__}).")
        finally:
            conn.close()
        if resp.status == 401:
            raise SwisError("SolarWinds rejected the username or password (401). Check cctv.solarwinds_username "
                            "and run: netmon set-password solarwinds")
        if resp.status != 200:
            try:
                msg = json.loads(data).get("Message", "")
            except ValueError:
                msg = ""
            raise SwisError(f"SolarWinds answered {resp.status}{': ' + msg[:300] if msg else ''}")
        return json.loads(data).get("results", [])


# ---------------------------------------------------------------- classification

def node_query(cc):
    cols = ("N.NodeID, N.Caption, N.IPAddress, N.Vendor, N.MachineType, N.Status, N.StatusDescription, "
            "N.UnManaged, N.Location")
    extra = []
    if cc["type_property"]:
        extra.append(f"CP.{cc['type_property']} AS DeviceType")
    if cc["institute_property"]:
        extra.append(f"CP.{cc['institute_property']} AS InstituteName")
    if not extra:
        return f"SELECT {cols} FROM Orion.Nodes N"
    return (f"SELECT {cols}, {', '.join(extra)} FROM Orion.Nodes N "
            "INNER JOIN Orion.NodesCustomProperties CP ON CP.NodeID = N.NodeID")


def _addr(ip):
    try:
        return ipaddress.ip_address(str(ip).strip())
    except ValueError:
        return None


def classify(node, cc):
    """('nvr' | 'camera', matched by 'tag' | 'name' | 'range') or None."""
    tag = str(node.get("DeviceType") or "").strip().lower()
    if tag:
        if tag in cc["nvr_types"]:
            return "nvr", "tag"
        if tag in cc["camera_types"]:
            return "camera", "tag"
        return None  # tagged as something else (SWITCH, WLC ...): never a camera
    if cc["nvr_name_re"] and cc["nvr_name_re"].search(str(node.get("Caption") or "")):
        return "nvr", "name"
    addr = _addr(node.get("IPAddress"))
    if addr and any(addr in n for n in cc["camera_ranges_nets"]):
        return "camera", "range"
    return None


def state_of(node):
    if node.get("UnManaged") in (True, 1, "true", "True"):
        return "unmonitored"
    try:
        code = int(node.get("Status"))
    except (TypeError, ValueError):
        return "unknown"
    if code == UP:
        return "up"
    if code in (WARNING, CRITICAL):
        return "warning"
    if code in (DOWN, UNREACHABLE):
        return "down"
    if code == UNMANAGED:
        return "unmonitored"
    return "unknown"


def institute_of(node, cfg):
    name = str(node.get("InstituteName") or "").strip()
    if name:
        return name
    sub = config_mod.subnet_for(cfg, str(node.get("IPAddress") or ""))
    return sub["institute"] if sub else "Unassigned"


# ---------------------------------------------------------------- down since

def backfill_down_since(query, node_ids):
    """{node id: last node-down event time (UTC)} and the oldest event kept, for nodes that are down now."""
    found, oldest = {}, None
    ids = sorted({int(i) for i in node_ids})
    for i in range(0, len(ids), 200):
        chunk = ", ".join(str(n) for n in ids[i:i + 200])
        rows = query("SELECT NetObjectID, MAX(EventTime) AS T FROM Orion.Events WHERE EventType = 1 "
                     f"AND NetObjectType = 'N' AND NetObjectID IN ({chunk}) GROUP BY NetObjectID")
        for r in rows:
            t = _utc(r.get("T"))
            if t is not None:
                found[int(r["NetObjectID"])] = t
    if len(found) < len(ids):
        rows = query("SELECT MIN(EventTime) AS Oldest FROM Orion.Events")
        oldest = _utc(rows[0].get("Oldest")) if rows else None
    return found, oldest


# ---------------------------------------------------------------- IPAM gap

def ipam_gap(query, cc, sw_ips):
    """Live IPs that IPAM sees in cctv.ipam_ranges but that are not SolarWinds nodes."""
    nets = cc["ipam_ranges_nets"]
    if not nets:
        return []
    subnets = query("SELECT GroupId, Address, CIDR FROM IPAM.GroupNode WHERE GroupTypeText = 'Subnet'")
    ids = []
    for s in subnets:
        try:
            net = ipaddress.ip_network(f"{s['Address']}/{s['CIDR']}", strict=False)
        except (KeyError, TypeError, ValueError):
            continue
        if any(net.version == n.version and net.subnet_of(n) for n in nets):
            ids.append(int(s["GroupId"]))
    rows = []
    for i in range(0, len(ids), 200):
        chunk = ", ".join(str(n) for n in ids[i:i + 200])
        rows += query("SELECT IPAddress, MAC, Vendor, DhcpClientName, SysName, LastSync FROM IPAM.IPNode "
                      f"WHERE Status = 1 AND SubnetId IN ({chunk})")
    out, seen = [], set()
    for r in rows:
        addr = _addr(r.get("IPAddress"))
        if not addr or str(addr) in sw_ips or str(addr) in seen or not any(addr in n for n in nets):
            continue
        seen.add(str(addr))
        out.append({"ip": str(addr), "mac": r.get("MAC") or "", "vendor": r.get("Vendor") or "",
                    "name": r.get("DhcpClientName") or r.get("SysName") or "", "lastSeen": _iso(_utc(r.get("LastSync")))})
    out.sort(key=lambda g: ipaddress.ip_address(g["ip"]))
    return out


# ---------------------------------------------------------------- one poll

def collect(cfg, query, store, now=None):
    """Read SolarWinds once and return the cctv.json snapshot (also updates the down-since state)."""
    cc = cfg["cctv"]
    now = now or datetime.now(timezone.utc)
    nodes = query(node_query(cc))
    state = store.read(*STATE_FILE, default={}) or {}
    down_state = state.get("down", {})
    devices = []
    for n in nodes:
        kind = classify(n, cc)
        if not kind:
            continue
        devices.append({"id": int(n["NodeID"]), "kind": kind[0], "matchedBy": kind[1],
                        "name": str(n.get("Caption") or "").strip() or str(n.get("IPAddress") or ""),
                        "ip": str(n.get("IPAddress") or ""), "institute": institute_of(n, cfg),
                        "location": str(n.get("Location") or "").strip(), "vendor": str(n.get("Vendor") or ""),
                        "model": str(n.get("MachineType") or ""), "type": str(n.get("DeviceType") or ""),
                        "status": state_of(n), "statusText": str(n.get("StatusDescription") or "")})

    # Down since: keep what we know, ask SolarWinds' events only for newly down devices.
    down_now = {str(d["id"]) for d in devices if d["status"] == "down"}
    kept = {k: v for k, v in down_state.items() if k in down_now}
    new = [int(k) for k in down_now if k not in kept]
    if new:
        try:
            found, oldest = backfill_down_since(query, new)
        except SwisError as exc:
            log.warning("Could not read node-down events: %s", exc)
            found, oldest = {}, None
        for nid in new:
            if nid in found:
                kept[str(nid)] = {"since": _iso(found[nid]), "source": "event"}
            elif oldest is not None:
                kept[str(nid)] = {"since": _iso(oldest), "source": "before-events"}
            else:
                kept[str(nid)] = {"since": _iso(now), "source": "first-seen"}
    limit = cc["escalate_hours"]
    for d in devices:
        info = kept.get(str(d["id"]))
        if d["status"] == "down" and info:
            since = _utc(info["since"])
            hours = max(0.0, (now - since).total_seconds() / 3600) if since else None
            d.update(downSince=info["since"], downSinceExact=info["source"] == "event", downSinceSource=info["source"],
                     downHours=round(hours, 1) if hours is not None else None,
                     escalated=hours is not None and hours >= limit)
        else:
            d.update(downSince=None, downSinceExact=None, downSinceSource=None, downHours=None, escalated=False)

    # Not in SolarWinds (IPAM), at most hourly.
    gap_state = state.get("gap") or {}
    gap, gap_at, gap_error = gap_state.get("rows", []), gap_state.get("at"), gap_state.get("error")
    last = _utc(gap_at)
    if cc["ipam_ranges_nets"] and (last is None or (now - last).total_seconds() >= GAP_MAX_AGE):
        try:
            gap = ipam_gap(query, cc, {str(n.get("IPAddress") or "") for n in nodes})
            gap_at, gap_error = _iso(now), None
        except SwisError as exc:
            log.warning("IPAM check failed: %s", exc)
            gap_error = str(exc)
    if not cc["ipam_ranges_nets"]:
        gap, gap_at, gap_error = [], None, None
    store.write({"down": kept, "gap": {"rows": gap, "at": gap_at, "error": gap_error}}, *STATE_FILE)

    devices.sort(key=lambda d: (d["kind"] != "nvr", d["institute"].lower(), d["name"].lower()))
    return {"schema": 1, "generatedAt": _iso(now), "nodesRead": len(nodes),
            "summary": summarize(devices, gap), "institutes": institutes(devices),
            "devices": devices, "notInSolarWinds": gap, "notInSolarWindsAt": gap_at, "notInSolarWindsError": gap_error,
            "rules": {"typeProperty": cc["type_property"], "cameraTypes": cc["camera_types"], "nvrTypes": cc["nvr_types"],
                      "nvrNamePattern": cc["nvr_name_pattern"], "cameraRanges": [str(n) for n in cc["camera_ranges_nets"]],
                      "ipamRanges": [str(n) for n in cc["ipam_ranges_nets"]], "escalateHours": limit,
                      "pollMinutes": cc["poll_minutes"]}}


def _counts(devs):
    c = {"total": len(devs)}
    for s in ("up", "warning", "down", "unmonitored", "unknown"):
        c[s] = sum(1 for d in devs if d["status"] == s)
    c["escalated"] = sum(1 for d in devs if d["escalated"])
    return c


def summarize(devices, gap):
    return {"cameras": _counts([d for d in devices if d["kind"] == "camera"]),
            "nvrs": _counts([d for d in devices if d["kind"] == "nvr"]),
            "escalated": sum(1 for d in devices if d["escalated"]), "notInSolarWinds": len(gap)}


def institutes(devices):
    out = {}
    for d in devices:
        i = out.setdefault(d["institute"], {"name": d["institute"], "cameras": 0, "camerasDown": 0, "nvrs": 0,
                                            "nvrsDown": 0, "escalated": 0})
        key = "cameras" if d["kind"] == "camera" else "nvrs"
        i[key] += 1
        i[key + "Down"] += d["status"] == "down"
        i["escalated"] += d["escalated"]
    return sorted(out.values(), key=lambda i: (-i["camerasDown"] - i["nvrsDown"], i["name"].lower()))


# ---------------------------------------------------------------- runner

def make_query(cfg, store):
    from . import creds
    cc = cfg["cctv"]
    user, password = creds.get(cfg, "solarwinds")
    client = Swis(cc["solarwinds_host"], cc["solarwinds_port"], user, password, store=store,
                  pinned=cc["solarwinds_cert_sha256"])
    return client.query


def poll_once(cfg, store, by="", query=None, clock=None):
    """One poll: writes cctv.json (on success) and cctv-status.json. Returns the status dict."""
    started = (clock or (lambda: datetime.now(timezone.utc)))()
    status = {"state": "running", "startedAt": _iso(started), "finishedAt": None, "by": by, "ok": None, "error": None,
              "pollMinutes": cfg["cctv"]["poll_minutes"]}
    prev = store.read("cctv-status.json", default={}) or {}
    status["lastSuccessAt"] = prev.get("lastSuccessAt")
    store.write(status, "cctv-status.json")
    t0 = time.monotonic()
    try:
        if cfg["cctv"]["source"] == "sheet":
            from .cctv_sheet import collect_sheet
            snap = collect_sheet(cfg, store, now=started)
        else:
            snap = collect(cfg, query or make_query(cfg, store), store, now=started)
        store.write(snap, "cctv.json")
        status.update(state="ok", ok=True, lastSuccessAt=snap["generatedAt"],
                      cameras=snap["summary"]["cameras"]["total"], nvrs=snap["summary"]["nvrs"]["total"])
    except Exception as exc:  # report every failure on the page; never crash the API thread
        msg = str(exc) if isinstance(exc, SwisError) or exc.__class__.__name__ in ("CredentialError", "SheetError") \
            else f"{exc.__class__.__name__}: {exc}"
        log.warning("CCTV poll failed: %s", msg)
        status.update(state="failed", ok=False, error=msg[:500])
    status.update(finishedAt=_iso(datetime.now(timezone.utc)), seconds=round(time.monotonic() - t0, 1))
    store.write(status, "cctv-status.json")
    return status


class Poller:
    """Polls SolarWinds every poll_minutes inside the API process; Refresh now wakes it early."""

    def __init__(self, cfg, store, poll=poll_once):
        self.cfg, self.store, self.poll = cfg, store, poll
        self.wake, self.lock = threading.Event(), threading.Lock()
        self.busy, self.requested_by = False, ""

    def request(self, by):
        """Ask for an immediate poll. False when one is already running."""
        with self.lock:
            if self.busy:
                return False
            self.requested_by = by
        self.wake.set()
        return True

    def running(self):
        return self.busy

    def run_one(self):
        with self.lock:
            self.busy, by, self.requested_by = True, self.requested_by, ""
        try:
            self.poll(self.cfg, self.store, by=by)
        finally:
            with self.lock:
                self.busy = False

    def loop(self, stop=None):
        interval = self.cfg["cctv"]["poll_minutes"] * 60
        while not (stop and stop.is_set()):
            self.run_one()
            self.wake.wait(interval)
            self.wake.clear()

    def start(self):
        threading.Thread(target=self.loop, name="cctv-poller", daemon=True).start()
