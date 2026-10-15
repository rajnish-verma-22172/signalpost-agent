"""Build the Signalpost submission dataset: >=1,000 live, evidence-backed company profiles.

Run from the repository root (needs HTTPS access to data.brreg.no, standard library only):

    python scripts/build_profiles.py

Steps:
  1. Collect candidate organization numbers from the public Enhetsregisteret search API
     (active, registered in the Register of Business Enterprises, not bankrupt/liquidating,
     at least one employee). Pages are sampled with a fixed seed so the run is reproducible.
  2. Look each candidate up live through the normal Signalpost pipeline until --target
     profiles have status "ok". Nothing is invented: missing/failed lookups are just skipped.
  3. Export exactly those profiles with the repository's own evaluator
     (artifacts/profiles-1000.json + report), reusing the lookups from step 2 (no extra requests).
  4. Optionally (--smoke) run the repository's 100-company smoke test fresh to measure
     per-run requests and runtime for the daily 100-company test.
  5. Write artifacts/SUBMISSION_SUMMARY.md with counts, coverage, timings, request counts,
     cost declaration, Python version and the commit hash.
"""

import argparse
import json
import platform
import random
import subprocess
import sys
import time
from pathlib import Path
from urllib.parse import urlencode
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from signalpost.evaluator import run_evaluation  # noqa: E402
from signalpost.service import Service  # noqa: E402
from signalpost.source import RegistrySource, valid_number  # noqa: E402
from signalpost.store import Store  # noqa: E402

SEARCH_URL = "https://data.brreg.no/enhetsregisteret/api/enheter"
SEARCH_FILTERS = {
    "konkurs": "false",
    "underAvvikling": "false",
    "underTvangsavviklingEllerTvangsopplosning": "false",
    "registrertIForetaksregisteret": "true",
    "fraAntallAnsatte": "1",
}
PAGE_SIZE = 100
MAX_PAGES = 100  # The search API serves at most 10,000 rows (size * pages).
USER_AGENT = "Signalpost-MVP/1.0 (public company registry lookup)"


def fetch_search_page(page, timeout=15):
    """Return organization numbers from one page of the public search API."""
    query = urlencode({**SEARCH_FILTERS, "size": PAGE_SIZE, "page": page})
    request = Request(f"{SEARCH_URL}?{query}",
                      headers={"Accept": "application/json", "User-Agent": USER_AGENT})
    with urlopen(request, timeout=timeout) as response:
        data = json.loads(response.read(5_000_001))
    rows = (data.get("_embedded") or {}).get("enheter") or []
    return [row.get("organisasjonsnummer") for row in rows]


def collect_candidates(pool_size, seed, fetch_page=None, pause=0.2):
    """Sample search pages with a fixed seed; return (unique valid numbers, search requests)."""
    fetch_page = fetch_page or fetch_search_page
    pages = list(range(MAX_PAGES))
    random.Random(seed).shuffle(pages)
    numbers, seen, requests = [], set(), 0
    for page in pages:
        if len(numbers) >= pool_size:
            break
        requests += 1
        for number in fetch_page(page):
            if isinstance(number, str) and valid_number(number) and number not in seen:
                seen.add(number)
                numbers.append(number)
        time.sleep(pause)
    return numbers[:pool_size], requests


def select_ok_profiles(service, candidates, target, pause=0.05, log=print):
    """Live-look-up candidates until `target` have status ok. Returns (ok numbers, stats)."""
    chosen, skipped = [], {}
    started = time.monotonic()
    requests_before = service.source.request_count
    for index, number in enumerate(candidates, 1):
        if len(chosen) >= target:
            break
        result = service.lookup(number, refresh=True)
        if result["status"] == "ok":
            chosen.append(number)
        else:
            skipped[result["status"]] = skipped.get(result["status"], 0) + 1
        if index % 100 == 0:
            log(f"  looked up {index}, ok so far: {len(chosen)}")
        time.sleep(pause)
    return chosen, {
        "lookup_requests": service.source.request_count - requests_before,
        "lookup_seconds": round(time.monotonic() - started, 1),
        "skipped": skipped,
    }


def git_commit():
    try:
        return subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT, capture_output=True,
                              text=True, check=True).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return "unknown (run `git rev-parse HEAD` and record it)"


def write_summary(path, *, target, seed, candidates, search_requests, stats, report, smoke):
    coverage = report.get("field_coverage", {})
    lines = [
        "# Signalpost submission summary",
        "",
        f"- Generated: {report['completed_at']}",
        f"- Code commit used for this run: `{git_commit()}` (record the final submitted hash with `git rev-parse HEAD`)",
        f"- Python: {platform.python_version()} ({platform.system()})",
        f"- Source: {report['source']} (public API, no key, no LLM, no paid API)",
        "",
        "## Company profiles",
        "",
        f"- Profiles delivered: **{report['statuses'].get('ok', 0)}** (target {target}), "
        f"file `artifacts/profiles-{target}.json`",
        f"- Output SHA-256: `{report['output_sha256']}`",
        f"- Candidates: {len(candidates)} active companies sampled from the public Enhetsregisteret search "
        f"(seed {seed}; filters: not bankrupt, not in liquidation, registered in Foretaksregisteret, >=1 employee)",
        f"- Lookups skipped (not used, never fabricated): {stats['skipped'] or 'none'}",
        "",
        "### Field coverage across delivered profiles",
        "",
        "| Field | Profiles with value |",
        "|---|---|",
        *[f"| {field} | {count} |" for field, count in sorted(coverage.items(), key=lambda kv: -kv[1])],
        "",
        "## Measured run cost",
        "",
        f"- Dataset build: {search_requests} search requests + {stats['lookup_requests']} lookup requests "
        f"in {stats['lookup_seconds']} s",
    ]
    if smoke:
        lines += [
            f"- Daily 100-company test (fresh smoke run): **{smoke['request_count']} outbound requests**, "
            f"**{smoke['runtime_seconds']:.0f} s**, statuses {smoke['statuses']}",
            "- Limits: 45 minutes, 2,000 outbound requests, $10 declared API cost per day",
        ]
    lines += [
        "- External API cost: **$0.00** (public registry, no paid APIs, no model calls)",
        "- Compute: runs on any machine with Python 3.11+; no GPU, no hosted service required",
        "",
        "## Run commands",
        "",
        "```",
        "python -m signalpost serve                                  # dashboard",
        "python -m signalpost evaluate --input <file> --output <out.json> --report <report.json>",
        "python scripts/build_profiles.py --smoke                    # rebuild this dataset",
        "```",
        "",
    ]
    Path(path).write_text("\n".join(lines), encoding="utf-8")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--target", type=int, default=1000, help="Profiles with status ok (default 1000)")
    parser.add_argument("--pool", type=int, default=1300, help="Candidates to sample (default 1300)")
    parser.add_argument("--seed", type=int, default=2026)
    parser.add_argument("--out-dir", type=Path, default=ROOT / "artifacts")
    parser.add_argument("--db", type=Path, default=ROOT / "data" / "profiles-build.sqlite3")
    parser.add_argument("--smoke", action="store_true", help="Also run the fresh 100-company smoke test")
    args = parser.parse_args(argv)
    if not 1 <= args.target <= args.pool <= 10_000:
        parser.error("Need 1 <= target <= pool <= 10000.")

    out = args.out_dir
    out.mkdir(parents=True, exist_ok=True)
    args.db.parent.mkdir(parents=True, exist_ok=True)
    service = Service(Store(args.db), RegistrySource())

    print(f"1/4 Sampling {args.pool} candidate companies from the public registry search...")
    candidates, search_requests = collect_candidates(args.pool, args.seed)
    print(f"    got {len(candidates)} candidates with {search_requests} search requests")
    if len(candidates) < args.target:
        sys.exit(f"Only {len(candidates)} candidates found; check network access to data.brreg.no.")

    print(f"2/4 Live lookups until {args.target} profiles are ok...")
    chosen, stats = select_ok_profiles(service, candidates, args.target)
    print(f"    ok: {len(chosen)}, requests: {stats['lookup_requests']}, "
          f"time: {stats['lookup_seconds']} s, skipped: {stats['skipped'] or 'none'}")
    if len(chosen) < args.target:
        sys.exit(f"Only {len(chosen)} ok profiles; rerun with a larger --pool.")

    print("3/4 Exporting profiles with the repository evaluator (reuses step 2 lookups)...")
    input_file = out / f"companies-{args.target}.txt"
    input_file.write_text("\n".join(chosen) + "\n", encoding="utf-8")
    report = run_evaluation(service, input_file, out / f"profiles-{args.target}.json",
                            out / f"profiles-{args.target}-report.json", refresh=False,
                            max_seconds=3600)
    if report["statuses"].get("ok", 0) < args.target:
        sys.exit(f"Export contains {report['statuses']} — expected {args.target} ok profiles.")

    smoke = None
    if args.smoke:
        print("4/4 Fresh 100-company smoke test (measures the daily test cost)...")
        smoke_db = args.db.with_name("smoke-build.sqlite3")
        smoke_service = Service(Store(smoke_db), RegistrySource())
        smoke_input = out / "smoke-100.txt"
        smoke_input.write_text("\n".join(chosen[:100]) + "\n", encoding="utf-8")
        smoke = run_evaluation(smoke_service, smoke_input, out / "smoke-results.json",
                               out / "smoke-report.json", refresh=True, smoke=True, max_seconds=1800)
        print(f"    requests: {smoke['request_count']}, runtime: {smoke['runtime_seconds']:.0f} s, "
              f"statuses: {smoke['statuses']}")
    else:
        print("4/4 Skipped smoke test (add --smoke to measure the daily 100-company cost)")

    write_summary(out / "SUBMISSION_SUMMARY.md", target=args.target, seed=args.seed,
                  candidates=candidates, search_requests=search_requests, stats=stats,
                  report=report, smoke=smoke)
    print(f"\nDone. Commit the artifacts/ folder, then record `git rev-parse HEAD`.\n"
          f"  {out / f'profiles-{args.target}.json'}\n  {out / 'SUBMISSION_SUMMARY.md'}")


if __name__ == "__main__":
    main()
