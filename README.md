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
   Refreshing saved profiles automatically uses batches of at most 100.
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
contains 1–10,000 string inputs; each input gets an ordered result. Duplicates are
trimmed and fetched once per batch, even with `--refresh`. Invalid
numbers do not trigger requests and do not get persisted. Lookup failures are
per-company results, not batch-wide errors; the CLI exits normally so the entire
result set remains usable. Invalid batch arguments produce a nonzero exit.

Normal lookup reuses saved results, **including saved failures**. Use `--refresh`
or the UI refresh buttons to retry. There is no automatic expiry or background
refresh. Each fresh valid lookup makes one outbound request with a 10-second
timeout, a 2 MB response limit, and no redirects or automatic retries. Batches
run sequentially to limit source load. `lookup --max-seconds 300` (the default)
stops starting new lookups after its time budget; unattempted rows get explicit
`failed` results, not invented facts. An in-flight lookup is allowed to finish.
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

The server is **loopback-first**. Local use needs no authentication. Hosted use
requires explicit Host/origin allowlists and an authentication secret; see the
deployment steps below. It limits request bodies, batch sizes and simultaneous
connections, escapes source content via DOM text nodes, and restricts navigable
evidence links to the official source. TLS must terminate at a reverse proxy.
There is no background job queue or multi-user account system.

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
For dashboard batching regression tests and a JavaScript syntax check, use
**Node.js 18+** (optional for running the app; both checks run in CI):

```sh
node --check signalpost/static/app.js
node --test tests/dashboard.test.cjs
```

## Hackathon submission readiness

This section maps the requirements in the supplied Signalpost challenge listing
to what this repository can demonstrate. A working local MVP is not, by itself,
proof that an entry meets the evaluator's coverage, accuracy, or execution
thresholds.

### Run command and declared costs

From the repository root, the one-command local dashboard launch is:

```sh
python -m signalpost serve
```

It requires Python 3.11+ and network access to `data.brreg.no` for live lookup.
For file evaluation, use the command below. The CLI supports up to 10,000 rows;
the interactive HTTP API remains limited to 100 numbers by default. Both return
`{"results":[...]}`. The organizer's downloadable contract was not available in
this environment: confirm its exact schema and required fields before claiming
official compatibility. This adapter does not invent unsupported company facts.

No model or LLM is used. Each fresh, valid company lookup makes one request to
the public Brønnøysund Enhetsregisteret API; cached lookups make no request.
There are no model/API provider charges in this implementation. Requests run
sequentially, with a 10-second timeout and no automatic retries.

**Measured cost (fresh live run of 100 companies, `artifacts/smoke-report.json`):**
100 outbound requests and about 107 seconds, with 100/100 lookups `ok`. That is
5% of the daily 2,000-request limit and about 4% of the 45-minute limit.
Declared external API cost: **$0.00 per day** (public registry, no paid APIs,
no model calls). Compute: any machine with Python 3.11+; no GPU or hosted
service is required.

### Submission dataset and remaining limitations

- **1,000 company profiles (done):** `artifacts/profiles-1000.json` contains
  1,000 live, evidence-backed profiles of active Norwegian companies, all with
  status `ok`, each fact carrying its source URL, source field, snippet and
  retrieval time. Inputs are in `artifacts/companies-1000.txt`; counts, field
  coverage and the output SHA-256 are in `artifacts/profiles-1000-report.json`
  and `artifacts/SUBMISSION_SUMMARY.md`. Rebuild with
  `python scripts/build_profiles.py --smoke` (fixed seed; needs access to
  `data.brreg.no`). The export reuses lookups made minutes earlier in the same
  run, so profiles are marked `cached`; their `retrieved_at` times show the live
  fetch. Companies whose lookup is not `ok` are skipped, never filled in.
- **Field availability:** some fields are only present when the registry
  publishes them (for example, employee count in 350 of 1,000 profiles and
  website in 179). Missing fields are left empty rather than inferred.
- **Evaluator compatibility:** the organizer's exact input/output schema and
  permitted-source guide are not checked into this repository. Outputs are
  `{"results":[...]}` profiles or claim/evidence envelopes
  (`--output-format envelopes`); confirm the current contract with the organizer.
- **Fact coverage:** the live adapter uses only the public Brønnøysund
  registry. It does not provide leadership, financial figures, jobs, news or
  multi-source research. These facts are never inferred or fabricated.
- **Submission package:** repository link, the exact commit from
  `git rev-parse HEAD`, run command, model/API details and the measured cost
  above.
- **Live verification:** tests and demo work offline, but live registry access
  must be checked from a network-enabled environment. Automated tests do not
  demonstrate live coverage or evaluator performance.
- **Deployment:** the configuration below is provided, but no hosting account
  has been provisioned and no public live URL is claimed.

### Evaluator: exact command

Save the organizer's supplied file locally, then run from the repository root:

```sh
python -m signalpost --db data/evaluation.sqlite3 evaluate \
  --input /absolute/path/to/companies.csv \
  --output artifacts/results.jsonl --output-format envelopes \
  --report artifacts/report.json \
  --max-seconds 1800
```

No installation or API key is needed. Supported UTF-8 input formats:

- `.csv`: header plus one company per row.
- `.json`: an array, or `{"organisation_numbers":[...]}`,
  `{"companies":[...]}` / `{"numbers":[...]}`.
- `.jsonl`: one JSON company object or registration-number string per line.
- `.txt`: one registration number per line (internal blank rows are invalid).

Rows can be registration-number strings, integers, or objects with
`organisation_number`, `registration_number`, `organization_number`,
`organisasjonsnummer`, or `orgnr`.
For another column/key, append `--number-field YOUR_COLUMN`. Names alone cannot
be resolved by this registry-number pipeline and return `invalid`. A structurally
invalid file, empty file, more than 10,000 rows, or a file over 20 MB is rejected
with exit code 2. All parsed rows, including malformed company objects and
duplicates, retain their position via zero-based `input_index` and original
`input` in the output. Company failures do not abort the batch.

Each result contains the existing profile, evidence, history, `cached` and
`stale` flags. States remain `ok`, `invalid`, `missing`, `ambiguous`, `blocked`,
or `failed`. Check status before using facts: failed refreshes can contain
explicitly stale last-known facts. Fresh source requests are the default;
`--use-cache` opts into stored results (including failures). Evaluation exits 0
when artifacts are written, **not** when all companies succeed; inspect the report.
Output/report/database/input paths must differ. Keep artifacts outside source
control; they include the original input and any returned public company data.

`--output-format profiles` (the default) produces the dashboard-compatible JSON
wrapper. `--output-format envelopes` produces one JSONL terminal envelope per
input with `organisation_number`, run timestamps/ID, claims, evidence, changes,
errors and the original `profile`. Claims distinguish `available`,
`not_available`, `blocked`, `ambiguous`, and `failed`; absent values remain null,
never invented zeroes. Unsuccessful refreshes do not promote stale profile facts
to available claims. Evidence includes the retrieved field snippet and its
SHA-256 (a **field-level snapshot**, not the full HTTP response). Each envelope's
`terminal_status: completed` means processing ended, not that lookup succeeded;
inspect `errors`, availability and `profile.status`. Operations are measured at
batch level in the report, not estimated per duplicate row.

This envelope option follows the general structure in a
[historical public mirror](https://github.com/biswajeetdev/signalpost-agent/blob/8268bf0e900f4363fd04e25409f974a6e5e96025/starter-kit/signalpost-starter-kit/OUTPUT_CONTRACT.md).
The mirror's prose and executable starter disagree, and it is not the current
authenticated contract. Field names, confidence/financial-period requirements,
and final harness compatibility still need confirmation against the organizer's
actual files. No evaluator scores or unsupported source coverage are claimed.

Budgets accept 0–86,400 seconds (exclusive of zero). At most one source request
runs at once per process. Budget checks happen between lookups, not by forcibly
interrupting a request or SQLite transaction; allow time for an in-flight request,
database writes and artifact serialization. Registry socket timeout is 10 seconds;
it is not an end-to-end network deadline. Use the host's process/job timeout for
a strict wall-clock termination (a forced termination cannot guarantee artifacts).
Larger files can be split into bounded runs; do not remove failed rows from a report.

### Real 100-company smoke verification

Obtain **100 distinct real, checksum-valid organization numbers** from the
organizer's permitted input. Do not generate fake numbers or use `--demo`.
The smoke command validates the count and runs the live pipeline, always fresh:

```sh
python -m signalpost --db data/smoke.sqlite3 smoke \
  --input /absolute/path/to/100-companies.csv \
  --output artifacts/smoke-results.json --report artifacts/smoke-report.json \
  --max-seconds 1800
```

Use `--number-field` if needed. Preserve the input, both artifacts, command and
commit hash. The report records input/output SHA-256, row counts, runtime,
actual outbound request attempts (including errors), status counts, every
non-`ok` row and field coverage. A timed-out budget still writes all 100 results
with explicit failures. Smoke exits **1** if any company is non-`ok`, **2** for
invalid arguments/input, **0** only for 100 successful results. Request count
can be below 100 when the budget expires; never present that as a passing run.
Offline tests exercise this flow with fixtures; **they are not a live report**.
No genuine 100-company report is bundled: organizer input and live network access
are required. Review coverage/accuracy against the actual contract separately.

### Deploy safely on Render (persistent service)

You must do these account actions; this repository cannot create a live URL:

1. Merge this change, sign in to Render, create a **Web Service**, and authorize
   access to this GitHub repository. Select the **Python** runtime, Python 3.11+
   (for example 3.12), build command `python --version`, start command
   `python -m signalpost serve`. No pip packages are required.
2. Choose an instance that supports a persistent disk (not an ephemeral/free
   filesystem). Add a disk mounted at `/var/lib/signalpost`. Use **one instance**
   and one process; SQLite is not a shared multi-instance database.
3. Set these environment variables in the provider's settings, replacing the
   hostname with the exact assigned service hostname:

   ```text
   SIGNALPOST_HOST=0.0.0.0
   SIGNALPOST_DB=/var/lib/signalpost/signalpost.sqlite3
   SIGNALPOST_ALLOWED_HOSTS=your-service.onrender.com
   SIGNALPOST_ALLOWED_ORIGINS=https://your-service.onrender.com
   SIGNALPOST_MAX_BATCH=100
   SIGNALPOST_MAX_CONCURRENT=2
   ```

4. Generate a secret locally with `python -c "import secrets; print(secrets.token_urlsafe(32))"`.
   Save it as secret environment variable **`SIGNALPOST_AUTH_TOKEN`** in Render.
   Do not put it in Git, URLs, screenshots, or submission artifacts. Configure
   the hostname and secret before starting a non-loopback service; missing
   security configuration deliberately prevents startup.
5. Render terminates HTTPS. Keep its backend port private and preserve the
   public Host header. Set the health-check path to `/healthz`. This exact GET
   path returns only `{"status":"ok"}` without authentication; Host/origin
   checks still apply. If the provider uses a different probe Host, add that
   exact value to the Host allowlist, never a wildcard. Protected dashboard/API
   paths require credentials and must not be used as unauthenticated probes.
   Set proxy request timeout above the batch budget plus overhead, or use much
   smaller interactive batches. Run long evaluator jobs via CLI, not the browser.
6. Open `https://your-service.onrender.com`. At the browser's Basic Auth prompt,
   use username **`signalpost`** and your secret as password. Check lookup →
   Evidence → Refresh → Export. Restart the service and confirm saved profiles
   survive. Only then share this **actual provider-assigned URL** with judges,
   supplying credentials privately. Use HTTPS only; Basic Auth is not encryption.

Allowed hosts are exact `Host` values, including a port when nonstandard.
Origins are exact `https://hostname[:port]` values, without paths/trailing slashes.
Comma-separate multiple values for a custom domain and list both its host and
origin. No wildcard allowlists or trusted forwarded-header bypasses are provided.
All dashboard/API resources require authentication when configured; only the
minimal GET `/healthz` liveness response is public. Credentials
are not embedded in JavaScript or localStorage. Rotate the shared secret to
revoke access; browsers may retain Basic Auth until closed. Add proxy rate limits
and IP restrictions where possible: bounded concurrency is not a distributed
rate limiter. Hosted HTTP batch cap is configurable only downward from 100.

For another container host, `docker build -t signalpost .` uses the included
non-root Dockerfile. Mount a persistent volume at `/var/lib/signalpost` writable
by UID/GID **10001**, pass the same environment variables, set `PORT` if needed,
and put the container behind an HTTPS proxy. Never expose backend HTTP publicly.
Back up SQLite with the process stopped before upgrades; do not run evaluation
and the hosted dashboard against the same database concurrently.

### Final submission checklist

1. Confirm the organizer's **actual** input/output contract, permitted sources,
   deadline, profile-count requirement, repository access and submission destination.
2. Run offline tests above, the real smoke command, and the evaluator against the
   supplied input. Inspect failures and unsupported fields; attach honest reports.
3. Record `git rev-parse HEAD`, repository link, exact evaluator command, Python
   version, input/output/report artifacts, and API/model/license/cost declarations.
   This code is MIT-licensed; it uses the public registry, no models and no paid
   API. Check registry data licensing/usage terms separately. Actual hosting cost
   is your selected provider plan, not a measured cost claimed by this project.
4. Submit through the organizer's confirmed channel. Include a live URL only if
   you have deployed and tested it; deployment and submission are external actions,
   not outcomes of automated offline tests.
