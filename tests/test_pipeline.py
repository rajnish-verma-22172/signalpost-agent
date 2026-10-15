import copy
import json
import tempfile
import unittest
from http.client import IncompleteRead
from pathlib import Path
from unittest.mock import patch
from urllib.error import HTTPError, URLError

from signalpost.service import Service
from signalpost.source import BASE_URL, DemoSource, LookupProblem, RegistrySource, valid_number
from signalpost.store import Store


NUMBER = "974760673"
RECORD = {
    "organisasjonsnummer": NUMBER,
    "navn": "Test Company",
    "antallAnsatte": 0,
    "registrertIMvaregisteret": False,
    "forretningsadresse": {"land": "Norge", "poststed": "OSLO"},
}


class FakeSource:
    name = "Test registry fixture"

    def __init__(self):
        self.payload = copy.deepcopy(RECORD)
        self.error = None
        self.calls = 0

    def fetch(self, number):
        self.calls += 1
        if self.error:
            raise self.error
        return copy.deepcopy(self.payload)


class PipelineTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store = Store(Path(self.temp.name) / "profiles.sqlite3")
        self.source = FakeSource()
        self.service = Service(self.store, self.source)

    def test_success_attaches_exact_evidence_to_every_fact(self):
        result = self.service.lookup(NUMBER)
        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["facts"]["employees"]["value"], 0)
        self.assertIs(result["facts"]["vat_registered"]["value"], False)
        for fact in result["facts"].values():
            evidence = fact["evidence"]
            self.assertEqual(evidence["url"], BASE_URL + NUMBER)
            self.assertEqual(evidence["retrieved_at"], result["checked_at"])
            self.assertEqual(json.loads(evidence["snippet"])[evidence["source_field"]], fact["value"])
        self.assertNotIn("website", result["facts"])

    def test_wrong_identity_is_ambiguous_and_never_stored_as_facts(self):
        self.source.payload["organisasjonsnummer"] = "923609016"
        result = self.service.lookup(NUMBER)
        self.assertEqual(result["status"], "ambiguous")
        self.assertEqual(result["facts"], {})

    def test_missing_and_invalid_payloads(self):
        for payload, expected in [(None, "failed"), ([], "failed"),
                                  ({"organisasjonsnummer": NUMBER}, "missing")]:
            with self.subTest(payload=payload):
                self.source.payload = payload
                result = self.service.lookup(NUMBER, refresh=True)
                self.assertEqual(result["status"], expected)
                self.assertFalse(result["facts"])

    def test_missing_blocked_and_failed_lookups_are_recorded(self):
        for status in ("missing", "blocked", "failed"):
            with self.subTest(status=status):
                self.source.error = LookupProblem(status, "Source problem")
                result = self.service.lookup(NUMBER, refresh=True)
                self.assertEqual(result["status"], status)
                self.assertEqual(result["history"][0]["status"], status)
                self.assertEqual(result["facts"], {})

    def test_checksum_validation_never_fetches_invalid_numbers(self):
        for number in ("123", "974760674", "000000000", "９７４７６０６７３", None, 974760673):
            with self.subTest(number=number):
                self.assertFalse(valid_number(number))
                self.assertEqual(self.service.lookup(number)["status"], "invalid")
        self.assertEqual(self.source.calls, 0)
        self.assertTrue(valid_number(NUMBER))

    def test_cached_lookup_and_unchanged_refresh_do_not_duplicate_facts(self):
        initial = self.service.lookup(NUMBER)
        cached = self.service.lookup(NUMBER)
        self.assertTrue(cached["cached"])
        self.assertEqual(self.source.calls, 1)
        with patch("signalpost.service.now", return_value="2026-10-16T10:00:00+00:00"):
            refreshed = self.service.lookup(NUMBER, refresh=True)
        self.assertFalse(refreshed["cached"])
        self.assertEqual(refreshed["history"][0]["changes"], [])
        self.assertEqual(len(refreshed["history"]), 2)
        self.assertEqual(refreshed["facts"]["name"]["first_seen"], initial["checked_at"])
        self.assertEqual(refreshed["facts"]["name"]["last_seen"], refreshed["checked_at"])
        with self.store.connect() as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM facts").fetchone()[0], len(initial["facts"]))

    def test_changed_removed_and_returning_fields_keep_history(self):
        self.service.lookup(NUMBER)
        self.source.payload["navn"] = "Renamed"
        del self.source.payload["antallAnsatte"]
        result = self.service.lookup(NUMBER, refresh=True)
        changes = {c["field"]: c for c in result["history"][0]["changes"]}
        self.assertEqual(changes["name"]["before"], "Test Company")
        self.assertEqual(changes["name"]["after"], "Renamed")
        self.assertEqual(changes["employees"]["kind"], "removed")
        self.assertNotIn("employees", result["facts"])
        self.source.payload["antallAnsatte"] = 0
        result = self.service.lookup(NUMBER, refresh=True)
        self.assertEqual(result["history"][0]["changes"][0]["kind"], "added")
        with self.store.connect() as db:
            names = db.execute("SELECT value, active FROM facts WHERE field = 'name' ORDER BY id").fetchall()
        self.assertEqual([(json.loads(row[0]), row[1]) for row in names],
                         [("Test Company", 0), ("Renamed", 1)])

    def test_failed_refresh_preserves_last_known_facts_then_recovers(self):
        initial = self.service.lookup(NUMBER)
        self.source.error = LookupProblem("blocked", "Try later")
        failed = self.service.lookup(NUMBER, refresh=True)
        self.assertTrue(failed["stale"])
        self.assertEqual(failed["facts"], initial["facts"])
        self.assertEqual(failed["history"][0]["changes"], [])
        self.source.error = None
        recovered = self.service.lookup(NUMBER, refresh=True)
        self.assertFalse(recovered["stale"])
        self.assertEqual(recovered["status"], "ok")

    def test_batch_isolates_failures_and_deduplicates_numbers(self):
        results = self.service.batch([NUMBER, "invalid", f" {NUMBER} "])
        self.assertEqual([r["status"] for r in results], ["ok", "invalid"])
        self.assertEqual(self.source.calls, 1)
        for numbers in ([], [NUMBER] * 101, "not a list", [123], ["x" * 101]):
            with self.subTest(numbers=numbers), self.assertRaises(ValueError):
                self.service.batch(numbers)

    def test_truncated_response_is_recorded_and_does_not_abort_batch(self):
        service = Service(self.store, RegistrySource())
        with patch("signalpost.source.build_opener") as opener:
            opener.return_value.open.side_effect = [
                IncompleteRead(b"partial"),
                HTTPError(BASE_URL + "923609016", 404, "not found", {}, None),
            ]
            results = service.batch([NUMBER, "923609016"])
        self.assertEqual([result["status"] for result in results], ["failed", "missing"])
        self.assertEqual(len(self.store.list()), 2)
        self.assertEqual(results[0]["history"][0]["status"], "failed")

    def test_store_persists_across_service_instances(self):
        self.service.lookup(NUMBER)
        other = Service(Store(self.store.path), FakeSource())
        result = other.lookup(NUMBER)
        self.assertTrue(result["cached"])
        self.assertEqual(other.source.calls, 0)
        self.assertEqual(len(other.store.list()), 1)

    def test_demo_is_labeled_and_has_only_one_fixture(self):
        source = DemoSource()
        self.assertIn("DEMO", source.name)
        self.assertEqual(source.fetch(NUMBER)["organisasjonsnummer"], NUMBER)
        with self.assertRaises(LookupProblem):
            source.fetch("923609016")


class RegistryTransportTests(unittest.TestCase):
    def test_http_error_mapping(self):
        for code, status in [(404, "missing"), (403, "blocked"), (429, "blocked"),
                             (451, "blocked"), (500, "failed"), (302, "failed")]:
            with self.subTest(code=code), patch("signalpost.source.build_opener") as opener:
                opener.return_value.open.side_effect = HTTPError(BASE_URL, code, "test", {}, None)
                with self.assertRaises(LookupProblem) as raised:
                    RegistrySource().fetch(NUMBER)
                self.assertEqual(raised.exception.status, status)

    def test_network_and_malformed_responses(self):
        for error in (URLError("offline"), TimeoutError(), IncompleteRead(b"partial")):
            with self.subTest(error=error), patch("signalpost.source.build_opener") as opener:
                opener.return_value.open.side_effect = error
                with self.assertRaises(LookupProblem) as raised:
                    RegistrySource().fetch(NUMBER)
                self.assertEqual(raised.exception.status, "failed")
        for raw in (b"not json", b"x" * 2_000_001):
            with self.subTest(size=len(raw)), patch("signalpost.source.build_opener") as opener:
                opener.return_value.open.return_value.__enter__.return_value.read.return_value = raw
                with self.assertRaises(LookupProblem):
                    RegistrySource().fetch(NUMBER)

    def test_success_uses_fixed_endpoint_timeout_and_bounded_response(self):
        with patch("signalpost.source.build_opener") as opener:
            response = opener.return_value.open.return_value.__enter__.return_value
            response.read.return_value = json.dumps(RECORD).encode()
            self.assertEqual(RegistrySource().fetch(NUMBER), RECORD)
            request = opener.return_value.open.call_args.args[0]
            self.assertEqual(request.full_url, BASE_URL + NUMBER)
            self.assertEqual(opener.return_value.open.call_args.kwargs["timeout"], 10)
            response.read.assert_called_once_with(2_000_001)
