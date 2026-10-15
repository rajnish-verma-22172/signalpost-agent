# Signalpost

A local-first Norwegian company research MVP: registration numbers in, structured
company profiles with field-level evidence out. Includes a dashboard, batch CLI,
SQLite persistence, and auditable refresh history. No API key, paid service, LLM,
Python packages, or frontend build step is required.

## Run locally

Requires **Python 3.11+** with SQLite (included in standard Python installations).
From the repository root:

```sh
python -m signalpost serve
```

Open **http://127.0.0.1:8000**. Enter `974760673` (Brønnøysundregistrene), or multiple
organization numbers separated by commas, spaces, semicolons, or new lines. Live
lookups require HTTPS access to `data.brreg.no`. On Windows, use `py` instead of
`python` if necessary. Stop the server with Ctrl+C.

For an offline walkthrough:

```sh
python -m signalpost --demo serve
```

Enter `974760673`. Demo mode uses **synthetic, clearly labeled fixture data**, not
actual company information; other valid numbers return `missing`. Its default
database is separate from live data. The evidence URL in demo mode identifies the
corresponding live endpoint, **not a source that substantiates the synthetic facts**.
Do not use demo output for an evaluation or submission dataset.

### Dashboard walkthrough

1. Submit one or more numbers. Each unique trimmed input gets a result.
2. Expand **Evidence** below any field to see its source link, retrieval time,
   exact JSON snippet, and first/last confirmation times.
3. Click **Refresh this company** or **Refresh saved profiles** to query again.
   Expand **Refresh history** for added, updated, and removed fields.
4. An unsuccessful refresh retains last-known facts with a stale-data warning.
5. **Export JSON** downloads the currently displayed results and their evidence.
   Saved profiles reload automatically when the application restarts.

### Batch CLI

```sh
python -m signalpost lookup 974760673 923609016
python -m signalpost lookup --refresh 974760673
python -m signalpost --demo lookup 974760673
python -m signalpost --db /tmp/signalpost-review.sqlite3 lookup 974760673
```

Output is JSON on stdout, suitable for redirecting to an output file. A batch
contains 1–100 string inputs; duplicates are trimmed and processed once. Invalid
numbers do not trigger requests and do not get persisted. Lookup failures are
per-company results, not batch-wide errors; the CLI exits normally so the entire
result set remains usable. Invalid batch arguments produce a nonzero exit.

Normal lookup reuses saved results, **including saved failures**. Use `--refresh`
or the UI refresh buttons to retry. There is no automatic expiry or background
refresh. Each fresh valid lookup makes one outbound request with a 10-second
timeout, a 2 MB response limit, and no redirects or automatic retries. Batches
run sequentially to limit source load; 100 timeouts can take about 17 minutes.
There are no model/API charges; network or hosting costs are environment-specific.

### HTTP API

```sh
curl -s http://127.0.0.1:8000/api/lookup \
  -H 'Content-Type: application/json' \
  -d '{"numbers":["974760673","invalid"]}'

curl -s http://127.0.0.1:8000/api/refresh \
  -H 'Content-Type: application/json' \
  -d '{"numbers":["974760673"]}'

curl -s http://127.0.0.1:8000/api/companies
curl -s http://127.0.0.1:8000/api/companies/974760673
curl -s http://127.0.0.1:8000/api/config
```

Lookup/refresh return `{"results":[...]}`. List returns `{"companies":[...]}`.
Malformed requests return an HTTP error with `{"error":"..."}`. Valid requests
return HTTP 200 even when individual lookups fail.

Illustrative live-response excerpt (timestamps and company data vary; other
facts and history omitted here):

```json
{
  "results": [{
    "registration_number": "974760673",
    "status": "ok",
    "checked_at": "2026-10-15T12:00:00+00:00",
    "cached": false,
    "stale": false,
    "facts": {
      "registration_number": {
        "value": "974760673",
        "evidence": {
          "source": "Brønnøysund Register Centre — Enhetsregisteret",
          "url": "https://data.brreg.no/enhetsregisteret/api/enheter/974760673",
          "retrieved_at": "2026-10-15T12:00:00+00:00",
          "source_field": "organisasjonsnummer",
          "snippet": "{\"organisasjonsnummer\": \"974760673\"}"
        },
        "first_seen": "2026-10-15T12:00:00+00:00",
        "last_seen": "2026-10-15T12:00:00+00:00"
      }
    }
  }]
}
```

| Status | Meaning |
| --- | --- |
| `ok` | Exact identity and company name confirmed; optional fields may be absent |
| `invalid` | Not a nine-digit Norwegian organization number with valid modulus-11 checksum |
| `missing` | HTTP 404, or matched record lacks a usable name |
| `ambiguous` | Source identity differs from the requested number; no new facts accepted |
| `blocked` | HTTP 401, 403, 429, or 451; no access-control bypass attempted |
| `failed` | Network/timeout, malformed response, unexpected HTTP status, or rejected redirect |

## Coverage and source policy

The single live source is the **official, public Brønnøysund Enhetsregisteret API**:
[documentation](https://data.brreg.no/enhetsregisteret/api/docs/index.html),
[registry data information](https://www.brreg.no/bruke-data-fra-bronnoysundregistrene/).
Public registry data is provided under the Norwegian Licence for Open Government
Data (NLOD); retain attribution when reusing/exporting data and check current
provider terms. The project's MIT license applies to code, not upstream data.

Supported fields, when supplied: registration number, name, organization form,
business/postal addresses, primary industry code/description, registered website,
employee count, founding/registry dates, VAT registration, bankruptcy,
liquidation, and latest filed accounts year. Address and classification objects
retain the registry's Norwegian keys. A registered website is a registry claim;
the app does not visit or independently verify it. A latest-accounts year is
**not** a financial statement. Zero employees and false boolean values are facts,
not missing data. Missing fields are omitted, explicitly listed in the UI, and
never guessed. Company number and exact identity are validated before accepting
facts; there is no fuzzy name search.

No search-engine scraping, paywall/login access, robots bypass, third-party
enrichment, personal contact collection, or LLM-generated facts are used.
An `ambiguous` result is a defensive identity-mismatch safeguard, not a claim
that an organization number normally identifies multiple companies.

## Architecture and persistence

```text
Dashboard or CLI
    → validation + batch service
    → fixed official HTTPS endpoint (or labeled offline fixture)
    → exact identity check + deterministic extraction
    → SQLite transaction: fact versions + lookup run + field-level diff
    → JSON profiles / dashboard evidence and history
```

- Source adapter handles transport errors and maps registry keys to stable fields.
- Service validates inputs, isolates source failures, and serializes concurrent
  lookups within the server process.
- SQLite stores structured JSON values and evidence on every fact version.
  Unchanged values update their last-seen time and latest evidence, without
  adding another fact version. Changed values retire the old version; removed
  fields are retired only after a successful response. Each fresh attempt adds
  one run, including unsuccessful and unchanged attempts.
- Failed, blocked, ambiguous, and missing refreshes never erase last-known facts.
  Historical retired versions retain their evidence in SQLite; the API exposes
  current facts and a before/after change log, not a full historical-evidence API.
- Live and demo defaults are `data/signalpost.sqlite3` and `data/demo.sqlite3`.
  They are ignored by Git. `--db` overrides the path; do not point live and demo
  modes at the same file. Back up the database while the server is stopped.
  Delete it to reset local state.

The server is intended for **trusted local use**, bound to loopback by default.
It rejects cross-origin writes and untrusted Host headers, limits JSON request
size, escapes source content via DOM text nodes, and restricts navigable evidence
links to the official source. It has no authentication, TLS, multi-worker job
queue, cancellation, or production resource controls. Do not expose it directly
to the Internet. A hosted deployment needs an authenticated TLS reverse proxy,
compatible Host/origin configuration, persistent storage, and workload limits.

## Tests and review

From the repository root:

```sh
python -m unittest discover -s tests -v
```

Tests use temporary databases, deterministic fixtures, mocked registry transport,
and a real loopback HTTP server; no Internet or keys are required. They cover
successful lookup, evidence, identity mismatch, missing fields/records, blocked
and failed transport, checksums, batch isolation, cache behavior, unchanged
refresh deduplication, added/changed/removed fields, stale-data retention,
persistence, HTTP validation, and dashboard delivery. CI runs the same command.
For an optional JavaScript syntax check, if Node is installed:

```sh
node --check signalpost/static/app.js
```

## Submission checklist and honest limits

- Record the reviewed commit with `git rev-parse HEAD`.
- Run the automated tests, then demonstrate lookup → evidence → unchanged
  refresh → JSON export using the dashboard.
- Run a live batch against the organizer's actual evaluation input, keep its JSON
  output, and record wall time, request count, statuses, and field coverage.
  No 100-company benchmark or live coverage score is claimed here.
- Verify the organizer's current permitted-source guide, evaluation schema,
  deadline, and submission channel. This repo's JSON API is an MVP interface,
  **not a verified implementation of an unavailable evaluator contract**.
- Registry-only coverage does not include leadership, financial figures, jobs,
  news, multi-source synthesis, or an evaluator-specific adapter. Those are
  extensions, not fabricated MVP data.
- Automated tests and offline demo work without network access. The development
  sandbox could not resolve `data.brreg.no`, so live retrieval must be verified
  on a network-enabled machine before submission.
- No public deployment has been provisioned and no entry has been submitted.
  This repository is the runnable review deliverable, not a submission receipt.
