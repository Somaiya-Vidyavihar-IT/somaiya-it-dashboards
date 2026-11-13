"""CCTV dashboard, sheet mode: the CCTV Google Sheet is the master list; the collector checks every device itself.

- The sheet is read with a Google service account (read-only Sheets scope). Its JSON key lives only on the
  collector host (cctv.service_account_file); the sheet is shared with the account's email as Viewer.
- Only the tabs in cctv.sheet_tabs are read (finished tabs). In each tab the header row is the first row with
  an "IP" cell; the columns used are Location, "Device Type" or "Particulars", IP, and when present Model,
  Serial No., Warranty, Date of Expiry and Status. Rows typed NVR are NVRs; camera/CCTV rows are cameras;
  HDD rows are skipped; anything else, bad IPs and duplicates are reported as sheet problems.
- Up/down: ICMP echo (or a TCP connect to cctv.tcp_ports) from the collector, cctv.check_tries attempts.
  "Down since" is the collector's own history in state/cctv-reach.json: "observed" when a device that answered
  in an earlier round stops answering, "first-seen" when it was already down the first time it was checked.
"""
import base64
import ipaddress
import json
import logging
import re
import socket
import struct
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

log = logging.getLogger("netmon.cctv.sheet")
SCOPE = "https://www.googleapis.com/auth/spreadsheets.readonly"
SHEETS_API = "https://sheets.googleapis.com/v4/spreadsheets/"
CACHE_FILE = ("state", "cctv-sheet-cache.json")
REACH_FILE = ("state", "cctv-reach.json")
NVR_RE = re.compile(r"\bnvr", re.I)
CAMERA_RE = re.compile(r"cam|cctv|ptz|dome|bullet", re.I)
SKIP_RE = re.compile(r"\bhdd\b|hard ?dis[ck]", re.I)


class SheetError(RuntimeError):
    pass


def _iso(dt):
    return dt.astimezone(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z") if dt else None


def _utc(text):
    try:
        return datetime.fromisoformat(str(text).replace("Z", "+00:00")) if text else None
    except ValueError:
        return None


# ---------------------------------------------------------------- Google service account (JWT bearer)

def _b64(data):
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def make_assertion(key, now=None):
    """Signed JWT asking Google for a read-only Sheets token for the service account in `key` (parsed JSON)."""
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import padding
    now = int(now if now is not None else time.time())
    header = {"alg": "RS256", "typ": "JWT"}
    if key.get("private_key_id"):
        header["kid"] = key["private_key_id"]
    claims = {"iss": key["client_email"], "scope": SCOPE, "aud": key.get("token_uri") or "https://oauth2.googleapis.com/token",
              "iat": now, "exp": now + 3600}
    signing = _b64(json.dumps(header, separators=(",", ":")).encode()) + "." + \
        _b64(json.dumps(claims, separators=(",", ":")).encode())
    pk = serialization.load_pem_private_key(key["private_key"].encode(), password=None)
    sig = pk.sign(signing.encode(), padding.PKCS1v15(), hashes.SHA256())
    return signing + "." + _b64(sig)


def _http(req, timeout=30):
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.load(resp)
    except urllib.error.HTTPError as exc:
        try:
            detail = json.load(exc).get("error", "")
            detail = detail.get("message", "") if isinstance(detail, dict) else detail
        except (ValueError, AttributeError):
            detail = ""
        raise SheetError(f"Google answered {exc.code}{': ' + str(detail)[:300] if detail else ''}")
    except (urllib.error.URLError, OSError) as exc:
        raise SheetError(f"Cannot reach Google ({exc.__class__.__name__}). The collector host needs HTTPS to "
                         "oauth2.googleapis.com and sheets.googleapis.com.")


def load_key(path):
    try:
        with open(path, encoding="utf-8") as fh:
            key = json.load(fh)
    except FileNotFoundError:
        raise SheetError(f"Service account key not found: {path}. Copy the JSON key there (see the setup guide).")
    except (OSError, ValueError) as exc:
        raise SheetError(f"Service account key {path} could not be read: {exc.__class__.__name__}")
    if key.get("type") != "service_account" or not key.get("client_email") or not key.get("private_key"):
        raise SheetError(f"{path} is not a Google service account JSON key")
    return key


def access_token(key, http=_http):
    body = urllib.parse.urlencode({"grant_type": "urn:ietf:params:oauth:grant-type:jwt-bearer",
                                   "assertion": make_assertion(key)}).encode()
    req = urllib.request.Request(key.get("token_uri") or "https://oauth2.googleapis.com/token", data=body,
                                 headers={"Content-Type": "application/x-www-form-urlencoded"}, method="POST")
    token = http(req).get("access_token")
    if not token:
        raise SheetError("Google did not issue an access token for the service account")
    return token


def a1_range(tab):
    return "'" + tab.replace("'", "''") + "'!A1:Z3000"


def read_tabs(sheet_id, tabs, token, http=_http):
    """{tab: rows} for the listed tabs (formatted values, as shown in the sheet)."""
    query = [("ranges", a1_range(t)) for t in tabs] + [("majorDimension", "ROWS"),
                                                      ("valueRenderOption", "FORMATTED_VALUE")]
    url = SHEETS_API + urllib.parse.quote(sheet_id) + "/values:batchGet?" + urllib.parse.urlencode(query)
    req = urllib.request.Request(url, headers={"Authorization": "Bearer " + token})
    try:
        data = http(req)
    except SheetError as exc:
        if "Unable to parse range" in str(exc):
            raise SheetError(f"A tab in cctv.sheet_tabs does not exist in the sheet ({exc}). Check the names "
                             f"{tabs} against the tab names exactly.")
        if " 403" in str(exc) or "answered 403" in str(exc):
            raise SheetError("Google refused the sheet (403). Share it with the service account's email as Viewer "
                             "and enable the Google Sheets API in its project.")
        raise
    out = {}
    for tab, vr in zip(tabs, data.get("valueRanges", [])):
        out[tab] = vr.get("values", [])
    return out


# ---------------------------------------------------------------- parsing

def _col(header, *names):
    for i, h in enumerate(header):
        if h in names:
            return i
    return None


def _ip(text):
    t = str(text or "").strip()
    try:
        return str(ipaddress.IPv4Address(t)) if re.fullmatch(r"\d{1,3}(\.\d{1,3}){3}", t) else None
    except ValueError:
        return None


def parse(tabs_rows):
    """(devices, problems, counts) from {tab: rows}."""
    devices, problems, seen = [], [], {}
    skipped = 0
    for tab, rows in tabs_rows.items():
        hi = next((i for i, r in enumerate(rows[:10]) if any(str(c).strip().lower() == "ip" for c in r)), None)
        if hi is None:
            problems.append({"tab": tab, "row": None, "issue": "no header row with an \"IP\" column", "text": ""})
            continue
        head = [str(c).strip().lower() for c in rows[hi]]
        c_ip, c_loc = _col(head, "ip"), _col(head, "location")
        c_type = _col(head, "device type", "particulars", "type")
        if c_type is None:
            problems.append({"tab": tab, "row": hi + 1, "issue": "no \"Device Type\" or \"Particulars\" column", "text": ""})
            continue
        opt = {k: _col(head, *names) for k, names in (("model", ("model",)), ("serial", ("serial no.", "serial no", "serial")),
                                                      ("warranty", ("warranty",)), ("expiry", ("date of expiry", "expiry")),
                                                      ("sheetStatus", ("status",)))}
        nvr = None
        for i, r in enumerate(rows[hi + 1:], start=hi + 2):
            cell = lambda c: str(r[c]).strip() if c is not None and c < len(r) and r[c] is not None else ""
            typ, raw_ip, loc = cell(c_type), cell(c_ip), cell(c_loc)
            if not (typ or raw_ip or loc):
                continue
            if SKIP_RE.search(typ):
                skipped += 1
                continue
            kind = "nvr" if NVR_RE.search(typ) else "camera" if CAMERA_RE.search(typ) else None
            if not kind:
                problems.append({"tab": tab, "row": i, "issue": "unknown device type" if typ else "device type is blank",
                                 "text": (typ or loc)[:120]})
                continue
            ip = _ip(raw_ip)
            if not ip:
                problems.append({"tab": tab, "row": i, "issue": "IP missing or not valid" if raw_ip else "IP is blank",
                                 "text": raw_ip[:60] if raw_ip else loc[:120]})
                continue
            if ip in seen:
                problems.append({"tab": tab, "row": i, "issue": f"duplicate IP (also {seen[ip]})", "text": ip})
                continue
            seen[ip] = f"{tab} row {i}"
            dev = {"kind": kind, "matchedBy": "sheet", "name": loc or ip, "ip": ip, "institute": tab, "tab": tab, "row": i,
                   "location": "", "type": typ, "vendor": "", "nvr": None if kind == "nvr" else nvr}
            for k, c in opt.items():
                dev[k] = cell(c)
            dev["model"] = dev.get("model") or ""
            if kind == "nvr":
                nvr = f"{loc or ip} ({ip})"
            devices.append(dev)
    return devices, problems, skipped


# ---------------------------------------------------------------- checks

def _ping_windows(ip, timeout_ms):
    import ctypes
    from ctypes import wintypes  # noqa: F401  (loads the Windows types on this platform only)
    iph = ctypes.WinDLL("iphlpapi")
    iph.IcmpCreateFile.restype = ctypes.c_void_p
    iph.IcmpSendEcho.argtypes = [ctypes.c_void_p, ctypes.c_ulong, ctypes.c_char_p, ctypes.c_ushort, ctypes.c_void_p,
                                 ctypes.c_void_p, ctypes.c_ulong, ctypes.c_ulong]
    iph.IcmpSendEcho.restype = ctypes.c_ulong
    iph.IcmpCloseHandle.argtypes = [ctypes.c_void_p]
    handle = iph.IcmpCreateFile()
    if not handle or handle == ctypes.c_void_p(-1).value:
        return None
    try:
        data = b"netmon-cctv"
        buf = ctypes.create_string_buffer(256 + len(data))
        addr = struct.unpack("<I", socket.inet_aton(ip))[0]
        n = iph.IcmpSendEcho(handle, addr, data, len(data), None, buf, len(buf), timeout_ms)
        if not n:
            return None
        # ICMP_ECHO_REPLY: Address (4), Status (4), RoundTripTime (4)
        reply_addr, status, rtt = struct.unpack_from("<III", buf.raw, 0)
        return rtt if status == 0 and reply_addr == addr else None
    finally:
        iph.IcmpCloseHandle(handle)


def _ping_command(ip, timeout_ms):
    secs = max(1, round(timeout_ms / 1000))
    t0 = time.monotonic()
    r = subprocess.run(["ping", "-c", "1", "-W", str(secs), ip], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return round((time.monotonic() - t0) * 1000) if r.returncode == 0 else None


def ping(ip, timeout_ms):
    """Round-trip ms, or None when no echo reply came back."""
    return _ping_windows(ip, timeout_ms) if sys.platform == "win32" else _ping_command(ip, timeout_ms)


def tcp(ip, ports, timeout_ms):
    for port in ports:
        t0 = time.monotonic()
        try:
            with socket.create_connection((ip, port), timeout=timeout_ms / 1000):
                return round((time.monotonic() - t0) * 1000)
        except OSError:
            continue
    return None


def check_all(ips, cc, probe=None):
    """{ip: rtt ms or None}; each device gets cc.check_tries attempts."""
    if probe is None:
        probe = (lambda ip: ping(ip, cc["check_timeout_ms"])) if cc["check"] == "ping" \
            else (lambda ip: tcp(ip, cc["tcp_ports"], cc["check_timeout_ms"]))

    def one(ip):
        for _ in range(cc["check_tries"]):
            rtt = probe(ip)
            if rtt is not None:
                return ip, rtt
        return ip, None
    with ThreadPoolExecutor(max_workers=cc["check_workers"]) as pool:
        return dict(pool.map(one, ips))


# ---------------------------------------------------------------- one round

def default_reader(cfg):
    cc = cfg["cctv"]

    def read():
        key = load_key(cc["service_account_file"])
        return read_tabs(cc["sheet_id"], cc["sheet_tabs"], access_token(key))
    return read


def load_sheet(cfg, store, now, reader=None):
    """Sheet rows from cache, re-read every cctv.sheet_minutes. A failed read keeps the last good copy."""
    cc = cfg["cctv"]
    cache = store.read(*CACHE_FILE, default={}) or {}
    fresh = cache.get("tabs") == cc["sheet_tabs"] and cache.get("sheetId") == cc["sheet_id"]
    last = _utc(cache.get("readAt"))
    error = None
    if not fresh or last is None or (now - last).total_seconds() >= cc["sheet_minutes"] * 60:
        try:
            rows = (reader or default_reader(cfg))()
            cache = {"sheetId": cc["sheet_id"], "tabs": cc["sheet_tabs"], "readAt": _iso(now), "rows": rows}
            store.write(cache, *CACHE_FILE)
        except Exception as exc:  # keep monitoring with the last good copy
            error = str(exc)[:400]
            log.warning("CCTV sheet read failed: %s", error)
            if not fresh or "rows" not in cache:
                raise SheetError(f"The CCTV sheet could not be read and there is no earlier copy: {error}")
    return cache["rows"], cache["readAt"], error


def collect_sheet(cfg, store, now=None, reader=None, probe=None):
    """Snapshot for cctv.json in sheet mode (same shape as SolarWinds mode, plus sheet fields)."""
    from .cctv import institutes, summarize
    cc = cfg["cctv"]
    now = now or datetime.now(timezone.utc)
    rows, read_at, read_error = load_sheet(cfg, store, now, reader)
    devices, problems, skipped = parse(rows)
    results = check_all([d["ip"] for d in devices], cc, probe)
    reach = store.read(*REACH_FILE, default={}) or {}
    seen, down = set(reach.get("seen", [])), dict(reach.get("down", {}))
    limit = cc["escalate_hours"]
    for d in devices:
        rtt = results.get(d["ip"])
        d["rttMs"] = rtt
        d["status"] = "up" if rtt is not None else "down"
        d["statusText"] = f"{cc['check']} reply in {rtt} ms" if rtt is not None else f"no {cc['check']} reply"
        if rtt is not None:
            down.pop(d["ip"], None)
        elif d["ip"] not in down:
            down[d["ip"]] = {"since": _iso(now), "source": "observed" if d["ip"] in seen else "first-seen"}
        seen.add(d["ip"])
        info = down.get(d["ip"]) if rtt is None else None
        if info:
            hours = max(0.0, (now - _utc(info["since"])).total_seconds() / 3600)
            d.update(downSince=info["since"], downSinceExact=info["source"] == "observed",
                     downSinceSource=info["source"], downHours=round(hours, 1), escalated=hours >= limit)
        else:
            d.update(downSince=None, downSinceExact=None, downSinceSource=None, downHours=None, escalated=False)
    current = {d["ip"] for d in devices}
    store.write({"seen": sorted(seen & current), "down": {k: v for k, v in down.items() if k in current}}, *REACH_FILE)
    devices.sort(key=lambda d: (d["kind"] != "nvr", d["institute"].lower(), d["row"]))
    return {"schema": 1, "source": "sheet", "generatedAt": _iso(now), "nodesRead": len(devices),
            "summary": dict(summarize(devices, []), sheetProblems=len(problems)), "institutes": institutes(devices),
            "devices": devices, "sheetProblems": problems, "sheetRowsSkipped": skipped, "sheetReadAt": read_at,
            "sheetError": read_error, "notInSolarWinds": [], "notInSolarWindsAt": None, "notInSolarWindsError": None,
            "rules": {"source": "sheet", "sheetTabs": cc["sheet_tabs"], "check": cc["check"], "tcpPorts": cc["tcp_ports"],
                      "checkTries": cc["check_tries"], "escalateHours": limit, "pollMinutes": cc["poll_minutes"],
                      "sheetMinutes": cc["sheet_minutes"], "ipamRanges": []}}
