import json
from pathlib import Path

from utils import selector_hints as sh


def test_selector_hints_seed_overwrite_and_lookup(tmp_path):
    hints_root = tmp_path / "hints"
    seeded = sh.seed_selector_hints(
        [
            {
                "site": "google_flights",
                "action": "quick_rebind_search",
                "role": "",
                "selector": "button[aria-label*='Search']",
                "display_lang": "en",
                "source": "debug_seed",
            },
            {
                "site": "google_flights",
                "action": "quick_rebind_search",
                "role": "",
                "selector": "button[aria-label*='検索']",
                "display_lang": "ja",
                "source": "debug_seed",
            },
        ],
        hints_root=hints_root,
    )
    assert seeded["seeded"] == 2

    en = sh.get_selector_hints(
        site="google_flights",
        action="quick_rebind_search",
        display_lang="en",
        hints_root=hints_root,
    )
    ja = sh.get_selector_hints(
        site="google_flights",
        action="quick_rebind_search",
        display_lang="ja",
        hints_root=hints_root,
    )
    assert en and en[0] == "button[aria-label*='Search']"
    assert ja and ja[0] == "button[aria-label*='検索']"

    overwritten = sh.seed_selector_hints(
        [
            {
                "site": "google_flights",
                "action": "quick_rebind_search",
                "role": "",
                "selector": "button:has-text('Search')",
                "display_lang": "en",
                "source": "debug_seed",
            }
        ],
        overwrite=True,
        hints_root=hints_root,
    )
    assert overwritten["groups_overwritten"] == 1
    en2 = sh.get_selector_hints(
        site="google_flights",
        action="quick_rebind_search",
        display_lang="en",
        hints_root=hints_root,
    )
    assert en2 and en2[0] == "button:has-text('Search')"
    store_path = hints_root / "google_flights.json"
    payload = json.loads(store_path.read_text(encoding="utf-8"))
    assert isinstance(payload.get("entries"), list)


def test_collect_selector_hints_from_run_extracts_google_debug_artifacts(tmp_path, monkeypatch):
    run_id = "20260225_123456_abcd12"
    run_dir = tmp_path / "runs" / run_id
    artifacts = run_dir / "artifacts"
    artifacts.mkdir(parents=True, exist_ok=True)

    (run_dir / "run.log").write_text(
        "\n".join(
            [
                "scenario.start site=google_flights url=https://www.google.com/travel/flights?hl=en&gl=JP#flt=HND.ITM.2026-03-01",
                "gf.deeplink.quick_rebind.search_click_ok selector=button[aria-label*='Search']",
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    (artifacts / "google_route_fill_dest_post_combobox_selector_probe.json").write_text(
        json.dumps(
            {
                "stage": "post_combobox",
                "role": "dest",
                "extra": {
                    "ok": True,
                    "combobox_debug": {
                        "activation_selector_used": "[role='combobox'][aria-label*='Where to']",
                        "input_selector_used": "input[role='combobox'][aria-label*='Where to']",
                        "generic_input_selector_used": False,
                    },
                },
                "selector_dom_probe": {
                    "url": "https://www.google.com/travel/flights?hl=en&gl=JP",
                    "active_element": {
                        "tag": "input",
                        "aria_label": "Where to? Osaka ITM",
                    },
                },
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    monkeypatch.setattr(sh, "get_run_dir", lambda _run_id: run_dir)
    monkeypatch.setattr(sh, "get_artifacts_dir", lambda _run_id: artifacts)

    hints = sh.collect_selector_hints_from_run(run_id)
    keys = {(h["action"], h.get("role", ""), h["selector"]) for h in hints}
    assert ("quick_rebind_search", "", "button[aria-label*='Search']") in keys
    assert ("route_fill_activation", "dest", "[role='combobox'][aria-label*='Where to']") in keys
    assert ("route_fill_input", "dest", "input[role='combobox'][aria-label*='Where to']") in keys


def test_selector_hint_failure_demotes_and_quarantine_removes(tmp_path):
    hints_root = tmp_path / "hints"
    selector = "button[aria-label*='Search']"
    sh.promote_selector_hint(
        site="google_flights",
        action="quick_rebind_search",
        role="",
        selector=selector,
        display_lang="en",
        hints_root=hints_root,
    )
    assert sh.record_selector_hint_failure(
        site="google_flights",
        action="quick_rebind_search",
        role="",
        selector=selector,
        display_lang="en",
        reason="nonsemantic_selector",
        hints_root=hints_root,
    )
    store = json.loads((hints_root / "google_flights.json").read_text(encoding="utf-8"))
    entry = next(e for e in store["entries"] if e.get("selector") == selector)
    assert int(entry.get("failures", 0)) == 1
    assert str(entry.get("last_failure_reason", "")) == "nonsemantic_selector"

    assert sh.quarantine_selector_hint(
        site="google_flights",
        action="quick_rebind_search",
        role="",
        selector=selector,
        display_lang="en",
        reason="poisoned",
        hints_root=hints_root,
    )
    store2 = json.loads((hints_root / "google_flights.json").read_text(encoding="utf-8"))
    assert not any(e.get("selector") == selector for e in store2.get("entries", []))
