"""Skyscanner adapter implementation - agent-only minimal deeplink/extract path."""

from __future__ import annotations

from typing import Any, Dict, Optional

from core.agent.plugins.base import RunContext
from core.agent.plugins.skyscanner.plugin import SkyscannerPlugin
from core.site_adapter import (
    SiteAdapter,
    SiteAdapterBindResult,
    SiteAdapterReadinessResult,
)


class SkyscannerAgentAdapter(SiteAdapter):
    """Minimal agent-first adapter for Skyscanner deeplink/open + extraction."""

    site_id = "skyscanner"

    def __init__(self):
        self._plugin = SkyscannerPlugin()
        self._ctx: Optional[RunContext] = None
        self._url = ""
        self._last_html = ""
        self._last_evidence: Dict[str, Any] = {}
        self._last_extraction: Dict[str, Any] = {}
        self._ready = False

    def bind_route(
        self,
        browser: Any,
        url: str,
        origin: str,
        dest: str,
        depart: str,
        return_date: Optional[str] = None,
        **kwargs,
    ) -> SiteAdapterBindResult:
        try:
            self._url = str(url or "")
            browser.goto(self._url)
            self._last_html = self._safe_content(browser)
            self._ctx = RunContext(
                site_key=self.site_id,
                url=self._url,
                locale=kwargs.get("mimic_locale", "") or "",
                region=kwargs.get("mimic_region", "") or "",
                currency=kwargs.get("mimic_currency", "") or "",
                is_domestic=bool(kwargs.get("is_domestic", False)),
                inputs={
                    "origin": origin,
                    "dest": dest,
                    "depart": depart,
                    "return_date": return_date or "",
                },
            )
            wait_evidence = self._bounded_wait_for_main(browser)
            self._last_evidence = {
                "url": self._url,
                "html_len": len(self._last_html or ""),
                "wait_main": wait_evidence,
            }
            return SiteAdapterBindResult(success=True, evidence=dict(self._last_evidence))
        except Exception as exc:
            evidence = {
                "url": str(url or ""),
                "html_len": len(self._last_html or ""),
                "gating_decisions": {"bind_stage": "goto"},
            }
            self._last_evidence = evidence
            return SiteAdapterBindResult(
                success=False,
                reason="agent_bind_failed",
                error_class=type(exc).__name__,
                error_message=str(exc)[:200],
                evidence=evidence,
            )

    def ensure_results_ready(
        self,
        browser: Any,
        html: Optional[str] = None,
    ) -> SiteAdapterReadinessResult:
        if self._ctx is None:
            return SiteAdapterReadinessResult(
                ready=False,
                reason="engine_not_initialized",
                evidence={"url": self._url, "html_len": 0, "gating_decisions": {"bound": False}},
            )

        html_text = html if isinstance(html, str) else self._safe_content(browser)
        self._last_html = html_text or self._last_html

        obs = self._plugin.dom_probe(self._last_html or "", self._ctx)
        page_kind = obs.page_class or "unknown"
        if not self._plugin.readiness(obs, self._ctx):
            evidence = {
                "url": self._url,
                "html_len": len(self._last_html or ""),
                "page_kind": page_kind,
                "extraction_strategy_attempted": "skipped_not_ready",
                "gating_decisions": {
                    "ready": False,
                    "probe_reason": obs.reason or "",
                },
            }
            self._last_evidence = evidence
            return SiteAdapterReadinessResult(
                ready=False,
                reason=obs.reason or "results_not_ready",
                evidence=evidence,
            )

        extraction = self._plugin.attempt_extraction(self._last_html or "", self._ctx)
        self._last_extraction = dict(extraction or {})

        if extraction.get("ok") and extraction.get("price") is not None:
            self._ready = True
            self._last_evidence = dict(extraction.get("evidence") or {})
            return SiteAdapterReadinessResult(
                ready=True,
                evidence=dict(self._last_evidence),
            )

        evidence = dict(extraction.get("evidence") or {})
        evidence.setdefault("url", self._url)
        evidence.setdefault("html_len", len(self._last_html or ""))
        evidence.setdefault("page_kind", page_kind)
        evidence.setdefault(
            "gating_decisions",
            {"ready": True, "extraction_attempted": True},
        )
        self._last_evidence = evidence
        return SiteAdapterReadinessResult(
            ready=False,
            reason=str(extraction.get("reason_code") or "missing_price"),
            evidence=evidence,
        )

    def capture_artifacts(self) -> Dict[str, Any]:
        return {
            "url": self._url,
            "last_html_available": bool(self._last_html),
            "ready": self._ready,
            "evidence": dict(self._last_evidence),
            "extraction": dict(self._last_extraction),
        }

    def _safe_content(self, browser: Any) -> str:
        try:
            html = browser.content()
            return html if isinstance(html, str) else ""
        except Exception:
            return ""

    def _bounded_wait_for_main(self, browser: Any) -> Dict[str, Any]:
        selectors = ["[role='main']", "main", "body"]
        attempts = 0
        for selector in selectors[:3]:
            attempts += 1
            try:
                if hasattr(browser, "wait_for"):
                    browser.wait_for(selector, timeout_ms=1200)
                    return {"ok": True, "selector_used": selector, "attempts": attempts}
            except Exception:
                continue
        return {"ok": False, "selector_used": "", "attempts": attempts}
