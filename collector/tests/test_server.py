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
        self.refreshes = []
        self.httpd = NetmonServer(cfg, verifier=verifier, start_run=self.runs.append,
                                  start_refresh=lambda email, ip: self.refreshes.append((email, ip)))
        self.store = Store(self.tmp.name)
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    def tearDown(self):
        self.httpd.shutdown()
        self.httpd.server_close()
        self.tmp.cleanup()

    def req(self, method, path, token=None, origin=ORIGIN, extra=None, body=None):
        conn = http.client.HTTPConnection("127.0.0.1", self.httpd.server_address[1], timeout=5)
        headers = dict(extra or {})
        if body is not None:
            body = json.dumps(body).encode() if not isinstance(body, bytes) else body
            headers["Content-Type"] = "application/json"
        if token:
            headers["Authorization"] = "Bearer " + token
        if origin:
            headers["Origin"] = origin
        conn.request(method, path, body=body, headers=headers)
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
        self.assertEqual(json.loads(body), {"email": "net@somaiya.edu", "canRun": True, "apiVersion": 2})
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


    def test_refresh_one_switch(self):
        post = lambda body, token="good": self.req("POST", "/api/v1/refresh", token, body=body)
        self.assertEqual(post({"ip": "10.0.0.1"}, "outsider")[0].status, 403)
        self.assertEqual(post({"ip": "10.0.0.1; whoami"})[0].status, 400)
        self.assertEqual(post({"ip": ["10.0.0.1"]})[0].status, 400)
        self.assertEqual(post(b"x" * 2000)[0].status, 400)
        self.assertEqual(post(b"not json")[0].status, 400)
        resp, body = post({"ip": "10.0.0.1"})
        self.assertEqual(resp.status, 409)
        self.assertIn("full discovery run with the updated collector", json.loads(body)["error"])
        self.store.write({"records": []}, "state", "last-run.json")
        self.store.write({"schema": 1, "devices": [{"ip": "10.0.0.1"}]}, "latest.json")
        self.assertEqual(post({"ip": "10.0.0.9"})[0].status, 404, "only switches from the last run")
        self.assertEqual(self.refreshes, [])
        self.assertEqual(post({"ip": "10.0.0.1"})[0].status, 202)
        self.assertEqual(self.refreshes, [("net@somaiya.edu", "10.0.0.1")])
        self.assertTrue(self.store.running("refresh.lock"), "the API takes the lock for the child process")
        resp, body = post({"ip": "10.0.0.1"})
        self.assertEqual(resp.status, 409)
        self.assertIn("10.0.0.1 is being refreshed", json.loads(body)["error"])
        status = json.loads(self.req("GET", "/api/v1/status", "good")[1])
        self.assertTrue(status["refreshing"])
        self.assertEqual((status["refresh"]["state"], status["refresh"]["ip"]), ("running", "10.0.0.1"))
        self.assertFalse(status["running"])
        self.store.release("refresh.lock")
        self.assertTrue(self.store.acquire({"trigger": "test"}))
        self.assertEqual(post({"ip": "10.0.0.1"})[0].status, 409, "not during a full run")
        self.store.release()
        resp, _ = self.req("OPTIONS", "/api/v1/refresh", extra={"Access-Control-Request-Method": "POST",
                                                                "Access-Control-Request-Headers": "content-type"})
        self.assertIn("Content-Type", resp.getheader("Access-Control-Allow-Headers"))
        with open(self.store.path("audit.log"), encoding="utf-8") as fh:
            self.assertIn("refresh-request ip=10.0.0.1 by=net@somaiya.edu", fh.read())


    def test_post_bodies_never_leak_into_the_next_request(self):
        """Regression (live): an answer sent before reading the POST body broke the next request on
        the same keep-alive connection, which the page reported as "API unreachable"."""
        conn = http.client.HTTPConnection("127.0.0.1", self.httpd.server_address[1], timeout=5)
        headers = {"Authorization": "Bearer good", "Origin": ORIGIN, "Content-Type": "application/json"}
        for path, token in (("/api/v1/nothing-here", "good"), ("/api/v1/run", "bad"), ("/api/v1/refresh", "bad")):
            conn.request("POST", path, body=b'{"ip": "10.0.0.1"}', headers=dict(headers, Authorization="Bearer " + token))
            resp = conn.getresponse()
            resp.read()
            self.assertIn(resp.status, (401, 404), path)
            conn.request("GET", "/api/v1/me", headers=headers)
            resp = conn.getresponse()
            self.assertEqual(resp.status, 200, f"same connection still works after POST {path}")
            resp.read()
        conn.close()


if __name__ == "__main__":
    unittest.main()
