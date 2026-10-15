import copy
import importlib.util
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from signalpost.service import Service
from signalpost.source import LookupProblem, valid_number
from signalpost.store import Store

ROOT = Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location("build_profiles", ROOT / "scripts" / "build_profiles.py")
build = importlib.util.module_from_spec(spec)
spec.loader.exec_module(build)


def valid_numbers(count, start=810000000):
    out, n = [], start
    while len(out) < count:
        if valid_number(str(n)):
            out.append(str(n))
        n += 1
    return out


class FakeRegistry:
    name = "Test registry fixture"

    def __init__(self, missing=()):
        self.request_count = 0
        self.missing = set(missing)

    def fetch(self, number):
        self.request_count += 1
        if number in self.missing:
            raise LookupProblem("missing", "No company found for this registration number.")
        return {"organisasjonsnummer": number, "navn": f"Company {number} AS",
                "organisasjonsform": {"kode": "AS", "beskrivelse": "Aksjeselskap"},
                "antallAnsatte": 3, "stiftelsesdato": "2015-01-01"}


class BuildProfilesTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.numbers = valid_numbers(260)

    def fake_pages(self, page):
        chunk = self.numbers[(page % 3) * 100:(page % 3) * 100 + 100]
        return chunk + ["123", None, chunk[0] if chunk else "x"]  # junk and duplicates

    def test_candidates_are_unique_valid_and_reproducible(self):
        a, requests = build.collect_candidates(250, seed=7, fetch_page=self.fake_pages, pause=0)
        b, _ = build.collect_candidates(250, seed=7, fetch_page=self.fake_pages, pause=0)
        self.assertEqual(a, b)
        self.assertEqual(len(a), len(set(a)))
        self.assertTrue(all(valid_number(n) for n in a))
        self.assertLessEqual(requests, build.MAX_PAGES)

    def test_selection_skips_non_ok_and_never_fabricates(self):
        missing = set(self.numbers[:5])
        service = Service(Store(Path(self.temp.name) / "s.sqlite3"), FakeRegistry(missing))
        chosen, stats = build.select_ok_profiles(service, self.numbers, 50, pause=0, log=lambda *_: None)
        self.assertEqual(len(chosen), 50)
        self.assertFalse(missing & set(chosen))
        self.assertEqual(stats["skipped"], {"missing": 5})
        self.assertEqual(stats["lookup_requests"], 55)

    def test_end_to_end_writes_dataset_report_smoke_and_summary(self):
        out, db = Path(self.temp.name) / "artifacts", Path(self.temp.name) / "data" / "b.sqlite3"
        registry = lambda: FakeRegistry(missing=self.numbers[:3])
        with mock.patch.object(build, "RegistrySource", side_effect=registry), \
             mock.patch.object(build, "fetch_search_page", side_effect=self.fake_pages), \
             mock.patch.object(build.time, "sleep"):
            build.main(["--target", "120", "--pool", "200", "--out-dir", str(out), "--db", str(db), "--smoke"])
        data = json.loads((out / "profiles-120.json").read_text(encoding="utf-8"))
        report = json.loads((out / "profiles-120-report.json").read_text(encoding="utf-8"))
        smoke = json.loads((out / "smoke-report.json").read_text(encoding="utf-8"))
        self.assertEqual(len(data["results"]), 120)
        self.assertTrue(all(r["status"] == "ok" for r in data["results"]))
        self.assertEqual(report["request_count"], 0)  # export reuses the live lookups
        self.assertEqual(smoke["request_count"], 100)
        summary = (out / "SUBMISSION_SUMMARY.md").read_text(encoding="utf-8")
        for text in ("Profiles delivered: **120**", "$0.00", "100 outbound requests", "| name | 120 |"):
            self.assertIn(text, summary)


if __name__ == "__main__":
    unittest.main()
