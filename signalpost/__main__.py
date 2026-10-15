import argparse
import json
from pathlib import Path

from .server import make_server
from .service import Service
from .source import DemoSource, RegistrySource
from .store import Store


def main():
    parser = argparse.ArgumentParser(description="Signalpost company research")
    parser.add_argument("--db", type=Path, help="SQLite file (default: data/signalpost.sqlite3)")
    parser.add_argument("--demo", action="store_true", help="Use labeled offline fixtures and a separate default database")
    commands = parser.add_subparsers(dest="command", required=True)
    serve = commands.add_parser("serve", help="Start the local dashboard")
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", default=8000, type=int)
    lookup = commands.add_parser("lookup", help="Print evidence-backed JSON")
    lookup.add_argument("numbers", nargs="+")
    lookup.add_argument("--refresh", action="store_true")
    args = parser.parse_args()
    db = args.db or Path("data/demo.sqlite3" if args.demo else "data/signalpost.sqlite3")
    service = Service(Store(db), DemoSource() if args.demo else RegistrySource())
    if args.command == "lookup":
        try:
            results = service.batch(args.numbers, args.refresh)
        except ValueError as exc:
            parser.error(str(exc))
        print(json.dumps({"results": results}, ensure_ascii=False, indent=2))
        return
    server = make_server(service, args.host, args.port)
    print(f"Signalpost: http://{args.host}:{server.server_port} — {service.source.name}", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
