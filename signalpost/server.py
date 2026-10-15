import base64
import binascii
import hmac
import ipaddress
import json
import re
import socket
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import BoundedSemaphore
from urllib.parse import urlsplit


STATIC = Path(__file__).parent / "static"


MAX_BODY_BYTES = 32_768
REQUEST_TIMEOUT = 15


def _authority(value):
    if not isinstance(value, str) or not value or re.search(r"[\s/*?#@\\]", value):
        raise ValueError("Allowlist hosts must be exact hostnames or IP addresses with optional ports.")
    parsed = urlsplit("//" + value)
    if not parsed.hostname or parsed.path or parsed.port == 0 or value.endswith(":"):
        raise ValueError("Invalid allowlist host.")
    if ":" in parsed.hostname:
        ipaddress.IPv6Address(parsed.hostname)
    elif not re.fullmatch(r"[A-Za-z0-9.-]+", parsed.hostname):
        raise ValueError("Invalid allowlist host.")
    return value


def _allowlist(values, *, origins=False):
    if isinstance(values, (str, bytes)):
        raise ValueError("Allowlists must be collections of exact values.")
    try:
        entries = tuple(values)
    except TypeError as exc:
        raise ValueError("Allowlists must be collections of exact values.") from exc
    if not entries:
        raise ValueError("Allowlists must not be empty.")
    for value in entries:
        if origins:
            if not isinstance(value, str):
                raise ValueError("Origins must be exact HTTP or HTTPS origins.")
            parsed = urlsplit(value)
            if parsed.scheme not in ("http", "https") or value != f"{parsed.scheme}://{parsed.netloc}":
                raise ValueError("Origins must be exact HTTP or HTTPS origins without paths.")
            _authority(parsed.netloc)
        else:
            _authority(value)
    return frozenset(entries)


def make_server(service, host="127.0.0.1", port=8000, *, allowed_hosts=None,
                allowed_origins=None, auth_token=None, max_batch=100, max_concurrent=2):
    if type(max_batch) is not int or not 1 <= max_batch <= 100:
        raise ValueError("max_batch must be an integer between 1 and 100.")
    if type(max_concurrent) is not int or not 1 <= max_concurrent <= 32:
        raise ValueError("max_concurrent must be an integer between 1 and 32.")
    if auth_token is not None and (not isinstance(auth_token, str) or len(auth_token) < 32):
        raise ValueError("auth_token must contain at least 32 characters.")
    try:
        loopback = ipaddress.ip_address(host).is_loopback
    except ValueError:
        loopback = host == "localhost"
    if not loopback and (allowed_hosts is None or allowed_origins is None or auth_token is None):
        raise ValueError("Non-loopback binding requires explicit allowed_hosts, allowed_origins and auth_token.")
    hosts = _allowlist(allowed_hosts) if allowed_hosts is not None else None
    origins = _allowlist(allowed_origins, origins=True) if allowed_origins is not None else None
    credential = f"signalpost:{auth_token}".encode("utf-8") if auth_token is not None else None

    class Server(ThreadingHTTPServer):
        address_family = socket.AF_INET6 if ":" in host else socket.AF_INET

        def __init__(self):
            self.workers = BoundedSemaphore(max_concurrent)
            super().__init__((host, port), Handler)

        def process_request(self, request, client_address):
            request.settimeout(REQUEST_TIMEOUT)
            if not self.workers.acquire(blocking=False):
                try:
                    request.settimeout(0.1)
                    request.sendall(b"HTTP/1.0 503 Service Unavailable\r\n"
                                    b"Content-Length: 0\r\nConnection: close\r\n\r\n")
                except OSError:
                    pass
                finally:
                    self.shutdown_request(request)
                return
            try:
                super().process_request(request, client_address)
            except BaseException:
                self.workers.release()
                raise

        def process_request_thread(self, request, client_address):
            try:
                super().process_request_thread(request, client_address)
            finally:
                self.workers.release()

    class Handler(BaseHTTPRequestHandler):
        def send(self, status, data, content_type="application/json; charset=utf-8", *, challenge=False):
            body = json.dumps(data, ensure_ascii=False).encode() if content_type.startswith("application/json") else data
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            if challenge:
                self.send_header("WWW-Authenticate", 'Basic realm="Signalpost", charset="UTF-8"')
            self.send_header("Content-Security-Policy", "default-src 'self'; style-src 'self'; script-src 'self'; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'")
            self.end_headers()
            if self.command != "HEAD":
                self.wfile.write(body)

        def parse_request(self):
            if not super().parse_request():
                return False
            self.public_health = self.command == "GET" and self.requestline.split()[1] == "/healthz"
            authority = self.headers.get("Host")
            if len(self.headers.get_all("Host", [])) != 1 or authority not in self.server.allowed_hosts:
                self.send(403, {"error": "Untrusted Host header."})
                return False
            origin = self.headers.get("Origin")
            if len(self.headers.get_all("Origin", [])) > 1 or (
                origin is not None and origin not in self.server.allowed_origins
            ):
                self.send(403, {"error": "Cross-origin requests are not allowed."})
                return False
            if credential is not None and not self.public_health:
                authorization = self.headers.get("Authorization", "").split()
                supplied = b""
                if len(authorization) == 2 and authorization[0].lower() == "basic":
                    try:
                        supplied = base64.b64decode(authorization[1], validate=True)
                    except (ValueError, binascii.Error):
                        pass
                if len(self.headers.get_all("Authorization", [])) != 1 or not hmac.compare_digest(supplied, credential):
                    self.send(401, {"error": "Authentication required."}, challenge=True)
                    return False
            return True

        def do_GET(self):
            if self.public_health:
                return self.send(200, {"status": "ok"})
            path = urlsplit(self.path).path
            if path == "/api/config":
                return self.send(200, {**service.describe(), "max_batch": max_batch,
                                       "max_concurrent": max_concurrent,
                                       "max_body_bytes": MAX_BODY_BYTES,
                                       "request_timeout": REQUEST_TIMEOUT})
            if path == "/api/companies":
                return self.send(200, {"companies": service.store.list()})
            if path.startswith("/api/companies/"):
                result = service.store.get(path.removeprefix("/api/companies/"))
                return self.send(200, result) if result else self.send(404, {"error": "Profile not found."})
            files = {"/": ("index.html", "text/html; charset=utf-8"),
                     "/app.js": ("app.js", "text/javascript; charset=utf-8"),
                     "/style.css": ("style.css", "text/css; charset=utf-8")}
            if path not in files:
                return self.send(404, {"error": "Not found."})
            filename, content_type = files[path]
            self.send(200, (STATIC / filename).read_bytes(), content_type)

        def do_POST(self):
            if self.path not in ("/api/lookup", "/api/refresh"):
                return self.send(404, {"error": "Not found."})
            if self.headers.get("Content-Type", "").split(";")[0] != "application/json":
                return self.send(415, {"error": "Use application/json."})
            try:
                if self.headers.get("Transfer-Encoding") or len(self.headers.get_all("Content-Length", [])) != 1:
                    raise ValueError("Provide one Content-Length without Transfer-Encoding.")
                size = int(self.headers.get("Content-Length", "0"))
                if not 0 < size <= MAX_BODY_BYTES:
                    return self.send(413, {"error": "Request must be between 1 and 32768 bytes."})
                data = json.loads(self.rfile.read(size))
                if not isinstance(data, dict):
                    raise ValueError("Request must be a JSON object.")
                numbers = data.get("numbers")
                if not isinstance(numbers, list) or not 1 <= len(numbers) <= max_batch:
                    raise ValueError(f"Provide a list of 1–{max_batch} registration numbers.")
                results = service.batch(numbers, refresh=self.path == "/api/refresh")
            except (ValueError, UnicodeError) as exc:
                return self.send(400, {"error": str(exc)})
            self.send(200, {"results": results})

        def setup(self):
            super().setup()
            self.connection.settimeout(REQUEST_TIMEOUT)

    server = Server()
    bound_host = f"[{host}]" if ":" in host else host
    server.allowed_hosts = hosts if hosts is not None else frozenset(
        f"{name}:{server.server_port}" for name in ("localhost", "127.0.0.1", bound_host)
    )
    server.allowed_origins = origins if origins is not None else frozenset(
        f"http://{name}" for name in server.allowed_hosts
    )
    return server
