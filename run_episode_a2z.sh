#!/usr/bin/env bash
set -euo pipefail

# run_episode_a2z.sh (TEE MODE, macOS-friendly)
# A2Z = run -> triage -> auto-heal (sandbox) -> summarize
# Tee mode captures stdout/stderr into a staging dir, then relocates to:
#   storage/runs/<run_id>/episode/stdout.log
#
# New: Debug budget profile selection (lite/deep/super_deep) for instrumentation runs.
# Policy remains in configs/thresholds.yaml; this script only selects profile per episode.

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT_DIR"

ts() { date +"%Y-%m-%d %H:%M:%S"; }
hr() { echo "------------------------------------------------------------"; }
ep_log() {
  # Print to console and append to episode stdout log when available.
  local msg="$1"
  echo "$msg"
  if [[ -n "${STDOUT_LOG:-}" ]]; then
    printf "%s\n" "$msg" >> "$STDOUT_LOG"
  fi
}
env_vars_remove_key() {
  # Remove any KEY=... entry from ENV_VARS in-place.
  local key="$1"
  local keep=()
  local item
  for item in "${ENV_VARS[@]:-}"; do
    if [[ "$item" == "${key}="* ]]; then
      continue
    fi
    keep+=("$item")
  done
  ENV_VARS=("${keep[@]}")
}

# ---- Config knobs (override via env) ----------------------------------------
: "${A2Z_DEBUG:=1}"                          # 1=debug mode on
: "${A2Z_DEBUG_PROFILE:=lite}"               # lite|deep|super_deep (selects debug_budgets profile)
: "${A2Z_DEBUG_ESCALATE:=1}"                 # 1=allow bounded escalation if runtime supports it
: "${A2Z_AUTO_HEAL:=1}"                      # 1=run auto-heal step after run
: "${A2Z_APPLY:=0}"                          # 1=apply patch in sandbox
: "${A2Z_LLM:=0}"                            # 1=allow LLM in auto-heal (default off)
: "${A2Z_TEST_CMD:=pytest -q tests/test_triage.py tests/test_failure_reasons.py}"
: "${A2Z_MAX_FILES:=2}"
: "${A2Z_MAX_LINES:=80}"
: "${A2Z_EPISODE_ROOT:=storage/runs/_episodes_staging}"
A2Z_TIMEOUT_SEC_WAS_SET=0
if [[ -n "${A2Z_TIMEOUT_SEC+x}" ]]; then
  A2Z_TIMEOUT_SEC_WAS_SET=1
fi
: "${A2Z_TIMEOUT_SEC:=0}"                    # 0=no wrapper timeout; set e.g. 1800
: "${A2Z_MAIN_ARGS:=}"                       # extra args appended to main.py, e.g. "--services google_flights"
: "${A2Z_MULTIMODAL_MODE:=judge_primary}"    # off|assist|primary|judge|judge_primary (default)
: "${A2Z_CAPTURE_FIXTURE:=0}"                # 1=best-effort fixture capture after run (debug helper)
: "${A2Z_ALLOW_HUMAN_INTERVENTION:=0}"       # 0=off by default (public-safe), 1=allow manual intervention window
: "${A2Z_HUMAN_INTERVENTION_MODE:=off}"      # off|assist|demo (demo=human A->Z, machine observer/loggers)
: "${A2Z_LAST_RESORT_HUMAN_INTERVENTION_WHEN_DISABLED:=1}" # 1=force one final manual window even when A2Z_ALLOW_HUMAN_INTERVENTION=0
: "${A2Z_MANUAL_INTERVENTION_TIMEOUT_SEC:=120}" # Manual intervention wait budget (seconds)
: "${A2Z_BROWSER_STORAGE_STATE_ENABLED:=1}"  # 1=persist/reuse browser storage state (cookies/localStorage)
: "${A2Z_BROWSER_STORAGE_STATE_PATH:=}"      # optional explicit storage state path
: "${A2Z_BROWSER_BLOCK_HEAVY_RESOURCES:=0}"  # 0=keep page resources for anti-bot scripts (recommended for Skyscanner)
# Phase 1 rollout for scenario->extractor pre-extract verdict v2.
# off    = do not emit v2 env vars (use config defaults)
# shadow = v2 disabled, shadow-compare logging enabled (recommended first)
# canary = v2 enabled, shadow-compare logging disabled (small batch only)
: "${A2Z_PREEXTRACT_VERDICT_V2_MODE:=shadow}" # off|shadow|canary
# Phase 2 rollout for Google Flights fallback-root month-header readiness gate.
# off    = gate disabled
# canary = gate enabled (Google legacy date-picker fail-fast on invalid fallback root)
# Default is canary because Phase 2 is promoted to baseline behavior in wrapper runs.
: "${A2Z_GF_DATE_ROOT_GATE_MODE:=canary}"    # off|canary
# Phase 3 rollout for Google Flights deeplink page-state recovery (bounded).
# off    = disabled
# canary = enabled (one-shot route-form activation + rebind on irrelevant-page deeplink fallback)
# Default is canary because Phase 3 is promoted to baseline behavior in wrapper runs.
: "${A2Z_GF_DEEPLINK_RECOVERY_MODE:=canary}" # off|canary
# Debug budgets env vars (only used when A2Z_DEBUG=1):
# Set DEBUG_BUDGETS_PROFILE to override debug_profile (lite|deep|super_deep), e.g:
#   export DEBUG_BUDGETS_PROFILE=deep    # Collect more evidence (2.0× timeout, +1 retry)
#   export DEBUG_BUDGETS_PROFILE=super_deep  # Long exploratory runs (stronger bounded escalation)
# Set DEBUG_BUDGETS_ESCALATE to control escalation (0|1), e.g:
#   export DEBUG_BUDGETS_ESCALATE=1      # Allow bounded escalation (default)
# Examples:
#   A2Z_DEBUG=1 DEBUG_BUDGETS_PROFILE=deep ./run_episode_a2z.sh
#   A2Z_DEBUG=1 DEBUG_BUDGETS_PROFILE=super_deep ./run_episode_a2z.sh
#   A2Z_DEBUG=1 DEBUG_BUDGETS_ESCALATE=0 ./run_episode_a2z.sh
# When A2Z_DEBUG=0, debug budget env vars are ignored (no behavior change).
# When A2Z_DEBUG=1, config debug_profile is used unless DEBUG_BUDGETS_PROFILE overrides it.
# ---------------------------------------------------------------------------

PYTHON_BIN="${PYTHON_BIN:-python}"

if [[ ! -f "main.py" ]]; then
  echo "[$(ts)] ❌ main.py not found. Run this script from repo root."
  exit 1
fi

# ---- Pick a portable timeout command (macOS usually lacks `timeout`) ---------
TIMEOUT_BIN=""
if command -v gtimeout >/dev/null 2>&1; then
  TIMEOUT_BIN="gtimeout"   # coreutils on macOS: brew install coreutils
elif command -v timeout >/dev/null 2>&1; then
  TIMEOUT_BIN="timeout"    # linux
fi

mkdir -p "$A2Z_EPISODE_ROOT"
EP_ID="$(date +"%Y%m%d_%H%M%S")"
EP_DIR="${A2Z_EPISODE_ROOT}/${EP_ID}"
mkdir -p "$EP_DIR"
STDOUT_LOG="${EP_DIR}/stdout.log"
: > "$STDOUT_LOG"

echo "[$(ts)] 🚀 A2Z episode (tee) starting"
echo "[$(ts)] 📁 episode_dir=$EP_DIR"
echo "[$(ts)] 🧾 stdout_log=$STDOUT_LOG"
EFFECTIVE_DEBUG_PROFILE="$A2Z_DEBUG_PROFILE"
EFFECTIVE_DEBUG_ESCALATE="$A2Z_DEBUG_ESCALATE"

# Respect explicit env overrides if caller set them (matches comments/examples below).
if [[ -n "${DEBUG_BUDGETS_PROFILE:-}" ]]; then
  EFFECTIVE_DEBUG_PROFILE="$DEBUG_BUDGETS_PROFILE"
fi
if [[ -n "${DEBUG_BUDGETS_ESCALATE:-}" ]]; then
  EFFECTIVE_DEBUG_ESCALATE="$DEBUG_BUDGETS_ESCALATE"
fi

ep_log "[$(ts)] 🧪 debug=$A2Z_DEBUG profile=$EFFECTIVE_DEBUG_PROFILE escalate=$EFFECTIVE_DEBUG_ESCALATE"
ep_log "[$(ts)] 🧷 preextract_verdict_v2_mode=$A2Z_PREEXTRACT_VERDICT_V2_MODE"
ep_log "[$(ts)] 📅 gf_date_root_gate_mode=$A2Z_GF_DATE_ROOT_GATE_MODE"
ep_log "[$(ts)] 🧭 gf_deeplink_recovery_mode=$A2Z_GF_DEEPLINK_RECOVERY_MODE"
ep_log "[$(ts)] 🧠 multimodal_mode=${A2Z_MULTIMODAL_MODE} (default: judge_primary = early VLM + code-model judge)"
if [[ "${A2Z_HUMAN_INTERVENTION_MODE}" == "assist" || "${A2Z_HUMAN_INTERVENTION_MODE}" == "demo" ]]; then
  A2Z_ALLOW_HUMAN_INTERVENTION=1
fi
if [[ "${A2Z_HUMAN_INTERVENTION_MODE}" == "off" && "${A2Z_ALLOW_HUMAN_INTERVENTION}" == "1" ]]; then
  A2Z_HUMAN_INTERVENTION_MODE="assist"
fi
ep_log "[$(ts)] 🧍 allow_human_intervention=${A2Z_ALLOW_HUMAN_INTERVENTION} manual_timeout_sec=${A2Z_MANUAL_INTERVENTION_TIMEOUT_SEC}"
ep_log "[$(ts)] 🧍 mode_human_intervention=${A2Z_HUMAN_INTERVENTION_MODE}"
ep_log "[$(ts)] 🧍‍♂️ last_resort_manual_when_disabled=${A2Z_LAST_RESORT_HUMAN_INTERVENTION_WHEN_DISABLED}"
ep_log "[$(ts)] 🍪 storage_state_enabled=${A2Z_BROWSER_STORAGE_STATE_ENABLED} storage_state_path=${A2Z_BROWSER_STORAGE_STATE_PATH:-<auto>}"
ep_log "[$(ts)] 🌐 browser_block_heavy_resources=${A2Z_BROWSER_BLOCK_HEAVY_RESOURCES}"

SUPER_DEEP_MODE=0
if [[ "$A2Z_DEBUG" == "1" ]] && [[ "${EFFECTIVE_DEBUG_PROFILE}" == "super_deep" ]]; then
  SUPER_DEEP_MODE=1
  # For long exploratory bug-capture runs, default to a long but finite wrapper timeout
  # unless the caller explicitly chose a value (including 0 for no wrapper timeout).
  if [[ "$A2Z_TIMEOUT_SEC_WAS_SET" == "0" ]]; then
    A2Z_TIMEOUT_SEC=21600   # 6h wrapper cap (main/scenario caps still apply)
  fi
  ep_log "[$(ts)] 🧬 super_deep=1 wrapper_timeout_sec=$A2Z_TIMEOUT_SEC"
fi
hr

# -----------------------------------------------------------------------------
# RUN main.py (tee)
# -----------------------------------------------------------------------------
RUN_CMD=("$PYTHON_BIN" "main.py")

if [[ "$A2Z_DEBUG" == "1" ]]; then
  # Best-effort: keep your existing CLI contract.
  RUN_CMD+=("--debug")
fi

# Optional extra args (space-separated string)
if [[ -n "$A2Z_MAIN_ARGS" ]]; then
  # shellcheck disable=SC2206
  EXTRA_ARGS=($A2Z_MAIN_ARGS)
  RUN_CMD+=("${EXTRA_ARGS[@]}")
fi

# Default multimodal mode unless caller explicitly passed one in A2Z_MAIN_ARGS.
if [[ " ${A2Z_MAIN_ARGS} " != *" --multimodal-mode "* ]] \
  && [[ " ${A2Z_MAIN_ARGS} " != *" --agentic-multimodal-mode "* ]] \
  && [[ " ${A2Z_MAIN_ARGS} " != *"--multimodal-mode="* ]] \
  && [[ " ${A2Z_MAIN_ARGS} " != *"--agentic-multimodal-mode="* ]]; then
  RUN_CMD+=("--multimodal-mode" "$A2Z_MULTIMODAL_MODE")
fi

# Env vars for the run (so you don't have to touch run.yaml)
# NOTE: runtime must read these. If you picked different names in code, change here.
ENV_VARS=()
if [[ "$A2Z_DEBUG" == "1" ]]; then
  ENV_VARS+=("DEBUG_BUDGETS_PROFILE=$EFFECTIVE_DEBUG_PROFILE")
  ENV_VARS+=("DEBUG_BUDGETS_ESCALATE=$EFFECTIVE_DEBUG_ESCALATE")
fi
# Keep extractor/runtime env aligned with wrapper default unless caller overrides.
if [[ -z "${FLIGHT_WATCHER_AGENTIC_MULTIMODAL_MODE:-}" ]]; then
  ENV_VARS+=("FLIGHT_WATCHER_AGENTIC_MULTIMODAL_MODE=$A2Z_MULTIMODAL_MODE")
else
  ENV_VARS+=("FLIGHT_WATCHER_AGENTIC_MULTIMODAL_MODE=${FLIGHT_WATCHER_AGENTIC_MULTIMODAL_MODE}")
fi
if [[ -z "${FLIGHT_WATCHER_ALLOW_HUMAN_INTERVENTION:-}" ]]; then
  ENV_VARS+=("FLIGHT_WATCHER_ALLOW_HUMAN_INTERVENTION=${A2Z_ALLOW_HUMAN_INTERVENTION}")
else
  ENV_VARS+=("FLIGHT_WATCHER_ALLOW_HUMAN_INTERVENTION=${FLIGHT_WATCHER_ALLOW_HUMAN_INTERVENTION}")
fi
if [[ -z "${FLIGHT_WATCHER_HUMAN_INTERVENTION_MODE:-}" ]]; then
  ENV_VARS+=("FLIGHT_WATCHER_HUMAN_INTERVENTION_MODE=${A2Z_HUMAN_INTERVENTION_MODE}")
else
  ENV_VARS+=("FLIGHT_WATCHER_HUMAN_INTERVENTION_MODE=${FLIGHT_WATCHER_HUMAN_INTERVENTION_MODE}")
fi
if [[ -z "${FLIGHT_WATCHER_LAST_RESORT_HUMAN_INTERVENTION_WHEN_DISABLED:-}" ]]; then
  ENV_VARS+=("FLIGHT_WATCHER_LAST_RESORT_HUMAN_INTERVENTION_WHEN_DISABLED=${A2Z_LAST_RESORT_HUMAN_INTERVENTION_WHEN_DISABLED}")
else
  ENV_VARS+=("FLIGHT_WATCHER_LAST_RESORT_HUMAN_INTERVENTION_WHEN_DISABLED=${FLIGHT_WATCHER_LAST_RESORT_HUMAN_INTERVENTION_WHEN_DISABLED}")
fi
if [[ -z "${FLIGHT_WATCHER_MANUAL_INTERVENTION_TIMEOUT_SEC:-}" ]]; then
  ENV_VARS+=("FLIGHT_WATCHER_MANUAL_INTERVENTION_TIMEOUT_SEC=${A2Z_MANUAL_INTERVENTION_TIMEOUT_SEC}")
else
  ENV_VARS+=("FLIGHT_WATCHER_MANUAL_INTERVENTION_TIMEOUT_SEC=${FLIGHT_WATCHER_MANUAL_INTERVENTION_TIMEOUT_SEC}")
fi
if [[ -z "${FLIGHT_WATCHER_BROWSER_STORAGE_STATE_ENABLED:-}" ]]; then
  ENV_VARS+=("FLIGHT_WATCHER_BROWSER_STORAGE_STATE_ENABLED=${A2Z_BROWSER_STORAGE_STATE_ENABLED}")
else
  ENV_VARS+=("FLIGHT_WATCHER_BROWSER_STORAGE_STATE_ENABLED=${FLIGHT_WATCHER_BROWSER_STORAGE_STATE_ENABLED}")
fi
if [[ -n "${A2Z_BROWSER_STORAGE_STATE_PATH:-}" ]]; then
  ENV_VARS+=("FLIGHT_WATCHER_BROWSER_STORAGE_STATE_PATH=${A2Z_BROWSER_STORAGE_STATE_PATH}")
fi
if [[ -z "${FLIGHT_WATCHER_BROWSER_BLOCK_HEAVY_RESOURCES:-}" ]]; then
  ENV_VARS+=("FLIGHT_WATCHER_BROWSER_BLOCK_HEAVY_RESOURCES=${A2Z_BROWSER_BLOCK_HEAVY_RESOURCES}")
else
  ENV_VARS+=("FLIGHT_WATCHER_BROWSER_BLOCK_HEAVY_RESOURCES=${FLIGHT_WATCHER_BROWSER_BLOCK_HEAVY_RESOURCES}")
fi

if [[ "$SUPER_DEEP_MODE" == "1" ]]; then
  # Richer logs + more artifacts for long exploratory runs. Respect explicit caller overrides.
  if [[ -z "${FLIGHT_WATCHER_LOG_LEVEL:-}" ]]; then
    ENV_VARS+=("FLIGHT_WATCHER_LOG_LEVEL=DEBUG")
  else
    ENV_VARS+=("FLIGHT_WATCHER_LOG_LEVEL=${FLIGHT_WATCHER_LOG_LEVEL}")
  fi
  if [[ -z "${FLIGHT_WATCHER_SCENARIO_EVIDENCE_DUMP_ENABLED:-}" ]]; then
    ENV_VARS+=("FLIGHT_WATCHER_SCENARIO_EVIDENCE_DUMP_ENABLED=1")
  else
    ENV_VARS+=("FLIGHT_WATCHER_SCENARIO_EVIDENCE_DUMP_ENABLED=${FLIGHT_WATCHER_SCENARIO_EVIDENCE_DUMP_ENABLED}")
  fi
  if [[ -z "${FLIGHT_WATCHER_SCENARIO_CANDIDATE_TIMEOUT_SEC:-}" ]]; then
    ENV_VARS+=("FLIGHT_WATCHER_SCENARIO_CANDIDATE_TIMEOUT_SEC=7200")
  else
    ENV_VARS+=("FLIGHT_WATCHER_SCENARIO_CANDIDATE_TIMEOUT_SEC=${FLIGHT_WATCHER_SCENARIO_CANDIDATE_TIMEOUT_SEC}")
  fi
  if [[ -z "${FLIGHT_WATCHER_SCENARIO_CANDIDATE_TIMEOUT_CAP_SEC:-}" ]]; then
    ENV_VARS+=("FLIGHT_WATCHER_SCENARIO_CANDIDATE_TIMEOUT_CAP_SEC=21600")
  else
    ENV_VARS+=("FLIGHT_WATCHER_SCENARIO_CANDIDATE_TIMEOUT_CAP_SEC=${FLIGHT_WATCHER_SCENARIO_CANDIDATE_TIMEOUT_CAP_SEC}")
  fi
  if [[ -z "${SCENARIO_MAX_RETRIES:-}" ]]; then
    ENV_VARS+=("SCENARIO_MAX_RETRIES=6")
  else
    ENV_VARS+=("SCENARIO_MAX_RETRIES=${SCENARIO_MAX_RETRIES}")
  fi
  if [[ -z "${SCENARIO_MAX_TURNS:-}" ]]; then
    ENV_VARS+=("SCENARIO_MAX_TURNS=6")
  else
    ENV_VARS+=("SCENARIO_MAX_TURNS=${SCENARIO_MAX_TURNS}")
  fi
  if [[ -z "${SCENARIO_RECOVERY_MAX_RETRIES:-}" ]]; then
    ENV_VARS+=("SCENARIO_RECOVERY_MAX_RETRIES=4")
  else
    ENV_VARS+=("SCENARIO_RECOVERY_MAX_RETRIES=${SCENARIO_RECOVERY_MAX_RETRIES}")
  fi
  if [[ -z "${SCENARIO_RECOVERY_MAX_TURNS:-}" ]]; then
    ENV_VARS+=("SCENARIO_RECOVERY_MAX_TURNS=4")
  else
    ENV_VARS+=("SCENARIO_RECOVERY_MAX_TURNS=${SCENARIO_RECOVERY_MAX_TURNS}")
  fi
  if [[ -z "${FLIGHT_WATCHER_GOOGLE_FLIGHTS_DATE_RELOAD_RETRY_MAX_ATTEMPTS:-}" ]]; then
    ENV_VARS+=("FLIGHT_WATCHER_GOOGLE_FLIGHTS_DATE_RELOAD_RETRY_MAX_ATTEMPTS=4")
  else
    ENV_VARS+=("FLIGHT_WATCHER_GOOGLE_FLIGHTS_DATE_RELOAD_RETRY_MAX_ATTEMPTS=${FLIGHT_WATCHER_GOOGLE_FLIGHTS_DATE_RELOAD_RETRY_MAX_ATTEMPTS}")
  fi
  if [[ -z "${FLIGHT_WATCHER_DEBUG_EXPLORATION_MODE:-}" ]]; then
    ENV_VARS+=("FLIGHT_WATCHER_DEBUG_EXPLORATION_MODE=super_deep")
  else
    ENV_VARS+=("FLIGHT_WATCHER_DEBUG_EXPLORATION_MODE=${FLIGHT_WATCHER_DEBUG_EXPLORATION_MODE}")
  fi
  ENV_VARS+=("PYTHONUNBUFFERED=1")
fi

# Legacy auto-heal knobs are no longer accepted by utils.auto_heal CLI.
if [[ "${A2Z_MAX_FILES}" != "2" ]] || [[ "${A2Z_MAX_LINES}" != "80" ]] || [[ "${A2Z_TEST_CMD}" != "pytest -q tests/test_triage.py tests/test_failure_reasons.py" ]] || [[ "${A2Z_LLM}" != "0" ]]; then
  ep_log "[$(ts)] ⚠️ A2Z_MAX_FILES/A2Z_MAX_LINES/A2Z_TEST_CMD/A2Z_LLM are legacy and ignored by current utils.auto_heal CLI."
fi

case "${A2Z_PREEXTRACT_VERDICT_V2_MODE}" in
  off)
    # Respect repo/config defaults; do not force env overrides.
    ;;
  shadow)
    ENV_VARS+=("FLIGHT_WATCHER_SCENARIO_PREEXTRACT_VERDICT_V2_ENABLED=0")
    ENV_VARS+=("FLIGHT_WATCHER_SCENARIO_PREEXTRACT_VERDICT_V2_SHADOW_COMPARE=1")
    ;;
  canary)
    ENV_VARS+=("FLIGHT_WATCHER_SCENARIO_PREEXTRACT_VERDICT_V2_ENABLED=1")
    ENV_VARS+=("FLIGHT_WATCHER_SCENARIO_PREEXTRACT_VERDICT_V2_SHADOW_COMPARE=0")
    ;;
  *)
    echo "[$(ts)] ❌ Invalid A2Z_PREEXTRACT_VERDICT_V2_MODE=${A2Z_PREEXTRACT_VERDICT_V2_MODE} (expected off|shadow|canary)"
    exit 3
    ;;
esac

# Respect explicit caller-provided runtime env overrides (do not overwrite with A2Z modes).
if [[ -n "${FLIGHT_WATCHER_SCENARIO_PREEXTRACT_VERDICT_V2_ENABLED:-}" ]] || [[ -n "${FLIGHT_WATCHER_SCENARIO_PREEXTRACT_VERDICT_V2_SHADOW_COMPARE:-}" ]]; then
  env_vars_remove_key "FLIGHT_WATCHER_SCENARIO_PREEXTRACT_VERDICT_V2_ENABLED"
  env_vars_remove_key "FLIGHT_WATCHER_SCENARIO_PREEXTRACT_VERDICT_V2_SHADOW_COMPARE"
  if [[ -n "${FLIGHT_WATCHER_SCENARIO_PREEXTRACT_VERDICT_V2_ENABLED:-}" ]]; then
    ENV_VARS+=("FLIGHT_WATCHER_SCENARIO_PREEXTRACT_VERDICT_V2_ENABLED=${FLIGHT_WATCHER_SCENARIO_PREEXTRACT_VERDICT_V2_ENABLED}")
  fi
  if [[ -n "${FLIGHT_WATCHER_SCENARIO_PREEXTRACT_VERDICT_V2_SHADOW_COMPARE:-}" ]]; then
    ENV_VARS+=("FLIGHT_WATCHER_SCENARIO_PREEXTRACT_VERDICT_V2_SHADOW_COMPARE=${FLIGHT_WATCHER_SCENARIO_PREEXTRACT_VERDICT_V2_SHADOW_COMPARE}")
  fi
  ep_log "[$(ts)] 🧷 preextract_verdict_v2_mode=env_override"
fi

case "${A2Z_GF_DATE_ROOT_GATE_MODE}" in
  off)
    ENV_VARS+=("FLIGHT_WATCHER_GF_SET_DATE_FALLBACK_ROOT_MONTH_HEADER_GATE_ENABLED=0")
    ;;
  canary)
    ENV_VARS+=("FLIGHT_WATCHER_GF_SET_DATE_FALLBACK_ROOT_MONTH_HEADER_GATE_ENABLED=1")
    ;;
  *)
    echo "[$(ts)] ❌ Invalid A2Z_GF_DATE_ROOT_GATE_MODE=${A2Z_GF_DATE_ROOT_GATE_MODE} (expected off|canary)"
    exit 4
    ;;
esac

if [[ -n "${FLIGHT_WATCHER_GF_SET_DATE_FALLBACK_ROOT_MONTH_HEADER_GATE_ENABLED:-}" ]]; then
  env_vars_remove_key "FLIGHT_WATCHER_GF_SET_DATE_FALLBACK_ROOT_MONTH_HEADER_GATE_ENABLED"
  ENV_VARS+=("FLIGHT_WATCHER_GF_SET_DATE_FALLBACK_ROOT_MONTH_HEADER_GATE_ENABLED=${FLIGHT_WATCHER_GF_SET_DATE_FALLBACK_ROOT_MONTH_HEADER_GATE_ENABLED}")
  ep_log "[$(ts)] 📅 gf_date_root_gate_mode=env_override"
fi

case "${A2Z_GF_DEEPLINK_RECOVERY_MODE}" in
  off)
    ENV_VARS+=("FLIGHT_WATCHER_GOOGLE_FLIGHTS_DEEPLINK_PAGE_STATE_RECOVERY_ENABLED=0")
    ;;
  canary)
    ENV_VARS+=("FLIGHT_WATCHER_GOOGLE_FLIGHTS_DEEPLINK_PAGE_STATE_RECOVERY_ENABLED=1")
    ;;
  *)
    echo "[$(ts)] ❌ Invalid A2Z_GF_DEEPLINK_RECOVERY_MODE=${A2Z_GF_DEEPLINK_RECOVERY_MODE} (expected off|canary)"
    exit 5
    ;;
esac

if [[ -n "${FLIGHT_WATCHER_GOOGLE_FLIGHTS_DEEPLINK_PAGE_STATE_RECOVERY_ENABLED:-}" ]] || [[ -n "${FLIGHT_WATCHER_GOOGLE_FLIGHTS_DEEPLINK_PAGE_STATE_RECOVERY_MAX_EXTRA_ACTIONS:-}" ]]; then
  env_vars_remove_key "FLIGHT_WATCHER_GOOGLE_FLIGHTS_DEEPLINK_PAGE_STATE_RECOVERY_ENABLED"
  env_vars_remove_key "FLIGHT_WATCHER_GOOGLE_FLIGHTS_DEEPLINK_PAGE_STATE_RECOVERY_MAX_EXTRA_ACTIONS"
  if [[ -n "${FLIGHT_WATCHER_GOOGLE_FLIGHTS_DEEPLINK_PAGE_STATE_RECOVERY_ENABLED:-}" ]]; then
    ENV_VARS+=("FLIGHT_WATCHER_GOOGLE_FLIGHTS_DEEPLINK_PAGE_STATE_RECOVERY_ENABLED=${FLIGHT_WATCHER_GOOGLE_FLIGHTS_DEEPLINK_PAGE_STATE_RECOVERY_ENABLED}")
  fi
  if [[ -n "${FLIGHT_WATCHER_GOOGLE_FLIGHTS_DEEPLINK_PAGE_STATE_RECOVERY_MAX_EXTRA_ACTIONS:-}" ]]; then
    ENV_VARS+=("FLIGHT_WATCHER_GOOGLE_FLIGHTS_DEEPLINK_PAGE_STATE_RECOVERY_MAX_EXTRA_ACTIONS=${FLIGHT_WATCHER_GOOGLE_FLIGHTS_DEEPLINK_PAGE_STATE_RECOVERY_MAX_EXTRA_ACTIONS}")
  fi
  ep_log "[$(ts)] 🧭 gf_deeplink_recovery_mode=env_override"
fi

echo "[$(ts)] ▶️ Running (tee): ${ENV_VARS[*]} ${RUN_CMD[*]}"
hr

set +e
if [[ "$A2Z_TIMEOUT_SEC" != "0" ]] && [[ -n "$TIMEOUT_BIN" ]]; then
  # Capture both stdout+stderr into tee
  env "${ENV_VARS[@]}" "$TIMEOUT_BIN" "$A2Z_TIMEOUT_SEC" "${RUN_CMD[@]}" 2>&1 | tee -a "$STDOUT_LOG"
  RUN_EXIT=${PIPESTATUS[0]}
else
  env "${ENV_VARS[@]}" "${RUN_CMD[@]}" 2>&1 | tee -a "$STDOUT_LOG"
  RUN_EXIT=${PIPESTATUS[0]}
fi
set -e

hr
echo "[$(ts)] main.py exit_code=$RUN_EXIT"

# ---- Extract run_id from log (preferred) ------------------------------------
extract_run_id_from_log() {
  local f="$1"
  "$PYTHON_BIN" - "$f" <<'PY'
import re, sys, pathlib
p = pathlib.Path(sys.argv[1])
if not p.exists():
    print("")
    raise SystemExit(0)
text = p.read_text(encoding="utf-8", errors="ignore")
patterns = [
    r"Debug mode enabled:\s+.*?storage/runs/([A-Za-z0-9._-]+)\b",
    r"\brun_id=([A-Za-z0-9._-]+)\b",
    r"storage/runs/([A-Za-z0-9._-]+)/(?:artifacts|run\.log|manifest\.json)\b",
]
for pattern in patterns:
    matches = re.findall(pattern, text)
    if matches:
        print(matches[-1])
        raise SystemExit(0)
print("")
PY
}

RUN_ID="$(extract_run_id_from_log "$STDOUT_LOG")"

# fallback: canonical latest pointer
if [[ -z "$RUN_ID" ]] && [[ -f "storage/latest_run_id.txt" ]]; then
  RUN_ID="$(tr -d '[:space:]' < "storage/latest_run_id.txt" || true)"
fi

if [[ -n "$RUN_ID" ]] && [[ "$RUN_ID" != "unknown" ]]; then
  if [[ ! -d "storage/runs/${RUN_ID}" ]]; then
    echo "[$(ts)] ❌ Detected run_id points to missing run directory: storage/runs/${RUN_ID}"
    echo "[$(ts)] ❌ Stale latest-run pointer or external cleanup detected; refusing to continue with incomplete artifacts."
    exit 2
  fi
  echo "[$(ts)] 🧾 Detected run_id: $RUN_ID"
else
  echo "[$(ts)] ❌ Could not detect run_id. Tee log preserved at: $STDOUT_LOG"
  exit 2
fi

EPISODE_RELOCATED=0
CANON_EP_DIR="storage/runs/${RUN_ID}/episode"
relocate_episode_outputs() {
  if [[ "${EPISODE_RELOCATED}" == "1" ]]; then
    return 0
  fi
  mkdir -p "$CANON_EP_DIR"

  if [[ -f "$STDOUT_LOG" ]]; then
    mv "$STDOUT_LOG" "$CANON_EP_DIR/stdout.log"
    STDOUT_LOG="$CANON_EP_DIR/stdout.log"
  fi

  if [[ -d "$EP_DIR" ]]; then
    shopt -s nullglob dotglob
    for item in "$EP_DIR"/*; do
      if [[ "$(basename "$item")" == "stdout.log" ]]; then
        continue
      fi
      mv "$item" "$CANON_EP_DIR"/
    done
    shopt -u nullglob dotglob
    rmdir "$EP_DIR" 2>/dev/null || true
  fi

  EP_DIR="$CANON_EP_DIR"
  EPISODE_RELOCATED=1
}

# Move episode artifacts into canonical run folder early so follow-up steps
# (triage/auto-heal/reporting) write to storage/runs/<run_id>/episode.
relocate_episode_outputs

# -----------------------------------------------------------------------------
# OPTIONAL FIXTURE CAPTURE (best-effort, debug helper)
# -----------------------------------------------------------------------------
if [[ "$A2Z_CAPTURE_FIXTURE" == "1" ]]; then
  hr
  echo "[$(ts)] 🧊 FIXTURE CAPTURE (best-effort)"
  if [[ -n "$RUN_ID" ]]; then
    set +e
    "$PYTHON_BIN" -m utils.capture_fixture --site all --run-id "$RUN_ID" --source auto
    CAPTURE_EXIT=$?
    set -e
    echo "[$(ts)] fixture_capture exit_code=$CAPTURE_EXIT (ignored)"
  else
    echo "[$(ts)] ⚠️ Skipping fixture capture (missing run_id)."
  fi
fi

# -----------------------------------------------------------------------------
# TRIAGE
# -----------------------------------------------------------------------------
hr
echo "[$(ts)] 🩺 TRIAGE"

set +e
if [[ -n "$RUN_ID" ]]; then
  "$PYTHON_BIN" -m utils.triage --log-file "$STDOUT_LOG" --run-id "$RUN_ID"
  TRIAGE_EXIT=$?
else
  "$PYTHON_BIN" -m utils.triage --log-file "$STDOUT_LOG"
  TRIAGE_EXIT=$?
fi
set -e
echo "[$(ts)] triage exit_code=$TRIAGE_EXIT"

# -----------------------------------------------------------------------------
# AUTO-HEAL (Tier2-lite) - dry-run default
# -----------------------------------------------------------------------------
if [[ "$A2Z_AUTO_HEAL" == "1" ]]; then
  hr
  echo "[$(ts)] 🧠 AUTO-HEAL (Tier2-lite) (sandboxed)"

  AH_CMD=("$PYTHON_BIN" "-m" "utils.auto_heal"
          "--log-file" "$STDOUT_LOG"
          "--episode-dir" "$EP_DIR")

  if [[ -n "$RUN_ID" ]]; then
    AH_CMD+=("--run-id" "$RUN_ID")
  fi
  if [[ "$A2Z_APPLY" == "1" ]]; then
    AH_CMD+=("--apply")
  fi

  echo "[$(ts)] ▶️ Running: ${AH_CMD[*]}"
  set +e
  "${AH_CMD[@]}"
  AH_EXIT=$?
  set -e
  echo "[$(ts)] auto_heal exit_code=$AH_EXIT"
  if [[ "$AH_EXIT" != "0" ]]; then
    ep_log "[$(ts)] ⚠️ auto_heal failed (exit=$AH_EXIT); continuing episode with diagnostics only."
  fi

  relocate_episode_outputs

  # Prefer canonical episode dir
  REPORT_PATH="${EP_DIR}/report.json"

  if [[ -f "$REPORT_PATH" ]]; then
    hr
    echo "[$(ts)] ✅ report.json: $REPORT_PATH"
    echo "[$(ts)] 🔎 Quick peek:"
    REPORT_PATH="$REPORT_PATH" STDOUT_LOG="$STDOUT_LOG" "$PYTHON_BIN" - <<'PY'
import json, os
p=os.environ["REPORT_PATH"]
d=json.load(open(p,"r",encoding="utf-8"))
reasons=d.get("reasons") or d.get("top_reasons") or []
stdout_log = os.environ.get("STDOUT_LOG", "")
runtime_llm_used = False
if stdout_log:
    try:
        with open(stdout_log, "r", encoding="utf-8", errors="ignore") as f:
            text = f.read()
        runtime_llm_used = any(
            token in text
            for token in (
                "llm.call.metrics",
                "llm.vlm_",
                "vlm.call.metrics",
            )
        )
    except Exception:
        runtime_llm_used = False
print("run_id:", d.get("run_id"))
print("episode_dir:", d.get("episode_dir"))
print("site:", d.get("site"))
print("apply_mode:", (d.get("safety") or {}).get("apply_mode"))
print("auto_heal_llm_used:", (d.get("safety") or {}).get("llm_used"))
print("runtime_llm_used_detected:", runtime_llm_used)
print("passed_tests:", (d.get("safety") or {}).get("passed_tests"))
print("reasons:")
for r in reasons[:8]:
    code=r.get("code") or r.get("reason_code")
    cnt=r.get("count")
    print(" -", code, cnt)
PY
  else
    echo "[$(ts)] ⚠️ report.json not found (expected ${EP_DIR}/report.json)."
  fi
fi

relocate_episode_outputs

hr
echo "[$(ts)] 📦 Episode artifacts:"
if [[ -d "$EP_DIR" ]]; then
  find "$EP_DIR" -maxdepth 1 -type f ! -name 'stdout.log' -print 2>/dev/null | sed 's/^/  - /' | head -n 30 || true
fi
echo "  - $STDOUT_LOG"

if [[ "${A2Z_PREEXTRACT_VERDICT_V2_MODE}" == "shadow" ]] && [[ -f "$STDOUT_LOG" ]]; then
  hr
  ep_log "[$(ts)] 🔎 Shadow compare grep (preextract verdict v2)"
  SHADOW_COUNT="$(grep -c "scenario.preextract.verdict_v2.shadow_mismatch" "$STDOUT_LOG" || true)"
  ep_log "[$(ts)] shadow_mismatch_count=$SHADOW_COUNT"
  if [[ "${SHADOW_COUNT:-0}" != "0" ]]; then
    grep "scenario.preextract.verdict_v2.shadow_mismatch" "$STDOUT_LOG" | tail -n 10 | tee -a "$STDOUT_LOG" || true
  fi
fi

hr
if [[ "$RUN_EXIT" != "0" ]]; then
  echo "[$(ts)] 🟥 Episode finished with main.py failure (exit=$RUN_EXIT)."
  exit "$RUN_EXIT"
fi
echo "[$(ts)] 🟩 Episode finished OK."

# ---- Check for Escalation Recommendation ----------------------------------
# If adaptive escalation is enabled and decision recommends debug profile,
# print a helpful message with re-run command.
if [[ -n "$RUN_ID" ]] && [[ -f "storage/runs/$RUN_ID/escalation.json" ]]; then
  hr
  echo "[$(ts)] 📈 ESCALATION RECOMMENDATION"

  # Parse escalation.json for recommendation
  "$PYTHON_BIN" - "storage/runs/$RUN_ID/escalation.json" <<'PARSE_ESC'
import json, sys
try:
    data = json.load(open(sys.argv[1], "r", encoding="utf-8"))
    should_escalate = data.get("should_escalate", False)
    to_profile = data.get("to_profile", "default")
    reason = data.get("reason", "Unknown")

    if should_escalate and to_profile == "debug":
        print(f"[RECOMMEND] Run detected stuckness: {reason}")
        print("[RECOMMEND] Threshold escalation (policy): THRESHOLDS_PROFILE=debug ./run_episode_a2z.sh")
        print("[RECOMMEND] Deeper instrumentation (A2Z debug budgets): A2Z_DEBUG_PROFILE=deep ./run_episode_a2z.sh")
except Exception as e:
    print(f"[WARN] Could not parse escalation.json: {e}")
PARSE_ESC
fi

hr
ep_log "[$(ts)] ℹ️ Runtime toggles:"
ep_log "  - Preextract verdict mode:  A2Z_PREEXTRACT_VERDICT_V2_MODE=off|shadow|canary"
ep_log "  - GF date gate:             A2Z_GF_DATE_ROOT_GATE_MODE=off|canary"
ep_log "  - GF deeplink recovery:     A2Z_GF_DEEPLINK_RECOVERY_MODE=off|canary"
ep_log "  - Human mode:               A2Z_HUMAN_INTERVENTION_MODE=off|assist|demo"
ep_log "  - Manual intervention:      A2Z_ALLOW_HUMAN_INTERVENTION=0|1 (legacy toggle; mode overrides)"
ep_log "  - Last-resort manual:       A2Z_LAST_RESORT_HUMAN_INTERVENTION_WHEN_DISABLED=0|1 (default 1)"
ep_log "  - Debug profile:            A2Z_DEBUG_PROFILE=lite|deep|super_deep"
ep_log "  - Optional service scope:   A2Z_MAIN_ARGS=\"--services google_flights\" ./run_episode_a2z.sh"
ep_log "[$(ts)] ℹ️ Multimodal mode guide (default = judge_primary):"
ep_log "  - off:          disable multimodal/VLM extraction overlay"
ep_log "  - assist:       late multimodal fallback after deterministic/LLM paths"
ep_log "  - primary:      try multimodal earlier (before text-LLM fallback)"
ep_log "  - judge:        assist + code-model verifies multimodal candidate"
ep_log "  - judge_primary:primary + code-model verifies multimodal candidate (default)"
ep_log "  - Example:      A2Z_MULTIMODAL_MODE=off ./run_episode_a2z.sh"
