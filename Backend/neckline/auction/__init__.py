"""K9-v3 D1 capture windows and scheduler skip reasons."""
from __future__ import annotations

from datetime import time

AUCTION_WINDOW_START = time(9, 26)
AUCTION_WINDOW_END = time(9, 29)
SETTLE_WINDOW_START = time(10, 0)
SETTLE_WINDOW_END = time(10, 5)

SKIP_NOT_WINDOW = "not_window"
SKIP_ALREADY_RAN = "already_ran"
SKIP_NO_LISTING = "no_listing"

__all__ = [
    "AUCTION_WINDOW_START", "AUCTION_WINDOW_END", "SETTLE_WINDOW_START", "SETTLE_WINDOW_END",
    "SKIP_NOT_WINDOW", "SKIP_ALREADY_RAN", "SKIP_NO_LISTING",
]
