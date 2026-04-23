# tests/unit/test_logging.py
import pytest


def test_configure_logging_info_does_not_raise():
    from app.core.logging import configure_logging
    configure_logging("INFO")


def test_configure_logging_debug_does_not_raise():
    from app.core.logging import configure_logging
    configure_logging("DEBUG")


def test_configure_logging_warning_does_not_raise():
    from app.core.logging import configure_logging
    configure_logging("WARNING")


def test_configure_logging_unknown_falls_back():
    """Unknown level must not raise — fall back to INFO (OBS-5 resilience)."""
    from app.core.logging import configure_logging
    configure_logging("NOT_A_REAL_LEVEL")


def test_get_logger_returns_logger():
    from app.core.logging import configure_logging, get_logger
    configure_logging("INFO")
    log = get_logger("test.module")
    assert log is not None


def test_logger_info_does_not_raise(capsys):
    from app.core.logging import configure_logging, get_logger
    configure_logging("INFO")
    log = get_logger("test.module")
    log.info("test_event", key="value")


def test_logger_warning_does_not_raise():
    from app.core.logging import configure_logging, get_logger
    configure_logging("WARNING")
    log = get_logger("test.module")
    log.warning("test_warn", count=5)


def test_logger_error_does_not_raise():
    from app.core.logging import configure_logging, get_logger
    configure_logging("INFO")
    log = get_logger("test.module")
    log.error("test_err", reason="demo")
