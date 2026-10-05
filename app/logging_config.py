"""UTC log formatting shared by the application and background services."""

from __future__ import annotations

import logging
import time


class UTCFormatter(logging.Formatter):
    converter = time.gmtime


def configure_logging(level: str = "INFO") -> None:
    """Configure concise UTC logs on stdout without logging request secrets."""
    handler = logging.StreamHandler()
    handler.setFormatter(
        UTCFormatter(
            "%(asctime)s.%(msecs)03dZ %(levelname)s %(name)s: %(message)s",
            datefmt="%Y-%m-%dT%H:%M:%S",
        )
    )
    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(level.upper())
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
