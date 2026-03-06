from core.browser.manual_intervention_policy import (
    is_skyscanner_px_captcha_url,
    is_verification_url,
    manual_intervention_diagnostic_selectors,
    should_mark_manual_observation_complete,
)


def test_is_verification_url_detects_generic_challenge_markers():
    assert is_verification_url("https://example.com/captcha?x=1") is True
    assert is_verification_url("https://example.com/interstitial/check") is True
    assert is_verification_url("https://example.com/sorry/index") is True


def test_is_verification_url_ignores_regular_results_urls():
    assert is_verification_url("https://www.google.com/travel/flights?hl=en&gl=JP") is False
    assert is_verification_url("https://www.google.com/travel/flights/search?tfs=abc") is False


def test_is_skyscanner_px_captcha_url_specific_detection():
    assert (
        is_skyscanner_px_captcha_url(
            "https://www.skyscanner.com/sttc/px/captcha-v2/index.html?url=L2ZsaWdodHM/"
        )
        is True
    )
    assert is_skyscanner_px_captcha_url("https://www.google.com/travel/flights") is False


def test_should_mark_manual_observation_complete_requires_non_challenge_url():
    ui_capture = {"event_count": 220, "direct_event_count": 120}
    assert (
        should_mark_manual_observation_complete(
            intervention_mode="demo",
            ui_capture=ui_capture,
            before_url="https://www.google.com/travel/flights?hl=en&gl=JP",
            after_url="https://example.com/captcha",
        )
        is False
    )


def test_should_mark_manual_observation_complete_accepts_high_signal_non_challenge():
    ui_capture = {"event_count": 260, "direct_event_count": 140}
    assert (
        should_mark_manual_observation_complete(
            intervention_mode="demo",
            ui_capture=ui_capture,
            before_url="https://www.google.com/travel/flights?hl=en&gl=JP",
            after_url="https://www.google.com/travel/flights/search?tfs=abc",
            challenge_token_changes=0,
            challenge_signature_changes=0,
        )
        is True
    )


def test_should_mark_manual_observation_complete_accepts_captcha_token_churn_when_target_closed():
    ui_capture = {"event_count": 0, "direct_event_count": 0}
    assert (
        should_mark_manual_observation_complete(
            intervention_mode="demo",
            ui_capture=ui_capture,
            before_url="https://www.skyscanner.com/sttc/px/captcha-v2/index.html?x=1",
            after_url="https://www.skyscanner.com/sttc/px/captcha-v2/index.html?x=1",
            challenge_token_changes=2,
            challenge_signature_changes=1,
        )
        is True
    )


def test_should_mark_manual_observation_complete_rejects_token_only_churn_on_same_captcha_surface():
    ui_capture = {"event_count": 8, "direct_event_count": 0, "proxy_event_count": 8}
    assert (
        should_mark_manual_observation_complete(
            intervention_mode="demo",
            ui_capture=ui_capture,
            before_url="https://www.skyscanner.com/sttc/px/captcha-v2/index.html?x=1",
            after_url="https://www.skyscanner.com/sttc/px/captcha-v2/index.html?x=1",
            challenge_token_changes=2,
            challenge_signature_changes=0,
        )
        is False
    )


def test_manual_intervention_diagnostic_selectors_contains_generic_and_site_specific():
    base = manual_intervention_diagnostic_selectors("")
    sky = manual_intervention_diagnostic_selectors("skyscanner")
    assert "#px-captcha" in base
    assert "iframe[title*='Human verification' i]" in base
    assert "iframe[src*='px-cloud.net']" in sky
    assert len(sky) >= len(base)
