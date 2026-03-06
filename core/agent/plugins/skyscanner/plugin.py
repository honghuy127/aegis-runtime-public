from __future__ import annotations

from typing import Any, Callable, Dict, Optional

from core.agent.plugins.base import RunContext, ServicePlugin
from core.agent.plugins.skyscanner.actions import minimal_wait_actions
from core.agent.plugins.skyscanner.objects import objects_minimal
from core.agent.types import Confidence, Observation
from core.plugins.services.skyscanner import classify_skyscanner_page, extract_price_from_html


def _extract_price_with_runtime_pipeline(
    *,
    html: str,
    ctx: RunContext,
) -> Dict[str, Any]:
    """Call the shared extractor entrypoint used by runtime paths."""
    from core.extractor import extract_price

    inputs = ctx.inputs or {}
    return extract_price(
        html=html,
        site="skyscanner",
        task="price",
        origin=inputs.get("origin"),
        dest=inputs.get("dest"),
        depart=inputs.get("depart"),
        return_date=inputs.get("return_date"),
        page_url=ctx.url,
    )


class SkyscannerPlugin(ServicePlugin):
    """Minimal Skyscanner agent plugin for deeplink/open + extraction readiness."""

    service_key = "skyscanner"

    def objects(self, ctx: RunContext):
        return objects_minimal(locale=getattr(ctx, "locale", "") or "")

    def action_catalog(self, ctx: RunContext):
        return minimal_wait_actions(locale=getattr(ctx, "locale", "") or "")

    def dom_probe(self, html: str, ctx: RunContext) -> Observation:
        info = classify_skyscanner_page(html or "", page_url=ctx.url)
        obs = Observation()
        page_kind = str(info.get("page_kind", "unknown") or "unknown")
        if page_kind == "flights_results":
            obs.page_class = "flights_results"
            obs.trip_product = "flights"
            obs.confidence = Confidence.medium
        elif page_kind == "irrelevant_page":
            obs.page_class = "irrelevant_page"
            obs.trip_product = "unknown"
            obs.confidence = Confidence.low
        elif page_kind == "flight_page_loading":
            obs.page_class = "flights_form"
            obs.trip_product = "flights"
            obs.confidence = Confidence.low
        else:
            obs.page_class = "unknown"
            obs.trip_product = "unknown"
            obs.confidence = Confidence.low
        obs.route_bound = None
        obs.reason = str(info.get("reason", "") or "")
        return obs

    def readiness(self, obs: Observation, ctx: RunContext) -> bool:
        _ = ctx
        return obs.page_class == "flights_results"

    def attempt_extraction(
        self,
        html: str,
        ctx: RunContext,
        *,
        extraction_fn: Optional[Callable[..., Dict[str, Any]]] = None,
    ) -> Dict[str, Any]:
        """Attempt shared extractor first, then fixture-safe HTML parser fallback."""
        html_text = html or ""
        classification = classify_skyscanner_page(html_text, page_url=ctx.url)
        strategies_attempted = []

        shared_out: Dict[str, Any] = {}
        shared_extract = extraction_fn
        if shared_extract is None:
            shared_extract = lambda **kwargs: _extract_price_with_runtime_pipeline(**kwargs)
        try:
            strategies_attempted.append("core.extractor.extract_price")
            shared_out = shared_extract(html=html_text, ctx=ctx) or {}
        except Exception:
            shared_out = {}

        if isinstance(shared_out, dict) and shared_out.get("price") is not None:
            price_raw = shared_out.get("price")
            try:
                price_value = int(float(price_raw))
            except Exception:
                price_value = None
            if price_value is not None:
                return {
                    "ok": True,
                    "price": price_value,
                    "currency": str(shared_out.get("currency") or "").upper() or "USD",
                    "reason_code": None,
                    "page_kind": classification.get("page_kind", "unknown"),
                    "extraction_strategy": "core.extractor.extract_price",
                    "evidence": {
                        "url": ctx.url,
                        "html_len": len(html_text),
                        "page_kind": classification.get("page_kind", "unknown"),
                        "extraction_strategy_attempted": "core.extractor.extract_price",
                        "gating_decisions": {"shared_extractor_returned_price": True},
                    },
                }

        parser_out = extract_price_from_html(html_text, page_url=ctx.url)
        parser_evidence = dict(parser_out.get("evidence") or {})
        strategies_attempted.append(str(parser_out.get("extraction_strategy") or ""))
        parser_evidence["strategies_attempted"] = [s for s in strategies_attempted if s]
        parser_evidence.setdefault("page_kind", classification.get("page_kind", "unknown"))
        parser_evidence.setdefault(
            "gating_decisions",
            {"shared_extractor_returned_price": False},
        )
        parser_out["evidence"] = parser_evidence
        return parser_out
