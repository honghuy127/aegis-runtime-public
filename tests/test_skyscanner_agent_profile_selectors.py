from core.agent.plugins.base import RunContext
from core.agent.plugins.skyscanner.plugin import SkyscannerPlugin


def _ctx(locale: str) -> RunContext:
    return RunContext(
        site_key="skyscanner",
        url="https://www.skyscanner.com/flights",
        locale=locale,
        region="US",
        currency="USD",
        is_domestic=False,
        inputs={"origin": "HND", "dest": "ITM", "depart": "2026-03-01"},
    )


def test_skyscanner_objects_and_actions_use_profile_wait_selectors():
    plugin = SkyscannerPlugin()
    objs = plugin.objects(_ctx("en-US"))
    actions = plugin.action_catalog(_ctx("en-US"))

    main_obj = next(obj for obj in objs if obj.role == "main")
    wait_action = next(action for action in actions if action.action_id == "wait_main")

    assert main_obj.selector_families
    assert wait_action.selectors
    assert main_obj.selector_families == wait_action.selectors
    assert "[role='main']" in wait_action.selectors
    assert "body" in wait_action.selectors
