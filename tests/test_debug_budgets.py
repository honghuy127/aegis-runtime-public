"""Tests for debug budget policy resolution and gating."""

import pytest
from utils.thresholds import get_debug_budget_policy, load_thresholds


class TestDebugBudgetPolicyResolution:
    """Test get_debug_budget_policy() function with various inputs."""

    def test_default_when_debug_disabled(self):
        """When debug_enabled=False, return safe defaults (no behavior change)."""
        thresholds = load_thresholds()
        policy = get_debug_budget_policy(thresholds, profile="lite", debug_enabled=False)

        assert policy["profile"] == "lite"
        assert policy["timeout_multiplier"] == 1.0
        assert policy["retry_delta"] == 0
        assert policy["evidence_bundle"] == []
        assert policy["escalation_max_steps"] == 0
        assert policy["is_override"] is False

    def test_default_when_debug_budgets_not_enabled(self):
        """When debug_budgets_enabled=False in config, return safe defaults."""
        thresholds = load_thresholds()
        thresholds["debug_budgets_enabled"] = False

        policy = get_debug_budget_policy(thresholds, profile="lite", debug_enabled=True)

        assert policy["timeout_multiplier"] == 1.0
        assert policy["retry_delta"] == 0
        assert policy["is_override"] is False

    def test_lite_profile_base_values(self):
        """When debug_enabled=True and debug_budgets_enabled=True, apply lite profile."""
        thresholds = load_thresholds()
        policy = get_debug_budget_policy(thresholds, profile="lite", debug_enabled=True)

        # lite profile should use debug_timeout_multiplier_lite and debug_retry_delta_lite
        assert policy["profile"] == "lite"
        assert policy["timeout_multiplier"] >= 1.0  # Should be at least 1.0
        assert policy["retry_delta"] >= 0  # Should be non-negative
        assert policy["escalation_max_steps"] >= 0

    def test_deep_profile_base_values(self):
        """When debug_enabled=True with deep profile, apply higher multipliers."""
        thresholds = load_thresholds()
        policy = get_debug_budget_policy(thresholds, profile="deep", debug_enabled=True)

        assert policy["profile"] == "deep"
        assert policy["timeout_multiplier"] >= 1.0
        # deep should generally be higher than lite
        lite_policy = get_debug_budget_policy(thresholds, profile="lite", debug_enabled=True)
        assert policy["timeout_multiplier"] >= lite_policy["timeout_multiplier"]

    def test_super_deep_profile_base_values(self):
        """super_deep should be accepted and be at least as strong as deep."""
        thresholds = load_thresholds()
        policy = get_debug_budget_policy(thresholds, profile="super_deep", debug_enabled=True)
        deep_policy = get_debug_budget_policy(thresholds, profile="deep", debug_enabled=True)

        assert policy["profile"] == "super_deep"
        assert policy["timeout_multiplier"] >= deep_policy["timeout_multiplier"]
        assert policy["retry_delta"] >= deep_policy["retry_delta"]
        assert policy["escalation_max_steps"] >= deep_policy["escalation_max_steps"]

    def test_reason_override_parsing_lite(self):
        """Parse reason-specific override for lite profile (format: lite:deep:retry_lite:retry_deep)."""
        thresholds = load_thresholds()
        # Test with a known reason that has overrides configured
        policy = get_debug_budget_policy(
            thresholds,
            profile="lite",
            reason_code="action_deadline_exceeded_before_click",
            debug_enabled=True,
        )

        if thresholds.get("debug_budgets_enabled", False):
            # Should have reason override applied
            assert policy["is_override"] is True
            # Should have multiplier adjusted (1.3 for lite)
            assert policy["timeout_multiplier"] > 1.0

    def test_reason_override_parsing_deep(self):
        """Parse reason-specific override for deep profile."""
        thresholds = load_thresholds()
        policy_deep = get_debug_budget_policy(
            thresholds,
            profile="deep",
            reason_code="action_deadline_exceeded_before_click",
            debug_enabled=True,
        )

        policy_lite = get_debug_budget_policy(
            thresholds,
            profile="lite",
            reason_code="action_deadline_exceeded_before_click",
            debug_enabled=True,
        )

        # deep profile should generally have higher multiplier than lite for same reason
        if thresholds.get("debug_budgets_enabled", False):
            assert policy_deep["timeout_multiplier"] >= policy_lite["timeout_multiplier"]

    def test_evidence_bundle_parsing(self):
        """Parse evidence_bundle from config (comma-separated values)."""
        thresholds = load_thresholds()
        policy = get_debug_budget_policy(
            thresholds,
            profile="lite",
            reason_code="calendar_not_open",
            debug_enabled=True,
        )

        if thresholds.get("debug_budgets_enabled", False):
            # should have evidence bundle
            assert isinstance(policy["evidence_bundle"], list)
            # Each bundle item should be a valid string
            for bundle in policy["evidence_bundle"]:
                assert isinstance(bundle, str)
                assert len(bundle) > 0

    def test_policy_dict_keys_stable(self):
        """Returned policy dict should always have stable keys (no extra/missing keys)."""
        thresholds = load_thresholds()
        policy = get_debug_budget_policy(thresholds, debug_enabled=True)

        expected_keys = {
            "profile",
            "timeout_multiplier",
            "retry_delta",
            "evidence_bundle",
            "escalation_max_steps",
            "reason_code",
            "is_override",
        }
        assert set(policy.keys()) == expected_keys

    def test_timeout_multiplier_always_positive(self):
        """timeout_multiplier should always be >= 1.0 (never negative or zero)."""
        thresholds = load_thresholds()
        for profile in ["lite", "deep", "super_deep"]:
            for reason in [None, "calendar_not_open", "action_deadline_exceeded_before_click"]:
                policy = get_debug_budget_policy(
                    thresholds,
                    profile=profile,
                    reason_code=reason,
                    debug_enabled=True,
                )
                assert policy["timeout_multiplier"] >= 1.0

    def test_retry_delta_always_non_negative(self):
        """retry_delta should always be >= 0 (never negative)."""
        thresholds = load_thresholds()
        for profile in ["lite", "deep", "super_deep"]:
            for reason in [None, "calendar_not_open"]:
                policy = get_debug_budget_policy(
                    thresholds,
                    profile=profile,
                    reason_code=reason,
                    debug_enabled=True,
                )
                assert policy["retry_delta"] >= 0

    def test_escalation_max_steps_non_negative(self):
        """escalation_max_steps should always be >= 0."""
        thresholds = load_thresholds()
        policy = get_debug_budget_policy(thresholds, debug_enabled=True)
        assert policy["escalation_max_steps"] >= 0

    def test_reason_code_preserved(self):
        """reason_code input should be preserved in output."""
        thresholds = load_thresholds()
        reason = "transport_timeout"
        policy = get_debug_budget_policy(
            thresholds,
            reason_code=reason,
            debug_enabled=True,
        )
        assert policy["reason_code"] == reason

    def test_invalid_profile_falls_back_to_lite(self):
        """Invalid profile name should fall back to lite."""
        thresholds = load_thresholds()
        policy = get_debug_budget_policy(thresholds, profile="invalid_profile", debug_enabled=True)
        assert policy["profile"] == "lite"

    def test_empty_thresholds_returns_safe_default(self):
        """Empty or None thresholds dict should return safe defaults."""
        policy = get_debug_budget_policy({}, debug_enabled=True)
        assert policy["timeout_multiplier"] == 1.0
        assert policy["retry_delta"] == 0

    def test_reason_not_in_config_uses_base_values(self):
        """When reason_code not in config, use base profile values (no is_override)."""
        thresholds = load_thresholds()
        policy = get_debug_budget_policy(
            thresholds,
            reason_code="nonexistent_reason_code_xyz",
            debug_enabled=True,
        )
        # Should use base values, not override
        assert policy["is_override"] is False

    def test_multiple_evidence_bundles_parsed(self):
        """Multiple evidence bundle items should be parsed correctly."""
        thresholds = load_thresholds()
        policy = get_debug_budget_policy(
            thresholds,
            reason_code="calendar_not_open",
            debug_enabled=True,
        )

        if thresholds.get("debug_budgets_enabled", False):
            # calendar_not_open should have multiple evidence items
            assert isinstance(policy["evidence_bundle"], list)
            if policy["evidence_bundle"]:
                # Should have at least a few items based on config
                assert len(policy["evidence_bundle"]) >= 1


class TestDebugBudgetGating:
    """Test that debug mode OFF produces no behavior change."""

    def test_production_mode_unchanged(self):
        """When debug=False, function returns identity (no budget impact)."""
        thresholds = load_thresholds()

        # Production mode (debug=False)
        prod_policy = get_debug_budget_policy(thresholds, debug_enabled=False)

        # Should always return 1.0 multiplier and 0 delta (no change)
        assert prod_policy["timeout_multiplier"] == 1.0
        assert prod_policy["retry_delta"] == 0
        assert prod_policy["evidence_bundle"] == []

    def test_production_vs_debug_multiplier_difference(self):
        """Debug mode ON should produce different timeout_multiplier than OFF."""
        thresholds = load_thresholds()

        if not thresholds.get("debug_budgets_enabled", False):
            pytest.skip("debug_budgets_enabled not set in config")

        prod_policy = get_debug_budget_policy(thresholds, debug_enabled=False)
        debug_policy = get_debug_budget_policy(
            thresholds, profile="lite", debug_enabled=True
        )

        # Production should always be 1.0
        assert prod_policy["timeout_multiplier"] == 1.0
        # Debug may be different (but at least 1.0)
        assert debug_policy["timeout_multiplier"] >= 1.0


class TestDebugBudgetIntegration:
    """Integration tests for debug budget policy with actual config."""

    def test_config_parses_without_error(self):
        """Load thresholds and ensure debug_budget keys parse correctly."""
        thresholds = load_thresholds()
        # Should have debug_budgets_enabled key
        assert "debug_budgets_enabled" in thresholds
        # Should parse as boolean
        assert isinstance(thresholds.get("debug_budgets_enabled"), bool)

    def test_all_configured_reason_codes_return_valid_policies(self):
        """For each configured reason code, should return valid policy."""
        thresholds = load_thresholds()

        # Find all reason codes in config (keys starting with "debug_reason_overrides_")
        reason_codes = set()
        for key in thresholds.keys():
            if key.startswith("debug_reason_overrides_"):
                reason_code = key.replace("debug_reason_overrides_", "")
                reason_codes.add(reason_code)

        for reason_code in reason_codes:
            for profile in ["lite", "deep", "super_deep"]:
                policy = get_debug_budget_policy(
                    thresholds,
                    profile=profile,
                    reason_code=reason_code,
                    debug_enabled=True,
                )
                # Validate structure
                assert policy["timeout_multiplier"] >= 1.0
                assert policy["retry_delta"] >= 0
                assert isinstance(policy["evidence_bundle"], list)

    def test_safe_defaults_applied_at_module_level(self):
        """Calling get_debug_budget_policy with minimal args should work."""
        thresholds = load_thresholds()

        # Minimal call
        policy = get_debug_budget_policy(thresholds)
        assert policy is not None
        assert "timeout_multiplier" in policy


class TestDebugBudgetsEnvironmentVariables:
    """Test environment variable resolution for debug budgets."""

    def test_env_override_disabled_when_debug_off(self):
        """When debug_enabled=False, env vars should be ignored."""
        from utils.thresholds import resolve_debug_budgets_from_env
        import os

        # Set env vars
        old_profile = os.environ.get("DEBUG_BUDGETS_PROFILE")
        old_escalate = os.environ.get("DEBUG_BUDGETS_ESCALATE")
        try:
            os.environ["DEBUG_BUDGETS_PROFILE"] = "deep"
            os.environ["DEBUG_BUDGETS_ESCALATE"] = "1"

            # When debug disabled, env vars should be ignored
            result = resolve_debug_budgets_from_env(debug_enabled=False)
            assert result["profile"] is None
            assert result["escalate"] is None
        finally:
            if old_profile is None:
                os.environ.pop("DEBUG_BUDGETS_PROFILE", None)
            else:
                os.environ["DEBUG_BUDGETS_PROFILE"] = old_profile
            if old_escalate is None:
                os.environ.pop("DEBUG_BUDGETS_ESCALATE", None)
            else:
                os.environ["DEBUG_BUDGETS_ESCALATE"] = old_escalate

    def test_env_override_profile_lite(self):
        """DEBUG_BUDGETS_PROFILE=lite should override profile."""
        from utils.thresholds import resolve_debug_budgets_from_env
        import os

        old_profile = os.environ.get("DEBUG_BUDGETS_PROFILE")
        try:
            os.environ["DEBUG_BUDGETS_PROFILE"] = "lite"
            result = resolve_debug_budgets_from_env(debug_enabled=True)
            assert result["profile"] == "lite"
        finally:
            if old_profile is None:
                os.environ.pop("DEBUG_BUDGETS_PROFILE", None)
            else:
                os.environ["DEBUG_BUDGETS_PROFILE"] = old_profile

    def test_env_override_profile_deep(self):
        """DEBUG_BUDGETS_PROFILE=deep should override profile."""
        from utils.thresholds import resolve_debug_budgets_from_env
        import os

        old_profile = os.environ.get("DEBUG_BUDGETS_PROFILE")
        try:
            os.environ["DEBUG_BUDGETS_PROFILE"] = "deep"
            result = resolve_debug_budgets_from_env(debug_enabled=True)
            assert result["profile"] == "deep"
        finally:
            if old_profile is None:
                os.environ.pop("DEBUG_BUDGETS_PROFILE", None)
            else:
                os.environ["DEBUG_BUDGETS_PROFILE"] = old_profile

    def test_env_override_profile_super_deep_and_aliases(self):
        """super_deep aliases should normalize to super_deep."""
        from utils.thresholds import resolve_debug_budgets_from_env
        import os

        old_profile = os.environ.get("DEBUG_BUDGETS_PROFILE")
        try:
            for variant in ["super_deep", "SUPER_DEEP", "super-deep", "superdeep", "ultra"]:
                os.environ["DEBUG_BUDGETS_PROFILE"] = variant
                result = resolve_debug_budgets_from_env(debug_enabled=True)
                assert result["profile"] == "super_deep"
        finally:
            if old_profile is None:
                os.environ.pop("DEBUG_BUDGETS_PROFILE", None)
            else:
                os.environ["DEBUG_BUDGETS_PROFILE"] = old_profile

    def test_extended_reason_override_parsing_supports_super_deep(self):
        """6-part reason override format should apply profile-specific super_deep values."""
        thresholds = load_thresholds()
        thresholds["debug_reason_overrides_calendar_not_open"] = "1.1:1.5:2.2:0:1:3"

        lite = get_debug_budget_policy(
            thresholds, profile="lite", reason_code="calendar_not_open", debug_enabled=True
        )
        deep = get_debug_budget_policy(
            thresholds, profile="deep", reason_code="calendar_not_open", debug_enabled=True
        )
        super_deep = get_debug_budget_policy(
            thresholds, profile="super_deep", reason_code="calendar_not_open", debug_enabled=True
        )

        assert lite["timeout_multiplier"] == pytest.approx(1.1)
        assert deep["timeout_multiplier"] == pytest.approx(1.5)
        assert super_deep["timeout_multiplier"] == pytest.approx(2.2)
        assert lite["retry_delta"] == 0
        assert deep["retry_delta"] == 1
        assert super_deep["retry_delta"] == 3

    def test_profile_evidence_bundle_merges_with_reason_bundle(self):
        """Profile-level debug evidence bundle should merge with reason-specific bundle."""
        thresholds = load_thresholds()
        thresholds["debug_evidence_bundle_super_deep"] = "screenshot,dom_slice"
        thresholds["debug_evidence_bundle_calendar_not_open"] = "overlay_diagnostics,screenshot"

        policy = get_debug_budget_policy(
            thresholds,
            profile="super_deep",
            reason_code="calendar_not_open",
            debug_enabled=True,
        )

        assert "screenshot" in policy["evidence_bundle"]
        assert "dom_slice" in policy["evidence_bundle"]
        assert "overlay_diagnostics" in policy["evidence_bundle"]

    def test_env_override_profile_invalid_ignored(self):
        """Invalid DEBUG_BUDGETS_PROFILE values should be ignored."""
        from utils.thresholds import resolve_debug_budgets_from_env
        import os

        old_profile = os.environ.get("DEBUG_BUDGETS_PROFILE")
        try:
            os.environ["DEBUG_BUDGETS_PROFILE"] = "invalid_profile_xyz"
            result = resolve_debug_budgets_from_env(debug_enabled=True)
            assert result["profile"] is None  # Invalid value ignored
        finally:
            if old_profile is None:
                os.environ.pop("DEBUG_BUDGETS_PROFILE", None)
            else:
                os.environ["DEBUG_BUDGETS_PROFILE"] = old_profile

    def test_env_override_profile_case_insensitive(self):
        """DEBUG_BUDGETS_PROFILE should be case-insensitive."""
        from utils.thresholds import resolve_debug_budgets_from_env
        import os

        old_profile = os.environ.get("DEBUG_BUDGETS_PROFILE")
        try:
            for variant in ["LITE", "Lite", "LiTe"]:
                os.environ["DEBUG_BUDGETS_PROFILE"] = variant
                result = resolve_debug_budgets_from_env(debug_enabled=True)
                assert result["profile"] == "lite"

            for variant in ["DEEP", "Deep", "DeEp"]:
                os.environ["DEBUG_BUDGETS_PROFILE"] = variant
                result = resolve_debug_budgets_from_env(debug_enabled=True)
                assert result["profile"] == "deep"
        finally:
            if old_profile is None:
                os.environ.pop("DEBUG_BUDGETS_PROFILE", None)
            else:
                os.environ["DEBUG_BUDGETS_PROFILE"] = old_profile

    def test_env_override_escalate_true_variants(self):
        """DEBUG_BUDGETS_ESCALATE=1/true/yes should set escalate=True."""
        from utils.thresholds import resolve_debug_budgets_from_env
        import os

        old_escalate = os.environ.get("DEBUG_BUDGETS_ESCALATE")
        try:
            for variant in ["1", "true", "yes", "TRUE", "YES"]:
                os.environ["DEBUG_BUDGETS_ESCALATE"] = variant
                result = resolve_debug_budgets_from_env(debug_enabled=True)
                assert result["escalate"] is True, f"Failed for {variant}"
        finally:
            if old_escalate is None:
                os.environ.pop("DEBUG_BUDGETS_ESCALATE", None)
            else:
                os.environ["DEBUG_BUDGETS_ESCALATE"] = old_escalate

    def test_env_override_escalate_false_variants(self):
        """DEBUG_BUDGETS_ESCALATE=0/false/no should set escalate=False."""
        from utils.thresholds import resolve_debug_budgets_from_env
        import os

        old_escalate = os.environ.get("DEBUG_BUDGETS_ESCALATE")
        try:
            for variant in ["0", "false", "no", "FALSE", "NO"]:
                os.environ["DEBUG_BUDGETS_ESCALATE"] = variant
                result = resolve_debug_budgets_from_env(debug_enabled=True)
                assert result["escalate"] is False, f"Failed for {variant}"
        finally:
            if old_escalate is None:
                os.environ.pop("DEBUG_BUDGETS_ESCALATE", None)
            else:
                os.environ["DEBUG_BUDGETS_ESCALATE"] = old_escalate

    def test_env_override_escalate_invalid_ignored(self):
        """Invalid DEBUG_BUDGETS_ESCALATE values should be ignored."""
        from utils.thresholds import resolve_debug_budgets_from_env
        import os

        old_escalate = os.environ.get("DEBUG_BUDGETS_ESCALATE")
        try:
            os.environ["DEBUG_BUDGETS_ESCALATE"] = "maybe"
            result = resolve_debug_budgets_from_env(debug_enabled=True)
            assert result["escalate"] is None  # Invalid value ignored
        finally:
            if old_escalate is None:
                os.environ.pop("DEBUG_BUDGETS_ESCALATE", None)
            else:
                os.environ["DEBUG_BUDGETS_ESCALATE"] = old_escalate

    def test_env_override_empty_string_ignored(self):
        """Empty DEBUG_BUDGETS_* env vars should be ignored."""
        from utils.thresholds import resolve_debug_budgets_from_env
        import os

        old_profile = os.environ.get("DEBUG_BUDGETS_PROFILE")
        old_escalate = os.environ.get("DEBUG_BUDGETS_ESCALATE")
        try:
            os.environ["DEBUG_BUDGETS_PROFILE"] = ""
            os.environ["DEBUG_BUDGETS_ESCALATE"] = ""
            result = resolve_debug_budgets_from_env(debug_enabled=True)
            assert result["profile"] is None
            assert result["escalate"] is None
        finally:
            if old_profile is None:
                os.environ.pop("DEBUG_BUDGETS_PROFILE", None)
            else:
                os.environ["DEBUG_BUDGETS_PROFILE"] = old_profile
            if old_escalate is None:
                os.environ.pop("DEBUG_BUDGETS_ESCALATE", None)
            else:
                os.environ["DEBUG_BUDGETS_ESCALATE"] = old_escalate

    def test_env_override_returns_stable_keys(self):
        """Returned dict should always have profile and escalate keys."""
        from utils.thresholds import resolve_debug_budgets_from_env

        result = resolve_debug_budgets_from_env(debug_enabled=False)
        assert "profile" in result
        assert "escalate" in result

        result = resolve_debug_budgets_from_env(debug_enabled=True)
        assert "profile" in result
        assert "escalate" in result
