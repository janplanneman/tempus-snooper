"""The Tempus API (https://tempus2.xyz/api/v0): one aiohttp session, at most one request start per second across
the whole bot, Retry-After on 429, and replies decoded defensively (a non-UTF-8 name must never fail a cycle)."""
from __future__ import annotations

import asyncio
import json
import logging
import time
from collections import deque
from email.utils import parsedate_to_datetime
from typing import Any, Deque, Dict, Optional

import aiohttp

log = logging.getLogger(__name__)

BASE_URL = "https://tempus2.xyz/api/v0"
BACKOFF = (2, 5, 10)  # seconds every caller waits after successive failed requests
HOUR = 3600


class TempusError(Exception):
    pass


class NotFound(TempusError):
    pass


def parse_retry_after(value: Optional[str], default: float = 5.0) -> float:
    """Seconds to wait: Retry-After is either a number of seconds or an HTTP date."""
    if not value:
        return default
    try:
        seconds = float(value)
    except ValueError:
        try:
            seconds = parsedate_to_datetime(value).timestamp() - time.time()
        except (TypeError, ValueError):
            return default
    return min(300.0, max(1.0, seconds))


class RateLimiter:
    """Spaces request starts at least `interval` seconds apart, for every caller together."""

    def __init__(self, interval: float) -> None:
        self.interval = interval
        self._next = 0.0
        self._lock = asyncio.Lock()

    async def wait(self) -> None:
        async with self._lock:
            now = time.monotonic()
            start = max(now, self._next)
            self._next = start + self.interval
        if start > now:
            await asyncio.sleep(start - now)

    def hold(self, seconds: float) -> None:
        """Nobody starts a request for the next `seconds` (after a 429 or a failure)."""
        self._next = max(self._next, time.monotonic() + seconds)


class TempusClient:
    def __init__(self, session: aiohttp.ClientSession, interval: float = 1.0, timeout: float = 20.0, retries: int = 2, base_url: str = BASE_URL) -> None:
        self.session = session
        self.base_url = base_url.rstrip("/")
        self.limiter = RateLimiter(interval)
        self.timeout = aiohttp.ClientTimeout(total=timeout)
        self.retries = retries
        self._requests: Deque[float] = deque()
        self._rate_limited: Deque[float] = deque()
        self._errors: Deque[float] = deque()
        self.rate_limited_total = 0
        self.last_error: Optional[str] = None
        self.last_error_at: Optional[float] = None

    def _mark(self, q: Deque[float]) -> None:
        t = time.time()
        q.append(t)
        while q and q[0] < t - HOUR:
            q.popleft()

    def _failed(self, err: str) -> None:
        self._mark(self._errors)
        self.last_error, self.last_error_at = err, time.time()

    def stats(self) -> Dict[str, Any]:
        cutoff = time.time() - HOUR
        for q in (self._requests, self._rate_limited, self._errors):
            while q and q[0] < cutoff:
                q.popleft()
        return {"requests_hour": len(self._requests), "rate_limited_hour": len(self._rate_limited), "errors_hour": len(self._errors),
                "rate_limited_total": self.rate_limited_total, "last_error": self.last_error, "last_error_at": self.last_error_at}

    async def get(self, path: str) -> Any:
        """GET a JSON reply. Raises NotFound on 404 and TempusError once the retries are used up."""
        url = f"{self.base_url}/{path.lstrip('/')}"
        err = "no attempt"
        for attempt in range(self.retries + 1):
            await self.limiter.wait()
            self._mark(self._requests)
            try:
                async with self.session.get(url, timeout=self.timeout, headers={"Accept": "application/json"}) as resp:
                    status = resp.status
                    retry_after = resp.headers.get("Retry-After")
                    body = await resp.read()
            except (aiohttp.ClientError, asyncio.TimeoutError, OSError) as e:
                err = f"{type(e).__name__}: {e}" if str(e) else type(e).__name__
                self._failed(err)
                log.warning("Tempus request failed (%s): %s", err, url)
                self.limiter.hold(BACKOFF[min(attempt, len(BACKOFF) - 1)])
                continue
            if status == 429:
                wait = parse_retry_after(retry_after)
                self._mark(self._rate_limited)
                self.rate_limited_total += 1
                err = f"HTTP 429, Retry-After {retry_after!r}"
                self._failed(err)
                log.warning("Tempus rate limited us on %s; waiting %.0f s before any request", url, wait)
                self.limiter.hold(wait)
                continue
            if status == 404:
                raise NotFound(url)
            if status >= 500:
                err = f"HTTP {status}"
                self._failed(err)
                log.warning("Tempus answered %s: %s", status, url)
                self.limiter.hold(BACKOFF[min(attempt, len(BACKOFF) - 1)])
                continue
            if status != 200:
                self._failed(f"HTTP {status}")
                raise TempusError(f"HTTP {status} from {url}")
            # names come from players and server owners: never let one bad byte fail the reply
            text = body.decode("utf-8", errors="replace")
            try:
                return json.loads(text)
            except ValueError as e:
                self._failed(f"invalid JSON: {e}")
                raise TempusError(f"invalid JSON from {url}: {e}") from None
        raise TempusError(f"{url}: {err}")
