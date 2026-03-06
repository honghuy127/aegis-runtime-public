"""LLMProvider adapter over existing llm.llm_client.call_llm."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from llm.llm_client import call_llm


@dataclass(frozen=True)
class OllamaLLMProvider:
    """Default provider adapter that delegates to `call_llm`."""

    key: str = "default"

    def call(self, prompt: str, model: str, **kwargs: Any) -> str:
        """Forward call to existing LLM client."""
        return call_llm(prompt=prompt, model=model, **kwargs)


def build_default_provider_plugins() -> dict:
    """Build default provider mapping."""
    return {"default": OllamaLLMProvider()}

