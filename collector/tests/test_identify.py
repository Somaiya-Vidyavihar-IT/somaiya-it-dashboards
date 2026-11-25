"""Cisco check before any switch password is sent to a host found by the campus IP scan.

Only local test servers on 127.0.0.1 are contacted.
"""
import asyncio
import http.server
import threading
import unittest

from netmon import config, identify, scan

CFG = config.validate({"collector": {"switch_username": "u"}, "subnets": [{"cidr": "10.0.0.0/24"}]})


class IsCisco(unittest.TestCase):
    def test_cisco_ios_banner_is_cisco(self):
        self.assertTrue(identify.is_cisco("SSH-2.0-Cisco-1.25", "", CFG))

    def test_openssh_banner_alone_is_not_cisco(self):
        self.assertFalse(identify.is_cisco("SSH-2.0-OpenSSH_8.9p1 Ubuntu-3ubuntu0.6", "", CFG))
        self.assertFalse(identify.is_cisco("SSH-2.0-OpenSSH_8.9p1", "Welcome | nginx", CFG))

    def test_web_title_cisco_is_cisco(self):
        self.assertTrue(identify.is_cisco(None, "Cisco Business Dashboard | ", CFG))
        self.assertTrue(identify.is_cisco("SSH-2.0-OpenSSH_7.4", "Switch | cisco-IOS", CFG), "web evidence counts with SSH open")

    def test_identify_garbage_and_silent_banners_are_not_cisco(self):
        for banner in (None, "", "HTTP/1.1 400", "Cisco but not an SSH identification line"):
            self.assertFalse(identify.is_cisco(banner, "", CFG), repr(banner))

    def test_custom_and_empty_pattern_lists(self):
        custom = config.validate({"collector": {"switch_username": "u", "cisco_ssh_banners": ["^SSH-2\\.0-OpenSSH_7\\.4$"],
                                                "cisco_web_patterns": []}, "subnets": [{"cidr": "10.0.0.0/24"}]})
        self.assertTrue(identify.is_cisco("SSH-2.0-OpenSSH_7.4", "", custom))
        self.assertFalse(identify.is_cisco(None, "Cisco Business Dashboard | ", custom), "empty list: nothing matches")


class _BannerServer:
    """asyncio TCP server on 127.0.0.1 that sends `greeting` (or nothing) and counts what the client sends."""

    def __init__(self, greeting):
        self.greeting, self.received, self.done = greeting, 0, threading.Event()
        self.loop = asyncio.new_event_loop()
        self.thread = threading.Thread(target=self.loop.run_forever, daemon=True)
        self.thread.start()
        self.server = asyncio.run_coroutine_threadsafe(asyncio.start_server(self._handle, "127.0.0.1", 0), self.loop).result(5)
        self.port = self.server.sockets[0].getsockname()[1]

    async def _handle(self, reader, writer):
        try:
            if self.greeting:
                writer.write(self.greeting)
                await writer.drain()
            while True:
                data = await asyncio.wait_for(reader.read(1024), 10)
                if not data:
                    break
                self.received += len(data)
        except (OSError, asyncio.TimeoutError):
            pass
        finally:
            writer.close()
            self.done.set()

    def close(self):
        async def stop():
            self.server.close()
            await self.server.wait_closed()
        asyncio.run_coroutine_threadsafe(stop(), self.loop).result(5)
        self.loop.call_soon_threadsafe(self.loop.stop)
        self.thread.join(5)
        self.loop.close()


class SshBanners(unittest.TestCase):
    def _read(self, greeting, timeout=0.5):
        srv = _BannerServer(greeting)
        try:
            out = scan.ssh_banners(["127.0.0.1"], timeout=timeout, concurrency=4, port=srv.port)
            self.assertTrue(srv.done.wait(5), "client closed the connection")
            self.assertEqual(srv.received, 0, "the client never sends anything")
            return out["127.0.0.1"]
        finally:
            srv.close()

    def test_cisco_banner_is_read(self):
        self.assertEqual(self._read(b"SSH-2.0-Cisco-1.25\r\n"), "SSH-2.0-Cisco-1.25")

    def test_silent_server_gives_none(self):
        self.assertIsNone(self._read(b""))

    def test_garbage_gives_none(self):
        self.assertIsNone(self._read(b"garbage"))
        self.assertIsNone(self._read(b"HTTP/1.1 400 Bad Request\r\n\r\n"))

    def test_only_the_first_line_and_255_bytes_are_kept(self):
        self.assertEqual(self._read(b"SSH-2.0-Cisco-1.25\r\nmore\r\n"), "SSH-2.0-Cisco-1.25")
        self.assertEqual(len(self._read(b"SSH-2.0-" + b"x" * 400 + b"\r\n")), 255)

    def test_closed_port_gives_none(self):
        srv = _BannerServer(b"")
        port = srv.port
        srv.close()  # nothing listens there now
        self.assertEqual(scan.ssh_banners(["127.0.0.1"], timeout=0.5, concurrency=4, port=port), {"127.0.0.1": None})
        self.assertEqual(scan.ssh_banners([]), {})


class _Web(http.server.BaseHTTPRequestHandler):
    body = b""
    server_header = ""
    requests = []

    def do_GET(self):  # noqa: N802
        type(self).requests.append((self.command, self.path, dict(self.headers)))
        self.send_response(200)
        self.send_header("Content-Type", "text/html")
        self.end_headers()
        self.wfile.write(self.body)

    def version_string(self):
        return self.server_header

    def log_message(self, *a):
        pass


class WebHint(unittest.TestCase):
    def _serve(self, body, server_header):
        handler = type("H", (_Web,), {"body": body, "server_header": server_header, "requests": []})
        httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
        threading.Thread(target=httpd.serve_forever, daemon=True).start()
        self.addCleanup(httpd.server_close)
        self.addCleanup(httpd.shutdown)
        return httpd.server_address[1], handler

    def test_title_and_server_header_from_one_unauthenticated_get(self):
        port, handler = self._serve(b"<html><head><TITLE>\n Cisco &amp; Switch\n</TITLE></head></html>", "cisco-IOS")
        self.assertEqual(identify.web_hint("127.0.0.1", [port], timeout=2), "Cisco & Switch | cisco-IOS")
        self.assertEqual(len(handler.requests), 1)
        method, path, headers = handler.requests[0]
        self.assertEqual((method, path), ("GET", "/"))
        self.assertFalse({k.lower() for k in headers} & {"authorization", "cookie"}, "no credentials sent")

    def test_title_after_64_kb_is_not_read(self):
        port, _ = self._serve(b"a" * 70000 + b"<title>Cisco</title>", "")
        self.assertEqual(identify.web_hint("127.0.0.1", [port], timeout=2), "")

    def test_nothing_listening_gives_empty(self):
        port, _ = self._serve(b"", "")
        self.assertEqual(identify.web_hint("127.0.0.1", [port], timeout=2), "", "no title, no server header")
        self.assertEqual(identify.web_hint("127.0.0.1", [], timeout=2), "")

    def test_https_with_a_self_signed_certificate(self):
        try:
            import datetime
            import ssl
            import tempfile
            from cryptography import x509
            from cryptography.hazmat.primitives import hashes, serialization
            from cryptography.hazmat.primitives.asymmetric import rsa
            from cryptography.x509.oid import NameOID
        except ImportError:
            self.skipTest("cryptography not installed")
        key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "switch.example.test")])
        now = datetime.datetime.now(datetime.timezone.utc)
        cert = (x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(key.public_key())
                .serial_number(1).not_valid_before(now - datetime.timedelta(minutes=5))
                .not_valid_after(now + datetime.timedelta(days=1)).sign(key, hashes.SHA256()))
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        pem = tmp.name + "/c.pem"
        with open(pem, "wb") as fh:
            fh.write(cert.public_bytes(serialization.Encoding.PEM))
            fh.write(key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                       serialization.NoEncryption()))
        handler = type("H", (_Web,), {"body": b"<title>Cisco CBS350</title>", "server_header": "", "requests": []})
        httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        ctx.load_cert_chain(pem)
        httpd.socket = ctx.wrap_socket(httpd.socket, server_side=True)
        threading.Thread(target=httpd.serve_forever, daemon=True).start()
        self.addCleanup(httpd.server_close)
        self.addCleanup(httpd.shutdown)
        self.assertEqual(identify.web_hint("127.0.0.1", [httpd.server_address[1]], timeout=2), "Cisco CBS350 | ",
                         "HTTPS first, certificate not checked")

    def test_web_hints_runs_each_host_in_the_pool(self):
        port, _ = self._serve(b"<title>Cisco Business Dashboard</title>", "")
        self.assertEqual(identify.web_hints({"127.0.0.1": [port]}, timeout=2), {"127.0.0.1": "Cisco Business Dashboard | "})
        self.assertEqual(identify.web_hints({}), {})


if __name__ == "__main__":
    unittest.main()
