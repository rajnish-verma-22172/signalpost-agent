"""File-based evaluation using the same evidence-backed lookup pipeline."""

import csv
import hashlib
import io
import json
from collections import Counter
from pathlib import Path
from time import monotonic
from uuid import uuid4

from .source import FIELDS, now, valid_number


MAX_INPUT_BYTES = 20_000_000
NUMBER_FIELDS = ("organisation_number", "registration_number", "organization_number", "organisasjonsnummer", "orgnr")


def read_companies(path, number_field=None):
    path = Path(path)
    with path.open("rb") as stream:
        raw = stream.read(MAX_INPUT_BYTES + 1)
    if len(raw) > MAX_INPUT_BYTES:
        raise ValueError("Input file exceeds 20 MB.")
    text = raw.decode("utf-8-sig")
    suffix = path.suffix.lower()
    if suffix == ".json":
        rows = json.loads(text)
        if isinstance(rows, dict):
            rows = rows.get("organisation_numbers", rows.get("companies", rows.get("numbers")))
    elif suffix == ".jsonl":
        rows = [json.loads(line) for line in text.splitlines() if line.strip()]
    elif suffix == ".csv":
        rows = list(csv.DictReader(io.StringIO(text)))
    elif suffix == ".txt":
        rows = text.splitlines()
    else:
        raise ValueError("Input must be .json, .jsonl, .csv, or .txt.")
    if not isinstance(rows, list) or not 1 <= len(rows) <= 10_000:
        raise ValueError("Input must contain 1–10000 companies.")
    numbers = []
    for row in rows:
        value = row
        if isinstance(row, dict):
            fields = (number_field,) if number_field else NUMBER_FIELDS
            value = next((row[key] for key in fields if key in row), "")
        if isinstance(value, int) and not isinstance(value, bool):
            value = str(value)
        # Unsupported rows remain in the output as invalid, never silently skipped.
        numbers.append(value.strip() if isinstance(value, str) and len(value) <= 100 else "")
    return raw, rows, numbers


def envelope(result, run_id, started_at, completed_at):
    """Export claim-level availability without promoting stale facts to live claims."""
    claims, evidence = [], []
    unavailable = {"blocked": "blocked", "ambiguous": "ambiguous", "failed": "failed",
                   "invalid": "failed"}.get(result["status"], "not_available")
    for field in ("registration_number", *FIELDS):
        fact = result["facts"].get(field) if result["status"] == "ok" else None
        ids = []
        if fact is not None:
            source = fact["evidence"]
            evidence_id = f"e{len(evidence) + 1}"
            ids.append(evidence_id)
            snippet = source["snippet"]
            evidence.append({
                "id": evidence_id, "source_url": source["url"],
                "source_class": "official_registry", "retrieved_at": source["retrieved_at"],
                "content_sha256": hashlib.sha256(snippet.encode("utf-8")).hexdigest(),
                "claim_span": snippet, "snapshot": snippet,
            })
        claims.append({
            "field": field, "value": fact["value"] if fact is not None else None,
            "availability": "available" if fact is not None else unavailable, "evidence_ids": ids,
        })
    return {
        "organisation_number": result["registration_number"],
        "input_index": result["input_index"], "input": result["input"],
        "run": {"run_id": run_id, "started_at": started_at,
                "completed_at": completed_at, "terminal_status": "completed"},
        "claims": claims, "evidence": evidence,
        "changes": result["history"][0]["changes"] if result["history"] else [],
        "errors": [] if result["status"] == "ok" else [
            {"status": result["status"], "message": result["message"]}],
        "profile": result,
    }


def run_evaluation(service, input_path, output_path, report_path, *,
                   number_field=None, max_seconds=300, refresh=True, smoke=False,
                   output_format="profiles"):
    if output_format not in ("profiles", "envelopes"):
        raise ValueError("Output format must be profiles or envelopes.")
    paths = [Path(p).resolve() for p in (input_path, output_path, report_path)]
    if len(set(paths)) != 3 or Path(service.store.path).resolve() in paths:
        raise ValueError("Input, output, report, and database paths must be distinct.")
    if "DEMO" in service.source.name.upper():
        raise ValueError("Evaluation and smoke tests require live mode, not --demo.")
    raw, rows, numbers = read_companies(input_path, number_field)
    if smoke and (len(numbers) != 100 or len(set(numbers)) != 100
                  or not all(valid_number(number) for number in numbers)):
        raise ValueError("Smoke input must contain exactly 100 distinct valid organization numbers.")
    started_at = now()
    run_id = str(uuid4())
    started = monotonic()
    requests_before = service.source.request_count
    results = service.batch(numbers, refresh=refresh, max_seconds=max_seconds)
    results = [{**result, "input_index": index, "input": row}
               for index, (row, result) in enumerate(zip(rows, results))]
    completed_at = now()
    if output_format == "envelopes":
        output = "".join(json.dumps(envelope(result, run_id, started_at, completed_at),
                                   ensure_ascii=False) + "\n" for result in results).encode()
    else:
        output = (json.dumps({"results": results}, ensure_ascii=False, indent=2) + "\n").encode()
    statuses = Counter(result["status"] for result in results)
    report = {
        "source": service.source.name,
        "run_id": run_id, "started_at": started_at, "completed_at": completed_at,
        "output_format": output_format,
        "input_sha256": hashlib.sha256(raw).hexdigest(),
        "output_sha256": hashlib.sha256(output).hexdigest(),
        "input_count": len(rows), "output_count": len(results),
        "request_count": service.source.request_count - requests_before,
        "max_seconds": max_seconds, "refresh": refresh, "smoke": smoke,
        "statuses": dict(statuses),
        "failures": [{"input_index": r["input_index"], "registration_number": r["registration_number"],
                      "status": r["status"], "message": r["message"]}
                     for r in results if r["status"] != "ok"],
        "field_coverage": dict(Counter(field for r in results if r["status"] == "ok"
                                       for field in r["facts"])),
    }
    Path(output_path).parent.mkdir(parents=True, exist_ok=True)
    Path(output_path).write_bytes(output)
    report["runtime_seconds"] = monotonic() - started
    Path(report_path).parent.mkdir(parents=True, exist_ok=True)
    Path(report_path).write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return report
