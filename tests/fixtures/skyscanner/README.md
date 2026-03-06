# Skyscanner Fixtures

Use `python -m utils.capture_fixture` to create sanitized Skyscanner fixtures:

```bash
python -m utils.capture_fixture --site skyscanner --run-id <run_id>
```

Keep fixtures HTML-only and sanitized. Do not store cookies, tokens, emails, or session identifiers.

Suggested samples:

- results page (`flights_results_sample_XX`)
- non-flight/consent/irrelevant page (`non_flight_scope_sample_XX`)
