import core.browser.session as browser


def test_remaining_timeout_floor_applies(monkeypatch):
    floor_ms = max(1, int(browser.DEFAULT_PLAYWRIGHT_ATTEMPT_TIMEOUT_FLOOR_MS))
    base = 1000.0
    deadline = base + max(0.001, (floor_ms - 10) / 1000.0)

    monkeypatch.setattr(browser.time, "monotonic", lambda: base)
    remaining = browser.BrowserSession._remaining_timeout_ms(deadline)
    assert remaining >= floor_ms


def test_remaining_timeout_returns_floor_when_expired(monkeypatch):
    """When deadline is exceeded, should return floor value (not 0) to avoid timeout=0."""
    floor_ms = max(1, int(browser.DEFAULT_PLAYWRIGHT_ATTEMPT_TIMEOUT_FLOOR_MS))
    base = 2000.0
    monkeypatch.setattr(browser.time, "monotonic", lambda: base + 5.0)
    remaining = browser.BrowserSession._remaining_timeout_ms(base)
    assert remaining == floor_ms