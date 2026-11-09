"""Campus-only HTTPS API for the hub's Network Monitoring page.

Access: the page sends the hub's Google access token. We confirm it with Google tokeninfo (issued to
the hub's client ID, verified email, allowed domain) and require the email on the IT Network Team
allowlist. Tokens are never logged or stored; only a hash is cached for at most five minutes.
"""
import gzip
import hashlib
import ipaddress
import json
import logging
import os
import ssl
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from .store import Store

log = logging.getLogger("netmon.server")
TOKENINFO = "https://oauth2.googleapis.com/tokeninfo"
API_VERSION = 1


def google_tokeninfo(token):
    """Return Google's token info dict, or None when Google says the token is invalid."""
    body = urllib.parse.urlencode({"access_token": token}).encode()
    req = urllib.request.Request(TOKENINFO, data=body, method="POST",
                                 headers={"Content-Type": "application/x-www-form-urlencoded"})
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return json.load(resp)
    except urllib.error.HTTPError as exc:
        if exc.code in (400, 401):
            return None
        raise


class Verifier:
    CACHE_SECONDS = 300

    def __init__(self, client_id, domains, team, fetch=google_tokeninfo, clock=time.time):
        self.client_id, self.domains, self.team = client_id, set(domains), set(team)
        self.fetch, self.clock = fetch, clock
        self.cache, self.lock = {}, threading.Lock()

    def check(self, token):
        """Return (http status, email, message)."""
        if not token or len(token) > 4096:
            return 401, None, "Connect with Google to view the network dashboard."
        key = hashlib.sha256(token.encode()).hexdigest()
        now = self.clock()
        with self.lock:
            hit = self.cache.get(key)
        if hit and hit[1] > now:
            email = hit[0]
        else:
            try:
                info = self.fetch(token)
            except Exception as exc:
                log.warning("Google token check unavailable: %s", exc.__class__.__name__)
                return 503, None, "Google sign-in could not be checked from the campus server. Try again shortly."
            if not info:
                return 401, None, "Google authorization is invalid or expired. Connect with Google again."
            if self.client_id not in (info.get("aud"), info.get("azp")):
                return 401, None, "This Google authorization was not issued to the Somaiya dashboard hub."
            email = str(info.get("email", "")).lower()
            if not email or str(info.get("email_verified", "")).lower() != "true":
                return 401, None, "Google did not provide a verified email for this account."
            try:
                ttl = int(info.get("expires_in", 0))
            except ValueError:
                ttl = 0
            if ttl <= 0:
                return 401, None, "Google authorization expired. Connect with Google again."
            with self.lock:
                if len(self.cache) > 1000:
                    self.cache = {k: v for k, v in self.cache.items() if v[1] > now}
                self.cache[key] = (email, now + min(self.CACHE_SECONDS, ttl))
        if email.rsplit("@", 1)[-1] not in self.domains:
            return 403, email, "This account's domain is not allowed."
        if email not in self.team:
            return 403, email, "This account is not on the IT Network Team list for the network dashboard."
        return 200, email, ""


class FileCache:
    """Serve large JSON files without re-reading them on every poll."""

    def __init__(self):
        self.items, self.lock = {}, threading.Lock()

    def get(self, path):
        try:
            mtime = os.path.getmtime(path)
        except FileNotFoundError:
            return None
        with self.lock:
            hit = self.items.get(path)
            if hit and hit[0] == mtime:
                return hit[1], hit[2]
        with open(path, "rb") as fh:
            raw = fh.read()
        packed = gzip.compress(raw, 6)
        with self.lock:
            self.items[path] = (mtime, raw, packed)
        return raw, packed


class Handler(BaseHTTPRequestHandler):
    server_version = "netmon"
    sys_version = ""
    protocol_version = "HTTP/1.1"
    timeout = 30  # idle keep-alive connections release their thread

    # ---- plumbing ----
    def _origin_allowed(self):
        origin = self.headers.get("Origin")
        return origin if origin and origin in self.server.cfg["server"]["allowed_origins"] else None

    def _send(self, code, payload=b"", ctype="application/json", headers=None, packed=None):
        body = payload if isinstance(payload, bytes) else json.dumps(payload).encode()
        if packed is not None and "gzip" in self.headers.get("Accept-Encoding", ""):
            body = packed
            headers = dict(headers or {}, **{"Content-Encoding": "gzip"})
        self.send_response(code)
        origin = self._origin_allowed()
        if origin:
            self.send_header("Access-Control-Allow-Origin", origin)
            self.send_header("Access-Control-Expose-Headers", "Content-Disposition")
        self.send_header("Vary", "Origin, Accept-Encoding")
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        for k, v in (headers or {}).items():
            self.send_header(k, v)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _error(self, code, message):
        self._send(code, {"error": message})

    def log_message(self, fmt, *args):  # request line only; never headers (they carry the token)
        log.info("%s %s", self.client_address[0], fmt % args)

    def _auth(self):
        header = self.headers.get("Authorization", "")
        token = header[7:].strip() if header.startswith("Bearer ") else ""
        status, email, message = self.server.verifier.check(token)
        if status != 200:
            if email:
                log.info("denied %s (%s)", email, status)
            self._error(status, message)
            return None
        return email

    def _file(self, name, ctype, download=None):
        cached = self.server.files.get(self.server.store.path(name))
        if cached is None:
            return self._error(404, "No data yet. The first collector run has not finished.")
        raw, packed = cached
        headers = {"Content-Disposition": f'attachment; filename="{download}"'} if download else None
        self._send(200, raw, ctype, headers, packed=None if download else packed)

    # ---- routes ----
    def do_OPTIONS(self):
        if not self._origin_allowed():
            return self._error(403, "Origin not allowed")
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", self.headers["Origin"])
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Authorization, Content-Type")
        if self.headers.get("Access-Control-Request-Private-Network") == "true":
            self.send_header("Access-Control-Allow-Private-Network", "true")
        self.send_header("Access-Control-Max-Age", "600")
        self.send_header("Vary", "Origin")
        self.send_header("Content-Length", "0")
        self.end_headers()

    def do_GET(self):
        path = urllib.parse.urlsplit(self.path).path.rstrip("/")
        if path == "/api/v1/health":
            return self._send(200, {"ok": True})
        if not path.startswith("/api/v1/"):
            return self._error(404, "Not found")
        email = self._auth()
        if not email:
            return
        store = self.server.store
        if path == "/api/v1/me":
            return self._send(200, {"email": email, "canRun": True, "apiVersion": API_VERSION})
        if path == "/api/v1/status":
            reach = store.read("reachability.json", default={}) or {}
            return self._send(200, {"run": store.read("run-status.json", default={}) or {},
                                    "running": store.running(), "reachabilityAt": reach.get("checkedAt"),
                                    "refresh": store.read("refresh-status.json", default={}) or {},
                                    "refreshing": store.running("refresh.lock")})
        if path == "/api/v1/snapshot":
            return self._file("latest.json", "application/json")
        if path == "/api/v1/reachability":
            return self._file("reachability.json", "application/json")
        if path == "/api/v1/download/inventory.xlsx":
            return self._file("inventory-latest.xlsx", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                              "somaiya-network-inventory.xlsx")
        if path == "/api/v1/download/topology.dot":
            return self._file("topology-latest.dot", "text/vnd.graphviz", "somaiya-network-topology.dot")
        return self._error(404, "Not found")

    def do_POST(self):
        path = urllib.parse.urlsplit(self.path).path.rstrip("/")
        if path == "/api/v1/refresh":
            return self._refresh()
        if path != "/api/v1/run":
            return self._error(404, "Not found")
        email = self._auth()
        if not email:
            return
        if self.server.store.running():
            return self._error(409, "A collector run is already in progress.")
        self.server.start_run(email)
        self.server.store.audit(f"run-request by={email} from={self.client_address[0]}")
        self._send(202, {"started": True})


    def _refresh(self):
        """Re-read one switch from the last full run (dashboard: Refresh switch / Reconnect)."""
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            length = -1
        if not 0 < length <= 1024:
            self.close_connection = True  # unread body
            return self._error(400, 'Send {"ip": "<switch management IP>"}.')
        body = self.rfile.read(length)
        email = self._auth()
        if not email:
            return
        try:
            ip = str(ipaddress.IPv4Address(str(json.loads(body).get("ip", ""))))
        except (ValueError, AttributeError, TypeError):
            return self._error(400, "That is not a valid switch IP address.")
        store = self.server.store
        if store.running():
            return self._error(409, "A full discovery run is in progress; it reads every switch, including this one.")
        if not os.path.exists(store.path("state", "last-run.json")):
            return self._error(409, "Switch refresh needs one full discovery run with the updated collector first. "
                                    "Use Run discovery now.")
        if ip not in self.server.known_switches():
            return self._error(404, "This address is not a switch from the last full discovery run.")
        if not store.acquire({"ip": ip, "by": email}, "refresh.lock"):
            busy = (store.read("refresh-status.json", default={}) or {}).get("ip") or "another switch"
            return self._error(409, f"{busy} is being refreshed. Try again when it finishes (usually under a minute).")
        store.write({"state": "running", "ip": ip, "by": email, "startedAt": None, "finishedAt": None, "ok": None,
                     "error": None}, "refresh-status.json")
        try:
            self.server.start_refresh(email, ip)
        except Exception:
            store.release("refresh.lock")
            store.write({"state": "failed", "ip": ip, "by": email, "ok": False,
                         "error": "The collector could not be started on the server."}, "refresh-status.json")
            log.exception("Could not start the refresh of %s", ip)
            return self._error(500, "The collector could not be started on the server.")
        store.audit(f"refresh-request ip={ip} by={email} from={self.client_address[0]}")
        self._send(202, {"started": True, "ip": ip})


class NetmonServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, cfg, verifier=None, start_run=None, ssl_ctx=None, start_refresh=None):
        srv = cfg["server"]
        super().__init__((srv["host"], srv["port"]), Handler)
        self.ssl_ctx = ssl_ctx
        self.cfg = cfg
        self.store = Store(cfg["collector"]["data_dir"])
        self.files = FileCache()
        self.verifier = verifier or Verifier(srv["google_client_id"], srv["allowed_domains"], srv["network_team"])
        self.start_run = start_run or self._spawn_run
        self.start_refresh = start_refresh or self._spawn_refresh
        self._known = (None, frozenset())

    def known_switches(self):
        """Switch IPs in latest.json; only these may be refreshed (no logins to arbitrary addresses)."""
        cached = self.files.get(self.store.path("latest.json"))
        if cached is None:
            return frozenset()
        raw = cached[0]
        if self._known[0] is not raw:
            self._known = (raw, frozenset(d.get("ip") for d in json.loads(raw).get("devices", [])))
        return self._known[1]

    def finish_request(self, request, client_address):
        # TLS handshake in the worker thread so a slow client cannot stall accept().
        if self.ssl_ctx is None:
            return super().finish_request(request, client_address)
        request.settimeout(30)
        try:
            tls = self.ssl_ctx.wrap_socket(request, server_side=True)
        except (ssl.SSLError, OSError):
            return
        try:
            super().finish_request(tls, client_address)
        finally:
            try:
                tls.close()
            except OSError:
                pass

    def _spawn_run(self, email):
        self._spawn(["run", "--trigger", "manual", "--by", email], "manual-run.log")

    def _spawn_refresh(self, email, ip):
        # --locked: this request already holds refresh.lock; the child releases it when done.
        self._spawn(["refresh", "--ip", ip, "--by", email, "--locked"], "refresh.log")

    def _spawn(self, args, log_name):
        python = self.cfg["server"]["python"] or sys.executable
        cmd = [python, "-m", "netmon", "--config", self.cfg["_path"], *args]
        logfile = open(self.store.path(log_name), "ab")
        flags = 0
        if os.name == "nt":
            flags = subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.DETACHED_PROCESS
        subprocess.Popen(cmd, cwd=os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                         stdout=logfile, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL, creationflags=flags)


def serve(cfg, dev_http=False):
    srv = cfg["server"]
    if not srv["google_client_id"] or not srv["network_team"]:
        raise SystemExit("server.google_client_id and server.network_team must be set in config.toml")
    if dev_http:
        cfg["server"]["host"] = "127.0.0.1"
        cfg["server"]["allowed_origins"] = list(srv["allowed_origins"]) + ["http://127.0.0.1:8765"]
    ctx = None
    if not dev_http:
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        ctx.minimum_version = ssl.TLSVersion.TLSv1_2
        ctx.load_cert_chain(srv["cert_file"], srv["key_file"])
    httpd = NetmonServer(cfg, ssl_ctx=ctx)
    log.info("netmon API listening on %s://%s:%s", "http" if dev_http else "https", srv["host"], srv["port"])
    httpd.serve_forever()
