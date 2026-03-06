# Test Fixtures

Deterministic HTML fixtures used by parser/extraction tests live under:

- `tests/fixtures/google_flights/`
- `tests/fixtures/skyscanner/`

## Capture Tool

Use the local fixture capture tool after a real run:

```bash
python -m utils.capture_fixture --site skyscanner --run-id <run_id>
python -m utils.capture_fixture --site google_flights --run-id <run_id>
python -m utils.capture_fixture --site all --source auto
```

## Sanitization Rules (shared)

The capture tool sanitizes HTML before writing fixtures:

- Removes `<script>` blocks
- Removes inline event handlers (`onclick`, `onload`, etc.)
- Redacts token/cookie/auth/session-like values
- Redacts email addresses
- Redacts long token-like strings and long numeric IDs
- Removes tracking query params (`utm_*`, `gclid`, `fbclid`)
- Redacts large `data:` base64 blobs
- Collapses repeated whitespace
- Enforces a size budget (default `250000` bytes)

## Safety Guidelines

- Never commit raw run HTML directly
- Always use `python -m utils.capture_fixture` (or equivalent sanitization)
- Inspect `.meta.json` output and sample HTML before committing
- Keep fixtures minimal and representative (results/non-flight/edge cases)

## Naming Conventions

Preferred fixture stems:

- `flights_results_sample_XX`
- `non_flight_scope_sample_XX`
- `page_sample_XX`

The capture tool auto-selects a stem and increments suffixes when needed.
