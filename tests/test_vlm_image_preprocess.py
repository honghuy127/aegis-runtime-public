"""Tests for extracted VLM image preprocessing helpers (Stage 2)."""

import base64
from pathlib import Path

import pytest

from llm import code_model as cm
from llm.vlm import image_preprocess as ip

pytestmark = [pytest.mark.vlm, pytest.mark.heavy]


def test_normalize_roi_bbox_clamps_to_unit_bounds():
    """ROI bbox should clamp coordinates and stay inside [0,1]."""
    bbox = ip._normalize_roi_bbox([-0.2, 0.9, 0.5, 0.5])
    assert bbox is not None
    x, y, w, h = bbox
    assert 0.0 <= x <= 1.0
    assert 0.0 <= y <= 1.0
    assert 0.0 <= w <= 1.0
    assert 0.0 <= h <= 1.0
    assert x + w <= 1.0
    assert y + h <= 1.0


def test_vlm_attempt_timeouts_respects_total_budget(monkeypatch):
    """Timeout splitter should stay bounded by configured min/max and total budget."""
    monkeypatch.setattr(cm, "_threshold_int", lambda key, default: {"vlm_attempt_timeout_min_sec": 90, "vlm_attempt_timeout_max_sec": 240}.get(key, default))
    assert cm._vlm_attempt_timeouts(900, 2) == [240, 240]
    assert cm._vlm_attempt_timeouts(60, 2) == [30, 30]


def test_dedupe_and_cap_payloads_dedupes_and_falls_back_with_oversize():
    """Dedupe/cap helper should dedupe equal blobs and fallback when all oversized."""
    payloads, sizes = ip._dedupe_and_cap_payloads(
        [b"ABCD", b"ABCD", b"0123456789"],
        original_bytes=b"ORIGINAL",
        max_bytes=8,
        max_variants=3,
    )
    assert sizes == [4]
    assert len(payloads) == 1
    assert base64.b64decode(payloads[0]) == b"ABCD"

    fallback_payloads, fallback_sizes = ip._dedupe_and_cap_payloads(
        [b"0123456789", b"ABCDEFGHIJK"],
        original_bytes=b"ORIGINAL_TOO",
        max_bytes=3,
        max_variants=2,
    )
    assert len(fallback_payloads) == 1
    assert fallback_sizes == [10]
    assert base64.b64decode(fallback_payloads[0]) == b"0123456789"


def test_encode_variants_includes_bottom_crop_when_enabled(tmp_path):
    """Bottom crop should be attempted when enabled and variant budget allows it."""
    image_path = tmp_path / "dummy.png"
    image_path.write_bytes(b"ORIGINAL_IMAGE_BYTES")
    calls = []

    def _fake_threshold_bool(key, default):
        overrides = {
            "vlm_image_preprocess_enabled": True,
            "vlm_image_include_top_crop": True,
            "vlm_image_include_center_crop": False,
            "vlm_image_include_bottom_crop": True,
        }
        return overrides.get(key, default)

    def _fake_threshold_int(key, default):
        overrides = {
            "vlm_image_max_variants": 4,
            "vlm_image_max_side_px": 960,
            "vlm_image_max_bytes": 100,
            "vlm_image_jpeg_quality": 65,
            "vlm_image_oversize_reencode_max_attempts": 2,
        }
        return overrides.get(key, default)

    def _fake_sips_make_variant(_bin, _src, out_path, **_kwargs):
        out = Path(out_path)
        calls.append(out.name)
        if out.name == "full.jpg":
            out.write_bytes(b"FULL")
        elif out.name == "top.jpg":
            out.write_bytes(b"TOP")
        elif out.name == "bottom.jpg":
            out.write_bytes(b"BOTTOM")
        else:
            out.write_bytes(b"OTHER")
        return True

    variants = ip._encode_image_base64_variants(
        str(image_path),
        threshold_bool_fn=_fake_threshold_bool,
        threshold_int_fn=_fake_threshold_int,
        threshold_float_fn=lambda _k, d: d,
        sips_binary_fn=lambda: "/usr/bin/sips",
        sips_dimensions_fn=lambda _bin, _path: (1200, 800),
        sips_make_variant_fn=_fake_sips_make_variant,
    )
    assert "bottom.jpg" in calls
    assert len(variants) >= 3
    assert base64.b64decode(variants[0]) == b"FULL"


def test_encode_variants_progressive_reencode_stops_when_under_cap(tmp_path):
    """Oversize full-frame should trigger bounded re-encodes until it fits byte cap."""
    image_path = tmp_path / "dummy.png"
    image_path.write_bytes(b"ORIGINAL_IMAGE_BYTES")
    calls = []

    def _fake_threshold_bool(key, default):
        overrides = {
            "vlm_image_preprocess_enabled": True,
            "vlm_image_include_top_crop": False,
            "vlm_image_include_center_crop": False,
            "vlm_image_include_bottom_crop": False,
        }
        return overrides.get(key, default)

    def _fake_threshold_int(key, default):
        overrides = {
            "vlm_image_max_variants": 1,
            "vlm_image_max_side_px": 960,
            "vlm_image_max_bytes": 8,
            "vlm_image_jpeg_quality": 65,
            "vlm_image_oversize_reencode_max_attempts": 4,
        }
        return overrides.get(key, default)

    def _fake_sips_make_variant(_bin, _src, out_path, **_kwargs):
        out = Path(out_path)
        calls.append(out.name)
        if out.name == "full.jpg":
            out.write_bytes(b"01234567890123456789")  # 20 bytes
        elif out.name == "full_reencode_1.jpg":
            out.write_bytes(b"0123456789012")  # 13 bytes
        elif out.name == "full_reencode_2.jpg":
            out.write_bytes(b"1234567")  # 7 bytes
        else:
            out.write_bytes(b"0123456789")
        return True

    variants = ip._encode_image_base64_variants(
        str(image_path),
        threshold_bool_fn=_fake_threshold_bool,
        threshold_int_fn=_fake_threshold_int,
        threshold_float_fn=lambda _k, d: d,
        sips_binary_fn=lambda: "/usr/bin/sips",
        sips_dimensions_fn=lambda _bin, _path: (1200, 800),
        sips_make_variant_fn=_fake_sips_make_variant,
    )
    assert calls[:3] == ["full.jpg", "full_reencode_1.jpg", "full_reencode_2.jpg"]
    assert len(variants) == 1
    assert base64.b64decode(variants[0]) == b"1234567"


def test_encode_variants_keeps_full_frame_first_ordering(tmp_path):
    """Accepted payload order should keep full-frame variant first when available."""
    image_path = tmp_path / "dummy.png"
    image_path.write_bytes(b"ORIGINAL_IMAGE_BYTES")

    def _fake_threshold_bool(key, default):
        overrides = {
            "vlm_image_preprocess_enabled": True,
            "vlm_image_include_top_crop": True,
            "vlm_image_include_center_crop": True,
            "vlm_image_include_bottom_crop": False,
        }
        return overrides.get(key, default)

    def _fake_threshold_int(key, default):
        overrides = {
            "vlm_image_max_variants": 3,
            "vlm_image_max_side_px": 960,
            "vlm_image_max_bytes": 100,
            "vlm_image_jpeg_quality": 65,
            "vlm_image_oversize_reencode_max_attempts": 1,
        }
        return overrides.get(key, default)

    def _fake_sips_make_variant(_bin, _src, out_path, **_kwargs):
        out = Path(out_path)
        if out.name == "full.jpg":
            out.write_bytes(b"FULL")
        elif out.name == "top.jpg":
            out.write_bytes(b"TOP")
        elif out.name == "center.jpg":
            out.write_bytes(b"CENTER")
        else:
            out.write_bytes(b"OTHER")
        return True

    variants = ip._encode_image_base64_variants(
        str(image_path),
        threshold_bool_fn=_fake_threshold_bool,
        threshold_int_fn=_fake_threshold_int,
        threshold_float_fn=lambda _k, d: d,
        sips_binary_fn=lambda: "/usr/bin/sips",
        sips_dimensions_fn=lambda _bin, _path: (1200, 800),
        sips_make_variant_fn=_fake_sips_make_variant,
    )
    assert len(variants) == 3
    assert base64.b64decode(variants[0]) == b"FULL"


def test_encode_variants_preprocess_disabled_returns_original(tmp_path):
    """Disabling preprocess should preserve original-byte base64 behavior."""
    image_path = tmp_path / "dummy.png"
    image_bytes = b"ORIGINAL_IMAGE_BYTES"
    image_path.write_bytes(image_bytes)

    variants = ip._encode_image_base64_variants(
        str(image_path),
        threshold_bool_fn=lambda key, default: False if key == "vlm_image_preprocess_enabled" else default,
        threshold_int_fn=lambda _k, d: d,
        threshold_float_fn=lambda _k, d: d,
        sips_binary_fn=lambda: None,
    )
    assert len(variants) == 1
    assert base64.b64decode(variants[0]) == image_bytes


def test_encode_variants_profile_diverse_includes_center_and_bottom(tmp_path):
    """Variant profile should switch crop set from default to diverse."""
    image_path = tmp_path / "dummy.png"
    image_path.write_bytes(b"ORIGINAL_IMAGE_BYTES")

    def _fake_threshold_bool(key, default):
        overrides = {
            "vlm_image_preprocess_enabled": True,
            "vlm_image_profile_default_include_top_crop": True,
            "vlm_image_profile_default_include_center_crop": False,
            "vlm_image_profile_default_include_bottom_crop": False,
            "vlm_image_profile_diverse_include_top_crop": True,
            "vlm_image_profile_diverse_include_center_crop": True,
            "vlm_image_profile_diverse_include_bottom_crop": True,
        }
        return overrides.get(key, default)

    def _fake_threshold_int(key, default):
        overrides = {
            "vlm_image_max_variants": 4,
            "vlm_image_profile_diverse_max_variants": 4,
            "vlm_image_max_side_px": 960,
            "vlm_image_max_bytes": 200,
            "vlm_image_jpeg_quality": 65,
            "vlm_image_oversize_reencode_max_attempts": 1,
        }
        return overrides.get(key, default)

    default_calls = []
    diverse_calls = []

    def _fake_sips_make_variant(_bin, _src, out_path, **_kwargs):
        out = Path(out_path)
        if _kwargs.get("offset_y") == 0 and out.name == "full.jpg":
            # full variant
            pass
        if out.name in {"full.jpg", "top.jpg", "center.jpg", "bottom.jpg"}:
            out.write_bytes(out.name.encode("ascii"))
        else:
            out.write_bytes(b"OTHER")
        return True

    def _default_sips_make_variant(_bin, _src, out_path, **kwargs):
        default_calls.append(Path(out_path).name)
        return _fake_sips_make_variant(_bin, _src, out_path, **kwargs)

    def _diverse_sips_make_variant(_bin, _src, out_path, **kwargs):
        diverse_calls.append(Path(out_path).name)
        return _fake_sips_make_variant(_bin, _src, out_path, **kwargs)

    default_variants = ip._encode_image_base64_variants(
        str(image_path),
        profile="default",
        threshold_bool_fn=_fake_threshold_bool,
        threshold_int_fn=_fake_threshold_int,
        threshold_float_fn=lambda _k, d: d,
        sips_binary_fn=lambda: "/usr/bin/sips",
        sips_dimensions_fn=lambda _bin, _path: (1200, 800),
        sips_make_variant_fn=_default_sips_make_variant,
    )
    diverse_variants = ip._encode_image_base64_variants(
        str(image_path),
        profile="diverse",
        threshold_bool_fn=_fake_threshold_bool,
        threshold_int_fn=_fake_threshold_int,
        threshold_float_fn=lambda _k, d: d,
        sips_binary_fn=lambda: "/usr/bin/sips",
        sips_dimensions_fn=lambda _bin, _path: (1200, 800),
        sips_make_variant_fn=_diverse_sips_make_variant,
    )

    assert "center.jpg" not in default_calls
    assert "bottom.jpg" not in default_calls
    assert "center.jpg" in diverse_calls
    assert "bottom.jpg" in diverse_calls
    assert len(default_variants) >= 1
    assert len(diverse_variants) >= 1
