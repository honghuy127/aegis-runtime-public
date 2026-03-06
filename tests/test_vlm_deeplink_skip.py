"""Tests for VLM page_kind deferral optimization for Google Flights deeplinks."""

import pytest
from unittest.mock import Mock, patch, MagicMock
from core.service_runners.google_flights import _is_google_flights_deeplink

pytestmark = [pytest.mark.vlm]


class TestDeeplinkDetection:
    """Test Google Flights deeplink pattern detection."""

    def test_detects_google_flights_deeplink(self):
        """Verify that Google Flights deeplink pattern is detected."""
        urls = [
            "https://www.google.com/travel/flights#flt=TYO.NRT/2024-12-20",
            "https://google.com/travel/flights#flt=LAX.JFK/2024-12-20",
            "https://www.google.co.jp/travel/flights#flt=HND.NRT/2024-12-20",
            "https://GOOGLE.COM/TRAVEL/FLIGHTS#FLT=NRT.HND/2024-12-20",
        ]
        for url in urls:
            assert _is_google_flights_deeplink(url), f"Should detect deeplink: {url}"

    def test_rejects_non_deeplink_urls(self):
        """Verify that non-deeplink URLs are rejected."""
        urls = [
            "https://www.google.com/travel/flights",
            "https://www.google.com/search?q=flights",
            "https://example.com/flights#flt=LAX.JFK",
            "",
            None,
        ]
        for url in urls:
            assert not _is_google_flights_deeplink(url), f"Should not detect deeplink: {url}"

    def test_rejects_non_string_input(self):
        """Verify that non-string inputs are rejected safely."""
        inputs = [None, 123, 45.67, [], {}, True]
        for inp in inputs:
            assert not _is_google_flights_deeplink(inp)


class TestVLMSkipStrategy:
    """Test VLM skip strategy for deeplinks."""

    def test_deeplink_activates_skip_strategy(self):
        """Verify that deeplink URLs would skip VLM on first pass."""
        test_cases = [
            {
                "url": "https://www.google.com/travel/flights#flt=TYO.NRT/2024-12-20",
                "turn": 1,
                "attempt": 1,
                "should_skip": True,
            },
            {
                "url": "https://www.google.com/travel/flights#flt=LAX.JFK/2024-12-20",
                "turn": 1,
                "attempt": 2,
                "should_skip": False,  # attempt > 1, should not skip
            },
            {
                "url": "https://www.google.com/travel/flights#flt=NRT.HND/2024-12-20",
                "turn": 2,
                "attempt": 1,
                "should_skip": False,  # turn > 1, should not skip
            },
            {
                "url": "https://www.google.com",
                "turn": 1,
                "attempt": 1,
                "should_skip": False,  # not a deeplink
            },
        ]

        for case in test_cases:
            url = case["url"]
            turn = case["turn"]
            attempt = case["attempt"]
            should_skip = case["should_skip"]

            # Simulate the skip logic
            is_deeplink = _is_google_flights_deeplink(url)
            skip_vlm_first_pass = (
                turn == 1
                and attempt == 1
                and is_deeplink
            )

            assert skip_vlm_first_pass == should_skip, (
                f"URL={url}, turn={turn}, attempt={attempt}; "
                f"expected skip={should_skip}, got skip={skip_vlm_first_pass}"
            )

    def test_non_deeplink_does_not_skip_vlm(self):
        """Verify that non-deeplink URLs don't trigger VLM skip."""
        url = "https://www.google.com/search?q=flights"
        skip_vlm_first_pass = (
            1 == 1  # turn == 1
            and 1 == 1  # attempt == 1
            and _is_google_flights_deeplink(url)
        )
        assert not skip_vlm_first_pass


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
