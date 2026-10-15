import base64
import json
import socket
import threading
import unittest
from http.client import HTTPConnection
from http.server import ThreadingHTTPServer
from unittest.mock import Mock, patch

from signalpost.server import make_server


TOKEN = "hosted-test-token-with-at-least-32-characters"


def basic(secret=TOKEN, username="signalpost"):
    return "Basic " + base64.b64encode(f"{username}:{secret}".encode()).decode()


class HostingTests(unittest.TestCase):
    def setUp(self):
        self.service = Mock()
        self.service.describe.return_value = {"source": "test", "max_batch": 10000}
        self.service.batch.side_effect = lambda numbers, refresh=False: numbers
        self.service.store.list.return_value = []
        self.service.store.get.return_value = None

    def start(self, **settings):
        server = make_server(self.service, port=0, **settings)
        server.daemon_threads = False
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()

        def cleanup():
            server.shutdown()
            server.server_close()
            thread.join()

        self.addCleanup(cleanup)
        return server

    def wait_for_workers(self, server):
        for worker in list(server._threads):
            worker.join(3)
            self.assertFalse(worker.is_alive(), "HTTP worker did not finish")

    def request(self, server, method="GET", path="/api/config", body=None, **headers):
        connection = HTTPConnection("127.0.0.1", server.server_port, timeout=3)
        try:
            connection.request(method, path, body, headers)
            response = connection.getresponse()
            data = response.read()
            if data and response.getheader("Content-Type", "").startswith("application/json"):
                data = json.loads(data)
            # Response delivery can precede worker cleanup and slot release.
            if response.status != 503:
                self.wait_for_workers(server)
            return response.status, data, dict(response.getheaders())
        finally:
            connection.close()

    def test_non_loopback_requires_all_explicit_security_settings(self):
        settings = {"allowed_hosts": ["dashboard.example"],
                    "allowed_origins": ["https://dashboard.example"], "auth_token": TOKEN}
        for missing in settings:
            with self.subTest(missing=missing), self.assertRaises(ValueError):
                make_server(self.service, host="0.0.0.0", port=0,
                            **{key: value for key, value in settings.items() if key != missing})
        server = self.start(host="0.0.0.0", **settings)
        status, _, _ = self.request(server, Host="dashboard.example", Origin="https://dashboard.example",
                                    Authorization=basic())
        self.assertEqual(status, 200)

    def test_bad_settings_fail_before_binding(self):
        cases = [
            {"auth_token": ""}, {"auth_token": "x" * 31}, {"auth_token": 123},
            {"max_batch": 0}, {"max_batch": 101}, {"max_batch": True}, {"max_batch": 1.5},
            {"max_concurrent": 0}, {"max_concurrent": -1}, {"max_concurrent": 33},
            {"max_concurrent": True},
            {"max_concurrent": 1.5}, {"allowed_hosts": []}, {"allowed_origins": []},
            {"allowed_hosts": "example.com"}, {"allowed_origins": "https://example.com"},
            {"allowed_hosts": ["*"]}, {"allowed_hosts": ["*.example.com"]},
            {"allowed_hosts": ["https://example.com"]}, {"allowed_hosts": ["user@example.com"]},
            {"allowed_hosts": ["example.com/path"]}, {"allowed_hosts": ["example.com:bad"]},
            {"allowed_hosts": ["example.com:65536"]}, {"allowed_hosts": ["example.com:"]},
            {"allowed_hosts": ["example.com\n"]}, {"allowed_hosts": [None]},
            {"allowed_origins": ["*"]}, {"allowed_origins": ["null"]},
            {"allowed_origins": ["https://*.example.com"]},
            {"allowed_origins": ["https://example.com/"]},
            {"allowed_origins": ["https://example.com?"]},
            {"allowed_origins": ["https://example.com#fragment"]},
            {"allowed_origins": ["https://user@example.com"]},
            {"allowed_origins": ["ftp://example.com"]}, {"allowed_origins": [None]},
        ]
        with patch("signalpost.server.ThreadingHTTPServer.server_bind") as bind:
            for settings in cases:
                with self.subTest(settings=settings), self.assertRaises(ValueError):
                    make_server(self.service, port=0, **settings)
            bind.assert_not_called()

    def test_basic_challenges_every_route_and_method_without_disclosing_token(self):
        server = self.start(auth_token=TOKEN)
        for method, path in [
            ("GET", "/"), ("GET", "/app.js"), ("GET", "/style.css"),
            ("GET", "/api/config"), ("GET", "/api/companies"),
            ("GET", "/api/companies/123"), ("GET", "/unknown"),
            ("POST", "/api/lookup"), ("POST", "/api/refresh"), ("OPTIONS", "/"),
        ]:
            with self.subTest(method=method, path=path):
                status, data, headers = self.request(server, method, path)
                self.assertEqual(status, 401)
                self.assertIn('Basic realm="Signalpost"', headers["WWW-Authenticate"])
                self.assertNotIn(TOKEN, str(data) + str(headers))
        self.service.batch.assert_not_called()
        self.service.store.list.assert_not_called()
        for authorization in [basic("wrong"), basic(username="wrong"), "Basic !!!", "Bearer " + TOKEN]:
            with self.subTest(authorization=authorization):
                self.assertEqual(self.request(server, Authorization=authorization)[0], 401)
        status, config, headers = self.request(server, Authorization=basic())
        self.assertEqual(status, 200)
        self.assertNotIn("WWW-Authenticate", headers)
        self.assertNotIn(TOKEN, json.dumps(config))
        self.assertEqual(self.request(server, path="/", Authorization=basic())[0], 200)

    def test_exact_https_allowlists_ignore_forwarded_headers_and_protect_get(self):
        server = self.start(allowed_hosts=["dashboard.example"],
                            allowed_origins=["https://dashboard.example"], auth_token=TOKEN)
        trusted = {"Host": "dashboard.example", "Authorization": basic()}
        for method in ("GET", "POST"):
            for origin in ("https://dashboard.example.evil", "http://dashboard.example",
                           "https://dashboard.example:443", "null", ""):
                with self.subTest(method=method, origin=origin):
                    self.assertEqual(self.request(server, method, Origin=origin, **trusted)[0], 403)
        for host in ("dashboard.example.evil", "dashboard.example:443", "localhost"):
            with self.subTest(host=host):
                self.assertEqual(self.request(server, Host=host, Authorization=basic(),
                                              **{"X-Forwarded-Host": "dashboard.example"})[0], 403)
        self.assertEqual(self.request(server, Origin="http://dashboard.example", **trusted,
                                      **{"X-Forwarded-Proto": "https"})[0], 403)
        self.assertEqual(self.request(server, Origin="https://dashboard.example", **trusted)[0], 200)
        self.assertEqual(self.request(server, **trusted)[0], 200)
        status, _, _ = self.request(
            server, "POST", "/api/lookup", json.dumps({"numbers": ["duplicate", "duplicate"]}),
            Origin="https://dashboard.example", **trusted, **{"Content-Type": "application/json"})
        self.assertEqual(status, 200)
        self.service.batch.assert_called_once_with(["duplicate", "duplicate"], refresh=False)

    def test_public_health_is_exact_get_only_and_checks_host_and_origin(self):
        server = self.start(allowed_hosts=["dashboard.example"],
                            allowed_origins=["https://dashboard.example"], auth_token=TOKEN)
        status, data, _ = self.request(server, path="/healthz", Host="dashboard.example")
        self.assertEqual(status, 200)
        self.assertEqual(data, {"status": "ok"})
        self.assertEqual(self.service.mock_calls, [])
        self.assertEqual(self.request(server, path="/healthz", Host="untrusted.example")[0], 403)
        self.assertEqual(self.request(server, path="/healthz", Host="dashboard.example",
                                      Origin="https://untrusted.example")[0], 403)
        self.assertEqual(self.request(server, path="/healthz", Host="dashboard.example",
                                      Origin="https://dashboard.example")[0], 200)
        for method, path in [
            ("HEAD", "/healthz"), ("POST", "/healthz"), ("OPTIONS", "/healthz"),
            ("GET", "/healthz?"), ("GET", "/healthz?probe=1"), ("GET", "/healthz/"),
            ("GET", "//healthz"), ("GET", "/%68ealthz"), ("GET", "/api/config"), ("GET", "/"),
        ]:
            with self.subTest(method=method, path=path):
                self.assertEqual(self.request(server, method, path, Host="dashboard.example")[0], 401)

    def test_api_limits_are_independent_of_service_limit(self):
        for maximum in (100, 2):
            server = self.start(max_batch=maximum, max_concurrent=3)
            status, config, _ = self.request(server)
            self.assertEqual(status, 200)
            self.assertEqual(config["max_batch"], maximum)
            self.assertEqual(config["max_concurrent"], 3)
            for route in ("/api/lookup", "/api/refresh"):
                status, _, _ = self.request(
                    server, "POST", route, json.dumps({"numbers": ["item"] * (maximum + 1)}),
                    **{"Content-Type": "application/json"})
                self.assertEqual(status, 400)
            self.service.batch.assert_not_called()
            status, data, _ = self.request(
                server, "POST", "/api/lookup", json.dumps({"numbers": ["item"] * maximum}),
                **{"Content-Type": "application/json"})
            self.assertEqual(status, 200)
            self.assertEqual(len(data["results"]), maximum)
            self.service.batch.reset_mock()

    def test_worker_limit_rejects_before_spawning_and_recovers(self):
        entered, release = threading.Event(), threading.Event()

        def batch(numbers, refresh=False):
            entered.set()
            release.wait(3)
            return numbers

        self.service.batch.side_effect = batch
        server = self.start(max_concurrent=1)
        responses = []
        with patch.object(ThreadingHTTPServer, "process_request", autospec=True,
                          side_effect=ThreadingHTTPServer.process_request) as spawn:
            thread = threading.Thread(target=lambda: responses.append(self.request(
                server, "POST", "/api/lookup", '{"numbers":["item"]}',
                **{"Content-Type": "application/json"})))
            thread.start()
            try:
                self.assertTrue(entered.wait(2))
                self.assertEqual(self.request(server)[0], 503)
                self.assertEqual(spawn.call_count, 1)
            finally:
                release.set()
                thread.join(3)
            self.assertFalse(thread.is_alive())
            self.assertEqual(responses[0][0], 200)
        self.assertTrue(server.workers.acquire(timeout=2))
        server.workers.release()
        self.assertEqual(self.request(server)[0], 200)

    def test_idle_socket_timeout_releases_worker(self):
        with patch("signalpost.server.REQUEST_TIMEOUT", 0.15):
            server = self.start(max_concurrent=1)
            with socket.create_connection(("127.0.0.1", server.server_port), timeout=2) as connection:
                connection.sendall(b"GET / HTTP/1.0\r\n")
                self.assertEqual(connection.recv(1), b"")
            self.assertTrue(server.workers.acquire(timeout=2))
            server.workers.release()
            self.assertEqual(self.request(server)[0], 200)

    def test_duplicate_security_headers_fail_closed(self):
        server = self.start(auth_token=TOKEN)
        authority = f"127.0.0.1:{server.server_port}"
        for extra, expected in [
            (f"Host: {authority}", 403),
            (f"Origin: http://{authority}\r\nOrigin: http://{authority}", 403),
            (f"Authorization: {basic()}", 401),
        ]:
            with self.subTest(extra=extra):
                with socket.create_connection(("127.0.0.1", server.server_port), timeout=2) as connection:
                    request = (f"GET /api/config HTTP/1.0\r\nHost: {authority}\r\n"
                               f"Authorization: {basic()}\r\n{extra}\r\n\r\n")
                    connection.sendall(request.encode())
                    self.assertIn(f" {expected} ".encode(), connection.recv(4096))
                self.wait_for_workers(server)

    @unittest.skipUnless(socket.has_ipv6, "IPv6 unavailable")
    def test_ipv6_loopback_defaults(self):
        try:
            server = self.start(host="::1")
        except OSError as exc:
            self.skipTest(str(exc))
        connection = HTTPConnection("::1", server.server_port, timeout=2)
        try:
            connection.request("GET", "/api/config")
            response = connection.getresponse()
            self.assertEqual(response.status, 200)
            response.read()
        finally:
            connection.close()
