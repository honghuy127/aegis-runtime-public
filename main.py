"""CLI entrypoint for multi-service flight price extraction runs."""

import argparse
import json
import logging
import os
import signal
import traceback
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Dict, List, Optional
from urllib.parse import parse_qs, urlparse

from core.alerts import build_alert_message, dispatch_alert, evaluate_alert
from core.alerts_config import load_alerts_config
from core.extractor import extract_price, looks_package_bundle_page
from core.flight_plan import resolve_flight_plan
from core.run_input_config import load_run_input_config
from core.scenario_runner import run_agentic_scenario
from core.services import (
    is_supported_service,
    service_name,
    service_url_candidates,
    url_matches_service_domain,
)
from core.services_config import load_services_config
from core.plugins.services.google_flights import (
    build_google_flights_deeplink,
    extract_price_from_html as extract_google_flights_price_from_html,
)
from core.plugins.adapters.services_adapter import plugin_strategy_enabled
from storage.knowledge_store import get_knowledge, record_package_url_hint
from storage.adaptive_policy import (
    apply_runtime_profile_env,
    record_service_outcome,
    recommend_runtime_profile,
)
from storage.maintenance import enforce_storage_limits
from storage.runs import get_last_price_record, init_db, save_run
from llm.llm_client import release_touched_ollama_models, reset_llm_circuit_state
from utils.evidence import write_service_evidence_checkpoint
from utils.thresholds import (
    get_threshold,
    get_thresholds_for_profile,
    reset_active_threshold_profile,
    set_active_threshold_profile,
)
from utils.run_episode import (
    RunEpisode,
    cleanup_old_runs,
    ensure_run_id,
    should_capture_artifacts,
)
from utils.run_paths import get_artifacts_dir, get_run_dir

# Module-level logger for structured output.
logger = logging.getLogger(__name__)


class ScenarioHardTimeout(TimeoutError):
    """Raised when the hard wall-clock timeout expires for one scenario."""


def _setup_logging():
    """Configure logging with structured format."""
    level_name = str(os.getenv("FLIGHT_WATCHER_LOG_LEVEL", "INFO") or "INFO").strip().upper()
    level_value = getattr(logging, level_name, logging.INFO)
    if not isinstance(level_value, int):
        level_value = logging.INFO
    logging.basicConfig(
        level=level_value,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
        handlers=[
            logging.StreamHandler(),
        ],
    )


def _parse_args():
    """Parse command-line arguments for one multi-service run."""
    parser = argparse.ArgumentParser(
        description="Run flight-price extraction across one or more booking services.",
    )
    parser.add_argument("--origin", help="Origin IATA code (e.g. HND)")
    parser.add_argument("--dest", help="Destination IATA code (e.g. ITM)")
    parser.add_argument("--depart", help="Departure date in YYYY-MM-DD")
    parser.add_argument("--return-date", help="Return date in YYYY-MM-DD")
    domestic_group = parser.add_mutually_exclusive_group()
    domestic_group.add_argument(
        "--domestic",
        dest="is_domestic",
        action="store_true",
        help="Prefer domestic-flight mode on sites with domestic/international split.",
    )
    domestic_group.add_argument(
        "--international",
        dest="is_domestic",
        action="store_false",
        help="Prefer international-flight mode on sites with domestic/international split.",
    )
    parser.set_defaults(is_domestic=None)
    parser.add_argument(
        "--max-trip-price",
        type=float,
        help="Optional max acceptable price for the full trip (per run override).",
    )
    parser.add_argument(
        "--max-transit",
        type=int,
        help="Optional maximum allowed transit/stops count (0 = nonstop).",
    )
    parser.add_argument(
        "--trip-type",
        choices=("one_way", "round_trip"),
        default=None,
        help="Trip type for scenario input; falls back to config when omitted.",
    )
    parser.add_argument(
        "--plan-file",
        help="Optional JSON file with flight inputs (origin/dest/depart/return_date/trip_type/is_domestic/max_transit).",
    )
    parser.add_argument(
        "--services-config",
        default="configs/services.yaml",
        help="Path to services config file (default: configs/services.yaml).",
    )
    parser.add_argument(
        "--services",
        help="Optional comma-separated service keys overriding config enabled_services.",
    )
    parser.add_argument(
        "--knowledge-user",
        help="Knowledge namespace key (email or GitHub ID) for shared learning.",
    )
    parser.add_argument(
        "--task",
        default=None,
        help="Extraction task label; falls back to input config or 'price'.",
    )
    parser.add_argument(
        "--save-html",
        action="store_true",
        help="Persist one HTML snapshot per service under storage/.",
    )
    llm_mode_group = parser.add_mutually_exclusive_group()
    llm_mode_group.add_argument(
        "--light-mode",
        dest="llm_mode",
        action="store_const",
        const="light",
        help="Use light LLM decoding profile (smaller num_predict / lower resource usage).",
    )
    llm_mode_group.add_argument(
        "--full-mode",
        dest="llm_mode",
        action="store_const",
        const="full",
        help="Use full LLM decoding profile (default).",
    )
    parser.set_defaults(llm_mode=None)
    parser.add_argument(
        "--multimodal-mode",
        "--agentic-multimodal-mode",
        dest="agentic_multimodal_mode",
        choices=("off", "assist", "primary", "judge", "judge_primary"),
        default=None,
        help=(
            "Multimodal extraction mode override. "
            "off=disable multimodal; "
            "assist=late VLM fallback; "
            "primary=earlier multimodal attempt; "
            "judge=assist + code-model verification gate; "
            "judge_primary=primary + code-model verification gate (default)."
        ),
    )
    mimic_group = parser.add_mutually_exclusive_group()
    mimic_group.add_argument(
        "--human-mimic",
        dest="human_mimic",
        action="store_true",
        help="Use slower human-like browser interactions to reduce anti-bot friction.",
    )
    mimic_group.add_argument(
        "--no-human-mimic",
        dest="human_mimic",
        action="store_false",
        help="Disable human-like interaction pacing.",
    )
    parser.set_defaults(human_mimic=None)
    parser.add_argument(
        "--input-config",
        default="configs/run.yaml",
        help="Path to run-input defaults file used when CLI params are missing.",
    )
    parser.add_argument("--mimic-locale", help="Browser locale, e.g. ja-JP or en-US.")
    parser.add_argument(
        "--mimic-timezone",
        help="Browser timezone id, e.g. Asia/Tokyo (UTC+9).",
    )
    parser.add_argument("--mimic-currency", help="Currency hint code, e.g. JPY.")
    parser.add_argument("--mimic-region", help="Region/country hint, e.g. JP.")
    parser.add_argument(
        "--mimic-latitude",
        type=float,
        help="Geolocation latitude for browser context.",
    )
    parser.add_argument(
        "--mimic-longitude",
        type=float,
        help="Geolocation longitude for browser context.",
    )
    parser.add_argument(
        "--alerts-config",
        default="configs/alerts.yaml",
        help="Path to alerts config file (default: configs/alerts.yaml).",
    )
    parser.add_argument(
        "--disable-alerts",
        action="store_true",
        help="Disable notifications for this run regardless of config.",
    )
    parser.add_argument(
        "--debug",
        action="store_true",
        help="Enable debug mode: create run episode folder with rich logs + artifacts.",
    )
    parser.add_argument(
        "--debug-dir",
        default="storage/runs",
        help="Base directory for debug run folders (default: storage/runs).",
    )
    parser.add_argument(
        "--debug-keep",
        type=int,
        default=0,
        help="Keep only last N debug run folders (0 = keep all).",
    )
    parser.add_argument(
        "--run-id",
        help="Optional run ID for debug mode (auto-generated if not provided).",
    )
    return parser.parse_args()


def _split_csv(text: str) -> List[str]:
    """Split comma-separated values into normalized tokens."""
    return [token.strip().lower() for token in text.split(",") if token.strip()]


def _resolve_services(args) -> Dict[str, object]:
    """Resolve selected services and URL map from config and optional override."""
    cfg = load_services_config(args.services_config)
    selected = list(cfg["enabled_services"])
    if args.services:
        selected = _split_csv(args.services)

    if not selected:
        raise ValueError("No services selected; check enabled_services in config.")

    unknown = [key for key in selected if not is_supported_service(key)]
    if unknown:
        raise ValueError(f"Unsupported service keys: {unknown}")

    return {
        "selected_services": selected,
        "service_urls": cfg["service_urls"],
        "service_url_hints": cfg.get("service_url_hints", {}),
    }


def _coalesce(preferred, fallback):
    """Return preferred value unless it is None/empty-string."""
    if preferred is None:
        return fallback
    if isinstance(preferred, str) and preferred.strip() == "":
        return fallback
    return preferred


def _resolve_runtime_args(args) -> Dict[str, object]:
    """Merge CLI options with run config defaults (CLI takes precedence)."""
    cfg = load_run_input_config(args.input_config)
    thresholds_profile = "debug" if args.debug else cfg.get("thresholds_profile", "default")
    profile_thresholds = get_thresholds_for_profile(str(thresholds_profile))
    llm_mode = _coalesce(getattr(args, "llm_mode", None), cfg.get("llm_mode"))
    llm_mode = llm_mode if llm_mode in ("full", "light") else "full"
    multimodal_mode = _coalesce(
        getattr(args, "agentic_multimodal_mode", None),
        cfg.get("agentic_multimodal_mode"),
    )
    if multimodal_mode not in ("off", "assist", "primary", "judge", "judge_primary"):
        multimodal_mode = "judge_primary"
    scenario_candidate_timeout_sec = int(
        profile_thresholds.get(
            "scenario_candidate_timeout_sec",
            get_threshold("scenario_candidate_timeout_sec", 120),
        )
    )
    scenario_candidate_timeout_sec = _env_int(
        "FLIGHT_WATCHER_SCENARIO_CANDIDATE_TIMEOUT_SEC",
        scenario_candidate_timeout_sec,
    )
    disable_http2_retry_timeout_sec = int(
        profile_thresholds.get(
            "scenario_disable_http2_retry_timeout_sec",
            get_threshold("scenario_disable_http2_retry_timeout_sec", 45),
        )
    )
    return {
        "origin": _coalesce(args.origin, cfg.get("origin")),
        "dest": _coalesce(args.dest, cfg.get("dest")),
        "depart": _coalesce(args.depart, cfg.get("depart")),
        "return_date": _coalesce(args.return_date, cfg.get("return_date")),
        "trip_type": _coalesce(args.trip_type, cfg.get("trip_type")),
        "is_domestic": args.is_domestic
        if args.is_domestic is not None
        else bool(cfg.get("is_domestic")),
        "max_trip_price": args.max_trip_price
        if args.max_trip_price is not None
        else cfg.get("max_trip_price"),
        "max_transit": args.max_transit
        if args.max_transit is not None
        else cfg.get("max_transit"),
        "plan_file": _coalesce(args.plan_file, cfg.get("plan_file")),
        "knowledge_user": _coalesce(args.knowledge_user, cfg.get("knowledge_user")),
        "task": _coalesce(args.task, _coalesce(cfg.get("task"), "price")),
        "save_html": bool(args.save_html or cfg.get("save_html", False)),
        "debug_save_service_html": bool(cfg.get("debug_save_service_html", True)),
        "llm_mode": llm_mode,
        "agentic_multimodal_mode": multimodal_mode,
        "scenario_candidate_timeout_sec": max(15, scenario_candidate_timeout_sec),
        "scenario_disable_http2_retry_timeout_sec": max(10, disable_http2_retry_timeout_sec),
        "human_mimic": args.human_mimic
        if args.human_mimic is not None
        else bool(cfg.get("human_mimic")),
        "mimic_locale": _coalesce(args.mimic_locale, cfg.get("mimic_locale")),
        "mimic_timezone": _coalesce(args.mimic_timezone, cfg.get("mimic_timezone")),
        "mimic_currency": _coalesce(args.mimic_currency, cfg.get("mimic_currency")),
        "mimic_region": _coalesce(args.mimic_region, cfg.get("mimic_region")),
        "mimic_latitude": args.mimic_latitude
        if args.mimic_latitude is not None
        else cfg.get("mimic_latitude"),
        "mimic_longitude": args.mimic_longitude
        if args.mimic_longitude is not None
        else cfg.get("mimic_longitude"),
        "google_flights_bootstrap_mode": _google_flights_bootstrap_mode(
            os.getenv(
                "FLIGHT_WATCHER_GOOGLE_FLIGHTS_BOOTSTRAP_MODE",
                cfg.get("google_flights_bootstrap_mode"),
            )
        ),
        "disable_alerts": bool(args.disable_alerts or cfg.get("disable_alerts", False)),
        "thresholds_profile": thresholds_profile,
        "trips": cfg.get("trips") or [],
        "debug": bool(args.debug or os.environ.get("DEBUG_RUN") == "1"),
        "debug_dir": args.debug_dir,
        "debug_keep": args.debug_keep,
        "run_id": ensure_run_id(args.run_id if hasattr(args, 'run_id') else None),
    }


def _selector_used_from_result(result: Dict[str, object]) -> str:
    """Extract persisted selector hint string from result payload."""
    hint = result.get("selector_hint")
    if isinstance(hint, dict) and isinstance(hint.get("css"), str):
        return hint["css"]
    return ""


def _save_service_html_debug(
    *,
    html: str,
    run_id: str,
    service_key: str,
    trip_index: int,
    suffix: str,
) -> Path:
    """Persist one rolling debug HTML snapshot per service/trip."""
    out_dir = get_artifacts_dir(run_id)
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"last_{service_key}_trip{trip_index}_{suffix}.html"
    path.write_text(html, encoding="utf-8")
    return path


def _save_service_error_debug(
    *,
    run_id: str,
    service_key: str,
    trip_index: int,
    candidate_url: str,
    error: str,
) -> Path:
    """Persist rolling + timestamped error metadata per service/trip."""
    out_dir = get_artifacts_dir(run_id)
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%d_%H%M%S")
    path = out_dir / f"last_{service_key}_trip{trip_index}_error.json"
    hist_path = out_dir / f"{stamp}_{service_key}_trip{trip_index}_error.json"
    payload = {
        "service": service_key,
        "trip_index": trip_index,
        "url": candidate_url,
        "error": error,
    }
    body = json.dumps(payload, indent=2, ensure_ascii=False)
    path.write_text(body, encoding="utf-8")
    hist_path.write_text(body, encoding="utf-8")

    # If scenario runner emitted a rolling HTML snapshot, keep a timestamped copy too.
    for stage in ("goto_error", "attempt_error", "last", "initial"):
        src = out_dir / f"scenario_{service_key}_{stage}.html"
        if not src.exists():
            continue
        dst = out_dir / f"{stamp}_{service_key}_trip{trip_index}_{stage}.html"
        try:
            dst.write_text(src.read_text(encoding="utf-8"), encoding="utf-8")
        except Exception:
            pass
        png_src = out_dir / f"scenario_{service_key}_{stage}.png"
        if png_src.exists():
            png_dst = out_dir / f"{stamp}_{service_key}_trip{trip_index}_{stage}.png"
            try:
                png_dst.write_bytes(png_src.read_bytes())
            except Exception:
                pass
    return path


def _last_service_visual_path(service_key: str, *, run_id: str) -> Path:
    """Return canonical last visual snapshot path emitted by scenario runner."""
    return get_artifacts_dir(run_id) / f"scenario_{service_key}_last.png"


def _google_flights_deep_link(
    *,
    origin: str,
    dest: str,
    depart: str,
    return_date: str = None,
    trip_type: str = "one_way",
    currency: str = "",
    locale: str = "",
    region: str = "",
    base_url: str = "https://www.google.com/travel/flights#e:1;sd:1;t:f",
) -> str:
    """Build a route-specific Google Flights URL to reduce UI interaction drift."""

    return build_google_flights_deeplink(
        {
            "origin": origin,
            "dest": dest,
            "depart": depart,
            "return_date": return_date,
            "trip_type": trip_type,
        },
        {
            "mimic_locale": locale,
            "mimic_region": region,
            "mimic_currency": currency,
        },
        base_url=base_url,
    )


def _google_flights_simple_bootstrap_url(
    *,
    base_url: str,
    locale: str = "",
    region: str = "",
) -> str:
    """Build a stable Google Flights bootstrap URL without route fragment state."""
    base = str(base_url or "https://www.google.com/travel/flights").strip() or "https://www.google.com/travel/flights"
    parsed = urlparse(base)
    clean = parsed._replace(query="", fragment="")
    base_query = parse_qs(parsed.query or "")
    lang_override = str(os.getenv("FLIGHT_WATCHER_GOOGLE_FLIGHTS_BOOTSTRAP_LANG", "") or "").strip()
    base_hl = str((base_query.get("hl") or [None])[0] or "").strip()
    # Decouple page display language from mimic locale by default.
    # Google Flights remains more stable/triageable in English while geolocation stays in `gl`.
    lang_source = lang_override or base_hl or "en"
    lang = lang_source.split("-", 1)[0].lower() or "en"
    base_gl = str((base_query.get("gl") or [None])[0] or "").strip()
    reg = str(region or "").strip().upper() or base_gl.upper()
    query_parts = [f"hl={lang}"]
    if reg:
        query_parts.append(f"gl={reg}")
    return f"{clean.geturl()}?{'&'.join(query_parts)}"


def _google_flights_bootstrap_mode(raw_mode: object) -> str:
    """Normalize Google Flights bootstrap policy mode."""
    mode = str(raw_mode or "").strip().lower().replace("-", "_")
    if mode in {"simple_only", "simple_first", "deeplink_first"}:
        return mode
    return "simple_only"


def _order_google_flights_url_candidates(
    *,
    url_candidates: List[str],
    service_url: str,
    origin: str,
    dest: str,
    depart: str,
    return_date: Optional[str],
    trip_type: str,
    currency: str,
    locale: str,
    region: str,
    bootstrap_mode: str,
) -> List[str]:
    """Order Google Flights candidates using simple bootstrap by default."""
    mode = _google_flights_bootstrap_mode(bootstrap_mode)
    simple_url = _google_flights_simple_bootstrap_url(
        base_url=service_url,
        locale=locale,
        region=region,
    )
    deep_link = _google_flights_deep_link(
        origin=origin,
        dest=dest,
        depart=depart,
        return_date=return_date,
        trip_type=trip_type,
        currency=currency,
        locale=locale,
        region=region,
        base_url=service_url,
    )
    ordered: List[str] = []

    def _push(url: str) -> None:
        if not url:
            return
        if url not in ordered:
            ordered.append(url)

    if mode == "deeplink_first":
        _push(deep_link)
        _push(simple_url)
    elif mode == "simple_first":
        _push(simple_url)
        _push(deep_link)
    else:
        _push(simple_url)
    for url in url_candidates:
        _push(url)
    return ordered


def _is_google_host(hostname: str) -> bool:
    """Return True for google.* hosts (e.g. google.com, google.co.jp, www.google.co.uk)."""
    host = (hostname or "").strip().lower().strip(".")
    if not host:
        return False
    labels = [part for part in host.split(".") if part]
    if not labels:
        return False
    return "google" in labels


@contextmanager
def _call_timeout(timeout_sec: int):
    """Bound one blocking call on Unix via SIGALRM."""
    seconds = max(0, int(timeout_sec))
    if seconds <= 0 or not hasattr(signal, "SIGALRM"):
        yield
        return

    def _handler(signum, frame):  # noqa: ARG001
        raise ScenarioHardTimeout(f"scenario_hard_timeout_after_{seconds}s")

    try:
        old_handler = signal.getsignal(signal.SIGALRM)
        signal.signal(signal.SIGALRM, _handler)
        signal.setitimer(signal.ITIMER_REAL, float(seconds))
    except Exception:
        # Non-main thread / unsupported runtime: run without hard timeout.
        yield
        return
    try:
        yield
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0.0)
        signal.signal(signal.SIGALRM, old_handler)


def _run_agentic_scenario_with_timeout(*, timeout_sec: int, **kwargs) -> str:
    """Execute one scenario call with a hard wall-clock timeout."""
    start_monotonic = time.monotonic()
    deadline_monotonic = start_monotonic + max(0, float(timeout_sec))
    old_budget = os.environ.get("FLIGHT_WATCHER_SCENARIO_BUDGET_SEC")
    os.environ["FLIGHT_WATCHER_SCENARIO_BUDGET_SEC"] = str(max(1, int(timeout_sec)))
    hard_timeout_raw = os.getenv("FLIGHT_WATCHER_SCENARIO_HARD_TIMEOUT_ENABLED", "0")
    hard_timeout_enabled = str(hard_timeout_raw).strip().lower() in {"1", "true", "yes", "on"}
    try:
        if not hard_timeout_enabled:
            return run_agentic_scenario(**kwargs)
        with _call_timeout(timeout_sec):
            return run_agentic_scenario(**kwargs)
    except ScenarioHardTimeout as exc:
        now_monotonic = time.monotonic()
        elapsed_sec = now_monotonic - start_monotonic
        logger.debug(
            "scenario.candidate_timeout timeout_sec=%s start_monotonic=%.6f now_monotonic=%.6f elapsed_sec=%.3f deadline_monotonic=%.6f",
            int(timeout_sec),
            start_monotonic,
            now_monotonic,
            elapsed_sec,
            deadline_monotonic,
        )
        raise RuntimeError(f"Scenario candidate timeout after {int(timeout_sec)}s") from exc
    except TimeoutError as exc:
        now_monotonic = time.monotonic()
        elapsed_sec = now_monotonic - start_monotonic
        remaining_budget_sec = deadline_monotonic - now_monotonic
        logger.debug(
            "scenario.timeout.foreign type=%s repr=%s msg=%s hard_timeout_enabled=%s elapsed_sec=%.3f remaining_budget_sec=%.3f trace=%s",
            type(exc),
            repr(exc),
            str(exc),
            hard_timeout_enabled,
            elapsed_sec,
            remaining_budget_sec,
            traceback.format_exc(),
        )
        if hard_timeout_enabled:
            if elapsed_sec < (timeout_sec * 0.2) and remaining_budget_sec > (timeout_sec * 0.5):
                logger.warning(
                    "scenario.event.foreign_timeout elapsed_sec=%.3f remaining_budget_sec=%.3f timeout_sec=%d",
                    elapsed_sec,
                    remaining_budget_sec,
                    timeout_sec,
                )
                raise RuntimeError("foreign_timeout") from exc
    finally:
        if old_budget is None:
            os.environ.pop("FLIGHT_WATCHER_SCENARIO_BUDGET_SEC", None)
        else:
            os.environ["FLIGHT_WATCHER_SCENARIO_BUDGET_SEC"] = old_budget


@contextmanager
def _temporary_env(overrides: Dict[str, Optional[str]]):
    """Temporarily set environment variables for one scoped call."""
    old: Dict[str, Optional[str]] = {}
    for key, value in (overrides or {}).items():
        old[key] = os.environ.get(key)
        if value is None:
            os.environ.pop(key, None)
        else:
            os.environ[key] = str(value)
    try:
        yield
    finally:
        for key, value in old.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


def _extract_price_compat(
    html: str,
    *,
    site_key: str,
    task: str,
    origin: str,
    dest: str,
    depart: str,
    return_date: Optional[str],
    screenshot_path: Optional[str],
    page_url: Optional[str],
) -> Dict[str, Any]:
    """Call extract_price with backward compatibility for monkeypatched signatures."""
    try:
        return extract_price(
            html,
            site=site_key,
            task=task,
            origin=origin,
            dest=dest,
            depart=depart,
            return_date=return_date,
            screenshot_path=screenshot_path,
            page_url=page_url,
        )
    except TypeError as exc:
        if "unexpected keyword argument" not in str(exc):
            raise
        return extract_price(
            html,
            site=site_key,
            task=task,
        )


def _env_bool(name: str, default: bool) -> bool:
    """Parse boolean env variable with conservative fallback."""
    raw = os.getenv(name)
    if raw is None:
        return bool(default)
    lowered = raw.strip().lower()
    if lowered in {"1", "true", "yes", "on"}:
        return True
    if lowered in {"0", "false", "no", "off"}:
        return False
    return bool(default)


def _env_int(name: str, default: int) -> int:
    """Parse integer env variable with conservative fallback."""
    raw = os.getenv(name)
    if raw is None:
        return int(default)
    try:
        return int(str(raw).strip())
    except Exception:
        return int(default)


def _selected_extract_strategy_label() -> str:
    """Return high-level extraction strategy label used for current run."""
    if not plugin_strategy_enabled():
        return "legacy"
    router_enabled = _env_bool("FLIGHT_WATCHER_PLUGIN_EXTRACT_ROUTER_ENABLED", True)
    if not router_enabled:
        return "legacy"
    strategy_key = str(
        os.getenv(
            "FLIGHT_WATCHER_EXTRACT_STRATEGY_KEY",
            get_threshold("extract_strategy_plugin_key", "html_llm"),
        )
        or "html_llm"
    ).strip().lower() or "html_llm"
    return f"plugin:{strategy_key}"


def _load_json_dict(path: Path) -> Dict[str, Any]:
    """Best-effort JSON object reader used for local diagnostic artifacts."""
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return dict(raw) if isinstance(raw, dict) else {}


def _scenario_preextract_route_state_v2_result(
    *,
    route_state: Dict[str, Any],
    service_key: str,
) -> Optional[Dict[str, Any]]:
    """Parse standardized scenario->extractor verdict from route_state artifact."""
    if not isinstance(route_state, dict):
        return None
    verdict = route_state.get("scenario_extract_verdict")
    if not isinstance(verdict, dict):
        return None

    verdict_service = str(verdict.get("service", "") or "").strip()
    if verdict_service and verdict_service != str(service_key or "").strip():
        return None

    if not bool(verdict.get("non_actionable")):
        return None

    reason = str(verdict.get("reason", "") or "").strip()
    if not reason:
        return None

    route_bound_raw = verdict.get("route_bound")
    scenario_ready_raw = verdict.get("scenario_ready")
    scope_class = str(verdict.get("scope_class", "") or "").strip()
    route_bind_reason = str(verdict.get("route_bind_reason", "") or "").strip()
    return {
        "price": None,
        "currency": None,
        "confidence": "low",
        "reason": reason,
        "source": "scenario_guard",
        "route_bound": bool(route_bound_raw) if isinstance(route_bound_raw, bool) else False,
        "scenario_ready": (
            bool(scenario_ready_raw) if isinstance(scenario_ready_raw, bool) else False
        ),
        "scope_class": scope_class,
        "route_bind_reason": route_bind_reason,
    }


def _scenario_preextract_route_state_legacy_result(
    *,
    route_state: Dict[str, Any],
    service_key: str,
) -> Optional[Dict[str, Any]]:
    """Legacy route_state pre-extract guard inference (kept for compatibility)."""
    if str(service_key or "").strip() != "google_flights" or not isinstance(route_state, dict):
        return None
    verdict = route_state.get("route_bind_verdict")
    if not isinstance(verdict, dict):
        return None
    route_bound = verdict.get("route_bound")
    verdict_reason = str(verdict.get("reason", "") or "").strip().lower()
    scope_verdicts = route_state.get("scope_verdicts")
    scenario_return_summary = route_state.get("scenario_return_summary")
    scope_final = ""
    if isinstance(scope_verdicts, dict):
        scope_final = str(scope_verdicts.get("final", "") or "").strip().lower()
    scenario_ready = None
    scenario_reason = ""
    if isinstance(scenario_return_summary, dict):
        scenario_ready = scenario_return_summary.get("ready")
        scenario_reason = str(scenario_return_summary.get("reason", "") or "").strip().lower()
    if route_bound is False and (
        verdict_reason == "explicit_mismatch"
        or verdict_reason.startswith("scope_non_flight_")
        or scope_final == "irrelevant_page"
    ):
        return {
            "price": None,
            "currency": None,
            "confidence": "low",
            "reason": "google_route_context_unbound",
            "source": "scenario_guard",
            "route_bound": False,
            "scenario_ready": False,
        }
    if (
        route_bound is False
        and scenario_ready is False
        and scenario_reason == "retries_exhausted"
    ):
        return {
            "price": None,
            "currency": None,
            "confidence": "low",
            "reason": "google_route_context_unbound",
            "source": "scenario_guard",
            "route_bound": False,
            "scenario_ready": False,
            "scope_class": scope_final,
            "route_bind_reason": verdict_reason,
        }
    if (
        route_bound is False
        and scenario_ready is False
        and _is_non_actionable_scenario_reason(scenario_reason)
    ):
        return {
            "price": None,
            "currency": None,
            "confidence": "low",
            "reason": scenario_reason,
            "source": "scenario_guard",
            "route_bound": False,
            "scenario_ready": False,
            "scope_class": scope_final,
            "route_bind_reason": verdict_reason,
        }
    return None


def _scenario_preextract_route_state_gate_flags() -> Dict[str, bool]:
    """Load feature flags for scenario pre-extract route-state verdict rollout."""
    return {
        "v2_enabled": _env_bool(
            "FLIGHT_WATCHER_SCENARIO_PREEXTRACT_VERDICT_V2_ENABLED",
            bool(get_threshold("scenario_preextract_verdict_v2_enabled", False)),
        ),
        "shadow_compare": _env_bool(
            "FLIGHT_WATCHER_SCENARIO_PREEXTRACT_VERDICT_V2_SHADOW_COMPARE",
            bool(get_threshold("scenario_preextract_verdict_v2_shadow_compare", True)),
        ),
    }


def _scenario_success_extract_scope_guard_overrides(
    *,
    run_id: str,
    service_key: str,
) -> Dict[str, str]:
    """Disable extractor scope guards when scenario already proved valid flight context."""
    safe_service = str(service_key or "").strip()
    if safe_service != "google_flights":
        return {}
    route_state_path = get_artifacts_dir(str(run_id)) / f"route_state_{safe_service}.json"
    if not route_state_path.exists():
        return {}
    route_state = _load_json_dict(route_state_path)
    verdict = route_state.get("scenario_extract_verdict")
    if not isinstance(verdict, dict):
        return {}
    if verdict.get("scenario_ready") is not True or verdict.get("route_bound") is not True:
        return {}
    scope_class = str(verdict.get("scope_class", "") or "").strip().lower()
    if scope_class != "flight_only":
        return {}
    route_bind_verdict = route_state.get("route_bind_verdict")
    support = ""
    reason = ""
    if isinstance(route_bind_verdict, dict):
        support = str(route_bind_verdict.get("support", "") or "").strip().lower()
        reason = str(route_bind_verdict.get("reason", "") or "").strip().lower()
    if support not in {"strong", "weak"} and reason != "route_bind_corroborated_local_fill":
        return {}
    return {
        "FLIGHT_WATCHER_VLM_SCOPE_GUARD_ENABLED": "0",
        "FLIGHT_WATCHER_LLM_SCOPE_GUARD_ENABLED": "0",
    }


def _scenario_success_google_deterministic_extract_fastpath(
    *,
    run_id: str,
    service_key: str,
    html: str,
    page_url: Optional[str],
) -> Optional[Dict[str, Any]]:
    """Use deterministic Google HTML extraction after scenario-proven success.

    This avoids expensive qwen/VLM extraction when the scenario already proved:
    ready=True, route_bound=True, and scope_class=flight_only.
    """
    if str(service_key or "").strip() != "google_flights":
        return None
    route_state_path = get_artifacts_dir(str(run_id)) / "route_state_google_flights.json"
    if not route_state_path.exists():
        return None
    route_state = _load_json_dict(route_state_path)
    verdict = route_state.get("scenario_extract_verdict")
    if not isinstance(verdict, dict):
        return None
    if verdict.get("scenario_ready") is not True or verdict.get("route_bound") is not True:
        return None
    if str(verdict.get("scope_class", "") or "").strip().lower() != "flight_only":
        return None
    plugin_out = extract_google_flights_price_from_html(html or "", page_url=page_url)
    if not isinstance(plugin_out, dict) or not plugin_out.get("ok"):
        return None
    price = plugin_out.get("price")
    if not isinstance(price, int):
        return None
    page_kind = str(plugin_out.get("page_kind", "") or "").strip().lower()
    if page_kind != "flights_results":
        return None
    return {
        "price": float(price),
        "currency": str(plugin_out.get("currency", "") or "JPY"),
        "confidence": "medium",
        "selector_hint": None,
        "source": "scenario_ready_plugin_fastpath",
        "reason": "",
        "route_bound": True,
        "scenario_ready": True,
        "scope_class": "flight_only",
        "route_bind_support": "strong",
        "route_bind_source": "scenario",
        "route_bind_reason": str(verdict.get("route_bind_reason", "") or "scenario_ready"),
        "page_class": "flight_only",
        "scope_guard": "skip",
        "scope_guard_basis": "deterministic",
        "extraction_strategy": str(plugin_out.get("extraction_strategy", "") or ""),
        "extraction_evidence": dict(plugin_out.get("evidence", {}) or {}),
    }


def _is_non_actionable_scenario_reason(reason: str) -> bool:
    """Return True when a scenario reason proves extraction is non-actionable.

    This covers both current and alias reason names for bounded deeplink recovery
    fail-fast exits so extraction/LLM salvage does not waste budget on known
    irrelevant-page states.
    """
    value = str(reason or "").strip().lower()
    if not value:
        return False
    if value.startswith("date_fill_failure_"):
        return True
    if value.startswith("blocked_interstitial_"):
        return True
    if value.startswith("manual_intervention_"):
        return True
    if value.startswith("demo_mode_manual_"):
        return True
    if value.startswith("demo_mode_observation_"):
        return True
    if value.startswith("assist_mode_manual_"):
        return True
    if value.startswith("assist_mode_observation_"):
        return True
    if value.startswith("deeplink_page_state_recovery_"):
        return True
    if value in {
        "retries_exhausted",
        "scenario_budget_soft_stop",
        "scenario_wall_clock_cap",
        "scenario_wall_clock_cap_exhausted",
        "deeplink_recovery_activation_unverified",
        "deeplink_recovery_rebind_unverified",
        "demo_mode_final_html_unavailable",
    }:
        return True
    return False


def _scenario_preextract_gate_result(*, run_id: str, service_key: str) -> Optional[Dict[str, Any]]:
    """Return a no-extract result when scenario artifacts already prove invalid page state."""
    artifacts_dir = get_artifacts_dir(str(run_id))
    safe_service = str(service_key or "").strip()
    if not safe_service:
        return None

    evidence_path = artifacts_dir / f"evidence_{safe_service}_state.json"
    if evidence_path.exists():
        evidence = _load_json_dict(evidence_path)
        checkpoints = evidence.get("checkpoints")
        if isinstance(checkpoints, dict):
            after = checkpoints.get("after_results_ready_check")
            if isinstance(after, dict):
                data = after.get("data")
                if isinstance(data, dict):
                    readiness = data.get("readiness")
                    route_bind = data.get("route_bind")
                    scope_guard = data.get("scope_guard")
                    ready = None
                    scenario_reason = ""
                    route_bound = None
                    if isinstance(readiness, dict):
                        ready = readiness.get("ready")
                        scenario_reason = str(readiness.get("override_reason", "") or "").strip()
                    if isinstance(route_bind, dict):
                        route_bound = route_bind.get("route_bound")
                    if (
                        ready is False
                        and isinstance(scenario_reason, str)
                        and scenario_reason
                        and (
                            _is_non_actionable_scenario_reason(scenario_reason)
                            or scenario_reason.startswith("blocked_interstitial_")
                        )
                    ):
                        return {
                            "price": None,
                            "currency": None,
                            "confidence": "low",
                            "reason": scenario_reason,
                            "source": "scenario_guard",
                            "route_bound": bool(route_bound) if isinstance(route_bound, bool) else False,
                            "scenario_ready": False,
                            "scope_class": (
                                str(scope_guard.get("page_class", "") or "")
                                if isinstance(scope_guard, dict)
                                else ""
                            ),
                        }

    scenario_error_path = get_run_dir(str(run_id)) / "scenario_last_error.json"
    if scenario_error_path.exists():
        scenario_error = _load_json_dict(scenario_error_path)
        err_service = str(scenario_error.get("site_key", "") or "").strip()
        stage = str(scenario_error.get("stage", "") or "").strip().lower()
        err_reason = str(scenario_error.get("error", "") or "").strip()
        if (
            err_service == safe_service
            and err_reason.startswith("blocked_interstitial_")
            and stage == "blocked_interstitial"
        ):
            blocked = scenario_error.get("blocked_interstitial")
            scope_class = ""
            if isinstance(blocked, dict):
                scope_class = str(blocked.get("page_kind", "") or "")
            return {
                "price": None,
                "currency": None,
                "confidence": "low",
                "reason": err_reason,
                "source": "scenario_guard",
                "route_bound": False,
                "scenario_ready": False,
                "scope_class": scope_class,
            }

    route_state_path = artifacts_dir / f"route_state_{safe_service}.json"
    if route_state_path.exists():
        route_state = _load_json_dict(route_state_path)
        summary = route_state.get("scenario_return_summary")
        if isinstance(summary, dict):
            summary_ready = summary.get("ready")
            summary_reason = str(summary.get("reason", "") or "").strip().lower()
            summary_non_actionable = _is_non_actionable_scenario_reason(summary_reason)
            if (
                summary_non_actionable
                and (
                    summary_ready is False
                    or summary_reason.startswith("demo_mode_observation_")
                    or summary_reason.startswith("assist_mode_observation_")
                )
                and not (
                    safe_service == "google_flights"
                    and summary_reason == "retries_exhausted"
                )
            ):
                scope_value = str(summary.get("scope_class", "") or "").strip().lower()
                route_verdict = route_state.get("route_bind_verdict")
                route_bound = None
                if isinstance(route_verdict, dict):
                    route_bound = route_verdict.get("route_bound")
                return {
                    "price": None,
                    "currency": None,
                    "confidence": "low",
                    "reason": summary_reason,
                    "source": "scenario_guard",
                    "route_bound": bool(route_bound) if isinstance(route_bound, bool) else False,
                    "scenario_ready": False,
                    "scope_class": scope_value,
                    "route_bind_reason": (
                        str(route_verdict.get("reason", "") or "")
                        if isinstance(route_verdict, dict)
                        else ""
                    ),
                }
        legacy_result = _scenario_preextract_route_state_legacy_result(
            route_state=route_state,
            service_key=safe_service,
        )
        v2_result = _scenario_preextract_route_state_v2_result(
            route_state=route_state,
            service_key=safe_service,
        )
        flags = _scenario_preextract_route_state_gate_flags()
        if flags.get("shadow_compare") and v2_result is not None:
            if (legacy_result is None) != (v2_result is None) or (
                legacy_result is not None
                and (
                    str(legacy_result.get("reason", "") or "").strip().lower()
                    != str(v2_result.get("reason", "") or "").strip().lower()
                )
            ):
                logger.info(
                    "scenario.preextract.verdict_v2.shadow_mismatch service=%s legacy_reason=%s v2_reason=%s use_v2=%s",
                    safe_service,
                    (legacy_result or {}).get("reason"),
                    (v2_result or {}).get("reason"),
                    bool(flags.get("v2_enabled")),
                )
        # Safety gate: if standardized v2 verdict explicitly marks non-actionable,
        # fail closed even when rollout flag is still in shadow mode.
        v2_reason = str((v2_result or {}).get("reason", "") or "").strip().lower()
        if v2_result is not None and _is_non_actionable_scenario_reason(v2_reason):
            return v2_result
        if flags.get("v2_enabled") and v2_result is not None:
            return v2_result
        if legacy_result is not None:
            return legacy_result
    return None


def _confidence_rank(raw: Any) -> int:
    """Map confidence string to comparable rank."""
    value = str(raw or "").strip().lower()
    if value == "high":
        return 3
    if value == "medium":
        return 2
    if value == "low":
        return 1
    return 0


def _should_salvage_extract(result: Dict[str, Any]) -> bool:
    """Return True when first extraction is weak enough to justify one salvage retry."""
    if not isinstance(result, dict):
        return True
    reason = str(result.get("reason", "") or "").strip().lower()
    if reason.startswith("scope_non_flight_"):
        return False
    if reason in {
        "vlm_non_flight_scope",
        "package_bundle_page",
        "html_non_flight_scope",
        "google_route_context_unbound",
        "deeplink_recovery_activation_unverified",
        "deeplink_recovery_rebind_unverified",
    }:
        return False
    if reason.startswith("deeplink_page_state_recovery_"):
        return False
    if result.get("price") is None:
        return True
    if not bool(get_threshold("extract_salvage_retry_on_low_confidence", True)):
        return False
    min_rank = _confidence_rank(get_threshold("extract_salvage_min_confidence", "medium"))
    return _confidence_rank(result.get("confidence")) < max(1, min_rank)


def _adjust_salvage_max_attempts_for_scenario_proven_missing_price(
    *,
    base_max_attempts: int,
    result: Dict[str, Any],
    scenario_scope_guard_overrides_active: bool,
) -> int:
    """Cap salvage retries when scenario already proved a valid flight context.

    This keeps one bounded salvage attempt for parser misses (`missing_price`) while
    avoiding repeated long retries after a successful Google Flights scenario.
    """
    capped = max(1, int(base_max_attempts))
    if not scenario_scope_guard_overrides_active:
        return capped
    if not isinstance(result, dict):
        return capped
    if result.get("price") is not None:
        return capped
    reason = str(result.get("reason", "") or "").strip().lower()
    if reason == "missing_price":
        return min(capped, 1)
    return capped


def _adaptive_scenario_candidate_timeout_sec(
    *,
    base_timeout_sec: int,
    llm_mode: str,
    adaptive_profile: Dict[str, Any],
) -> int:
    """Derive per-service scenario timeout from adaptive planner budget."""
    # 15sec minimum ensures even very fast queries get basic navigation time.
    timeout_sec = max(15, int(base_timeout_sec))
    if str(llm_mode).strip().lower() != "light":
        return timeout_sec
    if not isinstance(adaptive_profile, dict):
        return timeout_sec

    planner_timeout = int(adaptive_profile.get("llm_light_planner_timeout_sec", 0) or 0)
    extract_timeout = int(adaptive_profile.get("llm_light_extract_timeout_sec", 0) or 0)
    reason = str(adaptive_profile.get("reason", "") or "").strip().lower()
    if planner_timeout <= 0 and extract_timeout <= 0:
        return timeout_sec

    # Ensure scenario budget is not tighter than planning + extraction + recovery loops.
    # 120sec buffer: recovery loop in light mode typically needs 2x planning time headroom.
    # 180sec buffer: extraction requires 3min overhead for setup/safety/retries.
    recommended = max(planner_timeout + 120, extract_timeout + 180)
    if "enable_planner_escalation" in reason:
        # +300sec (5min) escalation for plan generation with heavy site exploration.
        recommended = max(recommended, planner_timeout + 300)
    if "enable_extract_escalation" in reason:
        # +420sec (7min) for extraction escalation; +360sec (6min) safety for robust planning.
        recommended = max(recommended, extract_timeout + 420, planner_timeout + 360)
    if "high_llm_timeout_pressure" in reason:
        # Maximum pressure mode: +540sec (9min) for extraction attempts,
        # +420sec (7min) for planning fallbacks when LLM is slow.
        recommended = max(recommended, extract_timeout + 540, planner_timeout + 420)

    # 1800sec (30min) is default scenario timeout cap; configurable via threshold.
    cap = _env_int(
        "FLIGHT_WATCHER_SCENARIO_CANDIDATE_TIMEOUT_CAP_SEC",
        int(get_threshold("scenario_candidate_timeout_cap_sec", 1800)),
    )
    # Ensure scenario budget accommodates planning + extraction + recovery.
    # Clamp result between (60sec minimum, cap) with 60sec as global safety floor.
    min_timeout = max(timeout_sec, recommended)  # Must be at least base + recommended
    max_timeout = max(60, cap)  # 60sec minimum ensures at least 1min base scenario run
    return min(min_timeout, max_timeout)  # Cap at configured maximum


def _prefer_result(primary: Dict[str, Any], candidate: Dict[str, Any]) -> Dict[str, Any]:
    """Pick the better extraction result between primary and salvage candidate."""
    if not isinstance(primary, dict):
        return candidate if isinstance(candidate, dict) else primary
    if not isinstance(candidate, dict):
        return primary
    p_price = primary.get("price")
    c_price = candidate.get("price")
    if p_price is None and c_price is not None:
        return candidate
    if p_price is not None and c_price is None:
        return primary
    if p_price is not None and c_price is not None:
        if _confidence_rank(candidate.get("confidence")) > _confidence_rank(primary.get("confidence")):
            return candidate
        return primary
    # Both have no price: keep the one with higher confidence/reason signal.
    if _confidence_rank(candidate.get("confidence")) > _confidence_rank(primary.get("confidence")):
        return candidate
    if str(primary.get("reason", "")).startswith("llm_request_failed_") and not str(
        candidate.get("reason", "")
    ).startswith("llm_request_failed_"):
        return candidate
    return primary


def _has_cli_trip_overrides(args) -> bool:
    """Detect whether CLI explicitly selected a single trip input."""
    return any(
        value is not None
        for value in (
            args.origin,
            args.dest,
            args.depart,
            args.return_date,
            args.trip_type,
            args.is_domestic,
            args.max_trip_price,
            args.max_transit,
            args.plan_file,
        )
    )


def _resolve_trip_plans(args, runtime: Dict[str, object], default_url: str):
    """Resolve one or many validated trip plans from runtime config."""
    if runtime.get("trips") and not _has_cli_trip_overrides(args):
        plans = []
        for index, trip in enumerate(runtime.get("trips") or [], start=1):
            if not isinstance(trip, dict):
                raise ValueError(f"Trip #{index} must be a mapping object in config")
            plans.append(
                resolve_flight_plan(
                    origin=_coalesce(trip.get("origin"), runtime.get("origin")),
                    dest=_coalesce(trip.get("dest"), runtime.get("dest")),
                    depart=_coalesce(trip.get("depart"), runtime.get("depart")),
                    return_date=_coalesce(trip.get("return_date"), runtime.get("return_date")),
                    trip_type=_coalesce(trip.get("trip_type"), runtime.get("trip_type")),
                    is_domestic=trip.get("is_domestic")
                    if "is_domestic" in trip
                    else runtime.get("is_domestic"),
                    max_trip_price=trip.get("max_trip_price")
                    if "max_trip_price" in trip
                    else runtime.get("max_trip_price"),
                    max_transit=trip.get("max_transit")
                    if "max_transit" in trip
                    else runtime.get("max_transit"),
                    url=default_url,
                    plan_file=None,
                )
            )
        if not plans:
            raise ValueError("No valid trips found in input config")
        return plans

    return [
        resolve_flight_plan(
            origin=runtime["origin"],
            dest=runtime["dest"],
            depart=runtime["depart"],
            return_date=runtime["return_date"],
            trip_type=runtime["trip_type"],
            is_domestic=runtime["is_domestic"],
            max_trip_price=runtime["max_trip_price"],
            max_transit=runtime["max_transit"],
            url=default_url,
            plan_file=runtime["plan_file"],
        )
    ]


def run_multi_service(args) -> List[Dict[str, object]]:
    """Execute scenario + extraction flow for all selected services."""
    _setup_logging()
    enforce_storage_limits()
    runtime = _resolve_runtime_args(args)
    set_active_threshold_profile(str(runtime.get("thresholds_profile", "default")))

    # Initialize debug mode if enabled
    run_episode = None
    debug_mode = runtime.get("debug", False)

    # Apply debug budget env var overrides (only when debug mode enabled)
    if debug_mode:
        from utils.thresholds import resolve_debug_budgets_from_env
        debug_budgets = resolve_debug_budgets_from_env(debug_enabled=True)
        if debug_budgets["profile"]:
            runtime["debug_budgets_profile"] = debug_budgets["profile"]
        if debug_budgets["escalate"] is not None:
            runtime["debug_budgets_escalate"] = debug_budgets["escalate"]

    if debug_mode:
        # Load models config for manifest
        models_config = {}
        try:
            import yaml
            models_path = Path("configs/models.yaml")
            if models_path.exists():
                with open(models_path) as f:
                    models_config = yaml.safe_load(f) or {}
        except Exception:
            pass

        # Clean up old runs if requested
        if runtime.get("debug_keep", 0) > 0:
            deleted = cleanup_old_runs(
                Path(runtime["debug_dir"]),
                runtime["debug_keep"]
            )
            if deleted > 0:
                logger.info(f"Cleaned up {deleted} old debug run folders")

        # Create run episode
        service_cfg = _resolve_services(args)
        run_episode = RunEpisode(
            run_id=runtime["run_id"],
            base_dir=Path(runtime["debug_dir"]),
            config_snapshot=runtime,
            services=service_cfg["selected_services"],
            models_config=models_config,
        )
        logger.info(f"Debug mode enabled: {run_episode.run_dir}")

        # Emit initial event
        run_episode.emit_event({
            "event": "run_started",
            "level": "info",
            "services": service_cfg["selected_services"],
        })

        # Write initial manifest
        run_episode.save_manifest()

    try:
        return _run_multi_service_impl(args, runtime, run_episode)
    finally:
        if run_episode:
            # Copy final summary and error files if they exist
            # Save final summary from outputs
            summary_path = Path("storage/final_summary.json")
            if summary_path.exists():
                run_episode.copy_summary_file(summary_path)

            # Emit final event
            run_episode.emit_event({
                "event": "run_finished",
                "level": "info",
            })

            # Finalize (update manifest with finish time)
            run_episode.finalize()


def _run_multi_service_impl(args, runtime: Dict[str, object], run_episode: Optional[RunEpisode]) -> List[Dict[str, object]]:
    """Internal implementation of run_multi_service (separated for debug wrapping)."""
    os.environ["FLIGHT_WATCHER_LLM_MODE"] = str(runtime.get("llm_mode", "full"))
    multimodal_mode = runtime.get("agentic_multimodal_mode")
    if isinstance(multimodal_mode, str) and multimodal_mode in {
        "off",
        "assist",
        "primary",
        "judge",
        "judge_primary",
    }:
        os.environ["FLIGHT_WATCHER_AGENTIC_MULTIMODAL_MODE"] = multimodal_mode
    else:
        os.environ.pop("FLIGHT_WATCHER_AGENTIC_MULTIMODAL_MODE", None)
    service_cfg = _resolve_services(args)
    selected = service_cfg["selected_services"]
    service_urls = service_cfg["service_urls"]
    service_url_hints = service_cfg.get("service_url_hints", {})
    alerts_config = load_alerts_config(args.alerts_config)
    if runtime["disable_alerts"]:
        alerts_config["enabled"] = False

    # Use first service URL only to satisfy FlightPlan URL validation;
    # each service run overrides URL with its own configured endpoint.
    first_url = service_urls[selected[0]]
    plans = _resolve_trip_plans(args, runtime, first_url)
    multi_trip = len(plans) > 1

    init_db()
    outputs: List[Dict[str, object]] = []
    for trip_index, plan in enumerate(plans, start=1):
        route_label = f"{plan.origin}->{plan.dest} ({plan.depart})"
        if plan.return_date:
            route_label = f"{route_label} return {plan.return_date}"
        route_label = f"{route_label} {'domestic' if plan.is_domestic else 'international'}"
        if plan.max_trip_price:
            route_label = f"{route_label} max={plan.max_trip_price}"
        if plan.max_transit is not None:
            route_label = f"{route_label} max_transit={plan.max_transit}"
        if multi_trip:
            logger.info(
                f"Processing trip {trip_index}/{len(plans)}: {route_label}",
                extra={"component": "main", "trip_index": trip_index, "route": f"{plan.origin}-{plan.dest}"}
            )

        route_task = str(runtime["task"])
        if multi_trip:
            return_key = plan.return_date or "one_way"
            route_task = f"{route_task}::{plan.origin}-{plan.dest}-{plan.depart}-{return_key}"

        for service_key in selected:
            service_url = service_urls[service_key]
            service_label = service_name(service_key)
            used_url = service_url
            allow_disable_http2_retry = True
            allow_engine_fallback_retry = True
            evidence_enabled = _env_bool(
                "FLIGHT_WATCHER_SCENARIO_EVIDENCE_DUMP_ENABLED",
                bool(get_threshold("scenario_evidence_dump_enabled", False)),
            )
            service_run_id = str(runtime["run_id"])
            extract_wall_clock_cap_sec = int(get_threshold("extract_wall_clock_cap_sec", 0))
            service_started_at = time.monotonic()
            adaptive_profile = recommend_runtime_profile(
                service_key,
                llm_mode=str(runtime.get("llm_mode", "full")),
            )
            apply_runtime_profile_env(adaptive_profile)
            service_candidate_timeout_sec = _adaptive_scenario_candidate_timeout_sec(
                base_timeout_sec=int(runtime["scenario_candidate_timeout_sec"]),
                llm_mode=str(runtime.get("llm_mode", "full")),
                adaptive_profile=adaptive_profile,
            )
            logger.info(
                f"[{service_key}] Adaptive profile configured: "
                f"reason={adaptive_profile.get('reason')} "
                f"planner_timeout={adaptive_profile.get('llm_light_planner_timeout_sec')} "
                f"extract_timeout={adaptive_profile.get('llm_light_extract_timeout_sec')} "
                f"scenario_timeout={service_candidate_timeout_sec}",
                extra={"component": "main", "service_key": service_key, "trip_index": trip_index}
            )
            os.environ["FLIGHT_WATCHER_EVIDENCE_RUN_ID"] = service_run_id
            try:
                html = None
                last_nav_error = None
                url_candidates = service_url_candidates(
                    service_key,
                    preferred_url=service_url,
                    is_domestic=plan.is_domestic,
                    knowledge=get_knowledge(
                        service_key,
                        user_id=runtime.get("knowledge_user"),
                    ),
                    seed_hints=service_url_hints.get(service_key),
                )
                service_host = (urlparse(service_url).hostname or "").lower()
                if service_key == "google_flights" and _is_google_host(service_host):
                    url_candidates = _order_google_flights_url_candidates(
                        url_candidates=url_candidates,
                        service_url=service_url,
                        origin=plan.origin,
                        dest=plan.dest,
                        depart=plan.depart,
                        return_date=plan.return_date,
                        trip_type=plan.trip_type,
                        currency=str(runtime.get("mimic_currency") or ""),
                        locale=str(runtime.get("mimic_locale") or ""),
                        region=str(runtime.get("mimic_region") or ""),
                        bootstrap_mode=str(runtime.get("google_flights_bootstrap_mode") or ""),
                    )
                start_url = url_candidates[0] if url_candidates else service_url
                logger.info(
                    f"Starting scenario for {service_label}: {start_url}",
                    extra={"component": "main", "service_key": service_key, "url": start_url}
                )

                # Debug mode: emit service start event
                if run_episode:
                    run_episode.emit_event({
                        "event": "service_started",
                        "level": "info",
                        "site": service_key,
                        "trip_index": trip_index,
                        "url": start_url,
                        "origin": plan.origin,
                        "dest": plan.dest,
                        "depart": plan.depart,
                    })

                for idx, candidate_url in enumerate(url_candidates):
                    if not url_matches_service_domain(
                        service_key,
                        candidate_url,
                        preferred_url=service_url,
                        seed_hints=service_url_hints.get(service_key),
                    ):
                        logger.debug(
                            f"Skipping cross-service candidate URL",
                            extra={"component": "main", "service_key": service_key, "url": candidate_url}
                        )
                        continue
                    used_url = candidate_url
                    candidate_started = time.monotonic()
                    try:
                        if idx > 0:
                            logger.info(
                                f"Retrying with fallback URL",
                                extra={"component": "main", "service_key": service_key, "url": candidate_url, "attempt": idx}
                            )
                        html = _run_agentic_scenario_with_timeout(
                            timeout_sec=service_candidate_timeout_sec,
                            url=candidate_url,
                            origin=plan.origin,
                            dest=plan.dest,
                            depart=plan.depart,
                            return_date=plan.return_date,
                            trip_type=plan.trip_type,
                            is_domestic=plan.is_domestic,
                            max_transit=plan.max_transit,
                            human_mimic=runtime["human_mimic"],
                            disable_http2=False,
                            knowledge_user=runtime.get("knowledge_user"),
                            mimic_locale=runtime["mimic_locale"],
                            mimic_timezone=runtime["mimic_timezone"],
                            mimic_currency=runtime["mimic_currency"],
                            mimic_region=runtime["mimic_region"],
                            mimic_latitude=runtime["mimic_latitude"],
                            mimic_longitude=runtime["mimic_longitude"],
                            site_key=service_key,
                            browser_engine="chromium",
                        )
                        used_url = candidate_url
                        if looks_package_bundle_page(
                            html=html,
                            site=service_key,
                            url=candidate_url,
                        ):
                            record_package_url_hint(
                                service_key,
                                source_url=candidate_url,
                                user_id=runtime.get("knowledge_user"),
                            )
                            if idx + 1 < len(url_candidates):
                                logger.info(
                                    f"Detected package/bundle page, trying next candidate",
                                    extra={"component": "main", "service_key": service_key, "url": candidate_url}
                                )
                                html = None
                                continue
                        elapsed = time.monotonic() - candidate_started
                        logger.info(
                            f"Scenario completed: {elapsed:.1f}s",
                            extra={"component": "main", "service_key": service_key, "url": candidate_url, "elapsed_sec": elapsed}
                        )
                        break
                    except Exception as nav_exc:
                        elapsed = time.monotonic() - candidate_started
                        last_nav_error = nav_exc
                        msg = str(nav_exc)
                        logger.warning(
                            f"Scenario failed: {msg[:100]}",
                            extra={"component": "main", "service_key": service_key, "url": candidate_url, "elapsed_sec": elapsed, "error_code": type(nav_exc).__name__}
                        )
                        _save_service_error_debug(
                            run_id=str(runtime["run_id"]),
                            service_key=service_key,
                            trip_index=trip_index,
                            candidate_url=candidate_url,
                            error=msg,
                        )
                        if "ERR_HTTP2_PROTOCOL_ERROR" in msg:
                            if allow_engine_fallback_retry:
                                try:
                                    logger.info(
                                        f"Retrying with browser_engine=webkit due to HTTP/2 error",
                                        extra={"component": "main", "service_key": service_key, "url": candidate_url}
                                    )
                                    webkit_started = time.monotonic()
                                    html = _run_agentic_scenario_with_timeout(
                                        timeout_sec=min(
                                            service_candidate_timeout_sec,
                                            runtime["scenario_disable_http2_retry_timeout_sec"],
                                        ),
                                        url=candidate_url,
                                        origin=plan.origin,
                                        dest=plan.dest,
                                        depart=plan.depart,
                                        return_date=plan.return_date,
                                        trip_type=plan.trip_type,
                                        is_domestic=plan.is_domestic,
                                        max_transit=plan.max_transit,
                                        human_mimic=runtime["human_mimic"],
                                        disable_http2=False,
                                        knowledge_user=runtime.get("knowledge_user"),
                                        mimic_locale=runtime["mimic_locale"],
                                        mimic_timezone=runtime["mimic_timezone"],
                                        mimic_currency=runtime["mimic_currency"],
                                        mimic_region=runtime["mimic_region"],
                                        mimic_latitude=runtime["mimic_latitude"],
                                        mimic_longitude=runtime["mimic_longitude"],
                                        site_key=service_key,
                                        browser_engine="webkit",
                                    )
                                    used_url = candidate_url
                                    webkit_elapsed = time.monotonic() - webkit_started
                                    logger.info(
                                        f"Webkit fallback completed: {webkit_elapsed:.1f}s",
                                        extra={"component": "main", "service_key": service_key, "url": candidate_url, "elapsed_sec": webkit_elapsed}
                                    )
                                    break
                                except Exception as webkit_exc:
                                    webkit_elapsed = time.monotonic() - webkit_started
                                    last_nav_error = webkit_exc
                                    msg = str(webkit_exc)
                                    logger.warning(
                                        f"Webkit fallback failed: {msg[:100]}",
                                        extra={"component": "main", "service_key": service_key, "url": candidate_url, "elapsed_sec": webkit_elapsed, "error_code": type(webkit_exc).__name__}
                                    )
                                    _save_service_error_debug(
                                        run_id=str(runtime["run_id"]),
                                        service_key=service_key,
                                        trip_index=trip_index,
                                        candidate_url=candidate_url,
                                        error=msg,
                                    )
                                    allow_engine_fallback_retry = False
                            if not allow_disable_http2_retry:
                                if idx + 1 < len(url_candidates):
                                    continue
                                raise last_nav_error
                            try:
                                logger.info(
                                    f"Retrying with disable-http2 flag",
                                    extra={"component": "main", "service_key": service_key, "url": candidate_url}
                                )
                                retry_started = time.monotonic()
                                html = _run_agentic_scenario_with_timeout(
                                    timeout_sec=min(
                                        service_candidate_timeout_sec,
                                        runtime["scenario_disable_http2_retry_timeout_sec"],
                                    ),
                                    url=candidate_url,
                                    origin=plan.origin,
                                    dest=plan.dest,
                                    depart=plan.depart,
                                    return_date=plan.return_date,
                                    trip_type=plan.trip_type,
                                    is_domestic=plan.is_domestic,
                                    max_transit=plan.max_transit,
                                    human_mimic=runtime["human_mimic"],
                                    disable_http2=True,
                                    knowledge_user=runtime.get("knowledge_user"),
                                    mimic_locale=runtime["mimic_locale"],
                                    mimic_timezone=runtime["mimic_timezone"],
                                    mimic_currency=runtime["mimic_currency"],
                                    mimic_region=runtime["mimic_region"],
                                    mimic_latitude=runtime["mimic_latitude"],
                                    mimic_longitude=runtime["mimic_longitude"],
                                    site_key=service_key,
                                )
                                used_url = candidate_url
                                retry_elapsed = time.monotonic() - retry_started
                                logger.info(
                                    f"Disable-http2 retry completed: {retry_elapsed:.1f}s",
                                    extra={"component": "main", "service_key": service_key, "url": candidate_url, "elapsed_sec": retry_elapsed}
                                )
                                break
                            except Exception as retry_exc:
                                retry_elapsed = time.monotonic() - retry_started
                                last_nav_error = retry_exc
                                msg = str(retry_exc)
                                logger.warning(
                                    f"Disable-http2 retry failed: {msg[:100]}",
                                    extra={"component": "main", "service_key": service_key, "url": candidate_url, "elapsed_sec": retry_elapsed, "error_code": type(retry_exc).__name__}
                                )
                                _save_service_error_debug(
                                    run_id=str(runtime["run_id"]),
                                    service_key=service_key,
                                    trip_index=trip_index,
                                    candidate_url=candidate_url,
                                    error=msg,
                                )
                                # If HTTP/2-off retries keep timing out, stop paying this cost
                                # for subsequent fallback URLs in this service run.
                                if (
                                    "Timeout" in msg
                                    or "ERR_TIMED_OUT" in msg
                                    or retry_elapsed > 120
                                ):
                                    allow_disable_http2_retry = False
                        if idx + 1 >= len(url_candidates):
                            raise last_nav_error
                        # Continue to fallback URLs for known navigation/network failures.
                        if (
                            "ERR_HTTP2_PROTOCOL_ERROR" in msg
                            or "ERR_CONNECTION" in msg
                            or "Page.goto:" in msg
                        ):
                            continue
                        # For non-navigation failures, do not hide the root cause.
                        raise last_nav_error
                if html is None:
                    raise last_nav_error if last_nav_error else RuntimeError(
                        "scenario returned no html"
                    )

                visual_path = _last_service_visual_path(service_key, run_id=str(runtime["run_id"]))
                screenshot_path = str(visual_path) if visual_path.exists() else None
                extract_started_at = time.monotonic()
                extract_scope_overrides = _scenario_success_extract_scope_guard_overrides(
                    run_id=service_run_id,
                    service_key=service_key,
                )
                write_service_evidence_checkpoint(
                    run_id=service_run_id,
                    service=service_key,
                    checkpoint="before_extraction",
                    enabled=evidence_enabled,
                    payload={
                        "run_id": service_run_id,
                        "service": service_key,
                        "url": used_url,
                        "intended": {
                            "origin": plan.origin,
                            "dest": plan.dest,
                            "depart": plan.depart,
                            "return_date": plan.return_date or "",
                            "trip_type": plan.trip_type,
                        },
                        "extraction": {
                            "selected_strategy": _selected_extract_strategy_label(),
                        },
                    },
                )
                gated_result = _scenario_preextract_gate_result(
                    run_id=service_run_id,
                    service_key=service_key,
                )
                if isinstance(gated_result, dict):
                    result = gated_result
                    write_service_evidence_checkpoint(
                        run_id=service_run_id,
                        service=service_key,
                        checkpoint="extraction_skipped",
                        enabled=evidence_enabled,
                        payload={
                            "run_id": service_run_id,
                            "service": service_key,
                            "reason": str(result.get("reason", "") or ""),
                            "source": "scenario_guard",
                            "route_bound": result.get("route_bound"),
                        },
                    )
                    logger.info(
                        "Extraction skipped by scenario_guard: service=%s reason=%s",
                        service_key,
                        result.get("reason"),
                        extra={
                            "component": "main",
                            "service_key": service_key,
                            "reason": result.get("reason"),
                        },
                    )
                else:
                    if extract_scope_overrides:
                        logger.info(
                            "Extraction scope guards bypassed by scenario verdict: service=%s reason=ready_route_bound_flight_only",
                            service_key,
                            extra={"component": "main", "service_key": service_key},
                        )
                    fast_result = _scenario_success_google_deterministic_extract_fastpath(
                        run_id=service_run_id,
                        service_key=service_key,
                        html=html,
                        page_url=used_url,
                    )
                    if isinstance(fast_result, dict) and fast_result.get("price") is not None:
                        result = fast_result
                        logger.info(
                            "Extraction fastpath succeeded after scenario verdict: service=%s source=%s price=%s",
                            service_key,
                            result.get("source"),
                            result.get("price"),
                            extra={"component": "main", "service_key": service_key},
                        )
                    else:
                        with _temporary_env(extract_scope_overrides):
                            result = _extract_price_compat(
                                html,
                                site_key=service_key,
                                task=runtime["task"],
                                origin=plan.origin,
                                dest=plan.dest,
                                depart=plan.depart,
                                return_date=plan.return_date,
                                screenshot_path=screenshot_path,
                                page_url=used_url,
                            )
                extract_elapsed_sec = time.monotonic() - extract_started_at
                if (
                    extract_wall_clock_cap_sec > 0
                    and extract_elapsed_sec >= float(extract_wall_clock_cap_sec)
                ):
                    result = {
                        "price": None,
                        "currency": None,
                        "confidence": "low",
                        "reason": "extract_wall_clock_cap",
                        "source": "watchdog",
                        "route_bound": False,
                    }

                salvage_enabled = bool(get_threshold("extract_salvage_retry_enabled", True))
                salvage_skip_after_elapsed_sec = max(
                    0,
                    int(get_threshold("extract_salvage_skip_after_elapsed_sec", 1800)),
                )
                if (
                    salvage_enabled
                    and extract_wall_clock_cap_sec > 0
                    and (time.monotonic() - extract_started_at)
                    >= float(extract_wall_clock_cap_sec)
                ):
                    salvage_enabled = False
                    if isinstance(result, dict):
                        result = dict(result)
                        result["price"] = None
                        result["currency"] = None
                        result["confidence"] = "low"
                        result["reason"] = "extract_wall_clock_cap"
                        result["source"] = "watchdog"
                service_elapsed_sec = time.monotonic() - service_started_at
                if (
                    salvage_enabled
                    and salvage_skip_after_elapsed_sec > 0
                    and service_elapsed_sec >= salvage_skip_after_elapsed_sec
                ):
                    salvage_enabled = False
                    logger.info(
                        f"Salvage skipped: elapsed budget exceeded ({service_elapsed_sec:.1f}s >= {salvage_skip_after_elapsed_sec}s)",
                        extra={"component": "main", "service_key": service_key, "elapsed_sec": service_elapsed_sec}
                    )

                # Validate candidate-level timeout budget before salvage iteration
                if salvage_enabled:
                    candidate_elapsed_sec = time.monotonic() - candidate_started
                    remaining_candidate_budget = max(
                        0,
                        int(service_candidate_timeout_sec) - candidate_elapsed_sec,
                    )
                    base_salvage_timeout_sec = int(
                        get_threshold("extract_salvage_llm_extract_timeout_sec", 60)
                    )
                    # Use 2x safety margin: don't salvage if less than 2x the base timeout remains
                    required_budget = 2 * base_salvage_timeout_sec
                    if remaining_candidate_budget < required_budget:
                        salvage_enabled = False
                        logger.info(
                            f"Salvage skipped: candidate timeout budget exhausted (remaining={remaining_candidate_budget}s < required={required_budget}s)",
                            extra={"component": "main", "service_key": service_key, "remaining_sec": remaining_candidate_budget, "required_sec": required_budget}
                        )

                if gated_result is None and salvage_enabled and _should_salvage_extract(result):
                    salvage_reason = result.get("reason") if isinstance(result, dict) else "unknown"
                    salvage_max_attempts = max(
                        1,
                        int(get_threshold("extract_salvage_max_attempts", 1)),
                    )
                    salvage_max_attempts = _adjust_salvage_max_attempts_for_scenario_proven_missing_price(
                        base_max_attempts=salvage_max_attempts,
                        result=result if isinstance(result, dict) else {},
                        scenario_scope_guard_overrides_active=bool(extract_scope_overrides),
                    )
                    reason_text = str(salvage_reason or "").strip().lower()
                    if reason_text in {"heuristic_no_route_match", "google_route_context_unbound"}:
                        route_miss_cap = max(
                            1,
                            int(get_threshold("extract_salvage_max_attempts_route_miss", 1)),
                        )
                        salvage_max_attempts = min(salvage_max_attempts, route_miss_cap)
                    if bool(extract_scope_overrides) and reason_text == "missing_price":
                        logger.info(
                            "Salvage capped after scenario-proven flight context: reason=missing_price attempts=%s",
                            salvage_max_attempts,
                            extra={"component": "main", "service_key": service_key},
                        )
                    salvage_timeout_backoff = float(
                        get_threshold("extract_salvage_timeout_backoff", 1.0)
                    )
                    salvage_stop_rank = _confidence_rank(
                        get_threshold("extract_salvage_stop_confidence", "high")
                    )

                    base_vlm_timeout = int(
                        get_threshold("extract_salvage_vlm_extract_timeout_sec", 40)
                    )
                    base_llm_timeout = int(
                        get_threshold("extract_salvage_llm_extract_timeout_sec", 60)
                    )

                    primary_result = result
                    best_result = primary_result
                    last_salvage_result = None
                    best_attempt_idx = -1

                    for salvage_attempt in range(salvage_max_attempts):
                        # Double-check budget before each attempt
                        candidate_elapsed_sec = time.monotonic() - candidate_started
                        remaining_candidate_budget = max(
                            0,
                            int(service_candidate_timeout_sec) - candidate_elapsed_sec,
                        )
                        if remaining_candidate_budget < base_llm_timeout:
                            logger.info(
                                f"Salvage loop terminated: candidate budget exhausted (remaining={remaining_candidate_budget}s < timeout={base_llm_timeout}s)",
                                extra={"component": "main", "service_key": service_key, "attempt": salvage_attempt}
                            )
                            break
                        if (
                            extract_wall_clock_cap_sec > 0
                            and (time.monotonic() - extract_started_at)
                            >= float(extract_wall_clock_cap_sec)
                        ):
                            if isinstance(best_result, dict):
                                best_result = dict(best_result)
                                best_result["price"] = None
                                best_result["currency"] = None
                                best_result["confidence"] = "low"
                                best_result["reason"] = "extract_wall_clock_cap"
                                best_result["source"] = "watchdog"
                            break
                        scale = salvage_timeout_backoff ** salvage_attempt
                        salvage_overrides = {
                            "FLIGHT_WATCHER_VLM_EXTRACT_ENABLED": "1",
                            "FLIGHT_WATCHER_LIGHT_TRY_LLM_EXTRACT_ON_HEURISTIC_MISS": "1",
                            "FLIGHT_WATCHER_LIGHT_TRY_VLM_ON_MISS": "1",
                            "FLIGHT_WATCHER_EXTRACT_SEMANTIC_CHUNK_ENABLED": "1",
                            "FLIGHT_WATCHER_VLM_EXTRACT_TIMEOUT_SEC": str(
                                max(1, int(base_vlm_timeout * scale))
                            ),
                            "FLIGHT_WATCHER_LLM_EXTRACT_TIMEOUT_SEC": str(
                                max(1, int(base_llm_timeout * scale))
                            ),
                        }
                        if bool(get_threshold("extract_salvage_force_full_mode", True)):
                            salvage_overrides["FLIGHT_WATCHER_LLM_MODE"] = "full"
                        if bool(get_threshold("extract_salvage_clear_circuit_before_retry", True)):
                            reset_llm_circuit_state()
                        logger.info(
                            f"Salvage attempt {salvage_attempt + 1}/{salvage_max_attempts}: {salvage_reason}",
                            extra={"component": "main", "service_key": service_key, "attempt": salvage_attempt + 1}
                        )
                        salvage_env = dict(extract_scope_overrides or {})
                        salvage_env.update(salvage_overrides)
                        with _temporary_env(salvage_env):
                            salvage_result = _extract_price_compat(
                                html,
                                site_key=service_key,
                                task=runtime["task"],
                                origin=plan.origin,
                                dest=plan.dest,
                                depart=plan.depart,
                                return_date=plan.return_date,
                                screenshot_path=screenshot_path,
                                page_url=used_url,
                            )
                        # Stop early when salvage keeps returning the same no-price miss reason.
                        if (
                            isinstance(best_result, dict)
                            and isinstance(salvage_result, dict)
                            and best_result.get("price") is None
                            and salvage_result.get("price") is None
                            and str(salvage_result.get("reason", "")) == str(best_result.get("reason", ""))
                        ):
                            last_salvage_result = salvage_result
                            break
                        last_salvage_result = salvage_result
                        merged_best = _prefer_result(best_result, salvage_result)
                        if merged_best is salvage_result:
                            best_result = salvage_result
                            best_attempt_idx = salvage_attempt + 1
                            if (
                                isinstance(best_result, dict)
                                and best_result.get("price") is not None
                                and _confidence_rank(best_result.get("confidence")) >= salvage_stop_rank
                            ):
                                break

                    chosen = _prefer_result(primary_result, best_result)
                    if chosen is best_result and chosen is not primary_result:
                        if isinstance(chosen, dict):
                            chosen["salvage_retry_used"] = True
                            chosen["salvage_retry_attempts"] = (
                                best_attempt_idx if best_attempt_idx > 0 else salvage_max_attempts
                            )
                        result = chosen
                    else:
                        result = primary_result
                    logger.info(
                        f"Salvage complete: primary={primary_result.get('price') if isinstance(primary_result, dict) else None}, "
                        f"salvage={last_salvage_result.get('price') if isinstance(last_salvage_result, dict) else None}, "
                        f"chosen={result.get('price') if isinstance(result, dict) else None}",
                        extra={"component": "main", "service_key": service_key}
                    )
                extract_elapsed_sec = time.monotonic() - extract_started_at
                logger.info(
                    f"Extraction completed in {extract_elapsed_sec:.1f}s: "
                    f"source={result.get('source') if isinstance(result, dict) else None}, "
                    f"price={result.get('price') if isinstance(result, dict) else None}",
                    extra={"component": "main", "service_key": service_key, "elapsed_sec": extract_elapsed_sec}
                )

                # Debug mode: emit extraction event and capture artifacts if needed
                if run_episode:
                    run_episode.emit_event({
                        "event": "extraction_completed",
                        "level": "info",
                        "site": service_key,
                        "trip_index": trip_index,
                        "price": result.get("price") if isinstance(result, dict) else None,
                        "currency": result.get("currency") if isinstance(result, dict) else None,
                        "confidence": result.get("confidence") if isinstance(result, dict) else None,
                        "source": result.get("source") if isinstance(result, dict) else None,
                        "reason": result.get("reason") if isinstance(result, dict) else None,
                        "elapsed_sec": extract_elapsed_sec,
                    })

                    # Capture artifacts on failure
                    if should_capture_artifacts(result):
                        try:
                            if html:
                                run_episode.save_artifact(
                                    html,
                                    f"{service_key}_trip{trip_index}_last.html"
                                )
                            logger.debug(
                                f"Saved debug artifacts for {service_key}",
                                extra={"component": "main", "service_key": service_key}
                            )
                        except Exception as artifact_exc:
                            logger.warning(
                                f"Failed to save debug artifacts: {artifact_exc}",
                                extra={"component": "main", "service_key": service_key}
                            )

                write_service_evidence_checkpoint(
                    run_id=service_run_id,
                    service=service_key,
                    checkpoint="after_extraction",
                    enabled=evidence_enabled,
                    payload={
                        "run_id": service_run_id,
                        "service": service_key,
                        "url": used_url,
                        "extraction": {
                            "selected_strategy": _selected_extract_strategy_label(),
                            "source": result.get("source") if isinstance(result, dict) else None,
                            "reason": result.get("reason") if isinstance(result, dict) else None,
                            "price": result.get("price") if isinstance(result, dict) else None,
                            "currency": result.get("currency") if isinstance(result, dict) else None,
                            "confidence": result.get("confidence") if isinstance(result, dict) else None,
                            "route_bound": result.get("route_bound") if isinstance(result, dict) else None,
                        },
                    },
                )

                if runtime["save_html"]:
                    suffix = f"{service_key}_trip{trip_index}" if multi_trip else service_key
                    html_path = Path("storage") / f"last_{suffix}.html"
                    html_path.parent.mkdir(parents=True, exist_ok=True)
                    html_path.write_text(html, encoding="utf-8")
                if runtime.get("debug_save_service_html"):
                    _save_service_html_debug(
                        html=html,
                        run_id=str(runtime["run_id"]),
                        service_key=service_key,
                        trip_index=trip_index,
                        suffix="ok",
                    )

                previous = get_last_price_record(service_key, route_task)
                save_run(
                    site=service_key,
                    task=route_task,
                    price=result.get("price"),
                    currency=result.get("currency"),
                    confidence=result.get("confidence"),
                    selector_used=_selector_used_from_result(result),
                )

                eval_config = dict(alerts_config)
                if plan.max_trip_price:
                    eval_config["target_price"] = plan.max_trip_price

                decision = evaluate_alert(
                    current_price=result.get("price"),
                    previous_price=previous.get("price") if previous else None,
                    previous_created_at=previous.get("created_at") if previous else None,
                    config=eval_config,
                )
                alert_payload = {"sent": False, "decision": decision}
                if decision.get("should_alert"):
                    message = build_alert_message(
                        service_key=service_key,
                        route_label=route_label,
                        current_price=result.get("price"),
                        previous_price=previous.get("price") if previous else None,
                        currency=result.get("currency"),
                        decision=decision,
                        result_url=used_url,
                    )
                    dispatch = dispatch_alert(
                        message=message,
                        config=eval_config,
                        service_key=service_key,
                    )
                    alert_payload = {
                        "sent": dispatch.get("ok", False),
                        "decision": decision,
                        "dispatch": dispatch,
                    }
                    logger.info(
                        f"Alert dispatched: {dispatch.get('ok', False)}",
                        extra={"component": "main", "service_key": service_key, "alert_sent": dispatch.get("ok", False)}
                    )

                payload = {
                    "trip_index": trip_index,
                    "service": service_key,
                    "url": used_url,
                    "result": result,
                    "llm_mode": runtime.get("llm_mode"),
                    "knowledge_user": runtime.get("knowledge_user"),
                    "origin": plan.origin,
                    "dest": plan.dest,
                    "depart": plan.depart,
                    "return_date": plan.return_date,
                    "trip_type": plan.trip_type,
                    "is_domestic": plan.is_domestic,
                    "max_trip_price": plan.max_trip_price,
                    "max_transit": plan.max_transit,
                    "task": route_task,
                    "alert": alert_payload,
                    "status": "ok",
                }
                outputs.append(payload)
                logger.info(
                    f"Service extraction result: {result.get('price') if isinstance(result, dict) else None} {result.get('currency') if isinstance(result, dict) else ''}",
                    extra={"component": "main", "service_key": service_key, "price": result.get('price') if isinstance(result, dict) else None}
                )
                try:
                    record_service_outcome(
                        site_key=service_key,
                        status="ok",
                        result=result,
                    )
                except Exception:
                    pass
            except Exception as exc:
                if runtime.get("debug_save_service_html") and isinstance(html, str) and html:
                    _save_service_html_debug(
                        html=html,
                        run_id=str(runtime["run_id"]),
                        service_key=service_key,
                        trip_index=trip_index,
                        suffix="error",
                    )

                # Debug mode: emit error event and capture artifacts
                if run_episode:
                    run_episode.emit_event({
                        "event": "service_error",
                        "level": "error",
                        "site": service_key,
                        "trip_index": trip_index,
                        "error": str(exc),
                        "error_type": type(exc).__name__,
                    })

                    # Save error artifacts
                    try:
                        if html:
                            run_episode.save_artifact(
                                html,
                                f"{service_key}_trip{trip_index}_error.html"
                            )
                        # Save error metadata
                        error_meta = {
                            "service": service_key,
                            "trip_index": trip_index,
                            "url": used_url,
                            "error": str(exc),
                            "error_type": type(exc).__name__,
                        }
                        run_episode.save_artifact(
                            error_meta,
                            f"{service_key}_trip{trip_index}_error.json"
                        )
                    except Exception as artifact_exc:
                        logger.warning(
                            f"Failed to save error artifacts: {artifact_exc}",
                            extra={"component": "main", "service_key": service_key}
                        )

                payload = {
                    "trip_index": trip_index,
                    "service": service_key,
                    "url": used_url,
                    "llm_mode": runtime.get("llm_mode"),
                    "knowledge_user": runtime.get("knowledge_user"),
                    "origin": plan.origin,
                    "dest": plan.dest,
                    "depart": plan.depart,
                    "return_date": plan.return_date,
                    "trip_type": plan.trip_type,
                    "is_domestic": plan.is_domestic,
                    "max_trip_price": plan.max_trip_price,
                    "max_transit": plan.max_transit,
                    "task": route_task,
                    "error": str(exc),
                    "status": "error",
                }
                outputs.append(payload)
                logger.error(
                    f"Service extraction failed: {str(exc)[:100]}",
                    extra={"component": "main", "service_key": service_key, "error_code": type(exc).__name__}
                )
                try:
                    record_service_outcome(
                        site_key=service_key,
                        status="error",
                        error=str(exc),
                    )
                except Exception:
                    pass
            finally:
                os.environ.pop("FLIGHT_WATCHER_EVIDENCE_RUN_ID", None)

    enforce_storage_limits()
    reset_active_threshold_profile()
    return outputs


def main():
    """Program entrypoint."""
    args = _parse_args()
    try:
        outputs = run_multi_service(args)
    except ValueError as exc:
        raise SystemExit(f"Invalid input: {exc}") from exc
    finally:
        try:
            release_touched_ollama_models()
        except Exception as exc:
            logger.warning(
                "ollama.release_touched_models_failed error=%s",
                exc,
                extra={"component": "main"},
            )
        reset_active_threshold_profile()

    logger.info("Run complete. Final summary:", extra={"component": "main"})
    print(json.dumps(outputs, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
