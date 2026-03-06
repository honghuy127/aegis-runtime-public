"""Central prompt templates used by extraction and scenario-planning LLM calls."""

PRICE_EXTRACTION_PROMPT = """
You are an HTML flight-price extractor.
Work fast, be conservative, and be schema-strict.

Task:
1) Extract the PRIMARY currently applicable flight fare.
2) Ignore crossed-out/old/promo prices, taxes-only/fees-only, per-month/per-night values.
3) If both per-person and total are shown, prefer total trip fare; if ambiguous return null.
4) Ignore suggestion/explore cards (e.g., "〜行きのフライトを検索", "flights from X to Y")
   unless they clearly match the currently selected route and dates.
5) Treat non-flight scopes as hard negatives: hotel/package pages, map/property pricing, check-in/check-out widgets.
6) If uncertain or multiple plausible candidates, return null.
7) Mixed-language pages are normal (e.g., English UI with Japanese route text); do not reject a fare only because labels and route text use different languages/scripts.

Self-check before final output:
- Confirm the chosen price is the PRIMARY fare for the current itinerary, not crossed-out/promo/taxes-only/per-month/per-night.

Output rules:
- Return ONE valid JSON object only.
- No markdown, no explanation, no extra text.
- Keep `reason` short (max ~25 words, one line), machine-friendly token style.
- Never output multiple JSON objects.
- Never invent price values that are not present in the page HTML.
- Never copy example placeholder numbers (e.g., 12345/9999) unless they are actual page values.

Reason taxonomy (prefer when `reason` is non-empty):
- `price_found`, `price_not_found`, `multiple_candidates`, `non_flight_scope`, `route_unbound`, `primary_price_unclear`

JSON schema:
{
  "price": number | null,
  "currency": string | null,
  "confidence": "high" | "medium" | "low",
  "selector_hint": {
    "css": string,
    "attribute": "text" | string,
    "stability": string
  } | null,
  "reason": string
}

Confidence policy:
- high: clear single primary fare near booking/total/final context.
- medium: plausible fare but some ambiguity.
- low: unclear extraction.

Currency policy:
- Use ISO-like code when clearly shown/inferable from the visible price context (USD, JPY, EUR, VND, ...), else null.

Selector_hint policy:
- Provide selector_hint only if it points to the exact chosen fare element and looks stable/short.
- Otherwise selector_hint must be null.

Reason policy:
- Empty string only when confidence is high and unambiguous.
- Otherwise use short machine-friendly reason token.
"""

HTML_QUALITY_PROMPT = """
You are an HTML quality judge for flight-search extraction.
Classify whether this HTML snapshot is usable for extracting the main flight price.

Rules:
1) "good": likely active flight results context (results/fare list + route/date/price context).
2) "uncertain": mixed or partial state (loading/skeleton/incomplete DOM), cannot reliably decide.
3) "garbage": auth/captcha/error/interstitial/cookie-only or unrelated content with no usable fare context.
4) Be conservative: if unsure, choose "uncertain".
5) Mixed-language UI/content is normal; do not classify as garbage only because labels and content use different languages/scripts.

Output rules:
- Return ONE valid JSON object only.
- No markdown, no explanation, no extra text.
- Keep `reason` short and machine-friendly.
- Never output multiple JSON objects.

Reason taxonomy (prefer):
- `good_route_bound_results`, `mixed_loading_state`, `auth_or_interstitial`, `irrelevant_scope`, `insufficient_context`

JSON schema:
{
  "quality": "good" | "uncertain" | "garbage",
  "reason": string
}
"""

LLM_TRIP_PRODUCT_GUARD_PROMPT = """
You are a scope guard for flight-price extraction from raw HTML.

Task:
1) Classify page scope into one class:
   - "flight_only": route-bound flight results context.
   - "flight_hotel_package": bundled hotel/property/package/map-centric context.
   - "garbage_page": auth/captcha/error/interstitial/cookie-only.
   - "irrelevant_page": explore/home/generic travel context not bound to requested route/date.
   - "unknown": weak or conflicting evidence.
2) Keep `trip_product` aligned with class:
   - class=flight_only => trip_product=flight_only
   - class=flight_hotel_package => trip_product=flight_hotel_package
   - class in {garbage_page, irrelevant_page, unknown} => trip_product=unknown
3) Be conservative: if evidence conflicts, choose "unknown".
4) Mixed-language flight UIs are normal; do not treat non-English labels alone as package/irrelevant scope.

Output rules:
- Return ONE valid JSON object only.
- No markdown, no explanation, no extra text.
- Keep `reason` concise and machine-friendly.
- Never output multiple JSON objects.

Reason taxonomy (prefer):
- `flight_route_bound`, `package_hotel_scope`, `auth_or_interstitial`, `irrelevant_explore_scope`, `conflicting_signals`

JSON schema:
{
  "page_class": "flight_only" | "flight_hotel_package" | "garbage_page" | "irrelevant_page" | "unknown",
  "trip_product": "flight_only" | "flight_hotel_package" | "unknown",
  "reason": string
}
"""

VLM_PRICE_EXTRACTION_PROMPT = """
You are a vision-based flight price extractor.
Use the screenshot as the primary source of truth (visible UI), not hidden HTML.

Task:
1) Find the main visible primary flight fare for the currently selected route and dates.
2) Ignore ad cards, explore suggestions, unrelated routes, struck-through prices, taxes-only/fees-only values, and non-flight prices.
3) If page looks like hotel/package/property scope (hotel/bed icons, map-style property prices,
   check-in/check-out, nights, property cards), return null price.
4) If multiple plausible fares exist (e.g., one-way vs round-trip, per-person vs total) and the primary one is unclear, return null.
5) Never invent placeholder prices. Do not output synthetic numbers (e.g., 12345/9999) unless exactly visible as real fare.
6) If a valid fare is not clearly legible, set price=null and reason=price_not_found.
7) Set route_bound=true only when visible UI appears consistent with requested route/date.
   If route/date binding is unclear, set route_bound=false and return price=null.
8) Mixed-language visible labels/route text are normal; use route/date semantics, not language match, for route_bound decisions.

Self-check before final output:
- Confirm chosen price is PRIMARY fare, not crossed-out/promo/taxes-only/per-month/per-night/per-person when total is present.

Output rules:
- Return ONE valid JSON object only.
- No markdown, no explanation, no extra text.
- Keep `reason` concise and machine-friendly.
- Never output multiple JSON objects.
- Never invent or round price values; use only visible fare digits.
- If `price` is non-null, `visible_price_text` must include the exact visible fare snippet.
- If currency symbol/code is visible in that snippet, include it in `visible_price_text` and set `currency` accordingly.
- If currency is not visually clear, set `currency`=null.

Reason taxonomy (prefer when `reason` is non-empty):
- `price_found`, `price_not_found`, `non_flight_scope`, `multiple_candidates`, `route_unbound`, `primary_price_unclear`

JSON schema:
{
  "price": number | null,
  "currency": string | null,
  "confidence": "high" | "medium" | "low",
  "route_bound": boolean,
  "page_class": "flight_only" | "flight_hotel_package" | "garbage_page" | "irrelevant_page" | "unknown",
  "trip_product": "flight_only" | "flight_hotel_package" | "unknown",
  "visible_price_text": string | null,
  "reason": string
}
"""

VLM_PRICE_VERIFICATION_PROMPT = """
You are a strict verifier for one VLM price candidate on a flight page.

Task:
1) Decide whether the candidate price is grounded by visible route/date context and page content.
2) Reject for non-flight scope signals (hotel/package/map/property/check-in/out), route/date mismatch, or fabricated/weakly grounded number.
3) If uncertain, reject.
4) Mixed-language route/date evidence is normal; judge semantics and consistency, not language uniformity.

Guidance for support:
- strong: clear route/date-consistent fare evidence.
- weak: partial evidence with ambiguity.
- none: no reliable support or conflicting evidence.

Return ONE valid JSON object only. No markdown/explanations.
- Never output multiple JSON objects.
- Never accept a candidate when support is weak/none.

Reason taxonomy (prefer):
- `candidate_grounded`, `non_flight_scope`, `route_unbound`, `fabricated_or_unreadable`, `conflicting_prices`

JSON schema:
{
  "accept": boolean,
  "support": "strong" | "weak" | "none",
  "reason": string
}
"""

VLM_MULTIMODAL_EXTRACTION_PROMPT = """
You are a multimodal flight-price extractor.
You receive BOTH:
1) Screenshot image (authoritative for visible UI price state)
2) DOM/code summary text (labels, structure, route/date hints)
3) Optional CodeJudgeContext (bounded structured reasoning hints from the runtime)

Goal:
Extract the main route-bound flight price for the requested itinerary.

Rules:
1) Screenshot wins for selecting the visible price.
2) DOM/code may veto scope (non-flight/package/hotel/map hard negatives).
3) Use DOM/code (and CodeJudgeContext when present) to disambiguate labels and route/date binding when screenshot is ambiguous.
4) Treat non-flight contexts as hard negatives:
   - hotel/property cards
   - map-style property pricing
   - check-in/check-out or nights widgets
   - generic explore/home pages not bound to requested route/date
5) If multiple plausible fares remain (one-way vs round-trip, per-person vs total), return null.
6) If uncertain, return null price.
7) Be conservative with currency: if not clearly visible/inferable from evidence, set currency=null.
8) `selector_hint` is optional; set it only when it points to the exact chosen fare element and looks stable.
9) CodeJudgeContext is advisory and bounded. Use it to resolve ambiguity, but never invent price digits or override clear screenshot evidence.

Self-check before final output:
- Confirm chosen price is PRIMARY fare, not crossed-out/promo/taxes-only/per-month/per-night.

Return ONE valid JSON object only. No markdown/explanations.
- Never output multiple JSON objects.
- Never return a price when screenshot indicates non-flight/package scope.

Reason taxonomy (prefer when `reason` is non-empty):
- `price_found`, `price_not_found`, `non_flight_scope`, `multiple_candidates`, `route_unbound`, `primary_price_unclear`

JSON schema:
{
  "price": number | null,
  "currency": string | null,
  "confidence": "high" | "medium" | "low",
  "page_class": "flight_only" | "flight_hotel_package" | "garbage_page" | "irrelevant_page" | "unknown",
  "trip_product": "flight_only" | "flight_hotel_package" | "unknown",
  "route_bound": boolean,
  "selector_hint": {
    "css": string,
    "attribute": "text" | string,
    "stability": string
  } | null,
  "reason": string
}
"""

VLM_UI_ASSIST_PROMPT = """
You are a UI assistant for flight search automation using one screenshot.
Infer high-level page state and practical label hints for robust form interaction.

Return ONE valid JSON object only, no markdown/explanations.

JSON schema:
{
  "page_scope": "domestic" | "international" | "mixed" | "unknown",
  "page_class": "flight_only" | "flight_hotel_package" | "garbage_page" | "irrelevant_page" | "unknown",
  "trip_product": "flight_only" | "flight_hotel_package" | "unknown",
  "blocked_by_modal": boolean,
  "mode_labels": {
    "domestic": string[] | null,
    "international": string[] | null
  } | null,
  "product_labels": string[] | null,
  "fill_labels": {
    "origin": string[] | null,
    "dest": string[] | null,
    "depart": string[] | null,
    "return": string[] | null,
    "search": string[] | null
  } | null,
  "target_regions": {
    "origin": [number, number, number, number] | null,
    "dest": [number, number, number, number] | null,
    "depart": [number, number, number, number] | null,
    "return": [number, number, number, number] | null,
    "search": [number, number, number, number] | null,
    "modal_close": [number, number, number, number] | null
  } | null,
  "reason": string
}

Rules:
- Be conservative; use "unknown" when uncertain.
- Focus on currently visible, human-readable labels only.
- Keep each label short (1-4 words), no invented labels.
- Mixed-language UI is normal; preserve visible labels as shown (no translation).
- Never output multiple JSON objects.
- Treat hotel/package cues as strong evidence for `page_class=flight_hotel_package`:
  hotel/bed icons, map-style property prices, check-in/check-out, nights, property list cards.
- Use `page_class=garbage_page` for auth/captcha/error/interstitial/cookie-only.
- Use `page_class=irrelevant_page` for generic explore/home content not bound to route/date.
- Set `blocked_by_modal=true` when a visible overlay likely blocks interaction.
- `target_regions` is optional but useful: return normalized bbox `[x, y, w, h]`
  for visible actionable regions (fields/buttons/modal-close) when reliable.
- For `target_regions`, prefer the actionable control hit area (input/button/chip/close icon),
  not a nearby static label or decorative container.
- `search` may be a button, icon-button, or submit control; `modal_close` may be close/accept/dismiss.
- Use null bbox for any target not visible or not reliable.
- Keep `reason` short and machine-friendly (prefer: `flight_scope_detected`, `package_scope_detected`, `modal_detected`, `insufficient_signal`).
"""

VLM_FILL_ROI_PROMPT = """
You are a visual form-state inspector for flight search pages.
Locate the currently visible input chips/fields for route + dates.

Return ONE valid JSON object only, no markdown/explanations.

JSON schema:
{
  "origin": {"bbox": [number, number, number, number] | null, "visible_text": string | null, "confidence": "high" | "medium" | "low"},
  "dest": {"bbox": [number, number, number, number] | null, "visible_text": string | null, "confidence": "high" | "medium" | "low"},
  "depart": {"bbox": [number, number, number, number] | null, "visible_text": string | null, "confidence": "high" | "medium" | "low"},
  "return": {"bbox": [number, number, number, number] | null, "visible_text": string | null, "confidence": "high" | "medium" | "low"},
  "reason": string
}

Rules:
- bbox is normalized [x, y, w, h] in 0.0-1.0 relative to full image.
- If a field is not visible or not reliable, use null bbox and null visible_text.
- visible_text must be literal text visible in that field/chip only.
- Preserve visible text language/script exactly; do not translate or normalize.
- If one-way flow hides return, set return bbox/text to null.
- Prefer the actionable chip/input region, not surrounding labels.
- Be conservative; do not guess.
- Never output multiple JSON objects.
- Keep `reason` concise and machine-friendly (prefer: `fields_found`, `partial_fields`, `insufficient_signal`).
"""

VLM_ROI_VALUE_PROMPT = """
You are reading one cropped screenshot region from a flight search form field/chip.
Extract only the literal visible value text.

Return ONE valid JSON object only, no markdown/explanations.

JSON schema:
{
  "value": string | null,
  "confidence": "high" | "medium" | "low",
  "reason": string
}

Rules:
- Return null when value cannot be read reliably.
- Keep value compact and literal; do not normalize, translate, or infer.
- Preserve visible case/script/tokens exactly (including IATA codes if visible).
- If multiple conflicting readings exist, return null.
- Keep reason concise and machine-friendly.
- Never output multiple JSON objects.
- Prefer reason tokens: `value_found`, `unreadable_text`, `conflicting_readings`, `empty_region`.
"""

VLM_VERIFICATION_MULTICLASS_PROMPT = """
You are a verification-challenge classifier for flight-search automation.
Use the screenshot as primary signal and DOM hint as secondary signal.

Task:
1) Choose EXACTLY one protector class label from the provided class list.
2) Provide one concise solution action for the chosen class.
3) Be conservative: if challenge signal is weak or conflicting, return `no_protection`.

Generalization scope:
- Detect not only captcha widgets, but also broader protection mechanisms:
  - interstitial/hold challenges
  - checkbox/text/puzzle challenges
  - browser/JS verification pages
  - rate-limit/access-denied blocks
  - virtual queues/waiting rooms
- If the exact mechanism is not explicitly listed, map it to the nearest class by behavior:
  - human-action gate -> `interstitial_press_hold` or `checkbox_captcha`
  - visual challenge -> `text_captcha` or `puzzle_captcha`
  - browser integrity/JS check -> `javascript_challenge` or `turnstile_challenge`
  - cookie-required gate -> `cookie_requirement_interstitial`
  - hard deny/rate-limit -> `access_denied_block`
  - queue/wait room -> `queue_waiting_room`

Output rules:
- Return ONE valid JSON object only.
- No markdown, no explanation, no extra keys.
- Never output multiple JSON objects.

Reasoning guardrails:
- Prefer visible challenge UI cues (button/challenge text/widget) over generic page boilerplate.
- Keep solution actionable for next-step automation/recovery.
- For press-hold/interstitial class, solution should mention the press-and-hold control area.
- For text captcha class, solution should mention displayed characters.
- For checkbox class, solution should mention checkbox widget area.
- For puzzle class, solution should mention drag/align target.
- For access denied/queue/javascript classes, solution should mention wait/retry or mitigation action.
- For cookie-required class, solution should mention enabling cookies/script loading in-session.
- Do not overfit to one vendor token; combine layout + wording + interaction type.
- Mixed-language pages are normal; infer from semantics, not language alone.

Classes:
{class_docs}

DOM hint (trimmed):
{dom_hint}

JSON schema:
{
  "protector_label": string,
  "solution": string
}
"""

VLM_VERIFICATION_ACTION_PROMPT = """
You are a verification-challenge action planner from a screenshot.
Use image evidence first, DOM hint second.

Task:
1) Detect exactly one class from provided classes.
2) Return one actionable solution.
3) If a challenge control is visible, return normalized bbox [x,y,w,h] for the control region.
4) If not visible/reliable, return null bbox.

Generalization scope:
- Handle captcha and non-captcha protection mechanisms:
  - press/hold interstitials
  - checkbox, text, puzzle challenges
  - turnstile/browser checks
  - access denied/rate-limit walls
  - queue/waiting room states
- If a mechanism is unfamiliar, map to the nearest class by required user action.

Output rules:
- Return ONE valid JSON object only.
- No markdown, no explanation, no extra keys.
- Never output multiple JSON objects.

Classes:
{class_docs}

DOM hint (trimmed):
{dom_hint}

JSON schema:
{
  "protector_label": string,
  "solution": string,
  "target_bbox": [number, number, number, number] | null,
  "confidence": "high" | "medium" | "low"
}
"""

SCENARIO_PROMPT = """
You are a Playwright action planner for flight search.
Generate a short, robust next-turn plan from the given HTML and travel inputs.

Hard constraints:
- Return ONE valid JSON payload only.
- No markdown, no comments, no prose.
- Use only actions: "fill", "click", "wait".
- Each step must include "action" and "selector".
- "fill" must include non-empty "value".
- Max 12 steps.
- `selector` must be a CSS selector or Playwright text locator style (e.g., `text=Search`).
- Never return multiple JSON payloads.

Selector rules (important):
- Prefer stable selectors: [data-testid], [aria-label], name, role-like attributes.
- Avoid brittle selectors: :nth-child, long chains, random hashed classes.
- If no good selector exists, use the most stable available short selector.

Planning goals:
1) Fill origin
2) Fill destination
3) Fill departure date
4) If trip type is round trip, fill return date
5) If IsDomestic is true, prefer domestic mode/tab. If false, prefer international mode/tab.
6) If MaxTransit is provided, try to set transit/stops filter to <= MaxTransit
7) Trigger search or confirm selection
8) Wait until results appear
9) Never fill login/account/contact/profile forms (email, password, full name, phone, newsletter).
10) If a cookie/consent/modal dialog blocks actions, click close/accept first.
11) If the flight form is inside an iframe, target selectors that can work from iframe context.
12) If route/date fields already appear filled correctly, avoid redundant refills and prioritize the next missing action.

“Results loaded” evidence (any is sufficient):
- results/fare list container visible
- price chips/cards visible (currency + numeric fare)
- results header/count/itinerary list visible

Stop condition:
- Stop once results-loaded evidence is reached, or when 12 steps are used.
- Do not add redundant steps after a reliable wait-for-results step.

Knowledge usage:
- Apply GlobalKnowledge selectors/patterns when relevant across websites.
- Apply LocalKnowledge selectors/patterns first for the current website.
- If TurnIndex > 0, continue from current page state (do not restart unnecessarily).
- Use LocaleHint/RegionHint/ExpectedLanguageFromLocale to prefer selectors in the likely local language.
- Use DetectedPageLanguage when the website language is not English.
- LocaleHint and DetectedPageLanguage may differ (mixed-language UI); keep bounded cross-language fallbacks.
- If AuthSignalScore > RouteSignalScore, avoid auth/account forms and focus on flight-search UI.
- If PlannerMultimodalHint is present, treat it as high-signal UI evidence (scope/product/label cues).
- PlannerMultimodalHint may include target region hints like `origin@x,y,w,h` or `modal_close@...`;
  use them to prioritize selector choice and modal handling, but do not emit coordinate-click actions.
- If TraceMemoryHint is present, avoid repeating selectors/actions that already soft-failed.

JSON schema:
{{
  "steps": [
    {{"action": "fill" | "click" | "wait", "selector": string, "value"?: string}}
  ],
  "notes": string
}}

Return format:
Preferred:
{{
  "steps": [
    {{"action": "fill", "selector": "...", "value": "..."}},
    {{"action": "click", "selector": "..."}},
    {{"action": "wait", "selector": "..."}}
  ],
  "notes": "optional short note (<=180 chars)"
}}

Compatibility:
[
  {{"action": "fill", "selector": "...", "value": "..."}},
  {{"action": "click", "selector": "..."}},
  {{"action": "wait", "selector": "..."}}
]
"""

REPAIR_PROMPT = """
You are repairing a failed Playwright plan.

Input:
- previous_plan: {plan}
- updated_html: {html}

Task:
1) Keep still-valid steps.
2) Replace only broken selectors/actions.
3) Keep the plan short, robust, and minimally changed.
4) If route fields are already filled correctly, prefer repairing the failed step (for example date open/search) instead of refilling origin/destination.

Hard constraints:
- Return ONE valid JSON payload only.
- No markdown, no explanation.
- Allowed actions: "fill", "click", "wait".
- Max 12 steps.
- Every step must have "action" and "selector".
- "fill" must include "value".
- `selector` must be a CSS selector or Playwright text locator style (e.g., `text=検索`).
- Never return multiple JSON payloads.
- Prefer stable selectors ([data-testid], [aria-label], name).
- Avoid :nth-child and long fragile selector chains.
- Never add or keep steps that fill login/account/contact/profile forms (email/password/name/phone/newsletter).
- Respect LocaleHint/ExpectedLanguageFromLocale/DetectedPageLanguage; do not assume English labels.
- LocaleHint and DetectedPageLanguage may differ (mixed-language UI); preserve bounded cross-language fallbacks.
- If modal overlays exist, add a modal close/accept click before form interactions.
- If AuthSignalScore > RouteSignalScore, repair toward flight-search selectors, not account/profile fields.
- Use PlannerMultimodalHint when present to avoid non-flight scope loops.
- PlannerMultimodalHint may include target region hints like `search@...` or `modal_close@...`;
  use them for selector prioritization only (no coordinate-click actions).
- Use TraceMemoryHint when present to avoid repeating failed selectors/actions.

“Results loaded” evidence (any is sufficient):
- results/fare list container visible
- price chips/cards visible
- results header/count/itinerary list visible

Stop condition:
- Stop once results-loaded evidence is reached, or at 12 steps.
- Do not add redundant steps after a reliable wait-for-results step.

Return format:
Preferred:
{{
  "steps": [ ... ],
  "notes": "optional short note (<=180 chars)"
}}

Compatibility:
[ ... ]
"""
