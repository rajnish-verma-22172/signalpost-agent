import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from signalpost.evaluator import read_companies, run_evaluation
from signalpost.service import Service
from signalpost.source import DemoSource, valid_number
from signalpost.store import Store
from tests.test_pipeline import FakeSource, NUMBER


class EvaluatorTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.source = FakeSource()
        self.source.request_count = 0
        original = self.source.fetch

        def fetch(number):
            self.source.request_count += 1
            return original(number)

        self.source.fetch = fetch
        self.service = Service(Store(self.root / "state.sqlite3"), self.source)

    def write_input(self, name, content):
        path = self.root / name
        path.write_text(content, encoding="utf-8")
        return path

    def run_file(self, path, **kwargs):
        return run_evaluation(self.service, path, self.root / "output.json",
                              self.root / "report.json", **kwargs)

    def test_formats_and_custom_column(self):
        for name, text, field in [
            ("british.json", json.dumps({"organisation_numbers": [
                {"organisation_number": NUMBER}, "invalid", NUMBER]}), None),
            ("input.json", json.dumps({"companies": [{"orgnr": NUMBER}, "invalid", NUMBER]}), None),
            ("input.jsonl", f'{{"id":"{NUMBER}"}}\n"invalid"\n"{NUMBER}"\n', "id"),
            ("input.csv", f"registration_number,name\n{NUMBER},Test\ninvalid,Unknown\n{NUMBER},Duplicate\n", None),
            ("input.txt", f"{NUMBER}\ninvalid\n{NUMBER}\n", None),
        ]:
            with self.subTest(name=name):
                path = self.write_input(name, text)
                report = self.run_file(path, number_field=field)
                results = json.loads((self.root / "output.json").read_text())["results"]
                self.assertEqual([r["status"] for r in results], ["ok", "invalid", "ok"])
                self.assertEqual([r["input_index"] for r in results], [0, 1, 2])
                self.assertEqual(report["request_count"], 1)
                self.assertEqual(report["input_count"], report["output_count"])
                self.assertEqual(report["failures"][0]["input_index"], 1)
                self.assertGreaterEqual(report["runtime_seconds"], 0)
                self.assertEqual(len(report["output_sha256"]), 64)

    def test_envelopes_export_evidence_and_do_not_invent_missing_fields(self):
        path = self.write_input("input.txt", f"{NUMBER}\ninvalid\n")
        self.run_file(path, output_format="envelopes")
        results = [json.loads(line) for line in (self.root / "output.json").read_text().splitlines()]
        self.assertEqual(len(results), 2)
        claims = {claim["field"]: claim for claim in results[0]["claims"]}
        self.assertEqual(claims["employees"]["value"], 0)
        self.assertEqual(claims["employees"]["availability"], "available")
        self.assertIsNone(claims["industry"]["value"])
        self.assertEqual(claims["industry"]["availability"], "not_available")
        self.assertEqual(results[0]["organisation_number"], NUMBER)
        evidence_ids = {item["id"] for item in results[0]["evidence"]}
        self.assertTrue(set(claims["employees"]["evidence_ids"]) <= evidence_ids)
        self.assertTrue(all(claim["availability"] == "failed" for claim in results[1]["claims"]))
        self.assertTrue(results[1]["errors"])

    def test_large_input_preserves_unsupported_rows(self):
        rows = [NUMBER, {}, None, {"registration_number": True}] * 300
        report = self.run_file(self.write_input("input.json", json.dumps(rows)))
        self.assertEqual(report["output_count"], 1200)
        self.assertEqual(report["statuses"], {"ok": 300, "invalid": 900})
        self.assertEqual(report["request_count"], 1)

    def test_bad_input_and_paths_are_rejected_without_overwriting(self):
        path = self.write_input("input.json", "[]")
        with self.assertRaises(ValueError):
            self.run_file(path)
        path.write_text('["974760673"]')
        with self.assertRaises(ValueError):
            run_evaluation(self.service, path, path, self.root / "report.json")
        self.assertEqual(json.loads(path.read_text()), [NUMBER])
        with self.assertRaises(ValueError):
            read_companies(self.write_input("unknown.xml", "data"))
        self.service.source = DemoSource()
        with self.assertRaisesRegex(ValueError, "live mode"):
            self.run_file(path)

    def test_smoke_requires_100_distinct_valid_numbers(self):
        path = self.write_input("input.txt", (NUMBER + "\n") * 100)
        with self.assertRaisesRegex(ValueError, "100 distinct"):
            self.run_file(path, smoke=True)
        numbers = []
        candidate = 100000000
        while len(numbers) < 100:
            if valid_number(str(candidate)):
                numbers.append(str(candidate))
            candidate += 1
        path.write_text("\n".join(numbers))
        # The smoke path uses actual pipeline calls, not canned evaluator results.
        def fetch(number):
            self.source.request_count += 1
            return {"organisasjonsnummer": number, "navn": "Test fixture"}
        self.source.fetch = fetch
        report = self.run_file(path, smoke=True)
        self.assertEqual(report["request_count"], 100)
        self.assertEqual(report["statuses"], {"ok": 100})
        self.assertEqual(report["failures"], [])

    def test_cli_runs_pipeline_and_writes_failure_report(self):
        path = self.write_input("input.txt", "invalid\n" * 101)
        command = [sys.executable, "-m", "signalpost", "--db", str(self.root / "cli.sqlite3"),
                   "evaluate", "--input", str(path), "--output", str(self.root / "cli.json"),
                   "--report", str(self.root / "cli-report.json")]
        result = subprocess.run(command, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)["output_count"], 101)
        result = subprocess.run(command[:2] + command[2:] + ["--max-seconds", "nan"],
                                capture_output=True, text=True)
        self.assertNotEqual(result.returncode, 0)
        with patch("sys.argv", ["signalpost", "--db", str(self.root / "main.sqlite3"),
                                "smoke", "--input", str(path), "--output", str(self.root / "cli.json"),
                                "--report", str(self.root / "cli-report.json")]):
            from signalpost.__main__ import main
            with self.assertRaises(SystemExit) as error:
                main()
            self.assertEqual(error.exception.code, 2)
