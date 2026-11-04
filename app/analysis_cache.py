"""Bounded process-local metadata cache. No document bytes or result text are retained."""

import hashlib
import hmac
import json
import secrets
from collections import OrderedDict
from dataclasses import dataclass
from threading import RLock
from time import monotonic
from typing import Callable, Literal


@dataclass(frozen=True)
class CacheDecision:
    status: Literal['hit', 'miss', 'busy', 'bypass']
    report_id: str | None = None


class AnalysisCache:
    """Coalesce active keys; reuse only explicit references to completed DB reports."""

    def __init__(self, ttl_seconds: int = 900, max_entries: int = 128,
                 clock: Callable[[], float] = monotonic):
        if ttl_seconds < 1 or max_entries < 1:
            raise ValueError('Cache TTL and capacity must be positive')
        self.ttl_seconds = ttl_seconds
        self.max_entries = max_entries
        self._clock = clock
        self._secret = secrets.token_bytes(32)
        self._entries: OrderedDict[str, tuple[float, str]] = OrderedDict()
        self._pending: set[str] = set()
        self._lock = RLock()

    def fingerprint(self, data: bytes, namespace: dict) -> str:
        digest = hmac.new(self._secret, b'analysis-cache-v1\0', hashlib.sha256)
        digest.update(json.dumps(namespace, sort_keys=True, ensure_ascii=False).encode('utf-8'))
        digest.update(b'\0')
        digest.update(data)
        return digest.hexdigest()

    def _expire(self) -> None:
        now = self._clock()
        for key, (expires, _) in list(self._entries.items()):
            if expires <= now:
                del self._entries[key]

    def claim(self, key: str) -> CacheDecision:
        with self._lock:
            self._expire()
            if key in self._entries:
                self._entries.move_to_end(key)
                return CacheDecision('hit', self._entries[key][1])
            if key in self._pending:
                return CacheDecision('busy')
            if len(self._pending) >= self.max_entries:
                return CacheDecision('bypass')
            self._pending.add(key)
            return CacheDecision('miss')

    def finish(self, key: str, report_id: str | None) -> None:
        with self._lock:
            self._pending.discard(key)
            self._expire()
            if report_id is not None:
                self._entries[key] = (self._clock() + self.ttl_seconds, report_id)
                self._entries.move_to_end(key)
                while len(self._entries) > self.max_entries:
                    self._entries.popitem(last=False)

    def discard(self, key: str) -> None:
        with self._lock:
            self._entries.pop(key, None)

    def invalidate_report(self, report_id: str) -> None:
        with self._lock:
            for key, (_, stored_id) in list(self._entries.items()):
                if stored_id == report_id:
                    del self._entries[key]

    def clear(self) -> None:
        with self._lock:
            self._entries.clear()
            self._pending.clear()
