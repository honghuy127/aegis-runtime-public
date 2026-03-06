"""HTTP client wrapper for calling a local Ollama text generation endpoint."""

import os
import time
from typing import Optional

import requests
from storage.runs import save_llm_metric
from utils.logging import get_logger
from utils.thresholds import get_threshold

# NOTE: Circuit breaker state is module-global and NOT thread-safe.
# This is acceptable in the current single-threaded context (scenario_runner.py is synchronous).
# If concurrent LLM calls are added (e.g., parallel service runs), migrate to threading.Lock().
_CIRCUIT_OPEN_UNTIL = 0.0
_LAST_MONOTONIC_TS = 0.0
_CIRCUIT_OPEN_UNTIL_BY_MODEL = {}
_VLM_VALIDATION_FAILURES = 0  # Counter for VLM validation failures (Phase 5)
_VLM_VALIDATION_FAILURE_THRESHOLD = 5  # Threshold for circuit-opening on VLM failures
_OLLAMA_VERSION_CACHE = {}  # Cache for Ollama version detection (host -> version_info)
_TOUCHED_OLLAMA_MODELS = set()  # Models used in current Python process (for best-effort unload)
log = get_logger(__name__)


def _detect_ollama_version(host: str = "http://localhost:11434", timeout_sec: int = 5) -> Optional[dict]:
    """Detect Ollama server version.

    Returns dict with key: version, or None if detection fails.
    Caches results to avoid repeated HTTP requests. Non-blocking; always returns quickly.
    Endpoint availability is NOT tested here - it's determined by actual call attempts.
    """
    if host in _OLLAMA_VERSION_CACHE:
        return _OLLAMA_VERSION_CACHE[host]

    try:
        # Try /api/version endpoint first with short timeout
        version_url = f"{host}/api/version"
        response = requests.get(version_url, timeout=max(1, int(timeout_sec / 2)))
        if response.status_code != 200:
            _OLLAMA_VERSION_CACHE[host] = None
            return None
        version_data = response.json()
        version = version_data.get("version", "unknown")

        result = {"version": version}
        _OLLAMA_VERSION_CACHE[host] = result
        log.debug(
            "ollama.version_detection host=%s version=%s",
            host,
            version,
        )
        return result
    except Exception as e:
        # Silently fail - version detection is optional diagnostic
        log.debug(
            "ollama.version_detection silent fallback host=%s error_type=%s",
            host,
            type(e).__name__,
        )
        _OLLAMA_VERSION_CACHE[host] = None
        return None
        return None


def _extract_text(body) -> Optional[str]:
    """Extract model text from common Ollama and proxy response shapes."""
    if not isinstance(body, dict):
        return None

    # /api/generate format
    if isinstance(body.get("response"), str):
        response = body["response"]
        if response.strip():
            return response
        # Some models emit answer content only via thinking when think-mode is enabled.
        thinking = body.get("thinking")
        if isinstance(thinking, str) and thinking.strip():
            return thinking
        return response

    # /api/chat format
    if isinstance(body.get("message"), dict):
        message = body["message"]
        content = message.get("content")
        if isinstance(content, str):
            return content
        # Some multimodal/model variants return content as structured parts.
        if isinstance(content, list):
            parts = []
            for item in content:
                if isinstance(item, str):
                    parts.append(item)
                elif isinstance(item, dict):
                    text = item.get("text") or item.get("content")
                    if isinstance(text, str) and text.strip():
                        parts.append(text)
            if parts:
                return "\n".join(parts).strip()
        # Reasoning models may emit only a thinking channel.
        thinking = message.get("thinking")
        if isinstance(thinking, str) and thinking.strip():
            return thinking

    # Proxy/alternate formats
    if isinstance(body.get("content"), str):
        return body["content"]
    if isinstance(body.get("output_text"), str):
        return body["output_text"]

    return None


def _ns_to_s(value) -> Optional[float]:
    """Convert Ollama duration nanoseconds to seconds."""
    if isinstance(value, (int, float)):
        return float(value) / 1_000_000_000.0
    return None


def _response_perf(body) -> dict:
    """Extract key timing/token metrics from one Ollama JSON response."""
    done_reason = body.get("done_reason") if isinstance(body, dict) else ""
    eval_count = body.get("eval_count") if isinstance(body, dict) else None
    prompt_eval_count = body.get("prompt_eval_count") if isinstance(body, dict) else None
    eval_duration_s = _ns_to_s(body.get("eval_duration") if isinstance(body, dict) else None)
    tps = None
    if isinstance(eval_count, int) and eval_count > 0 and isinstance(eval_duration_s, float) and eval_duration_s > 0:
        tps = float(eval_count) / eval_duration_s
    return {
        "done_reason": done_reason or "",
        "eval_count": eval_count if isinstance(eval_count, int) else 0,
        "prompt_eval_count": prompt_eval_count if isinstance(prompt_eval_count, int) else 0,
        "tokens_per_sec": tps,
    }


def _llm_stall_detected(
    *,
    tokens_per_sec: Optional[float],
    elapsed_s: float,
    enabled: bool,
    min_tokens_per_sec: float,
    min_elapsed_sec: float,
) -> bool:
    """Return True when response throughput/elapsed indicates a stalled model call."""
    if not enabled:
        return False
    if elapsed_s < float(min_elapsed_sec):
        return False
    if tokens_per_sec is None:
        return False
    try:
        return float(tokens_per_sec) < float(min_tokens_per_sec)
    except Exception:
        return False


def call_llm(
    prompt: str,
    model: str = "qwen3:latest",
    think: Optional[bool] = None,
    timeout_sec: Optional[int] = None,
    json_mode: bool = False,
    num_ctx: Optional[int] = None,
    num_predict: Optional[int] = None,
    temperature: Optional[float] = None,
    images: Optional[list] = None,
    endpoint_policy: str = "auto",
    strict_json: bool = False,
    fail_fast_on_timeout: bool = False,
) -> str:
    """Send one generation request and return the raw text reply.

    When `think` is set, it explicitly toggles reasoning mode for thinking-capable models.
    """
    global _CIRCUIT_OPEN_UNTIL, _LAST_MONOTONIC_TS, _CIRCUIT_OPEN_UNTIL_BY_MODEL
    call_started = time.perf_counter()
    default_connect_timeout = int(get_threshold("ollama_connect_timeout_sec", 10))
    default_read_timeout = int(get_threshold("ollama_read_timeout_sec", 300))
    default_total_timeout = int(
        get_threshold("ollama_total_timeout_sec", max(120, default_read_timeout))
    )
    llm_call_wall_clock_cap_sec = int(get_threshold("llm_call_wall_clock_cap_sec", 0))
    stall_abort_enabled = bool(get_threshold("llm_stall_abort_enabled", False))
    stall_tokens_per_sec = float(get_threshold("llm_stall_tokens_per_sec", 0.15))
    stall_min_elapsed_sec = float(get_threshold("llm_stall_min_elapsed_sec", 180))
    default_circuit_open_sec = int(get_threshold("ollama_circuit_open_sec", 180))
    circuit_open_sec = int(
        os.getenv("OLLAMA_CIRCUIT_OPEN_SEC", str(default_circuit_open_sec))
    )
    connect_timeout = int(os.getenv("OLLAMA_CONNECT_TIMEOUT_SEC", str(default_connect_timeout)))
    read_timeout = timeout_sec or int(
        os.getenv("OLLAMA_READ_TIMEOUT_SEC", str(default_read_timeout))
    )
    total_timeout = int(os.getenv("OLLAMA_TOTAL_TIMEOUT_SEC", str(default_total_timeout)))
    if timeout_sec:
        # If caller explicitly requests one timeout budget, honor it as upper bound.
        total_timeout = min(total_timeout, int(timeout_sec))
    if llm_call_wall_clock_cap_sec > 0:
        total_timeout = min(total_timeout, int(llm_call_wall_clock_cap_sec))
    total_timeout = max(1, int(total_timeout))
    now = time.monotonic()
    model_key = (model or "").strip().lower()
    if model_key:
        _TOUCHED_OLLAMA_MODELS.add(model_key)
    # Defensive reset for clock rollback/test monkeypatch scenarios.
    if now + 1.0 < _LAST_MONOTONIC_TS:
        log.warning("llm.circuit.reset reason=clock_rollback_detected")
        _CIRCUIT_OPEN_UNTIL = 0.0
        _CIRCUIT_OPEN_UNTIL_BY_MODEL = {}
    _LAST_MONOTONIC_TS = now

    # Some tests monkeypatch monotonic() to 0.0. In that synthetic clock mode,
    # stale in-memory circuit state from prior calls is not meaningful.
    if now <= 1.0 and _CIRCUIT_OPEN_UNTIL > 1.0:
        log.debug("llm.circuit.reset reason=synthetic_clock_mode")
        _CIRCUIT_OPEN_UNTIL = 0.0
    if now <= 1.0 and _CIRCUIT_OPEN_UNTIL_BY_MODEL:
        log.debug("llm.circuit.reset reason=synthetic_clock_mode model_count=%s", len(_CIRCUIT_OPEN_UNTIL_BY_MODEL))
        _CIRCUIT_OPEN_UNTIL_BY_MODEL = {}

    # Guard against stale in-memory circuit state leaking across unrelated runs.
    max_reasonable_open_window = max(1.0, float(circuit_open_sec)) * 4.0
    if (_CIRCUIT_OPEN_UNTIL - now) > max_reasonable_open_window:
        log.warning(
            "llm.circuit.reset reason=stale_state_pruned window_sec=%.1f open_duration=%.1f",
            max_reasonable_open_window,
            _CIRCUIT_OPEN_UNTIL - now,
        )
        _CIRCUIT_OPEN_UNTIL = 0.0
    if _CIRCUIT_OPEN_UNTIL_BY_MODEL:
        pruned = {}
        stale_count = 0
        for key, ts in _CIRCUIT_OPEN_UNTIL_BY_MODEL.items():
            if not isinstance(ts, (int, float)):
                stale_count += 1
                continue
            if ts <= now:
                continue
            if (ts - now) > max_reasonable_open_window:
                stale_count += 1
                continue
            pruned[key] = float(ts)
        if stale_count > 0:
            log.debug(
                "llm.circuit.reset reason=stale_model_states_pruned models_pruned=%s models_remaining=%s",
                stale_count,
                len(pruned),
            )
        _CIRCUIT_OPEN_UNTIL_BY_MODEL = pruned

    circuit_open_until = max(
        float(_CIRCUIT_OPEN_UNTIL),
        float(_CIRCUIT_OPEN_UNTIL_BY_MODEL.get(model_key, 0.0)),
    )
    if now < circuit_open_until:
        retry_after = max(1, int(circuit_open_until - now))
        elapsed_s = time.perf_counter() - call_started
        runtime_mode = os.getenv("FLIGHT_WATCHER_LLM_MODE", "full").strip().lower()

        # Log circuit breaker rejection
        if model_key and circuit_open_until == _CIRCUIT_OPEN_UNTIL_BY_MODEL.get(model_key, 0.0):
            log.warning(
                "llm.circuit.rejected scope=model model=%s retry_after_s=%s elapsed_s=%.3f",
                model_key,
                retry_after,
                elapsed_s,
            )
        else:
            log.warning(
                "llm.circuit.rejected scope=global retry_after_s=%s elapsed_s=%.3f",
                retry_after,
                elapsed_s,
            )

        _persist_metric(
            status="error",
            category="circuit_open",
            mode=runtime_mode,
            think=think,
            model=model,
            endpoint="none",
            attempts=0,
            elapsed_s=elapsed_s,
            retry_after_s=retry_after,
            error_count=1,
            error_text=f"retry after {retry_after}s",
        )
        log.warning(
            "llm.call.metrics status=error category=circuit_open mode=%s think=%s model=%s endpoint=none attempts=0 elapsed_s=%.3f retry_after_s=%s",
            runtime_mode,
            think,
            model,
            elapsed_s,
            retry_after,
        )
        raise RuntimeError(f"LLM request failed [circuit_open]: retry after {retry_after}s")

    # Log circuit recovery (transition from open to closed)
    if _CIRCUIT_OPEN_UNTIL > 0 and _CIRCUIT_OPEN_UNTIL <= now:
        log.info(
            "llm.circuit.close scope=global reason=timeout_expired elapsed_since_open=%.1f",
            now - (_CIRCUIT_OPEN_UNTIL - float(circuit_open_sec)),
        )
        _CIRCUIT_OPEN_UNTIL = 0.0

    if model_key and model_key in _CIRCUIT_OPEN_UNTIL_BY_MODEL:
        model_circuit_until = _CIRCUIT_OPEN_UNTIL_BY_MODEL[model_key]
        if model_circuit_until <= now:
            log.info(
                "llm.circuit.close scope=model model=%s reason=timeout_expired elapsed_since_open=%.1f",
                model_key,
                now - (model_circuit_until - float(circuit_open_sec)),
            )
            _CIRCUIT_OPEN_UNTIL_BY_MODEL.pop(model_key, None)
    deadline = now + float(total_timeout)

    # Log Ollama version info on first call (cached)
    if "http://localhost:11434" not in _OLLAMA_VERSION_CACHE:
        _detect_ollama_version("http://localhost:11434", timeout_sec=connect_timeout)

    chat_message = {"role": "user", "content": prompt}
    if isinstance(images, list) and images:
        chat_message["images"] = images
    chat_payload = {
        "model": model,
        "messages": [chat_message],
        "stream": False,
    }
    if json_mode:
        chat_payload["format"] = "json"
    if think is not None:
        chat_payload["think"] = think

    generate_payload = {
        "model": model,
        "prompt": prompt,
        "stream": False,
    }
    if isinstance(images, list) and images:
        generate_payload["images"] = list(images)
    if json_mode:
        generate_payload["format"] = "json"
    if think is not None:
        generate_payload["think"] = think

    options = {}
    if num_ctx is not None and int(num_ctx) > 0:
        options["num_ctx"] = int(num_ctx)
    if num_predict is not None and int(num_predict) > 0:
        options["num_predict"] = int(num_predict)
    if temperature is not None:
        options["temperature"] = float(temperature)
    if options:
        chat_payload["options"] = dict(options)
        generate_payload["options"] = dict(options)

    if json_mode and strict_json:
        chat_attempts = [
            ("http://localhost:11434/api/chat", chat_payload),
        ]
        generate_attempts = [
            ("http://localhost:11434/api/generate", generate_payload),
        ]
    else:
        chat_attempts = [
            ("http://localhost:11434/api/chat", chat_payload),
            ("http://localhost:11434/api/chat", {k: v for k, v in chat_payload.items() if k != "format"}),
        ]
        generate_attempts = [
            ("http://localhost:11434/api/generate", generate_payload),
            ("http://localhost:11434/api/generate", {k: v for k, v in generate_payload.items() if k != "format"}),
        ]
    policy = str(endpoint_policy or "auto").strip().lower()
    if policy == "generate_only":
        attempts = generate_attempts
    elif policy == "chat_only":
        attempts = chat_attempts + generate_attempts  # Fallback to generate if chat fails
    elif policy == "prefer_generate":
        attempts = generate_attempts + chat_attempts
    else:
        attempts = chat_attempts + generate_attempts

    errors = []
    timed_out_endpoints = set()
    saw_transport_timeout = False
    saw_token_cap = False
    last_done_reason = ""
    attempt_count = 0
    runtime_mode = os.getenv("FLIGHT_WATCHER_LLM_MODE", "full").strip().lower()
    for idx, (url, payload) in enumerate(attempts):
        if url in timed_out_endpoints:
            continue
        attempt_count += 1
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            if llm_call_wall_clock_cap_sec > 0:
                errors.append("llm_wall_clock_cap")
            else:
                errors.append("timeout_budget_exhausted")
            break
        # Reserve a fair share for each remaining unique endpoint so one stalled
        # attempt (usually /chat) doesn't starve the next fallback (/generate).
        remaining_urls = {
            u for (u, _) in attempts[idx:] if u not in timed_out_endpoints
        }
        endpoint_slots = max(1, len(remaining_urls))
        fair_read_budget = max(1, int(remaining / endpoint_slots))
        request_connect_timeout = max(1, int(min(float(connect_timeout), remaining)))
        request_read_timeout = max(
            1,
            int(min(float(read_timeout), remaining, fair_read_budget)),
        )
        try:
            response = requests.post(
                url,
                json=payload,
                timeout=(request_connect_timeout, request_read_timeout),
            )
            try:
                response.raise_for_status()
                body = response.json()
            finally:
                try:
                    response.close()
                except Exception:
                    pass
            text = _extract_text(body)
            perf = _response_perf(body)
            if text is not None:
                if str(text).strip():
                    elapsed_s = time.perf_counter() - call_started
                    if _llm_stall_detected(
                        tokens_per_sec=perf["tokens_per_sec"],
                        elapsed_s=elapsed_s,
                        enabled=stall_abort_enabled,
                        min_tokens_per_sec=stall_tokens_per_sec,
                        min_elapsed_sec=stall_min_elapsed_sec,
                    ):
                        _persist_metric(
                            status="error",
                            category="timeout_or_stall",
                            mode=runtime_mode,
                            think=think,
                            model=model,
                            endpoint=url.rsplit("/", 1)[-1],
                            attempts=attempt_count,
                            elapsed_s=elapsed_s,
                            done_reason=perf["done_reason"] or None,
                            eval_count=perf["eval_count"],
                            prompt_eval_count=perf["prompt_eval_count"],
                            tokens_per_sec=perf["tokens_per_sec"],
                            error_count=1,
                            error_text="llm_stalled",
                        )
                        log.warning(
                            "llm.call.metrics status=error category=timeout_or_stall mode=%s think=%s model=%s endpoint=%s attempts=%s elapsed_s=%.3f reason=llm_stalled eval_count=%s prompt_eval_count=%s tokens_per_sec=%s",
                            runtime_mode,
                            think,
                            model,
                            url.rsplit("/", 1)[-1],
                            attempt_count,
                            elapsed_s,
                            perf["eval_count"],
                            perf["prompt_eval_count"],
                            f"{perf['tokens_per_sec']:.2f}" if isinstance(perf["tokens_per_sec"], float) else "na",
                        )
                        raise RuntimeError("LLM request failed [timeout_or_stall]: llm_stalled")
                    _persist_metric(
                        status="ok",
                        mode=runtime_mode,
                        think=think,
                        model=model,
                        endpoint=url.rsplit("/", 1)[-1],
                        attempts=attempt_count,
                        elapsed_s=elapsed_s,
                        done_reason=perf["done_reason"] or "stop",
                        eval_count=perf["eval_count"],
                        prompt_eval_count=perf["prompt_eval_count"],
                        tokens_per_sec=perf["tokens_per_sec"],
                    )
                    log.info(
                        "llm.call.metrics status=ok mode=%s think=%s model=%s endpoint=%s attempts=%s elapsed_s=%.3f done_reason=%s eval_count=%s prompt_eval_count=%s tokens_per_sec=%s",
                        runtime_mode,
                        think,
                        model,
                        url.rsplit("/", 1)[-1],
                        attempt_count,
                        elapsed_s,
                        perf["done_reason"] or "stop",
                        perf["eval_count"],
                        perf["prompt_eval_count"],
                        f"{perf['tokens_per_sec']:.2f}" if isinstance(perf["tokens_per_sec"], float) else "na",
                    )
                    return text
                done_reason = perf["done_reason"]
                last_done_reason = done_reason or last_done_reason
                if done_reason == "length":
                    saw_token_cap = True
                    errors.append(f"{url} empty_output done_reason=length token_cap")
                else:
                    if done_reason in ("", None):
                        keys = list(body.keys()) if isinstance(body, dict) else []
                        errors.append(
                            f"{url} empty_output done_reason='' keys={keys}"
                        )
                        continue
                    errors.append(f"{url} empty_output done_reason={done_reason!r}")
                continue

            if isinstance(body, dict) and body.get("error"):
                errors.append(f"{url} error={body.get('error')}")
            else:
                keys = list(body.keys()) if isinstance(body, dict) else []
                errors.append(f"{url} missing text field keys={keys}")
        except requests.exceptions.Timeout as exc:
            saw_transport_timeout = True
            timed_out_endpoints.add(url)
            errors.append(f"{url} {type(exc).__name__}: {exc}")
            if fail_fast_on_timeout:
                break
        except Exception as exc:
            errors.append(f"{url} {type(exc).__name__}: {exc}")

    if saw_transport_timeout and circuit_open_sec > 0:
        opened_until = time.monotonic() + float(circuit_open_sec)
        if model_key:
            _CIRCUIT_OPEN_UNTIL_BY_MODEL[model_key] = float(opened_until)
            log.error(
                "llm.circuit.open scope=model model=%s opened_until_sec=%.1f reason=transport_timeout",
                model_key,
                circuit_open_sec,
            )
        else:
            _CIRCUIT_OPEN_UNTIL = float(opened_until)
            log.error(
                "llm.circuit.open scope=global opened_until_sec=%.1f reason=transport_timeout",
                circuit_open_sec,
            )

    category = "unknown"
    if saw_token_cap:
        category = "token_cap"
    elif any("llm_stalled" in str(err).lower() for err in errors):
        category = "timeout_or_stall"
    elif any("llm_wall_clock_cap" in str(err).lower() for err in errors):
        category = "timeout_or_stall"
    elif saw_transport_timeout or any("timeout" in str(err).lower() for err in errors):
        category = "timeout"
    elif any("empty_output" in str(err).lower() for err in errors):
        category = "empty_output"
    elif any("httperror" in str(err).lower() or "500" in str(err) for err in errors):
        category = "server_error"

    elapsed_s = time.perf_counter() - call_started

    # Log Ollama version info in case of failure for debugging
    version_info = _OLLAMA_VERSION_CACHE.get("http://localhost:11434")
    version_log_part = ""
    if version_info:
        version_log_part = f" ollama_version={version_info.get('version','?')}"

    _persist_metric(
        status="error",
        category=category,
        mode=runtime_mode,
        think=think,
        model=model,
        endpoint="none",
        attempts=attempt_count,
        elapsed_s=elapsed_s,
        done_reason=last_done_reason or None,
        error_count=len(errors),
        error_text=" | ".join(errors),
    )
    log.warning(
        "llm.call.metrics status=error category=%s mode=%s think=%s model=%s endpoint=none attempts=%s elapsed_s=%.3f error_count=%s%s",
        category,
        runtime_mode,
        think,
        model,
        attempt_count,
        elapsed_s,
        len(errors),
        version_log_part,
    )
    raise RuntimeError(f"LLM request failed [{category}] after fallbacks:" + version_log_part + " " + " | ".join(errors))


def _persist_metric(**kwargs) -> None:
    """Best-effort persistence of LLM call metrics into SQLite."""
    try:
        save_llm_metric(**kwargs)
    except Exception as exc:
        log.warning("llm.call.metrics_persist_failed error=%s", exc)


def reset_llm_circuit_state(model: Optional[str] = None) -> None:
    """Reset in-memory circuit breaker state globally or for one model."""
    global _CIRCUIT_OPEN_UNTIL, _CIRCUIT_OPEN_UNTIL_BY_MODEL, _VLM_VALIDATION_FAILURES
    if isinstance(model, str) and model.strip():
        model_key = model.strip().lower()
        if model_key in _CIRCUIT_OPEN_UNTIL_BY_MODEL:
            log.info(
                "llm.circuit.reset scope=model model=%s previous_state=open",
                model_key,
            )
            _CIRCUIT_OPEN_UNTIL_BY_MODEL.pop(model_key, None)
        else:
            log.debug(
                "llm.circuit.reset scope=model model=%s previous_state=closed",
                model_key,
            )
        return

    # Global reset
    had_open_global = _CIRCUIT_OPEN_UNTIL > time.monotonic()
    had_open_models = bool(_CIRCUIT_OPEN_UNTIL_BY_MODEL)

    if had_open_global:
        log.info("llm.circuit.reset scope=global previous_state=open")
    if had_open_models:
        model_count = len(_CIRCUIT_OPEN_UNTIL_BY_MODEL)
        log.info(
            "llm.circuit.reset scope=models count=%s previous_state=open",
            model_count,
        )

    _CIRCUIT_OPEN_UNTIL = 0.0
    _CIRCUIT_OPEN_UNTIL_BY_MODEL = {}
    _VLM_VALIDATION_FAILURES = 0


def record_vlm_validation_failure() -> None:
    """Record VLM validation failure for Phase 5 circuit-open detection."""
    global _VLM_VALIDATION_FAILURES
    _VLM_VALIDATION_FAILURES += 1

    remaining_before_open = max(0, _VLM_VALIDATION_FAILURE_THRESHOLD - _VLM_VALIDATION_FAILURES)

    if _VLM_VALIDATION_FAILURES >= _VLM_VALIDATION_FAILURE_THRESHOLD:
        log.error(
            "llm.vlm_circuit.open failures_count=%s threshold=%s reason=validation_failure_threshold_reached",
            _VLM_VALIDATION_FAILURES,
            _VLM_VALIDATION_FAILURE_THRESHOLD,
        )
    else:
        log.warning(
            "llm.vlm_circuit.failure failures_count=%s threshold=%s remaining_failures_until_open=%s",
            _VLM_VALIDATION_FAILURES,
            _VLM_VALIDATION_FAILURE_THRESHOLD,
            remaining_before_open,
        )


def get_vlm_validation_failure_count() -> int:
    """Get current VLM validation failure counter."""
    return _VLM_VALIDATION_FAILURES


def should_skip_vlm_extraction(model: Optional[str] = None) -> bool:
    """
    Check if VLM extraction should be skipped due to validation circuit-open.

    Phase 5: Fast-fail when VLM has repeated validation failures,
    preventing cascading errors across multiple scenarios.
    """
    if _VLM_VALIDATION_FAILURES >= _VLM_VALIDATION_FAILURE_THRESHOLD:
        log.debug(
            "llm.vlm_circuit.skip reason=validation_failures_exceeded failures_count=%s threshold=%s",
            _VLM_VALIDATION_FAILURES,
            _VLM_VALIDATION_FAILURE_THRESHOLD,
        )
        return True
    if isinstance(model, str) and model.strip():
        model_lower = model.strip().lower()
        circuit_until = _CIRCUIT_OPEN_UNTIL_BY_MODEL.get(model_lower, 0.0)
        if time.monotonic() < circuit_until:
            log.debug(
                "llm.vlm_circuit.skip reason=model_circuit_open model=%s",
                model_lower,
            )
            return True

    if time.monotonic() < _CIRCUIT_OPEN_UNTIL:
        log.debug(
            "llm.vlm_circuit.skip reason=global_circuit_open",
        )
        return True

    return False


def get_touched_ollama_models(*, reset: bool = False) -> list[str]:
    """Return sorted Ollama models used in this process.

    This tracks model names touched by `call_llm()` and is used for best-effort
    model unload at process teardown. It does NOT indicate the Ollama server was
    started by this process.
    """
    models = sorted(str(m) for m in _TOUCHED_OLLAMA_MODELS if isinstance(m, str) and m.strip())
    if reset:
        _TOUCHED_OLLAMA_MODELS.clear()
    return models


def release_touched_ollama_models(
    *,
    host: str = "http://localhost:11434",
    timeout_sec: int = 3,
) -> dict:
    """Best-effort unload touched Ollama models from server memory.

    This does not stop `ollama serve`. It only asks Ollama to unload model
    runners (`keep_alive=0`) for models used during this process.
    """
    timeout_sec = max(1, int(timeout_sec))
    models = get_touched_ollama_models(reset=False)
    if not models:
        return {
            "ok": True,
            "attempted": 0,
            "released": [],
            "failed": {},
            "host": host,
            "reason": "no_touched_models",
        }

    released = []
    failed = {}
    unload_url = f"{str(host).rstrip('/')}/api/generate"
    for model_name in models:
        payload = {
            "model": model_name,
            "prompt": "",
            "stream": False,
            "keep_alive": 0,
        }
        try:
            response = requests.post(
                unload_url,
                json=payload,
                timeout=(timeout_sec, timeout_sec),
            )
            try:
                response.raise_for_status()
            finally:
                try:
                    response.close()
                except Exception:
                    pass
            released.append(model_name)
        except Exception as exc:
            failed[model_name] = f"{type(exc).__name__}: {exc}"

    if not failed:
        _TOUCHED_OLLAMA_MODELS.clear()
    else:
        _TOUCHED_OLLAMA_MODELS.difference_update(released)

    result = {
        "ok": not bool(failed),
        "attempted": len(models),
        "released": released,
        "failed": failed,
        "host": host,
    }
    if failed:
        log.warning(
            "ollama.release_touched_models ok=%s attempted=%s released=%s failed=%s",
            result["ok"],
            result["attempted"],
            len(released),
            len(failed),
        )
    else:
        log.info(
            "ollama.release_touched_models ok=true attempted=%s released=%s",
            result["attempted"],
            len(released),
        )
    return result
