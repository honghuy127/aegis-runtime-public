# Google Flights Fixtures

Use `python -m utils.capture_fixture` to create sanitized Google Flights fixtures:

```bash
python -m utils.capture_fixture --site google_flights --run-id <run_id>
```

Keep fixtures small and deterministic. Prefer sanitized result pages or explicit non-flight pages for parser tests.

Do not commit raw pages containing:

- auth/session tokens
- cookies
- email addresses
- large inline blobs (`data:` base64)
