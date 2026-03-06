"""Stage-0 safety-net tests for incremental refactors."""

import base64
from pathlib import Path

import pytest

from core import services as svc
from llm import code_model as cm
from llm import prompts as prompt_defs

pytestmark = [pytest.mark.llm, pytest.mark.heavy]


def test_parse_json_from_raw_tolerates_wrappers():
    """Parser should recover JSON payload from noisy fenced text."""
    raw = """note
```json
{"price": 12345, "currency": "JPY", "confidence": "high", "reason": ""}
```
"""
    parsed = cm._parse_json_from_raw(raw)
    assert isinstance(parsed, dict)
    assert parsed.get("price") == 12345
    assert parsed.get("currency") == "JPY"


def test_coerce_price_payload_keeps_null_with_reason():
    """Coercer should keep explicit no-price explanation payloads."""
    raw = '{"price": null, "currency": "JPY", "confidence": "medium", "reason": "non_flight_scope"}'
    payload = cm._coerce_price_payload_from_raw(raw)
    assert payload == {
        "price": None,
        "currency": "JPY",
        "confidence": "medium",
        "reason": "non_flight_scope",
    }


def test_base_domain_handles_public_suffixes():
    """Base-domain helper should keep registrable domain for 3rd-level suffixes."""
    assert svc._base_domain("travel.example.co.jp") == "example.co.jp"
    assert svc._base_domain("www.google.com") == "google.com"
    assert svc._base_domain("www.skyscanner.co.uk") == "skyscanner.co.uk"


def test_service_url_candidates_keeps_preferred_first_for_single_flow():
    """Preferred URL should stay first when split-flow front-loading is inactive."""
    preferred = "https://example.local/flights"
    urls = svc.service_url_candidates(
        "google_flights",
        preferred_url=preferred,
        is_domestic=True,
        knowledge={"site_type": "single_flow"},
        seed_hints={"generic": []},
    )
    assert urls
    assert urls[0] == preferred


def test_encode_image_base64_variants_dedupes_and_applies_byte_cap(monkeypatch, tmp_path):
    """Variant encoder should dedupe equal blobs and skip oversized candidates."""
    image_path = tmp_path / "dummy.png"
    image_path.write_bytes(b"ORIGINAL_IMAGE_BYTES")

    monkeypatch.setattr(cm, "_sips_binary", lambda: "/usr/bin/sips")
    monkeypatch.setattr(cm, "_sips_dimensions", lambda _bin, _path: (1200, 800))

    def _fake_threshold_bool(key, default):
        overrides = {
            "vlm_image_preprocess_enabled": True,
            "vlm_image_include_top_crop": True,
            "vlm_image_include_center_crop": True,
        }
        return overrides.get(key, default)

    def _fake_threshold_int(key, default):
        overrides = {
            "vlm_image_max_variants": 4,
            "vlm_image_max_side_px": 960,
            "vlm_image_max_bytes": 8,
            "vlm_image_jpeg_quality": 65,
        }
        return overrides.get(key, default)

    monkeypatch.setattr(cm, "_threshold_bool", _fake_threshold_bool)
    monkeypatch.setattr(cm, "_threshold_int", _fake_threshold_int)
    monkeypatch.setattr(cm, "_threshold_float", lambda _key, default: default)

    def _fake_sips_make_variant(_bin, _src, out_path, **_kwargs):
        out = Path(out_path)
        if out.name == "full.jpg":
            out.write_bytes(b"ABCD")
        elif out.name == "top.jpg":
            out.write_bytes(b"ABCD")  # duplicate
        else:
            out.write_bytes(b"0123456789")  # oversized (10 > max_bytes=8)
        return True

    monkeypatch.setattr(cm, "_sips_make_variant", _fake_sips_make_variant)

    variants = cm._encode_image_base64_variants(str(image_path))
    assert len(variants) == 1
    assert base64.b64decode(variants[0]) == b"ABCD"


def test_prompt_contracts_keep_expected_keys_and_enums():
    """Prompt schemas should continue to advertise expected keys/enums."""
    assert '"confidence": "high" | "medium" | "low"' in prompt_defs.PRICE_EXTRACTION_PROMPT
    assert '"quality": "good" | "uncertain" | "garbage"' in prompt_defs.HTML_QUALITY_PROMPT
    assert '"page_class": "flight_only" | "flight_hotel_package" | "garbage_page" | "irrelevant_page" | "unknown"' in prompt_defs.LLM_TRIP_PRODUCT_GUARD_PROMPT
    assert '"visible_price_text": string | null' in prompt_defs.VLM_PRICE_EXTRACTION_PROMPT
    assert '"support": "strong" | "weak" | "none"' in prompt_defs.VLM_PRICE_VERIFICATION_PROMPT
    assert '"route_bound": boolean' in prompt_defs.VLM_MULTIMODAL_EXTRACTION_PROMPT
    assert '"page_scope": "domestic" | "international" | "mixed" | "unknown"' in prompt_defs.VLM_UI_ASSIST_PROMPT
    assert '"origin": {"bbox": [number, number, number, number] | null' in prompt_defs.VLM_FILL_ROI_PROMPT
    assert '"value": string | null' in prompt_defs.VLM_ROI_VALUE_PROMPT
    assert 'Use only actions: "fill", "click", "wait".' in prompt_defs.SCENARIO_PROMPT
    assert 'Allowed actions: "fill", "click", "wait".' in prompt_defs.REPAIR_PROMPT
