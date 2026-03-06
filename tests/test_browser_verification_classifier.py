from core.browser.verification_challenges import (
    classify_verification_challenge_multiclass,
    get_verification_protection_method_groups,
)
from core.scenario_runner.skyscanner.interstitials import detect_skyscanner_interstitial_block


def test_verification_protection_method_groups_have_expected_classes():
    groups = get_verification_protection_method_groups()
    assert isinstance(groups, dict)
    assert "interstitial_press_hold" in groups
    assert "text_captcha" in groups
    assert "checkbox_captcha" in groups
    assert "puzzle_captcha" in groups
    assert "turnstile_challenge" in groups
    assert "javascript_challenge" in groups
    assert "cookie_requirement_interstitial" in groups
    assert "access_denied_block" in groups
    assert "queue_waiting_room" in groups
    assert "no_protection" in groups
    assert len(groups) == 10


def test_classify_verification_challenge_detects_press_hold_interstitial():
    html = """
    <html><body>
      <div id="px-captcha"></div>
      <h1>Are you a person or a robot?</h1>
      <button>Press & Hold</button>
    </body></html>
    """
    out = classify_verification_challenge_multiclass(html_text=html, use_vision_light=False)
    assert out["protector_label"] == "interstitial_press_hold"
    assert "press-and-hold" in out["solution"]


def test_classify_verification_challenge_detects_text_captcha():
    html = """
    <html><body>
      <img alt="captcha image"/>
      <p>Please type the characters you see in the image.</p>
    </body></html>
    """
    out = classify_verification_challenge_multiclass(html_text=html, use_vision_light=False)
    assert out["protector_label"] == "text_captcha"
    assert "characters" in out["solution"]


def test_classify_verification_challenge_detects_checkbox_when_widget_markers_present():
    html = """
    <html><body>
      <div class="g-recaptcha" data-sitekey="demo"></div>
      <label>I'm not a robot</label>
    </body></html>
    """
    out = classify_verification_challenge_multiclass(html_text=html, use_vision_light=False)
    assert out["protector_label"] == "checkbox_captcha"
    assert "checkbox" in out["solution"]


def test_classify_verification_challenge_detects_cookie_requirement():
    html = """
    <html><body>
      <section>Still having problems accessing the page?</section>
      <section>Try checking you have JavaScript and cookies turned on.</section>
    </body></html>
    """
    out = classify_verification_challenge_multiclass(html_text=html, use_vision_light=False)
    assert out["protector_label"] == "cookie_requirement_interstitial"
    assert "cookies" in out["solution"]


def test_classify_verification_challenge_defaults_to_no_protection():
    out = classify_verification_challenge_multiclass(html_text="<html><body>normal search page</body></html>", use_vision_light=False)
    assert out["protector_label"] == "no_protection"
    assert out["solution"] == "no verification protection detected"


def test_classify_verification_challenge_ignores_recaptcha_token_on_route_results_surface():
    html = """
    <html><body>
      <main data-testid="day-view">Results</main>
      <script>window.__STATE__={updatedPriceAmount:27860, pageName:"day-view"}</script>
      <a href="/transport/flights/fuk/hnd/260502/260608/">route</a>
      <script src="https://www.google.com/recaptcha/api.js"></script>
    </body></html>
    """
    out = classify_verification_challenge_multiclass(html_text=html, use_vision_light=False)
    assert out["protector_label"] == "no_protection"
    assert out["solution"] == "no verification protection detected"


def test_skyscanner_interstitial_uses_multiclass_classifier_evidence():
    html = """
    <html><head><title>Skyscanner</title></head>
    <body>
      <script src="/captcha.js"></script>
      <div id="px-captcha"></div>
      <h1>Are you a person or a robot?</h1>
    </body></html>
    """
    out = detect_skyscanner_interstitial_block(html)
    assert out["reason"] == "blocked_interstitial_captcha"
    assert out["block_type"] == "captcha"
    classifier = out["evidence"]["verification.classifier"]
    assert classifier["protector_label"] == "interstitial_press_hold"
    assert "press-and-hold" in classifier["solution"]


def test_skyscanner_interstitial_detection_ignores_results_surface_with_recaptcha_reference_only():
    html = """
    <html><body>
      <main data-testid="day-view">Results</main>
      <script>window.__STATE__={updatedPriceAmount:27860, pageName:"day-view"}</script>
      <a href="/transport/flights/fuk/hnd/260502/260608/">route</a>
      <script src="https://www.google.com/recaptcha/api.js"></script>
    </body></html>
    """
    out = detect_skyscanner_interstitial_block(html)
    assert out == {}
