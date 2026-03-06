"""Dedicated smoke test script for local Ollama server health.

Run manually from project root:
    python tests/manual/ollama_server_smoke.py --model qwen3:8b
    python tests/manual/ollama_server_smoke.py --model qwen3:8b --vlm-image storage/debug_html/scenario_google_flights_last.png
    python tests/manual/ollama_server_smoke.py --model qwen3-vl:8b --vlm-image storage/debug_html/scenario_google_flights_last.png --extreme-only
"""

import argparse
import math
import os
import platform
from pathlib import Path
import sys
import time
from typing import Any, Dict, List, Optional, Tuple

import requests

# Ensure project-root imports (e.g. `llm.code_model`) work when running
# `python tests/manual/ollama_server_smoke.py` directly.
_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Smoke test local Ollama server endpoints.")
    parser.add_argument("--host", default="http://localhost:11434", help="Ollama base URL.")
    parser.add_argument("--model", required=True, help="Model name to test (e.g. qwen3:8b).")
    parser.add_argument(
        "--timeout-sec",
        type=int,
        default=1200,
        help="HTTP timeout seconds for each request.",
    )
    parser.add_argument(
        "--runs",
        type=int,
        default=1,
        help="Number of timing runs per endpoint/mode (default: 1).",
    )
    parser.add_argument(
        "--prompt",
        default="Reply with exactly: OK",
        help="Prompt used for generate/chat checks.",
    )
    parser.add_argument(
        "--num-predict",
        type=int,
        default=128,
        help="num_predict for think_off runs.",
    )
    parser.add_argument(
        "--think-num-predict",
        type=int,
        default=512,
        help="num_predict for think_on runs.",
    )
    parser.add_argument(
        "--num-ctx",
        type=int,
        default=0,
        help="Optional num_ctx override (0 = use Ollama/model default).",
    )
    parser.add_argument(
        "--json-mode",
        action="store_true",
        help="Set format=json in requests.",
    )
    parser.add_argument(
        "--strict-think-output",
        action="store_true",
        help="Treat think_on empty output with done_reason=length as hard failure.",
    )
    parser.add_argument(
        "--vlm-image",
        default="",
        help="Optional screenshot path for real-case VLM stress test.",
    )
    parser.add_argument(
        "--vlm-site",
        default="google_flights",
        help="Site key for VLM extraction context (default: google_flights).",
    )
    parser.add_argument("--origin", default="HND", help="Origin IATA/context for VLM test.")
    parser.add_argument("--dest", default="ITM", help="Destination IATA/context for VLM test.")
    parser.add_argument("--depart", default="", help="Departure date (YYYY-MM-DD) for VLM test.")
    parser.add_argument("--return-date", default="", help="Return date (YYYY-MM-DD) for VLM test.")
    parser.add_argument(
        "--vlm-timeout-sec",
        type=int,
        default=0,
        help="Timeout seconds for each VLM call (0 = use --timeout-sec).",
    )
    parser.add_argument(
        "--no-vlm-reset-circuit-between-runs",
        action="store_true",
        help="Disable circuit reset between VLM runs (default behavior is reset enabled).",
    )
    parser.add_argument(
        "--extreme-only",
        action="store_true",
        help="Run only the real-case VLM stress section (skip generate/chat benchmarks).",
    )
    parser.add_argument(
        "--min-vlm-success-rate",
        type=float,
        default=0.2,
        help="Minimum acceptable VLM success rate [0..1] for extreme mode checks.",
    )
    parser.add_argument(
        "--no-vlm-skip-ui-on-extract-timeout",
        action="store_true",
        help="Always run VLM UI-assist even when extract already failed by timeout.",
    )
    return parser.parse_args()


def _post_json(url: str, payload: Dict[str, Any], timeout_sec: int) -> Dict[str, Any]:
    response = requests.post(url, json=payload, timeout=(5, timeout_sec))
    response.raise_for_status()
    body = response.json()
    if not isinstance(body, dict):
        raise RuntimeError(f"Non-JSON object response from {url}: {type(body).__name__}")
    return body


def _timed_post_json(url: str, payload: Dict[str, Any], timeout_sec: int) -> Tuple[Dict[str, Any], float]:
    started = time.perf_counter()
    body = _post_json(url, payload, timeout_sec)
    elapsed = time.perf_counter() - started
    return body, elapsed


def _extract_text(body: Dict[str, Any]) -> str:
    if isinstance(body.get("response"), str):
        return body["response"]
    message = body.get("message")
    if isinstance(message, dict) and isinstance(message.get("content"), str):
        return message["content"]
    if isinstance(message, dict) and isinstance(message.get("thinking"), str):
        # Some reasoning models emit thinking separately.
        return message["thinking"]
    return ""


def _to_seconds(value: Any) -> Optional[float]:
    if isinstance(value, (int, float)):
        return float(value) / 1_000_000_000.0
    return None


def _extract_metrics(body: Dict[str, Any]) -> Dict[str, Any]:
    metrics: Dict[str, Any] = {
        "done_reason": body.get("done_reason") if isinstance(body.get("done_reason"), str) else "",
        "eval_count": body.get("eval_count") if isinstance(body.get("eval_count"), int) else 0,
        "prompt_eval_count": body.get("prompt_eval_count")
        if isinstance(body.get("prompt_eval_count"), int)
        else 0,
    }
    metrics["load_duration_s"] = _to_seconds(body.get("load_duration"))
    metrics["total_duration_s"] = _to_seconds(body.get("total_duration"))
    metrics["prompt_eval_duration_s"] = _to_seconds(body.get("prompt_eval_duration"))
    metrics["eval_duration_s"] = _to_seconds(body.get("eval_duration"))
    eval_duration = metrics.get("eval_duration_s")
    eval_count = metrics.get("eval_count", 0)
    if isinstance(eval_duration, float) and eval_duration > 0 and eval_count > 0:
        metrics["tokens_per_sec"] = float(eval_count) / eval_duration
    else:
        metrics["tokens_per_sec"] = None
    return metrics


def _host_report() -> None:
    print("[0] Host snapshot")
    print(
        f"- platform={platform.system()} {platform.release()} "
        f"machine={platform.machine()} python={platform.python_version()}"
    )
    print(
        f"- cpu_logical={os.cpu_count()} processor={platform.processor() or 'unknown'}"
    )
    if hasattr(os, "getloadavg"):
        try:
            load1, load5, load15 = os.getloadavg()
            print(f"- load_avg_1m={load1:.2f} load_avg_5m={load5:.2f} load_avg_15m={load15:.2f}")
        except Exception:
            pass


def _percentile(values: List[float], q: float) -> float:
    """Nearest-rank percentile for small sample sets."""
    if not values:
        return 0.0
    q = max(0.0, min(1.0, float(q)))
    ordered = sorted(values)
    rank = max(1, int(math.ceil(q * len(ordered))))
    return float(ordered[rank - 1])


def _is_vlm_model_name(model: str) -> bool:
    """Best-effort detector for multimodal/vision model names."""
    name = str(model or "").strip().lower()
    return "vl" in name or "vision" in name


def main() -> int:
    args = _parse_args()
    base = args.host.rstrip("/")
    failures: List[Tuple[str, str]] = []
    warnings: List[Tuple[str, str]] = []
    timings: List[Dict[str, Any]] = []
    vlm_timings: List[Dict[str, Any]] = []
    is_vlm_model = _is_vlm_model_name(args.model)

    if args.extreme_only and not args.vlm_image:
        print("ERROR: --extreme-only requires --vlm-image")
        return 1

    _host_report()

    print(f"[1] Checking tags endpoint: {base}/api/tags")
    installed_names = set()
    tags_ok = False
    try:
        tags_resp = requests.get(f"{base}/api/tags", timeout=(5, args.timeout_sec))
        tags_resp.raise_for_status()
        tags_body = tags_resp.json()
        models = tags_body.get("models", []) if isinstance(tags_body, dict) else []
        installed_names = {m.get("name") for m in models if isinstance(m, dict)}
        print(f"Found {len(installed_names)} models.")
        if args.model not in installed_names:
            raise RuntimeError(f"model '{args.model}' not found in /api/tags")
        tags_ok = True
    except Exception as exc:
        print(f"ERROR [1]: {exc}")
        failures.append(("tags", str(exc)))

    def _run_case(name: str, endpoint: str, think: bool) -> None:
        mode = "think_on" if think else "think_off"
        print(f"Testing {name} ({mode}) with model={args.model} runs={args.runs}")
        last_text = ""
        run_times: List[float] = []
        run_tps: List[float] = []
        run_load_s: List[float] = []
        for run_idx in range(1, max(1, args.runs) + 1):
            try:
                options: Dict[str, Any] = {
                    "num_predict": int(args.think_num_predict if think else args.num_predict),
                    "temperature": 0,
                }
                if args.num_ctx and args.num_ctx > 0:
                    options["num_ctx"] = int(args.num_ctx)
                if endpoint == "generate":
                    payload = {
                        "model": args.model,
                        "prompt": args.prompt,
                        "stream": False,
                        "think": think,
                        "options": options,
                    }
                    if args.json_mode:
                        payload["format"] = "json"
                    body, elapsed = _timed_post_json(
                        f"{base}/api/generate",
                        payload,
                        args.timeout_sec,
                    )
                else:
                    payload = {
                        "model": args.model,
                        "messages": [{"role": "user", "content": args.prompt}],
                        "stream": False,
                        "think": think,
                        "options": options,
                    }
                    if args.json_mode:
                        payload["format"] = "json"
                    body, elapsed = _timed_post_json(
                        f"{base}/api/chat",
                        payload,
                        args.timeout_sec,
                    )

                metrics = _extract_metrics(body)
                text = _extract_text(body).strip()
                reason = metrics.get("done_reason", "")
                sample = {
                    "endpoint": endpoint,
                    "think": think,
                    "elapsed_s": elapsed,
                    "text_nonempty": bool(text),
                    "metrics": metrics,
                }
                if not text:
                    msg = f"/api/{endpoint} returned empty text (done_reason={reason!r})"
                    if reason == "length" and not args.strict_think_output and (think or is_vlm_model):
                        warnings.append((f"{endpoint}_{mode}_run{run_idx}", msg))
                        run_times.append(elapsed)
                        timings.append(sample)
                        print(
                            f"- run {run_idx}: WARN in {elapsed:.2f}s ({msg})"
                        )
                        continue
                    raise RuntimeError(msg)
                last_text = text[:120]
                run_times.append(elapsed)
                timings.append(sample)
                if isinstance(metrics.get("tokens_per_sec"), float):
                    run_tps.append(float(metrics["tokens_per_sec"]))
                if isinstance(metrics.get("load_duration_s"), float):
                    run_load_s.append(float(metrics["load_duration_s"]))
                print(
                    f"- run {run_idx}: ok in {elapsed:.2f}s "
                    f"(reason={reason or 'stop'} tps={metrics.get('tokens_per_sec') or 'n/a'})"
                )
            except Exception as exc:
                print(f"- run {run_idx}: ERROR: {exc}")
                failures.append((f"{endpoint}_{mode}_run{run_idx}", str(exc)))

        if run_times:
            avg_s = sum(run_times) / len(run_times)
            min_s = min(run_times)
            max_s = max(run_times)
            extra = ""
            if run_tps:
                extra += f" tps_avg={sum(run_tps)/len(run_tps):.1f}"
            if run_load_s:
                extra += f" load_avg={sum(run_load_s)/len(run_load_s):.2f}s"
            print(
                f"/api/{endpoint} {mode} response: {last_text!r} "
                f"(avg={avg_s:.2f}s min={min_s:.2f}s max={max_s:.2f}s{extra})"
            )

    if args.extreme_only:
        print("[2] Extreme-only mode: skipping /api/generate and /api/chat benchmarks")
    else:
        print(f"[2] Benchmarking /api/generate")
        _run_case("generate", "generate", think=False)
        _run_case("generate", "generate", think=True)

        print(f"[3] Benchmarking /api/chat")
        _run_case("chat", "chat", think=False)
        _run_case("chat", "chat", think=True)

        print("[4] Timing summary")
        if timings:
            for endpoint in ("generate", "chat"):
                for think in (False, True):
                    samples = [
                        t["elapsed_s"]
                        for t in timings
                        if t.get("endpoint") == endpoint and bool(t.get("think")) == think
                    ]
                    if not samples:
                        continue
                    avg_s = sum(samples) / len(samples)
                    p95_s = _percentile(samples, 0.95)
                    mode = "think_on" if think else "think_off"
                    print(f"- /api/{endpoint} {mode}: avg={avg_s:.2f}s p95={p95_s:.2f}s n={len(samples)}")
                    if len(samples) >= 3:
                        warm = samples[0]
                        steady = sum(samples[1:]) / len(samples[1:])
                        if steady > 0:
                            print(f"  warmup_factor={warm/steady:.2f}x (run1 vs steady)")
        else:
            print("- no successful timing samples")

    if args.vlm_image:
        print("[5] Real-case VLM stress")
        try:
            from llm.code_model import analyze_page_ui_with_vlm, parse_image_with_vlm
        except Exception as exc:
            failures.append(("vlm_import", str(exc)))
            print(f"ERROR [5]: cannot import VLM helpers: {exc}")
        else:
            vlm_timeout = int(args.vlm_timeout_sec or args.timeout_sec)
            reset_circuit = not bool(args.no_vlm_reset_circuit_between_runs)
            skip_ui_on_extract_timeout = not bool(args.no_vlm_skip_ui_on_extract_timeout)
            reset_fn = None
            if reset_circuit:
                try:
                    from llm.llm_client import reset_llm_circuit_state
                    reset_fn = reset_llm_circuit_state
                except Exception as exc:
                    print(f"- WARN: could not import circuit reset helper: {exc}")
                    reset_circuit = False
            print(
                f"- image={args.vlm_image} site={args.vlm_site} runs={max(1, args.runs)} "
                f"timeout={vlm_timeout}s reset_circuit={reset_circuit}"
            )
            for run_idx in range(1, max(1, args.runs) + 1):
                if reset_circuit and callable(reset_fn):
                    try:
                        reset_fn(args.model)
                    except Exception:
                        pass
                started = time.perf_counter()
                extract_reason = ""
                try:
                    parsed = parse_image_with_vlm(
                        args.vlm_image,
                        site=args.vlm_site,
                        task="price",
                        origin=args.origin,
                        dest=args.dest,
                        depart=args.depart,
                        return_date=args.return_date,
                        timeout_sec=vlm_timeout,
                    )
                    elapsed = time.perf_counter() - started
                    price = parsed.get("price")
                    reason = str(parsed.get("reason") or "")
                    extract_reason = reason
                    ok = price is not None
                    vlm_timings.append(
                        {
                            "kind": "extract",
                            "elapsed_s": elapsed,
                            "ok": ok,
                            "reason": reason,
                        }
                    )
                    print(
                        f"- run {run_idx} extract: {'ok' if ok else 'miss'} "
                        f"in {elapsed:.2f}s price={price} reason={reason or 'n/a'}"
                    )
                except Exception as exc:
                    failures.append((f"vlm_extract_run{run_idx}", str(exc)))
                    print(f"- run {run_idx} extract: ERROR: {exc}")
                    extract_reason = "exception"

                if skip_ui_on_extract_timeout and extract_reason.startswith("llm_request_failed_"):
                    print(f"- run {run_idx} ui_assist: skipped (extract request failure)")
                    continue

                started = time.perf_counter()
                if reset_circuit and callable(reset_fn):
                    try:
                        reset_fn(args.model)
                    except Exception:
                        pass
                try:
                    ui = analyze_page_ui_with_vlm(
                        args.vlm_image,
                        site=args.vlm_site,
                        origin=args.origin,
                        dest=args.dest,
                        depart=args.depart,
                        return_date=args.return_date,
                        timeout_sec=vlm_timeout,
                    )
                    elapsed = time.perf_counter() - started
                    ok = isinstance(ui, dict) and bool(ui)
                    vlm_timings.append(
                        {
                            "kind": "ui_assist",
                            "elapsed_s": elapsed,
                            "ok": ok,
                            "reason": str(ui.get("page_scope", "")) if isinstance(ui, dict) else "",
                        }
                    )
                    if ok:
                        scope = ui.get("page_scope")
                        product = ui.get("trip_product")
                        print(
                            f"- run {run_idx} ui_assist: ok in {elapsed:.2f}s "
                            f"(scope={scope} product={product})"
                        )
                    else:
                        print(f"- run {run_idx} ui_assist: miss in {elapsed:.2f}s")
                except Exception as exc:
                    failures.append((f"vlm_ui_run{run_idx}", str(exc)))
                    print(f"- run {run_idx} ui_assist: ERROR: {exc}")

            if vlm_timings:
                print("- VLM timing summary")
                success_rates: Dict[str, float] = {}
                for kind in ("extract", "ui_assist"):
                    samples = [s for s in vlm_timings if s.get("kind") == kind]
                    if not samples:
                        continue
                    elapsed = [float(s["elapsed_s"]) for s in samples]
                    avg_s = sum(elapsed) / len(elapsed)
                    p95_s = _percentile(elapsed, 0.95)
                    success_rate = sum(1 for s in samples if bool(s.get("ok"))) / len(samples)
                    success_rates[kind] = success_rate
                    print(
                        f"  - {kind}: avg={avg_s:.2f}s p95={p95_s:.2f}s "
                        f"success_rate={success_rate:.0%} n={len(samples)}"
                    )

                worst = max(float(s["elapsed_s"]) for s in vlm_timings)
                recommended = int(max(60, worst * 2.2))
                print(
                    f"  - suggested vlm_extract_timeout_sec ~= {recommended}s "
                    f"(2.2x worst-case run)"
                )
                min_rate = max(0.0, min(1.0, float(args.min_vlm_success_rate)))
                for kind in ("extract", "ui_assist"):
                    if kind not in success_rates:
                        continue
                    if success_rates[kind] < min_rate:
                        failures.append(
                            (
                                f"vlm_{kind}_success_rate",
                                f"{success_rates[kind]:.3f} < min_rate {min_rate:.3f}",
                            )
                        )

    print("[6] Summary")
    if warnings:
        print(f"WARN checks: {len(warnings)}")
        for name, reason in warnings:
            print(f"- {name}: {reason}")
    if failures:
        print(f"FAILED checks: {len(failures)}")
        for name, reason in failures:
            print(f"- {name}: {reason}")
        if not tags_ok:
            print("Hint: if /api/tags fails, check if Ollama server is running.")
        elif installed_names and args.model not in installed_names:
            print("Hint: pull model first, e.g. `ollama pull <model>`. ")
        else:
            joined = " | ".join(reason for _, reason in failures).lower()
            if "done_reason='length'" in joined:
                print(
                    "Hint: output hit token limit. Increase --think-num-predict or "
                    "disable think for latency-sensitive paths."
                )
            elif "timed out" in joined:
                print(
                    "Hint: server reachable but requests timed out. Try a smaller model or larger --timeout-sec."
                )
            else:
                print("Hint: check Ollama logs for server-side model/runtime errors.")
        return 1

    print("All checks passed.")
    if timings:
        # Recommend a read-timeout target from observed slow-path timings.
        slow_path = [
            t["elapsed_s"]
            for t in timings
            if bool(t.get("think"))
        ]
        if slow_path:
            suggested = int(max(30, (max(slow_path) * 2.0)))
            print(
                f"Suggested config: set llm_planner_timeout_sec and llm_repair_timeout_sec "
                f"to around {suggested}s based on think_on runs."
            )
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except requests.exceptions.RequestException as exc:
        print(f"ERROR: HTTP failure: {exc}")
        raise SystemExit(1)
    except Exception as exc:  # pragma: no cover - manual script guard
        print(f"ERROR: {exc}")
        raise SystemExit(1)
