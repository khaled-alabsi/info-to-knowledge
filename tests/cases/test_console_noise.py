"""The console carries the run's own lines: an HTTP client's line per request is silenced."""

import logging

from support import harness

import cli


def test_the_http_client_logs_only_warnings_once_a_command_has_set_up_logging():
    httpx_logger = logging.getLogger("httpx")
    previous_level = httpx_logger.level
    httpx_logger.setLevel(logging.NOTSET)
    try:
        cli.main(["models"])
        level = httpx_logger.getEffectiveLevel()
    finally:
        httpx_logger.setLevel(previous_level)
    harness.check("an HTTP request line never reaches the console", level >= logging.WARNING,
                  logging.getLevelName(level))
