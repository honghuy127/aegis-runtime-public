# Configuration Guide

This project keeps runtime configuration in `configs/`. Use this page as a quick "what to touch when" map.

## File-by-file

### `configs/run.yaml`
Default runtime inputs and behavior when CLI flags are omitted.

Touch this file when you want to:
- change default trip fields (`origin`, `dest`, `depart`, `return_date`, `trip_type`)
- set domestic/international preference (`is_domestic`)
- set run-level defaults (`llm_mode`, `human_mimic`, `save_html`, `disable_alerts`)
- define config-driven multi-trip runs (`trips:`)

### `configs/services.yaml`
Enabled service list and entry URL hints.

Touch this file when you want to:
- enable/disable providers for one run profile (`enabled_services`)
- override root URLs (`*_url`)
- provide bootstrap URL hints (`*_url_hints`, domestic/international split hints)
- keep Skyscanner available but disabled by default unless manually enabled
- enforce conservative request pacing/rate limiting for any enabled provider

### `configs/models.yaml`
Model names used by planner, extractor, and vision paths.

Touch this file when you want to:
- switch planner model (`planner`)
- switch HTML extraction model (`coder`)
- switch vision model (`vision`)

### `configs/thresholds.yaml`
Feature flags and runtime tuning knobs.

Touch this file when you want to:
- tune scenario retry/timeout budgets
- enable/disable VLM features and multimodal modes
- adjust scope guard and extraction acceptance behavior
- tune light/full profile behavior

Keep changes small and tested because this file has the highest behavioral impact.

### `configs/alerts.yaml`
Alert policy and channel settings.

Touch this file when you want to:
- enable/disable notifications
- set direction and threshold policy
- configure channel behavior (Telegram/email env var names)

### `configs/knowledge_rules.yaml`
Token/rule lists used by knowledge and heuristic classification.

Touch this file when you want to:
- adjust token-driven categorization (domestic/international/package/auth/scope)
- tune failure-reason mapping without code changes
- tune i18n fallback labels used by Google Flights placeholder/action matching:
  - `placeholder_dest_tokens`, `placeholder_origin_tokens`
  - `action_search_tokens`, `action_done_tokens`, `action_reset_tokens`
  - `tab_flights_tokens`, `tab_hotels_tokens`
 - tune grouped semantic token sets used by extractor/scenario logic:
   - `tokens.page.*` (for example `hotel`, `flight`, `package`)
   - `tokens.hints.*` (for example `auth`, `results`, `route_fields`)
   - `tokens.google.*` (for example `non_flight_map`, `non_flight_hotel`, `bundle_word`)

### `configs/service_ui_profiles.json`
Service UI selector labels and profile hints for scenario flows.

Touch this file when you want to:
- tune service-specific click/wait/select labels
- improve product/mode toggle targeting
- adjust fallback selector lists per service

## Common recipes

### Domestic vs international split
1. Set defaults in `configs/run.yaml` via `is_domestic`.
2. Add or tune `*_domestic_url_hints` and `*_international_url_hints` in `configs/services.yaml`.
3. If needed, tune mode labels/selectors in `configs/service_ui_profiles.json`.

### Multimodal extraction mode
1. Set `agentic_multimodal_mode` in `configs/thresholds.yaml` (`off`, `assist`, `primary`).
2. Tune multimodal timeout/DOM budget keys in `configs/thresholds.yaml`.
3. Keep plugin/router fallback enabled so uncertain outputs still return legacy-safe results.

### Alerts by target price
1. Enable alerts in `configs/alerts.yaml`.
2. Set `target_price` and optional cooldown.
3. Optionally override per run with CLI `--max-trip-price`.

## Precedence rules (important)
- CLI args override `configs/run.yaml`.
- Environment flags can override threshold defaults for many runtime switches.
- If plugin extraction returns uncertain output, router is expected to return `{}` and fallback to legacy extraction.

Notes on threshold keys:
- Many runtime knobs are optional threshold keys resolved via `get_threshold(...)` defaults.
- A key may be valid and actively used even if it is not explicitly present in `configs/thresholds.yaml`.
- Add optional keys to `configs/thresholds.yaml` only when you need explicit, versioned overrides.

## Troubleshooting notes

- If interaction logs show extremely low per-step timeouts (for example `Timeout 25ms exceeded`), tune:
  - `browser_action_selector_timeout_ms` / per-site override keys
  - `browser_wait_selector_timeout_ms` / per-site override keys
  - `browser_selector_timeout_min_ms` (minimum clamp floor)
- Search-click fallback now avoids broad bare-text selectors like `text=検索` by preferring button/submit/role=button patterns. If search clicks still miss, tune service-specific `search_selectors` in `configs/service_ui_profiles.json`.
- Google fallback label matching is config-driven from `configs/knowledge_rules.yaml` and locale-prioritized (not locale-locked). Add locale variants there instead of editing Python.
- Google Flights can now run deterministic route/date verification after fill steps and before search-click:
  - `scenario_google_flights_verify_after_fill_enabled`
  - `scenario_google_flights_verify_after_fill_fail_closed`
  - `scenario_google_flights_verify_min_confidence`
  The default confidence gate is now `high`, and destination placeholders like `目的地を探索` are treated as unfilled (blocked).
  Use this when route chips drift silently (wrong city/date despite successful fill logs).
- Hybrid route-binding gate (DOM + optional VLM verification) can block wrong-itinerary prices even when a price is detected:
  - `scenario_route_bind_gate_enabled`
  - `scenario_route_bind_gate_requires_strong`
  - `scenario_route_bind_vlm_verify_enabled`
  - `scenario_route_bind_vlm_timeout_sec`
  - `scenario_route_bind_fail_closed_on_mismatch`
  This gate protects against scope/readiness ambiguity by requiring route/date binding support before accepting medium/high-confidence prices.
- Google Flights deeplinks now follow mimic runtime params:
  - `google_flights_deeplink_use_mimic_params` (updates `hl/gl/c` deterministically)
  If you run with `mimic_locale/en-US`, `mimic_region/US`, `mimic_currency/USD`, deeplink search context will follow those values.
- On explicit route mismatch (for example destination drift), one bounded reset can run:
  - `google_flights_reset_on_route_mismatch_enabled`
  - `google_flights_reset_on_route_mismatch_max_attempts`
  This re-opens the deeplink and tries a gentle clear/reset before continuing repair flow.
- Google Flights can prioritize rewind replay-fills before broad follow-up retries when mismatch is strongly suspected:
  - `google_flights_rewind_priority_on_route_mismatch_enabled`
  - `google_flights_rewind_priority_on_route_mismatch_max_per_attempt`
  - `google_flights_rewind_priority_requires_strong_signal`
  This keeps repair focused on restoring requested route/date chips and is strictly bounded per attempt.
- Google Flights can run one deterministic force-bind repair turn before broad retries:
  - `google_flights_force_route_bind_repair_enabled`
  - `google_flights_force_route_bind_repair_max_per_attempt`
  - `google_flights_force_bind_dest_refill_max`
  The repair sequence prefers Flights tab context, reset/clear, strict destination selectors, date `完了` confirmation, then search.
- Selector hints are normalized with stability labels; brittle class-chain selectors can automatically lower confidence:
  - `extract_selector_stability_normalize_enabled`
  - `extract_confidence_downgrade_on_brittle_selector`
  - `extract_confidence_downgrade_min`
  If `selector_hint` shows hashed/brittle classes, confidence downgrade is expected behavior.
- Optional evidence checkpoints can be enabled for high-signal debugging without changing extraction logic:
  - `scenario_evidence_dump_enabled`
  This writes compact per-service checkpoint JSON under `storage/runs/<run_id>/artifacts/evidence_<service>_state.json` (initial load, before search click, ready/unready verdict, extraction start/end).
- Optional wall-clock watchdogs can bound long-running stages without lowering internal model/browser timeouts:
  - `scenario_wall_clock_cap_sec`
  - `extract_wall_clock_cap_sec`
  - `llm_call_wall_clock_cap_sec`
  Defaults are `0` (disabled). For slow local 7B/8B models, start conservative (for example scenario 900-1200, extract 900-1200, llm call 600-900) and tune upward only when needed.
- Optional throughput-stall abort for LLM calls:
  - `llm_stall_abort_enabled`
  - `llm_stall_tokens_per_sec`
  - `llm_stall_min_elapsed_sec`
  Keep disabled by default; enable only when logs show very low throughput for long elapsed periods and repeated non-progress stalls.
- If extraction returns `route_not_bound`, inspect `storage/runs/<run_id>/artifacts/evidence_<service>_state.json` together with `storage/runs/<run_id>/artifacts/` snapshots to compare expected route vs observed DOM/VLM fields.
- VLM screenshot preprocessing now keeps full-frame first and can optionally add a bottom crop:
  - `vlm_image_include_bottom_crop`
  - `vlm_image_bottom_crop_height_ratio`
  - `vlm_image_oversize_reencode_max_attempts`
  - `vlm_image_profile_default_*` / `vlm_image_profile_diverse_*`
  Defaults preserve prior behavior (`bottom_crop=false`). Re-encode attempts help avoid sending oversize fallback images when byte caps are strict.
- VLM extraction supports one bounded adaptive retry for crop/view uncertainty:
  - `vlm_extract_adaptive_retry_enabled`
  - `vlm_extract_adaptive_retry_max_attempts`
  - `vlm_extract_adaptive_retry_on_reasons`
  - `vlm_extract_adaptive_retry_variant_profile_primary`
  - `vlm_extract_adaptive_retry_variant_profile_retry`
  - `vlm_extract_adaptive_retry_timeout_backoff_ratio`
  - `vlm_extract_adaptive_retry_min_timeout_sec`
  This keeps cost bounded (default one retry) and falls back safely to the primary result if retry is not better.
