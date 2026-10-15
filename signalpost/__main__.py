import argparse
import json
import os
from pathlib import Path

from .evaluator import run_evaluation
from .server import make_server
from .service import Service
from .source import DemoSource, RegistrySource
from .store import Store


def main():
    parser = argparse.ArgumentParser(description="Signalpost company research")
    parser.add_argument("--db", type=Path, default=os.getenv("SIGNALPOST_DB"),
                        help="SQLite file (or SIGNALPOST_DB; default: data/signalpost.sqlite3)")
    parser.add_argument("--demo", action="store_true", help="Use labeled offline fixtures and a separate default database")
    commands = parser.add_subparsers(dest="command", required=True)
    serve = commands.add_parser("serve", help="Start the local dashboard")
    serve.add_argument("--host", default=os.getenv("SIGNALPOST_HOST", "127.0.0.1"))
    serve.add_argument("--port", default=os.getenv("PORT", "8000"), type=int)
    serve.add_argument("--allowed-hosts", default=os.getenv("SIGNALPOST_ALLOWED_HOSTS"),
                       help="Comma-separated exact Host headers, including port if present")
    serve.add_argument("--allowed-origins", default=os.getenv("SIGNALPOST_ALLOWED_ORIGINS"),
                       help="Comma-separated exact browser origins (https://host)")
    serve.add_argument("--max-batch", type=int, default=os.getenv("SIGNALPOST_MAX_BATCH", "100"))
    serve.add_argument("--max-concurrent", type=int, default=os.getenv("SIGNALPOST_MAX_CONCURRENT", "2"))
    lookup = commands.add_parser("lookup", help="Print evidence-backed JSON")
    lookup.add_argument("numbers", nargs="+")
    lookup.add_argument("--refresh", action="store_true")
    lookup.add_argument("--max-seconds", type=float, default=300)
    for name in ("evaluate", "smoke"):
        command = commands.add_parser(name, help="Run live file evaluation" if name == "evaluate"
                                      else "Verify exactly 100 distinct live companies")
        command.add_argument("--input", type=Path, required=True)
        command.add_argument("--output", type=Path, required=True)
        command.add_argument("--report", type=Path, required=True)
        command.add_argument("--number-field", help="Organization-number column/key in company objects")
        command.add_argument("--output-format", choices=("profiles", "envelopes"), default="profiles",
                             help="profiles JSON (default) or claim/evidence envelopes JSONL")
        command.add_argument("--max-seconds", type=float, default=1800 if name == "smoke" else 300)
        command.add_argument("--use-cache", action="store_true",
                             help="Reuse stored results; smoke always requires fresh requests")
    args = parser.parse_args()
    db = args.db or Path("data/demo.sqlite3" if args.demo else "data/signalpost.sqlite3")
    service = Service(Store(db), DemoSource() if args.demo else RegistrySource())
    if args.command == "lookup":
        try:
            results = service.batch(args.numbers, args.refresh, max_seconds=args.max_seconds)
        except ValueError as exc:
            parser.error(str(exc))
        print(json.dumps({"results": results}, ensure_ascii=False, indent=2))
        return
    if args.command in ("evaluate", "smoke"):
        if args.command == "smoke" and args.use_cache:
            parser.error("Smoke verification cannot use cached results.")
        try:
            report = run_evaluation(
                service, args.input, args.output, args.report, number_field=args.number_field,
                max_seconds=args.max_seconds, refresh=not args.use_cache, smoke=args.command == "smoke",
                output_format=args.output_format,
            )
        except (OSError, ValueError, UnicodeError) as exc:
            parser.error(str(exc))
        print(json.dumps(report, ensure_ascii=False, indent=2))
        if args.command == "smoke" and report["failures"]:
            raise SystemExit(1)
        return
    try:
        server = make_server(
            service, args.host, args.port,
            allowed_hosts=args.allowed_hosts.split(",") if args.allowed_hosts else None,
            allowed_origins=args.allowed_origins.split(",") if args.allowed_origins else None,
            auth_token=os.getenv("SIGNALPOST_AUTH_TOKEN"),
            max_batch=args.max_batch, max_concurrent=args.max_concurrent,
        )
    except ValueError as exc:
        parser.error(str(exc))
    print(f"Signalpost: http://{args.host}:{server.server_port} — {service.source.name}", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
