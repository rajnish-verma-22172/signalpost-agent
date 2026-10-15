import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit


STATIC = Path(__file__).parent / "static"


def make_server(service, host="127.0.0.1", port=8000):
    class Handler(BaseHTTPRequestHandler):
        def send(self, status, data, content_type="application/json; charset=utf-8"):
            body = json.dumps(data, ensure_ascii=False).encode() if content_type.startswith("application/json") else data
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Content-Security-Policy", "default-src 'self'; style-src 'self'; script-src 'self'; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'")
            self.end_headers()
            self.wfile.write(body)

        def trusted_host(self):
            allowed = {f"localhost:{self.server.server_port}", f"127.0.0.1:{self.server.server_port}",
                       f"{host}:{self.server.server_port}"}
            return self.headers.get("Host") in allowed

        def do_GET(self):
            if not self.trusted_host():
                return self.send(403, {"error": "Untrusted Host header."})
            path = urlsplit(self.path).path
            if path == "/api/config":
                return self.send(200, service.describe())
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
            origin = self.headers.get("Origin")
            if not self.trusted_host() or (origin and origin != f"http://{self.headers.get('Host')}"):
                return self.send(403, {"error": "Cross-origin requests are not allowed."})
            if self.path not in ("/api/lookup", "/api/refresh"):
                return self.send(404, {"error": "Not found."})
            if self.headers.get("Content-Type", "").split(";")[0] != "application/json":
                return self.send(415, {"error": "Use application/json."})
            try:
                size = int(self.headers.get("Content-Length", "0"))
                if not 0 < size <= 32_768:
                    return self.send(413, {"error": "Request must be between 1 and 32768 bytes."})
                data = json.loads(self.rfile.read(size))
                if not isinstance(data, dict):
                    raise ValueError("Request must be a JSON object.")
                results = service.batch(data.get("numbers"), refresh=self.path == "/api/refresh")
            except (ValueError, UnicodeError) as exc:
                return self.send(400, {"error": str(exc)})
            self.send(200, {"results": results})

        def setup(self):
            super().setup()
            self.connection.settimeout(15)

    return ThreadingHTTPServer((host, port), Handler)
