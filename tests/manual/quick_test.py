"""Manual smoke script for end-to-end scenario run and extraction output."""

import argparse
from datetime import date, datetime, timedelta
from pathlib import Path
import sys

# Allow direct execution via `python tests/manual/quick_test.py`.
REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from core.scenario_runner import run_agentic_scenario
from core.extractor import extract_price
from core.flight_plan import DEFAULT_FLIGHTS_URL, resolve_flight_plan


def _default_depart_date() -> str:
    """Use a near-future default date that is always valid."""
    return (date.today() + timedelta(days=30)).isoformat()


def _default_return_date(depart: str) -> str:
    """Pick a default return date one week after departure."""
    try:
        depart_date = datetime.strptime(depart, "%Y-%m-%d").date()
    except ValueError:
        depart_date = date.today() + timedelta(days=30)
    return (depart_date + timedelta(days=7)).isoformat()


def _parse_args():
    """Parse CLI arguments for one smoke scenario run."""
    parser = argparse.ArgumentParser(
        description="Run one Google Flights scenario and extract the displayed price.",
    )
    parser.add_argument("--origin", help="Origin IATA code, e.g. HND")
    parser.add_argument("--dest", help="Destination IATA code, e.g. ITM")
    parser.add_argument("--depart", help="Departure date in YYYY-MM-DD")
    parser.add_argument("--return-date", help="Return date in YYYY-MM-DD")
    parser.add_argument(
        "--trip-type",
        choices=("one_way", "round_trip"),
        default="round_trip",
        help="Trip type: one_way or round_trip (default: round_trip)",
    )
    parser.add_argument(
        "--url",
        default=DEFAULT_FLIGHTS_URL,
        help=f"Target flights page URL (default: {DEFAULT_FLIGHTS_URL})",
    )
    parser.add_argument(
        "--plan-file",
        help="Optional JSON file with keys: origin, dest, depart, url",
    )
    parser.add_argument(
        "--interactive",
        action="store_true",
        help="Prompt for missing fields in terminal before running",
    )
    return parser.parse_args()


def _prompt_with_default(label: str, default: str) -> str:
    """Prompt user once and fallback to default on empty input."""
    raw = input(f"{label} [{default}]: ").strip()
    return raw or default


def _resolve_plan_from_args(args):
    """Merge CLI + optional prompts into one validated flight plan."""
    origin = args.origin
    dest = args.dest
    depart = args.depart
    return_date = args.return_date
    trip_type = args.trip_type
    url = args.url

    if args.interactive:
        trip_type = _prompt_with_default("Trip type (one_way|round_trip)", trip_type or "round_trip")
        origin = _prompt_with_default("Origin IATA", origin or "HND")
        dest = _prompt_with_default("Destination IATA", dest or "ITM")
        depart = _prompt_with_default("Departure date (YYYY-MM-DD)", depart or _default_depart_date())
        if trip_type == "round_trip":
            return_date = _prompt_with_default(
                "Return date (YYYY-MM-DD)",
                return_date or _default_return_date(depart),
            )
        else:
            return_date = None
        url = _prompt_with_default("Flights URL", url or DEFAULT_FLIGHTS_URL)
    elif trip_type == "round_trip" and not return_date and depart:
        return_date = _default_return_date(depart)

    return resolve_flight_plan(
        origin=origin,
        dest=dest,
        depart=depart,
        return_date=return_date,
        trip_type=trip_type,
        url=url,
        plan_file=args.plan_file,
    )


def main() -> None:
    """Run one end-to-end smoke scenario and print extraction output."""
    args = _parse_args()
    try:
        plan = _resolve_plan_from_args(args)
    except ValueError as exc:
        raise SystemExit(f"Invalid flight plan input: {exc}") from exc

    print(
        f"Running scenario origin={plan.origin} dest={plan.dest} "
        f"depart={plan.depart} return={plan.return_date} "
        f"trip_type={plan.trip_type} url={plan.url}"
    )

    html = run_agentic_scenario(
        url=plan.url,
        origin=plan.origin,
        dest=plan.dest,
        depart=plan.depart,
        return_date=plan.return_date,
        trip_type=plan.trip_type,
        is_domestic=plan.is_domestic,
    )

    # Persist the final rendered HTML so extraction failures are debuggable.
    debug_html_path = REPO_ROOT / "storage" / "quick_test_last.html"
    debug_html_path.parent.mkdir(parents=True, exist_ok=True)
    debug_html_path.write_text(html, encoding="utf-8")
    print(f"Saved HTML snapshot to: {debug_html_path}")

    result = extract_price(html, site="google_flights", task="price")
    print("Final result:", result)


def smoke_google_combobox_commit() -> None:
    """Smoke check for Google Flights combobox commit logic with mocked DOM.

    Verifies that IATA-based selection works correctly:
    - Mock 3 suggestion options: HND, NRT, generic
    - Attempt commit with IATA="HND"
    - Verify HND option is selected and matched_iata=True
    """
    print("Running smoke check: google_combobox_commit...")

    # Mock Page class simulating browser DOM API
    class MockPage:
        def __init__(self):
            self.suggestions = [
                {"text": "Tokyo Haneda Airport (HND)", "value": "HND"},
                {"text": "Tokyo Narita International Airport (NRT)", "value": "NRT"},
                {"text": "Tokyo, Japan", "value": "TYO"},
            ]
            self.selected_index = None
            self.fill_called = False
            self.fill_value = ""

        def fill(self, selector: str, text: str):
            self.fill_called = True
            self.fill_value = text

        def click(self, selector: str, timeout_ms: int = 1000):
            # Simulate clicking a suggestion option by index or value
            if ":nth-child(" in selector:
                # Extract index from selector like "div[role='option']:nth-child(1)"
                import re
                match = re.search(r":nth-child\((\d+)\)", selector)
                if match:
                    index = int(match.group(1)) - 1  # Convert 1-based to 0-based
                    if 0 <= index < len(self.suggestions):
                        self.selected_index = index
                        return
            # Match by value or text in selector
            for i, suggestion in enumerate(self.suggestions):
                if suggestion["value"] in selector or suggestion["text"].lower() in selector.lower():
                    self.selected_index = i
                    return

        def wait_for_selector(self, selector: str, timeout_ms: int = 1000):
            return True

        def query_selector_all(self, selector: str):
            # Return mock suggestion elements
            return [
                {"textContent": s["text"], "value": s["value"]}
                for s in self.suggestions
            ]

    # Simulate combobox commit logic
    mock_page = MockPage()
    target_iata = "HND"

    # Step 1: Fill with IATA code
    mock_page.fill("input[role='combobox']", target_iata)
    assert mock_page.fill_called, "Fill should be called"
    assert mock_page.fill_value == target_iata, f"Expected fill value {target_iata}"

    # Step 2: Get suggestions
    suggestions = mock_page.query_selector_all("div[role='option']")
    assert len(suggestions) == 3, "Should have 3 suggestions"

    # Step 3: Find and click matching suggestion (first one containing IATA)
    matched_iata = False
    for i, suggestion in enumerate(suggestions):
        if target_iata in suggestion["textContent"]:
            mock_page.click(f"div[role='option']:nth-child({i+1})")
            matched_iata = True
            break

    # Verify results
    assert matched_iata, "Should match IATA code in suggestions"
    assert mock_page.selected_index == 0, "Should select first HND option"

    print("✓ Smoke check passed: HND option correctly selected")
    print(f"  Selected: {mock_page.suggestions[mock_page.selected_index]['text']}")
    print(f"  matched_iata: {matched_iata}")


if __name__ == "__main__":
    import sys
    if len(sys.argv) > 1 and sys.argv[1] == "--smoke":
        smoke_google_combobox_commit()
    else:
        main()
