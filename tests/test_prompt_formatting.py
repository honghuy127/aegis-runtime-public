from collections import defaultdict

from llm import prompts as p


def test_scenario_and_repair_prompts_format_literal_json_examples():
    rendered_scenario = p.SCENARIO_PROMPT.format_map(
        defaultdict(
            str,
            {
                "html": "<html></html>",
                "origin": "HND",
                "dest": "ITM",
                "depart": "2026-03-01",
                "return_date": "2026-03-08",
                "trip_type": "round_trip",
                "is_domestic": True,
                "max_transit": "",
                "turn_index": 1,
                "global_knowledge": "",
                "local_knowledge": "",
                "site_key": "google_flights",
                "mimic_locale": "ja-JP",
                "mimic_region": "JP",
            },
        )
    )
    rendered_repair = p.REPAIR_PROMPT.format(plan="[]", html="<html></html>")
    assert '"steps": [' in rendered_scenario
    assert "previous_plan:" in rendered_repair
