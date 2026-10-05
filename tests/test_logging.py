from __future__ import annotations

import logging

from app.logging_config import UTCFormatter


def test_log_timestamp_is_iso8601_utc() -> None:
    formatter = UTCFormatter(
        "%(asctime)s.%(msecs)03dZ %(levelname)s %(name)s: %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%S",
    )
    record = logging.LogRecord(
        name="whm.test",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg="cycle completed",
        args=(),
        exc_info=None,
    )
    record.created = 0
    formatted = formatter.format(record)
    assert formatted.startswith("1970-01-01T00:00:00.")
    assert "Z INFO whm.test: cycle completed" in formatted
