from core.route_binding import classify_google_deeplink_page_state_recovery_reason


def test_classify_google_deeplink_page_state_recovery_reason_irrelevant_page():
    out = classify_google_deeplink_page_state_recovery_reason(
        "rebind_unready_non_flight_scope_irrelevant_page"
    )
    assert out["eligible"] is True
    assert out["canonical_reason"] == "non_flight_scope_irrelevant_page"
    assert out["scope_class"] == "irrelevant_page"


def test_classify_google_deeplink_page_state_recovery_reason_noneligible():
    out = classify_google_deeplink_page_state_recovery_reason(
        "rebind_unready_missing_contextual_price_card"
    )
    assert out["eligible"] is False
    assert out["canonical_reason"] == ""
