"""Central logger factory used across modules for consistent log formatting."""

import logging


def get_logger(name: str):
    """Initialize base logging config and return a named logger instance."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s"
    )
    return logging.getLogger(name)
