from llm.json_parsing import parse_json_from_raw


def test_parse_json_from_raw_recovers_steps_object_fragment():
    raw = '\n  "steps": [{"action":"wait","selector":["body"]}], "notes": ["fragment"]\n'
    parsed = parse_json_from_raw(raw)
    assert isinstance(parsed, dict)
    assert isinstance(parsed.get("steps"), list)
    assert parsed["steps"][0]["action"] == "wait"
    assert parsed.get("notes") == ["fragment"]
