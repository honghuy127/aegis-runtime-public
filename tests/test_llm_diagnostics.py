"""Tests for runtime LLM error diagnostics classification."""

import pytest

from llm.code_model import _classify_llm_error

pytestmark = [pytest.mark.llm, pytest.mark.heavy]


def test_classify_llm_error_token_cap():
    exc = RuntimeError("LLM request failed [token_cap] after fallbacks")
    assert _classify_llm_error(exc) == "token_cap"


def test_classify_llm_error_timeout():
    exc = RuntimeError("ReadTimeout: ... timeout_budget_exhausted")
    assert _classify_llm_error(exc) == "timeout"


def test_classify_llm_error_circuit_open():
    exc = RuntimeError("LLM request failed [circuit_open]: retry after 10s")
    assert _classify_llm_error(exc) == "circuit_open"
