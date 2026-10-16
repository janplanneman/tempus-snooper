from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

import aiosqlite

log = logging.getLogger(__name__)

SCHEMA_PATH = Path(__file__).with_name("schema.sql")


class Storage:
    def __init__(self, db: aiosqlite.Connection) -> None:
        self._db = db

    @classmethod
    async def open(cls, path: str) -> "Storage":
        db = await aiosqlite.connect(path)
        db.row_factory = aiosqlite.Row
        await db.executescript(SCHEMA_PATH.read_text(encoding="utf-8"))
        await db.commit()
        log.info("SQLite database ready at %s", path)
        return cls(db)

    async def close(self) -> None:
        await self._db.close()

    async def server_maps(self) -> Dict[int, Dict[str, Any]]:
        async with self._db.execute("SELECT server_id, map, since, exact, last_seen FROM server_maps") as cur:
            return {r["server_id"]: dict(r) for r in await cur.fetchall()}

    async def save_server_maps(self, rows: Sequence[Dict[str, Any]]) -> None:
        await self._db.executemany(
            "INSERT INTO server_maps (server_id, map, since, exact, last_seen) VALUES (:server_id, :map, :since, :exact, :last_seen) "
            "ON CONFLICT(server_id) DO UPDATE SET map = excluded.map, since = excluded.since, exact = excluded.exact, last_seen = excluded.last_seen",
            rows,
        )
        await self._db.commit()

    async def replace_maps(self, rows: Sequence[Dict[str, Any]], ts: float) -> None:
        await self._db.execute("DELETE FROM maps")
        await self._db.executemany(
            "INSERT OR REPLACE INTO maps (name, id, tier_soldier, tier_demoman, rating_soldier, rating_demoman, updated) "
            "VALUES (:name, :id, :tier_soldier, :tier_demoman, :rating_soldier, :rating_demoman, :updated)",
            [dict(r, updated=ts) for r in rows],
        )
        await self._db.commit()

    async def maps(self) -> List[Dict[str, Any]]:
        async with self._db.execute("SELECT * FROM maps") as cur:
            return [dict(r) for r in await cur.fetchall()]

    async def ranks(self) -> Dict[int, Tuple[Optional[int], float]]:
        """player id -> (rank or None if unranked, fetched_at)"""
        async with self._db.execute("SELECT player_id, rank, fetched_at FROM player_ranks") as cur:
            return {r["player_id"]: (r["rank"], r["fetched_at"]) for r in await cur.fetchall()}

    async def save_rank(self, player_id: int, rank: Optional[int], points: Optional[float], total_ranked: Optional[int], country: Optional[str], ts: float) -> None:
        await self._db.execute(
            "INSERT INTO player_ranks (player_id, rank, points, total_ranked, country, fetched_at) VALUES (?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(player_id) DO UPDATE SET rank = excluded.rank, points = excluded.points, total_ranked = excluded.total_ranked, "
            "country = excluded.country, fetched_at = excluded.fetched_at",
            (player_id, rank, points, total_ranked, country, ts),
        )
        await self._db.commit()

    async def seen_record_ids(self) -> set:
        async with self._db.execute("SELECT record_id FROM seen_records") as cur:
            return {r[0] for r in await cur.fetchall()}

    async def mark_records_seen(self, records: Iterable[Tuple[int, str]], ts: float) -> None:
        await self._db.executemany("INSERT OR IGNORE INTO seen_records (record_id, kind, seen_at) VALUES (?, ?, ?)", [(rid, kind, ts) for rid, kind in records])
        await self._db.commit()

    async def prune_seen_records(self, before: float) -> None:
        await self._db.execute("DELETE FROM seen_records WHERE seen_at < ?", (before,))
        await self._db.commit()

    async def get_wr(self, zone_id: int, cls: int) -> Optional[Dict[str, Any]]:
        async with self._db.execute("SELECT * FROM wrs WHERE zone_id = ? AND class = ?", (zone_id, cls)) as cur:
            row = await cur.fetchone()
        return dict(row) if row else None

    async def save_wrs(self, rows: Sequence[Dict[str, Any]]) -> None:
        """Remember WRs; an older one never replaces a newer one."""
        await self._db.executemany(
            "INSERT INTO wrs (zone_id, class, record_id, player_id, player, duration, date) VALUES (:zone_id, :class, :record_id, :player_id, :player, :duration, :date) "
            "ON CONFLICT(zone_id, class) DO UPDATE SET record_id = excluded.record_id, player_id = excluded.player_id, player = excluded.player, "
            "duration = excluded.duration, date = excluded.date WHERE excluded.date >= COALESCE(wrs.date, 0)",
            rows,
        )
        await self._db.commit()

    async def add_pending_demo(self, record_id: int, post: Dict[str, Any], messages: List[Tuple[int, int]], next_check: float) -> None:
        await self._db.execute("INSERT OR REPLACE INTO pending_demos (record_id, post, messages, attempts, next_check) VALUES (?, ?, ?, 0, ?)",
                               (record_id, json.dumps(post), json.dumps(messages), next_check))
        await self._db.commit()

    async def due_pending_demos(self, now: float, limit: int) -> List[Dict[str, Any]]:
        async with self._db.execute("SELECT * FROM pending_demos WHERE next_check <= ? ORDER BY next_check LIMIT ?", (now, limit)) as cur:
            rows = [dict(r) for r in await cur.fetchall()]
        for r in rows:
            r["post"], r["messages"] = json.loads(r["post"]), json.loads(r["messages"])
        return rows

    async def pending_demo_count(self) -> int:
        async with self._db.execute("SELECT COUNT(*) FROM pending_demos") as cur:
            return (await cur.fetchone())[0]

    async def retry_pending_demo(self, record_id: int, attempts: int, next_check: float) -> None:
        await self._db.execute("UPDATE pending_demos SET attempts = ?, next_check = ? WHERE record_id = ?", (attempts, next_check, record_id))
        await self._db.commit()

    async def delete_pending_demo(self, record_id: int) -> None:
        await self._db.execute("DELETE FROM pending_demos WHERE record_id = ?", (record_id,))
        await self._db.commit()

    async def record_snapshot(self, ts: float, servers: Sequence[Dict[str, Any]]) -> None:
        """One history point: every server with players on it, and the total."""
        await self._db.execute("INSERT OR REPLACE INTO snapshots (ts, players, servers) VALUES (?, ?, ?)", (ts, sum(s["players"] for s in servers), len(servers)))
        await self._db.executemany(
            "INSERT OR REPLACE INTO server_snapshots (ts, server_id, region, map, players) VALUES (?, ?, ?, ?, ?)",
            [(ts, s["server_id"], s["region"], s.get("map"), s["players"]) for s in servers],
        )
        await self._db.commit()

    async def snapshots_since(self, since: float, region: Optional[str] = None) -> List[Dict[str, Any]]:
        """{"ts", "players", "servers"} per snapshot; with a region, only that region's players (0 when it was empty)."""
        if region is None:
            sql, args = "SELECT ts, players, servers FROM snapshots WHERE ts >= ? ORDER BY ts", (since,)
        else:
            sql = ("SELECT s.ts AS ts, COALESCE(SUM(ss.players), 0) AS players, COUNT(ss.server_id) AS servers FROM snapshots s "
                   "LEFT JOIN server_snapshots ss ON ss.ts = s.ts AND ss.region = ? WHERE s.ts >= ? GROUP BY s.ts ORDER BY s.ts")
            args = (region, since)
        async with self._db.execute(sql, args) as cur:
            return [dict(r) for r in await cur.fetchall()]

    async def first_snapshot(self) -> Optional[float]:
        async with self._db.execute("SELECT MIN(ts) FROM snapshots") as cur:
            return (await cur.fetchone())[0]

    async def snapshot_count(self) -> int:
        async with self._db.execute("SELECT COUNT(*) FROM snapshots") as cur:
            return (await cur.fetchone())[0]

    async def prune_snapshots(self, before: float) -> None:
        await self._db.execute("DELETE FROM snapshots WHERE ts < ?", (before,))
        await self._db.execute("DELETE FROM server_snapshots WHERE ts < ?", (before,))
        await self._db.commit()

    async def kv_get(self, key: str) -> Optional[str]:
        async with self._db.execute("SELECT value FROM kv WHERE key = ?", (key,)) as cur:
            row = await cur.fetchone()
        return row[0] if row else None

    async def kv_set(self, key: str, value: str) -> None:
        await self._db.execute("INSERT INTO kv (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value", (key, value))
        await self._db.commit()
