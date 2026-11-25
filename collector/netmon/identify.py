"""Cisco check for hosts found by the campus IP scan, before any switch password is sent to them.

Evidence, read without logging in:
  - the SSH identification line (scan.ssh_banners: read on connect, nothing sent), and
  - one unauthenticated `GET /` on the web ports (HTTPS first, then HTTP; certificate not checked;
    no credentials, no cookies, no redirects followed; at most 64 KB read): page title and Server header.
A host is Cisco only if the banner matches collector.cisco_ssh_banners or the page matches
collector.cisco_web_patterns. An empty pattern list matches nothing (the safe direction).
"""
import html
import re
import socket
import ssl
import time
import warnings
from concurrent.futures import ThreadPoolExecutor

MAX_BODY = 64 * 1024
_CONTROL = re.compile(r"[\x00-\x1f\x7f]")
_TITLE = re.compile(rb"<title[^>]*>(.*?)</title", re.I | re.S)
_SERVER = re.compile(rb"^server:[ \t]*([^\r\n]*)", re.I | re.M)


def is_cisco(banner, web, cfg):
    """True if the SSH banner or the web hint matches the configured Cisco patterns."""
    col = cfg["collector"]
    if isinstance(banner, str) and banner.startswith("SSH-") \
            and any(r.search(banner) for r in col.get("cisco_ssh_re") or []):
        return True
    return isinstance(web, str) and bool(web) and any(r.search(web) for r in col.get("cisco_web_re") or [])


def _clean(text, limit):
    return " ".join(_CONTROL.sub(" ", text).split())[:limit]


def _ssl_context():
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE  # campus switches use self-signed certificates
    try:  # older SG/Catalyst web servers only speak old TLS; this connection carries no credentials
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", DeprecationWarning)
            ctx.minimum_version = ssl.TLSVersion.TLSv1
        ctx.set_ciphers("DEFAULT:@SECLEVEL=0")
    except (ValueError, ssl.SSLError):
        pass
    return ctx


def _get(ip, port, tls, timeout):
    """One `GET /` with no credentials; returns the first 64 KB of the raw response (headers + body)."""
    deadline = time.monotonic() + timeout
    sock = socket.create_connection((ip, port), timeout=timeout)
    try:
        if tls:
            sock.settimeout(max(0.1, deadline - time.monotonic()))
            sock = _ssl_context().wrap_socket(sock)
        host = ip if port in (80, 443) else f"{ip}:{port}"
        sock.sendall(f"GET / HTTP/1.0\r\nHost: {host}\r\nUser-Agent: netmon-identify\r\nAccept: text/html\r\n"
                     "Connection: close\r\n\r\n".encode("ascii"))
        data = b""
        while len(data) < MAX_BODY:
            left = deadline - time.monotonic()
            if left <= 0:
                break
            sock.settimeout(left)
            try:
                chunk = sock.recv(MAX_BODY - len(data))
            except (socket.timeout, ssl.SSLError):
                break
            if not chunk:
                break
            data += chunk
        return data[:MAX_BODY]
    finally:
        sock.close()


def _hint(raw):
    if not raw.startswith(b"HTTP/"):
        return ""
    head = raw.split(b"\r\n\r\n", 1)[0]
    m = _SERVER.search(head)
    server = _clean(m.group(1).decode("utf-8", "replace"), 100) if m else ""
    t = _TITLE.search(raw)
    title = _clean(html.unescape(t.group(1).decode("utf-8", "replace")), 150) if t else ""
    return f"{title} | {server}" if title or server else ""


def _schemes(port):
    return {443: (True,), 80: (False,)}.get(port, (True, False))


def web_hint(ip, ports, timeout=4.0):
    """'<title> | <Server header>' from the first web port that answers with either, else ''."""
    for port in sorted(ports, key=lambda p: p != 443):  # HTTPS first
        for tls in _schemes(port):
            try:
                hint = _hint(_get(ip, port, tls, timeout))
            except Exception:  # refused, timeout, TLS failure, junk: no evidence from this port
                continue
            if hint:
                return hint
    return ""


def web_hints(targets, timeout=4.0, concurrency=64):
    """{ip: web_hint} for {ip: [open web ports]}, in a thread pool."""
    if not targets:
        return {}
    with ThreadPoolExecutor(max_workers=concurrency) as pool:
        futures = {ip: pool.submit(web_hint, ip, list(ports), timeout) for ip, ports in targets.items()}
        return {ip: f.result() for ip, f in futures.items()}
