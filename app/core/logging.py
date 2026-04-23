"""
Structured logging configuration using structlog.

This module provides configure_logging() and get_logger() for use
throughout the application.
"""
import logging
import sys
from typing import Any

try:
    import structlog

    def configure_logging(level: str = "INFO") -> None:
        """Configure structlog for JSON-line structured output."""
        log_level = getattr(logging, level.upper(), logging.INFO)
        logging.basicConfig(
            format="%(message)s",
            stream=sys.stdout,
            level=log_level,
        )
        structlog.configure(
            processors=[
                structlog.stdlib.filter_by_level,
                structlog.stdlib.add_logger_name,
                structlog.stdlib.add_log_level,
                structlog.stdlib.PositionalArgumentsFormatter(),
                structlog.processors.TimeStamper(fmt="iso"),
                structlog.processors.StackInfoRenderer(),
                structlog.processors.format_exc_info,
                structlog.processors.UnicodeDecoder(),
                structlog.processors.JSONRenderer(),
            ],
            context_class=dict,
            logger_factory=structlog.stdlib.LoggerFactory(),
            wrapper_class=structlog.stdlib.BoundLogger,
            cache_logger_on_first_use=True,
        )

    def get_logger(name: str) -> Any:
        """Return a structlog bound logger."""
        return structlog.get_logger(name)

except ImportError:
    # Fallback to stdlib logging if structlog is not installed
    def configure_logging(level: str = "INFO") -> None:
        """Configure stdlib logging as fallback."""
        log_level = getattr(logging, level.upper(), logging.INFO)
        logging.basicConfig(
            format="%(asctime)s %(levelname)-5.5s [%(name)s] %(message)s",
            stream=sys.stdout,
            level=log_level,
        )

    def get_logger(name: str) -> Any:
        """Return a stdlib logger."""
        return logging.getLogger(name)
