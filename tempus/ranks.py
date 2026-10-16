"""Overall Tempus rank per player, looked up once per player (one request each) and cached for a day.

Lookups run in the background, new players first, and share the API's 1 request/s budget with the status poll,
so the board never waits for them: a player whose rank isn't known yet is shown without one.
"""
from __future__ import annotations

import asyncio
import logging
import time
from collections import deque
from typing import Deque, Dict, Iterable, Optional, Set, Tuple

from .storage import Storage
from .tempus_client import NotFound, TempusClient

log = logging.getLogger(__name__)

FAILURE_PAUSE_S = 30.0


def rank_from_reply(data: object) -> Tuple[Optional[int], Optional[float], Optional[int], Optional[str]]:
    """(rank, points, total_ranked, country). Players without points get rank total+1 from Tempus: treated as unranked."""
    data = data if isinstance(data, dict) else {}
    info = data.get("rank_info") if isinstance(data.get("rank_info"), dict) else {}
    player = data.get("player_info") if isinstance(data.get("player_info"), dict) else {}
    points = info.get("points")
    rank = info.get("rank")
    rank = int(rank) if isinstance(rank, (int, float)) and points else None
    total = info.get("total_ranked")
    return rank, float(points) if isinstance(points, (int, float)) else None, int(total) if isinstance(total, (int, float)) else None, player.get("country") or None


class RankService:
    def __init__(self, store: Storage, client: TempusClient, ttl_s: float, interval_s: float) -> None:
        self.store = store
        self.client = client
        self.ttl_s = ttl_s
        self.interval_s = interval_s
        self._ranks: Dict[int, Tuple[Optional[int], float]] = {}
        self._new: Deque[int] = deque()
        self._stale: Deque[int] = deque()
        self._queued: Set[int] = set()
        self._wake: Optional[asyncio.Event] = None
        self.lookups = 0
        self.failures = 0

    async def load(self) -> None:
        self._ranks = await self.store.ranks()
        log.info("Rank cache: %d players", len(self._ranks))

    def __len__(self) -> int:
        return len(self._ranks)

    @property
    def queued(self) -> int:
        return len(self._new) + len(self._stale)

    def known(self, ids: Iterable[int]) -> Dict[int, Optional[int]]:
        """player id -> rank (None = unranked) for the players looked up already; unknown ones are left out."""
        return {pid: self._ranks[pid][0] for pid in ids if pid in self._ranks}

    def want(self, ids: Iterable[int], now: Optional[float] = None) -> None:
        """Queue lookups for these players unless their rank is cached and fresh. Tempus id 0 means not registered yet."""
        now = now if now is not None else time.time()
        added = False
        for pid in ids:
            if not pid or pid < 0 or pid in self._queued:
                continue
            cached = self._ranks.get(pid)
            if cached is None:
                self._new.append(pid)
            elif now - cached[1] > self.ttl_s:
                self._stale.append(pid)
            else:
                continue
            self._queued.add(pid)
            added = True
        if added and self._wake is not None:
            self._wake.set()

    def _next(self) -> Optional[int]:
        q = self._new or self._stale
        return q.popleft() if q else None

    async def lookup(self, pid: int) -> None:
        try:
            data = await self.client.get(f"players/id/{pid}/rank")
        except NotFound:
            data = None
        rank, points, total, country = rank_from_reply(data)
        ts = time.time()
        self._ranks[pid] = (rank, ts)
        await self.store.save_rank(pid, rank, points, total, country, ts)
        self.lookups += 1

    async def run_forever(self) -> None:
        self._wake = asyncio.Event()
        while True:
            pid = self._next()
            if pid is None:
                self._wake.clear()
                await self._wake.wait()
                continue
            try:
                await self.lookup(pid)
            except Exception as e:
                # dropped from the queue; the next status poll queues it again if the player is still online
                self.failures += 1
                log.warning("Rank lookup for player %s failed: %s", pid, e)
                await asyncio.sleep(FAILURE_PAUSE_S)
            finally:
                self._queued.discard(pid)
            await asyncio.sleep(self.interval_s)
