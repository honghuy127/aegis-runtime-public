"""Route-binding fusion and acceptance gate tests."""

import pytest

from core import extractor as ex
from core.route_binding import (
    dom_route_bind_probe,
    fuse_route_bind_verdict,
    vlm_route_bind_probe,
)

pytestmark = [pytest.mark.vlm]


def test_dom_probe_explicit_mismatch_blocks_support():
    """Explicit DOM contradiction (e.g., CTS vs expected ITM) should be mismatch/none."""
    html = """
    <html><body>
      <input aria-label="出発地" value="東京 (HND)">
      <input aria-label="目的地" value="札幌 (CTS)">
      <input aria-label="出発日" value="2026-03-01">
      <input aria-label="復路" value="2026-03-08">
    </body></html>
    """
    dom = dom_route_bind_probe(
        html,
        origin="HND",
        dest="ITM",
        depart="2026-03-01",
        return_date="2026-03-08",
    )
    assert dom["support"] == "none"
    assert "dest" in dom["mismatch_fields"]


def test_dom_probe_ignores_numeric_dest_noise_from_generic_to_labels():
    """Generic 'to' labels with numeric counters should not override real destination field."""
    html = """
    <html><body>
      <input aria-label="Where from?" value="Tokyo">
      <div aria-label="Children aged 2 to 11" aria-valuenow="0"></div>
      <input role="combobox" aria-label="Where to? Osaka ITM" value="Osaka">
      <input aria-label="Departure" value="Sun, Mar 1">
      <input aria-label="Return" value="Sun, Mar 8">
    </body></html>
    """
    dom = dom_route_bind_probe(
        html,
        origin="HND",
        dest="ITM",
        depart="2026-03-01",
        return_date="2026-03-08",
    )
    assert dom["observed"]["dest"] == "Osaka"
    assert "dest" not in dom["mismatch_fields"]


def test_dom_probe_prefers_route_payload_over_placeholder_like_where_to_label():
    """Route probe should prefer 'Where to? Osaka ITM' over hidden placeholder 'Where to?'."""
    html = """
    <html><body>
      <input aria-label="Where from?" value="Tokyo">
      <input role="combobox" aria-label="Where to? " value="">
      <input role="combobox" aria-label="Where to? Osaka ITM" value="">
      <input aria-label="Departure" value="Sun, Mar 1">
      <input aria-label="Return" value="Sun, Mar 8">
    </body></html>
    """
    dom = dom_route_bind_probe(
        html,
        origin="HND",
        dest="ITM",
        depart="2026-03-01",
        return_date="2026-03-08",
    )
    assert dom["observed"]["dest"] == "Where to? Osaka ITM"
    assert "dest" not in dom["mismatch_fields"]


def test_dom_probe_ignores_control_labels_and_duplicate_date_labels():
    """Control labels like swap/doubled field labels should not outrank real input values."""
    html = """
    <html><body>
      <button aria-label="Swap origin and destination.">Swap origin and destination.</button>
      <button aria-label="Swap origin and destination.">Swap origin and destination.</button>
      <input aria-label="Where from?" value="Fukuoka">
      <input aria-label="Where to? Tokyo HND" value="Tokyo">
      <button aria-label="Departure">Departure</button>
      <div aria-label="Departure">Departure</div>
      <input aria-label="Departure" value="Sat, May 2">
      <input aria-label="Return" value="Mon, Jun 8">
    </body></html>
    """
    dom = dom_route_bind_probe(
        html,
        origin="FUK",
        dest="HND",
        depart="2026-05-02",
        return_date="2026-06-08",
    )
    assert dom["observed"]["origin"] == "Fukuoka"
    assert dom["observed"]["dest"] == "Tokyo"
    assert "origin" not in dom["mismatch_fields"]
    assert "dest" not in dom["mismatch_fields"]


def test_fuse_dom_weak_vlm_strong_promotes_to_strong():
    """Weak DOM + strong VLM support should produce strong final verdict."""
    dom = {
        "support": "weak",
        "source": "dom",
        "reason": "dom_partial_or_unknown",
        "observed": {"origin": "東京 (HND)", "dest": "", "depart": "2026-03-01", "return": ""},
        "mismatch_fields": [],
    }
    vlm = vlm_route_bind_probe(
        {
            "fields": {
                "origin": {"matched": True, "observed": "東京 (HND)"},
                "dest": {"matched": True, "observed": "大阪 (ITM)"},
                "depart": {"matched": True, "observed": "2026-03-01"},
                "return": {"matched": True, "observed": "2026-03-08"},
            }
        },
        origin="HND",
        dest="ITM",
        depart="2026-03-01",
        return_date="2026-03-08",
    )
    verdict = fuse_route_bind_verdict(
        dom_probe=dom,
        vlm_probe=vlm,
        require_strong=True,
        fail_closed_on_mismatch=True,
    )
    assert verdict["support"] == "strong"
    assert verdict["route_bound"] is True
    assert verdict["source"] == "mixed"


def test_fuse_dom_none_vlm_none_stays_none():
    """No usable evidence from both probes should remain unbound."""
    verdict = fuse_route_bind_verdict(
        dom_probe={"support": "none", "source": "unknown", "observed": {}, "mismatch_fields": []},
        vlm_probe={"support": "none", "source": "unknown", "observed": {}, "mismatch_fields": []},
        require_strong=True,
        fail_closed_on_mismatch=True,
    )
    assert verdict["support"] == "none"
    assert verdict["route_bound"] is False


def test_fuse_requires_strong_flag_blocks_weak_support():
    """When strong evidence is required, weak support must not be route-bound."""
    verdict = fuse_route_bind_verdict(
        dom_probe={"support": "weak", "source": "dom", "observed": {}, "mismatch_fields": []},
        vlm_probe={"support": "none", "source": "unknown", "observed": {}, "mismatch_fields": []},
        require_strong=True,
        fail_closed_on_mismatch=True,
    )
    assert verdict["support"] == "weak"
    assert verdict["route_bound"] is False


def test_google_plugin_route_binding_accepts_weak_support_when_strong_not_required(monkeypatch):
    """Plugin route binding should treat weak support as bound when strong isn't required."""
    plugin = ex.GoogleFlightsPlugin()
    monkeypatch.setattr(
        ex,
        "get_threshold",
        lambda key, default=None: (
            False if key == "scenario_route_bind_gate_requires_strong" else default
        ),
    )
    ctx = {
        "route_bind_verdict_getter": lambda: {
            "route_bound": False,
            "support": "weak",
            "source": "dom",
            "reason": "dom_partial_or_unknown",
        }
    }
    assert plugin.evaluate_route_binding("<html></html>", ctx) is True


def test_google_plugin_route_binding_rejects_weak_support_when_strong_required(monkeypatch):
    """Plugin route binding should reject weak-only support when strong is required."""
    plugin = ex.GoogleFlightsPlugin()
    monkeypatch.setattr(
        ex,
        "get_threshold",
        lambda key, default=None: (
            True if key == "scenario_route_bind_gate_requires_strong" else default
        ),
    )
    ctx = {
        "route_bind_verdict_getter": lambda: {
            "route_bound": False,
            "support": "weak",
            "source": "dom",
            "reason": "dom_partial_or_unknown",
        }
    }
    assert plugin.evaluate_route_binding("<html></html>", ctx) is False


def test_scope_conflict_resolve_requires_strong_route_support():
    """Conflict resolver must only override LLM non-flight when route support is strong."""
    plugin = ex.GoogleFlightsPlugin()
    weak = plugin.resolve_scope_conflict(
        candidate_source="llm",
        vlm_non_flight=False,
        llm_non_flight=True,
        deterministic_flight_evidence=True,
        route_bind_support="weak",
        vlm_affirms_flight=True,
        price_grounded=True,
        ctx={},
        llm_page_class="irrelevant_page",
        llm_trip_product="unknown",
    )
    assert weak["resolved"] is False
    strong = plugin.resolve_scope_conflict(
        candidate_source="llm",
        vlm_non_flight=False,
        llm_non_flight=True,
        deterministic_flight_evidence=True,
        route_bind_support="strong",
        vlm_affirms_flight=True,
        price_grounded=True,
        ctx={},
        llm_page_class="irrelevant_page",
        llm_trip_product="unknown",
    )
    assert strong["resolved"] is True


def test_route_bind_gate_blocks_high_confidence_when_unbound(monkeypatch):
    """High-confidence candidate must be blocked when route bound is false."""

    def _threshold(key, default=None):
        values = {
            "scenario_route_bind_gate_enabled": True,
            "scenario_route_bind_gate_requires_strong": True,
            "scenario_route_bind_fail_closed_on_mismatch": True,
        }
        return values.get(key, default)

    monkeypatch.setattr(ex, "get_threshold", _threshold)
    out = ex._apply_google_route_bind_gate(  # noqa: SLF001
        {
            "price": 25986.0,
            "currency": "JPY",
            "confidence": "high",
            "source": "plugin_html_llm",
            "reason": "ok",
        },
        html="<html></html>",
        site="google_flights",
        origin="HND",
        dest="ITM",
        depart="2026-03-01",
        return_date="2026-03-08",
        screenshot_path=None,
        verdict_getter=lambda: {
            "route_bound": False,
            "support": "none",
            "source": "dom",
            "reason": "no_evidence",
            "observed": {},
        },
    )
    assert out["price"] is None
    assert out["reason"] == "route_not_bound"


def test_route_bind_gate_weak_support_downgrades_when_strong_not_required(monkeypatch):
    """Weak bound candidates should downgrade confidence when weak is allowed."""

    def _threshold(key, default=None):
        values = {
            "scenario_route_bind_gate_enabled": True,
            "scenario_route_bind_gate_requires_strong": False,
            "scenario_route_bind_fail_closed_on_mismatch": True,
        }
        return values.get(key, default)

    monkeypatch.setattr(ex, "get_threshold", _threshold)
    out = ex._apply_google_route_bind_gate(  # noqa: SLF001
        {
            "price": 25986.0,
            "currency": "JPY",
            "confidence": "medium",
            "source": "llm",
            "reason": "ok",
        },
        html="<html></html>",
        site="google_flights",
        origin="HND",
        dest="ITM",
        depart="2026-03-01",
        return_date="2026-03-08",
        screenshot_path=None,
        verdict_getter=lambda: {
            "route_bound": True,
            "support": "weak",
            "source": "dom",
            "reason": "weak_evidence",
            "observed": {},
        },
    )
    assert out["price"] == 25986.0
    assert out["confidence"] == "low"


def test_route_bind_gate_rejects_weak_support_when_strong_required(monkeypatch):
    """Weak support should be rejected for non-low confidence when strong is required."""

    def _threshold(key, default=None):
        values = {
            "scenario_route_bind_gate_enabled": True,
            "scenario_route_bind_gate_requires_strong": True,
            "scenario_route_bind_fail_closed_on_mismatch": True,
        }
        return values.get(key, default)

    monkeypatch.setattr(ex, "get_threshold", _threshold)
    out = ex._apply_google_route_bind_gate(  # noqa: SLF001
        {
            "price": 25986.0,
            "currency": "JPY",
            "confidence": "medium",
            "source": "llm",
            "reason": "ok",
        },
        html="<html></html>",
        site="google_flights",
        origin="HND",
        dest="ITM",
        depart="2026-03-01",
        return_date="2026-03-08",
        screenshot_path=None,
        verdict_getter=lambda: {
            "route_bound": True,
            "support": "weak",
            "source": "dom",
            "reason": "weak_evidence",
            "observed": {},
        },
    )
    assert out["price"] is None
    assert out["reason"] == "route_not_bound"
