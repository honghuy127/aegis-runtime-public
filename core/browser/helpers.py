"""Browser helper utilities for logging and wait operations."""

import time


def _wait_with_heartbeat(wait_fn, timeout_ms: int, log_every_ms: int = 5000, **context_fields):
    """
    Execute a wait function with periodic heartbeat logs to track long-running waits.

    Args:
        wait_fn: Callable to execute the actual wait (should accept timeout_ms parameter).
        timeout_ms: Total timeout in milliseconds.
        log_every_ms: Interval for heartbeat logs (default 5s).
        **context_fields: Additional fields to include in heartbeat logs (action, role, selector, etc.).

    Returns:
        The result from wait_fn.

    Raises:
        Any exception raised by wait_fn.
    """
    from utils.logging import get_logger
    log = get_logger(__name__)

    start_time = time.monotonic()
    last_log_time = start_time
    max_wait_time = timeout_ms / 1000.0

    # For waits < 5s, no heartbeat needed
    if timeout_ms < log_every_ms:
        return wait_fn(timeout_ms=timeout_ms)

    # For long waits, we need to poll and log periodically
    # We'll try the wait in smaller chunks and log progress
    chunk_ms = min(log_every_ms, timeout_ms)
    remaining_ms = timeout_ms
    last_error = None

    while remaining_ms > 0:
        current_chunk_ms = min(chunk_ms, remaining_ms)
        elapsed_ms = int((time.monotonic() - start_time) * 1000)

        # Log heartbeat if enough time has passed since last log
        if (time.monotonic() - last_log_time) >= (log_every_ms / 1000.0):
            log.info(
                "scenario.step.waiting waited_ms=%d timeout_ms=%d %s",
                elapsed_ms,
                timeout_ms,
                " ".join(f"{k}={v}" for k, v in context_fields.items() if v is not None),
            )
            last_log_time = time.monotonic()

        try:
            # Try the wait operation with current chunk timeout
            return wait_fn(timeout_ms=current_chunk_ms)
        except Exception as exc:
            # If it's an interrupt or the main timeout has expired, re-raise
            if isinstance(exc, (TimeoutError, KeyboardInterrupt)):
                elapsed_ms = int((time.monotonic() - start_time) * 1000)
                if elapsed_ms >= timeout_ms:
                    raise
            # Store error and retry if we have time left
            last_error = exc
            remaining_ms = timeout_ms - int((time.monotonic() - start_time) * 1000)
            if remaining_ms <= 0:
                raise

    # If we exhausted all time and have an error, raise it
    if last_error:
        raise last_error
