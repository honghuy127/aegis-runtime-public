"""Stage-1 tests for plugin interface registry wiring."""

from core.plugins.interfaces import ExtractionStrategy, LLMProvider, ServicePlugin
from core.plugins.registry import (
    get_plugin,
    get_provider,
    get_service,
    get_strategy,
    list_provider_plugins,
    list_service_plugins,
    list_strategy_plugins,
)


def test_default_plugin_registries_are_populated():
    """Registry should expose default service/strategy/provider entries."""
    services = list_service_plugins()
    strategies = list_strategy_plugins()
    providers = list_provider_plugins()
    assert "google_flights" in services
    assert "html_llm" in strategies
    assert "default" in providers


def test_registry_entries_conform_to_protocols():
    """Default plugins should satisfy runtime-checkable protocols."""
    assert isinstance(get_service("google_flights"), ServicePlugin)
    assert isinstance(get_strategy("html_llm"), ExtractionStrategy)
    assert isinstance(get_provider("default"), LLMProvider)
    plugin = get_service("google_flights")
    assert callable(plugin.url_candidates)
    assert callable(plugin.scenario_profile)
    assert callable(plugin.readiness_probe)
    assert callable(plugin.extraction_hints)
    for key in ("html_llm", "vlm_image", "vlm_multimodal"):
        strategy = get_strategy(key)
        assert callable(strategy.strategy_key)
        assert strategy.key == strategy.strategy_key()


def test_unknown_plugin_lookup_raises_key_error():
    """Unknown keys should fail fast."""
    try:
        get_strategy("not-a-real-strategy")
        assert False, "expected KeyError"
    except KeyError:
        pass


def test_generic_get_plugin_dispatch():
    """Generic dispatch helper should map to concrete registries."""
    assert get_plugin("service", "google_flights").key == "google_flights"
    assert get_plugin("strategy", "html_llm").key == "html_llm"
    assert get_plugin("provider", "default").key == "default"
