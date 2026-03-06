import pytest

from llm.language_signals import detect_ui_language
from llm.code_model import build_ui_language_hint_block

pytestmark = [pytest.mark.llm, pytest.mark.heavy]


def test_detect_ui_language_html_lang_ja():
    assert detect_ui_language("<html lang='ja-JP'>", "") == ("ja", "html_lang")


def test_detect_ui_language_kana_without_lang():
    assert detect_ui_language("<div>あア</div>", "") == ("ja", "kana")


def test_detect_ui_language_locale_fallback():
    assert detect_ui_language("<div>Hello</div>", "en-US") == ("en", "locale")


@pytest.mark.parametrize("site", ["google_flights", "skyscanner"])
def test_site_rule_in_prompt_for_bilingual_sites(site):
    block, _, _ = build_ui_language_hint_block(html="", mimic_locale="", site=site)
    assert "SITE_RULE" in block


def test_site_rule_not_in_prompt_for_other_sites():
    block, _, _ = build_ui_language_hint_block(html="", mimic_locale="", site="expedia")
    assert "SITE_RULE" not in block
