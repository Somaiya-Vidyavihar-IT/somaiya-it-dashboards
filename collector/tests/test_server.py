import gzip
import http.client
import json
import os
import tempfile
import threading
import unittest

from netmon import config
from netmon.server import NetmonServer, Verifier
from netmon.store import Store

CLIENT = "fixture.apps.googleusercontent.com"
ORIGIN = "https://somaiya-vidyavihar-it.github.io"


def info(email="net@somaiya.edu", aud=CLIENT, verified="true", expires=3000):
    return {"aud": aud, "azp": aud, "email": email, "email_verified": verified, "expires_in": str(expires)}


class VerifierRules(unittest.TestCase):
    def make(self, result):
        calls = []

        def fetch(token):
            calls.append(token)
            if isinstance(result, Exception):
                raise result
            return result
        return Verifier(CLIENT, ["somaiya.edu"], ["net@somaiya.edu"], fetch=fetch), calls

    def test_allowlisted_team_member_passes_and_is_cached(self):
        v, calls = self.make(info())
        self.assertEqual(v.check("tok")[:2], (200, "net@somaiya.edu"))
        self.assertEqual(v.check("tok")[0], 200)
        self.assertEqual(len(calls), 1, "second check served from cache")

    def test_rejections(self):
        cases = [(None, 401), (info(aud="other.apps.googleusercontent.com"), 401), (info(verified="false"), 401),
                 (info(expires=0), 401), (info(email="x@gmail.com"), 403), (info(email="other@somaiya.edu"), 403),
                 (OSError("offline"), 503)]
        for result, status in cases:
            v, _ = self.make(result)
            self.assertEqual(v.check("tok")[0], status, result)
        v, calls = self.make(info())
        self.assertEqual(v.check("")[0], 401)
        self.assertEqual(calls, [], "empty token never reaches Google")


class ApiEndToEnd(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        cfg = config.validate({"collector": {"switch_username": "u", "data_dir": self.tmp.name},
                               "subnets": [{"cidr": "10.0.0.0/24"}],
                               "server": {"host": "127.0.0.1", "port": 0, "google_client_id": CLIENT,
                                          "network_team": ["net@somaiya.edu"]}})
        cfg["_path"] = "unused"
        self.runs = []
        verifier = Verifier(CLIENT, ["somaiya.edu"], ["net@somaiya.edu"],
                            fetch=lambda t: info() if t == "good" else info(email="other@somaiya.edu") if t == "outsider" else None)
        self.httpd = NetmonServer(cfg, verifier=verifier, start_run=self.runs.append)
        self.store = Store(self.tmp.name)
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    def tearDown(self):
        self.httpd.shutdown()
        self.httpd.server_close()
        self.tmp.cleanup()

    def req(self, method, path, token=None, origin=ORIGIN, extra=None):
        conn = http.client.HTTPConnection("127.0.0.1", self.httpd.server_address[1], timeout=5)
        headers = dict(extra or {})
        if token:
            headers["Authorization"] = "Bearer " + token
        if origin:
            headers["Origin"] = origin
        conn.request(method, path, headers=headers)
        resp = conn.getresponse()
        body = resp.read()
        conn.close()
        return resp, body

    def test_health_is_open_everything_else_needs_team_member(self):
        self.assertEqual(self.req("GET", "/api/v1/health")[0].status, 200)
        self.assertEqual(self.req("GET", "/api/v1/me")[0].status, 401)
        self.assertEqual(self.req("GET", "/api/v1/me", "bad")[0].status, 401)
        self.assertEqual(self.req("GET", "/api/v1/snapshot", "outsider")[0].status, 403)
        resp, body = self.req("GET", "/api/v1/me", "good")
        self.assertEqual(resp.status, 200)
        self.assertEqual(json.loads(body), {"email": "net@somaiya.edu", "canRun": True, "apiVersion": 1})
        self.assertEqual(resp.getheader("Access-Control-Allow-Origin"), ORIGIN)
        self.assertEqual(resp.getheader("Cache-Control"), "no-store")

    def test_cors_only_for_the_hub_origin(self):
        resp, _ = self.req("OPTIONS", "/api/v1/me", extra={"Access-Control-Request-Private-Network": "true",
                                                           "Access-Control-Request-Method": "GET"})
        self.assertEqual(resp.status, 204)
        self.assertEqual(resp.getheader("Access-Control-Allow-Private-Network"), "true")
        self.assertIn("Authorization", resp.getheader("Access-Control-Allow-Headers"))
        self.assertEqual(self.req("OPTIONS", "/api/v1/me", origin="https://evil.example")[0].status, 403)
        resp, _ = self.req("GET", "/api/v1/me", "good", origin="https://evil.example")
        self.assertIsNone(resp.getheader("Access-Control-Allow-Origin"))

    def test_snapshot_served_gzip_and_downloads(self):
        self.assertEqual(self.req("GET", "/api/v1/snapshot", "good")[0].status, 404)
        self.store.write({"schema": 1, "devices": []}, "latest.json")
        resp, body = self.req("GET", "/api/v1/snapshot", "good", extra={"Accept-Encoding": "gzip"})
        self.assertEqual(resp.getheader("Content-Encoding"), "gzip")
        self.assertEqual(json.loads(gzip.decompress(body))["schema"], 1)
        with open(self.store.path("topology-latest.dot"), "wb") as fh:
            fh.write(b"graph campus {}\n")
        resp, body = self.req("GET", "/api/v1/download/topology.dot", "good")
        self.assertIn("attachment", resp.getheader("Content-Disposition"))
        self.assertEqual(body, b"graph campus {}\n")

    def test_run_now_starts_once(self):
        self.assertEqual(self.req("POST", "/api/v1/run", "outsider")[0].status, 403)
        self.assertEqual(self.runs, [])
        self.assertEqual(self.req("POST", "/api/v1/run", "good")[0].status, 202)
        self.assertEqual(self.runs, ["net@somaiya.edu"])
        self.assertTrue(self.store.acquire({"trigger": "test"}))
        self.assertEqual(self.req("POST", "/api/v1/run", "good")[0].status, 409)
        resp, body = self.req("GET", "/api/v1/status", "good")
        self.assertTrue(json.loads(body)["running"])
        self.store.release()
        with open(self.store.path("audit.log"), encoding="utf-8") as fh:
            self.assertIn("run-request by=net@somaiya.edu", fh.read())


if __name__ == "__main__":
    unittest.main()
