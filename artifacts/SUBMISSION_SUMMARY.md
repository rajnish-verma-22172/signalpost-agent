# Signalpost submission summary

- Generated: 2026-10-15T18:55:31.704075+00:00
- Code commit used for this run: `0b5e9f5d49dabff76f2df8b1950a87eec8740809` (record the final submitted hash with `git rev-parse HEAD`)
- Python: 3.14.4 (Darwin)
- Source: Brønnøysund Register Centre — Enhetsregisteret (public API, no key, no LLM, no paid API)

## Company profiles

- Profiles delivered: **1000** (target 1000), file `artifacts/profiles-1000.json`
- Output SHA-256: `91825c4331b13da8d51bb8fbcaf27bafd57212899b9bd6e4d39dacd36b25464b`
- Candidates: 1300 active companies sampled from the public Enhetsregisteret search (seed 2026; filters: not bankrupt, not in liquidation, registered in Foretaksregisteret, >=1 employee)
- Lookups skipped (not used, never fabricated): none

### Field coverage across delivered profiles

| Field | Profiles with value |
|---|---|
| bankrupt | 1000 |
| business_address | 1000 |
| in_liquidation | 1000 |
| industry | 1000 |
| name | 1000 |
| organization_form | 1000 |
| registered | 1000 |
| registration_number | 1000 |
| vat_registered | 1000 |
| founded | 950 |
| latest_accounts_year | 944 |
| employees | 350 |
| postal_address | 250 |
| website | 179 |

## Measured run cost

- Dataset build: 13 search requests + 1000 lookup requests in 1107.2 s
- Daily 100-company test (fresh smoke run): **100 outbound requests**, **107 s**, statuses {'ok': 100}
- Limits: 45 minutes, 2,000 outbound requests, $10 declared API cost per day
- External API cost: **$0.00** (public registry, no paid APIs, no model calls)
- Compute: runs on any machine with Python 3.11+; no GPU, no hosted service required

## Run commands

```
python -m signalpost serve                                  # dashboard
python -m signalpost evaluate --input <file> --output <out.json> --report <report.json>
python scripts/build_profiles.py --smoke                    # rebuild this dataset
```
