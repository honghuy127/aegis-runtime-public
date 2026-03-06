from core.agent.plugins.base import RunContext
from core.agent.plugins.google_flights.plugin import GoogleFlightsPlugin
from core.agent.types import Confidence, Observation


def _ctx(locale: str) -> RunContext:
    return RunContext(
        site_key="google_flights",
        url="https://www.google.com/travel/flights",
        locale=locale,
        region="JP",
        currency="JPY",
        is_domestic=True,
        inputs={"origin": "HND", "dest": "ITM", "depart": "2026-03-01"},
    )


def test_objects_use_profile_locale_order_for_origin_field():
    plugin = GoogleFlightsPlugin()
    ja_objects = plugin.objects(_ctx("ja-JP"))
    en_objects = plugin.objects(_ctx("en-US"))

    ja_origin = next(obj for obj in ja_objects if obj.role == "origin")
    en_origin = next(obj for obj in en_objects if obj.role == "origin")

    assert ja_origin.selector_families
    assert en_origin.selector_families
    assert "出発地" in ja_origin.selector_families[0]
    assert "From" in en_origin.selector_families[0]
    # Cross-locale fallback should still be present for non-English locale.
    assert any("From" in s for s in ja_origin.selector_families)
    assert not any("出発地" in s for s in en_origin.selector_families)


def test_action_catalog_uses_profile_selectors_for_dest_and_search():
    plugin = GoogleFlightsPlugin()
    actions = plugin.action_catalog(_ctx("en-US"))
    by_id = {a.action_id: a for a in actions}

    fill_dest = by_id["fill_dest"]
    submit = by_id["submit_search"]

    assert fill_dest.selectors
    assert any("To" in s or "Destination" in s for s in fill_dest.selectors)
    assert not any("目的地" in s for s in fill_dest.selectors)

    assert submit.selectors
    # Profile search selectors include both EN and JA plus semantic submit fallback.
    assert any("Search" in s for s in submit.selectors)
    assert any("button[type='submit']" == s for s in submit.selectors)


def test_route_bind_confidence_uses_shared_aliases_for_weak_match(monkeypatch):
    plugin = GoogleFlightsPlugin()

    def _fake_aliases(code, provider):  # noqa: ARG001
        table = {
            "HND": {"HND", "Tokyo", "東京"},
            "ITM": {"ITM", "Osaka", "大阪"},
        }
        return table.get(str(code or "").upper(), {str(code or "").upper()})

    monkeypatch.setattr(
        "core.agent.plugins.google_flights.plugin.get_airport_aliases_for_provider",
        _fake_aliases,
    )

    obs = Observation(
        page_class="flights_results",
        fields={"origin": "Tokyo", "dest": "Osaka", "depart": "2026-03-01"},
    )
    confidence = plugin.route_bind_confidence(obs, _ctx("en-US"))
    assert confidence == Confidence.medium


def test_parse_route_fields_detects_alias_tokens_from_shared_store(monkeypatch):
    plugin = GoogleFlightsPlugin()

    def _fake_aliases(code, provider):  # noqa: ARG001
        table = {
            "HND": {"HND", "Tokyo", "東京"},
            "ITM": {"ITM", "Osaka", "大阪"},
        }
        return table.get(str(code or "").upper(), {str(code or "").upper()})

    monkeypatch.setattr(
        "core.agent.plugins.google_flights.plugin.get_airport_aliases_for_provider",
        _fake_aliases,
    )

    html = "<html><body><div>From Tokyo</div><div>To Osaka</div><div>2026-03-01</div></body></html>"
    fields = plugin._parse_route_fields(html, _ctx("en-US"))  # noqa: SLF001
    assert fields.get("origin") == "Tokyo"
    assert fields.get("dest") == "Osaka"
