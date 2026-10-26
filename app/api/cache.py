"""Per-process degraded-mode cache (spec section 5).

Populated on every successful Redis read, bounded to one entry per configured
symbol, and only served while the quote is at most `max_age_seconds` old.
"""

from __future__ import annotations

import threading
from collections import OrderedDict

from app.common.models import Quote


class LocalQuoteCache:
    def __init__(self, max_entries: int, max_age_seconds: float) -> None:
        self.max_entries = max(1, max_entries)
        self.max_age_seconds = max_age_seconds
        self._entries: OrderedDict[str, Quote] = OrderedDict()
        self._lock = threading.Lock()

    def put(self, quote: Quote) -> None:
        with self._lock:
            self._entries[quote.symbol] = quote
            self._entries.move_to_end(quote.symbol)
            while len(self._entries) > self.max_entries:
                self._entries.popitem(last=False)

    def get_fresh(self, symbol: str, now: float) -> Quote | None:
        """Return the cached quote if it is no older than max_age_seconds."""
        with self._lock:
            quote = self._entries.get(symbol)
            if quote is None or now - quote.event_ts > self.max_age_seconds:
                return None
            self._entries.move_to_end(symbol)
            return quote

    def __len__(self) -> int:
        return len(self._entries)
