import json
import socket
from datetime import datetime, timezone
from http.client import HTTPException
from urllib.error import HTTPError, URLError
from urllib.request import HTTPRedirectHandler, Request, build_opener


BASE_URL = "https://data.brreg.no/enhetsregisteret/api/enheter/"
FIELDS = {
    "name": "navn",
    "organization_form": "organisasjonsform",
    "business_address": "forretningsadresse",
    "postal_address": "postadresse",
    "industry": "naeringskode1",
    "website": "hjemmeside",
    "employees": "antallAnsatte",
    "founded": "stiftelsesdato",
    "registered": "registreringsdatoEnhetsregisteret",
    "vat_registered": "registrertIMvaregisteret",
    "bankrupt": "konkurs",
    "in_liquidation": "underAvvikling",
    "latest_accounts_year": "sisteInnsendteAarsregnskap",
}


def now():
    return datetime.now(timezone.utc).isoformat()


def valid_number(number):
    if not isinstance(number, str) or len(number) != 9 or not number.isascii() or not number.isdigit():
        return False
    check = (11 - sum(int(n) * w for n, w in zip(number[:8], (3, 2, 7, 6, 5, 4, 3, 2))) % 11) % 11
    return number[0] != "0" and check != 10 and check == int(number[-1])


class LookupProblem(Exception):
    def __init__(self, status, message):
        super().__init__(message)
        self.status = status


class NoRedirects(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class RegistrySource:
    name = "Brønnøysund Register Centre — Enhetsregisteret"

    def fetch(self, number):
        request = Request(BASE_URL + number, headers={
            "Accept": "application/json",
            "User-Agent": "Signalpost-MVP/1.0 (public company registry lookup)",
        })
        try:
            with build_opener(NoRedirects).open(request, timeout=10) as response:
                raw = response.read(2_000_001)
            if len(raw) > 2_000_000:
                raise LookupProblem("failed", "Registry response exceeded the size limit.")
            return json.loads(raw)
        except HTTPError as exc:
            if exc.code == 404:
                raise LookupProblem("missing", "No company found for this registration number.") from exc
            if exc.code in (401, 403, 429, 451):
                raise LookupProblem("blocked", "Registry access was denied or rate-limited; try again later.") from exc
            raise LookupProblem("failed", f"Registry returned HTTP {exc.code}.") from exc
        except (HTTPException, URLError, TimeoutError, socket.timeout, OSError, ValueError) as exc:
            raise LookupProblem("failed", "Registry could not be reached or returned invalid JSON.") from exc


class DemoSource:
    name = "OFFLINE DEMO — synthetic fixture, not live registry data"

    def fetch(self, number):
        if number != "974760673":
            raise LookupProblem("missing", "Offline demo only contains 974760673.")
        return {
            "organisasjonsnummer": number,
            "navn": "DEMO COMPANY — synthetic profile",
            "organisasjonsform": {"kode": "ORGL", "beskrivelse": "Demo organization"},
            "forretningsadresse": {"land": "Norge", "poststed": "OSLO", "postnummer": "0150"},
            "antallAnsatte": 42,
            "registrertIMvaregisteret": False,
        }


def extract(number, payload, source_name, retrieved_at):
    if not isinstance(payload, dict):
        raise LookupProblem("failed", "Registry response was not a company object.")
    if payload.get("organisasjonsnummer") != number:
        raise LookupProblem("ambiguous", "Returned identity does not match the requested registration number. No facts accepted.")
    if not isinstance(payload.get("navn"), str) or not payload["navn"].strip():
        raise LookupProblem("missing", "The matched registry record has no company name.")
    facts = {}
    for field, key in {"registration_number": "organisasjonsnummer", **FIELDS}.items():
        value = payload.get(key)
        if value is None or value == "" or value == [] or value == {}:
            continue
        facts[field] = {
            "value": value.strip() if isinstance(value, str) else value,
            "evidence": {
                "source": source_name,
                "url": BASE_URL + number,
                "retrieved_at": retrieved_at,
                "source_field": key,
                "snippet": json.dumps({key: value}, ensure_ascii=False, sort_keys=True),
            },
        }
    return facts
