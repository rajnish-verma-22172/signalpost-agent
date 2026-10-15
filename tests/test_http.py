import json
import tempfile
import threading
import unittest
from http.client import HTTPConnection
from pathlib import Path

from signalpost.server import make_server
from signalpost.service import Service
from signalpost.store import Store
from tests.test_pipeline import FakeSource, NUMBER


class HTTPIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.source = FakeSource()
        self.service = Service(Store(Path(self.temp.name) / "http.sqlite3"), self.source)
        self.server = make_server(self.service, port=0)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.addCleanup(self.cleanup)

    def cleanup(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()
        self.temp.cleanup()

    def request(self, method, path, body=None, headers=None):
        connection = HTTPConnection("127.0.0.1", self.server.server_port, timeout=5)
        try:
            connection.request(method, path, body, headers or {})
            response = connection.getresponse()
            data = response.read()
            if response.getheader("Content-Type", "").startswith("application/json"):
                data = json.loads(data)
            # Response delivery can precede worker cleanup and slot release;
            # wait so the next request in the same test never sees a spurious 503.
            if response.status != 503:
                self.wait_for_workers()
            return response.status, data, dict(response.getheaders())
        finally:
            connection.close()

    def wait_for_workers(self):
        for worker in list(getattr(self.server, "_threads", None) or []):
            worker.join(3)
            self.assertFalse(worker.is_alive(), "HTTP worker did not finish")

    def post(self, path, data):
        return self.request("POST", path, json.dumps(data), {"Content-Type": "application/json"})

    def test_end_to_end_lookup_cache_refresh_history_and_list(self):
        status, data, _ = self.post("/api/lookup", {"numbers": [NUMBER, "invalid"]})
        self.assertEqual(status, 200)
        self.assertEqual([r["status"] for r in data["results"]], ["ok", "invalid"])
        self.assertIn("evidence", data["results"][0]["facts"]["name"])
        _, cached, _ = self.post("/api/lookup", {"numbers": [NUMBER]})
        self.assertTrue(cached["results"][0]["cached"])
        self.source.payload["navn"] = "Updated via HTTP"
        _, updated, _ = self.post("/api/refresh", {"numbers": [NUMBER]})
        self.assertEqual(updated["results"][0]["facts"]["name"]["value"], "Updated via HTTP")
        self.assertEqual(len(updated["results"][0]["history"]), 2)
        _, saved, _ = self.request("GET", "/api/companies")
        self.assertEqual(saved["companies"][0]["facts"], updated["results"][0]["facts"])
        status, detail, _ = self.request("GET", f"/api/companies/{NUMBER}")
        self.assertEqual(status, 200)
        self.assertEqual(detail["status"], "ok")
        self.assertEqual(self.source.calls, 2)

    def test_static_ui_and_configuration(self):
        for path, marker in [("/", b"Evidence included"), ("/app.js", b"textContent"), ("/style.css", b"@media")]:
            with self.subTest(path=path):
                status, body, headers = self.request("GET", path)
                self.assertEqual(status, 200)
                self.assertIn(marker, body)
                self.assertIn("frame-ancestors 'none'", headers["Content-Security-Policy"])
        _, config, _ = self.request("GET", "/api/config")
        self.assertEqual(config["source"], self.source.name)
        self.assertEqual(config["max_batch"], 100)
        self.assertEqual(config["max_concurrent"], 2)
        self.assertEqual(config["max_body_bytes"], 32768)
        self.assertEqual(config["request_timeout"], 15)

    def test_bad_requests_are_explicit_and_do_not_call_source(self):
        for data in ({}, [], {"numbers": []}, {"numbers": [123]}, {"numbers": [NUMBER] * 101}):
            with self.subTest(data=data):
                self.assertEqual(self.post("/api/lookup", data)[0], 400)
        self.assertEqual(self.request("POST", "/api/lookup", "{", {"Content-Type": "application/json"})[0], 400)
        self.assertEqual(self.request("POST", "/api/lookup", "{}")[0], 415)
        self.assertEqual(self.request("POST", "/api/lookup", "x" * 32769, {"Content-Type": "application/json"})[0], 413)
        self.assertEqual(self.source.calls, 0)

    def test_cross_origin_host_and_unknown_paths_are_rejected(self):
        body = json.dumps({"numbers": [NUMBER]})
        for extra in ({"Origin": "https://evil.example"}, {"Host": "evil.example"}):
            headers = {"Content-Type": "application/json", **extra}
            self.assertEqual(self.request("POST", "/api/lookup", body, headers)[0], 403)
        self.assertEqual(self.request("GET", "/", headers={"Host": "evil.example"})[0], 403)
        self.assertEqual(self.request("GET", "/api/companies", headers={"Origin": "https://evil.example"})[0], 403)
        for path in ("/../source.py", "/api/companies/unknown", "/no-such-file"):
            self.assertEqual(self.request("GET", path)[0], 404)
        self.assertEqual(self.post("/no-such-route", {})[0], 404)
        self.assertEqual(self.source.calls, 0)
