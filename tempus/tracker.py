"""The poll loop: servers/statusList every POLL_INTERVAL_SECONDS (one request for every server and player), the map
tier list daily, and the activity feed every few minutes for new records. A failed request or a bad server entry
never stops the loop: the board keeps the last good data and says how old it is."""
from __future__ import annotations

import asyncio
import logging
import re
import time
from typing import Any, Awaitable, Callable, Dict, List, Optional, Sequence, Tuple

from .config import Config
from .ranks import RankService
from .regions import region_for
from .storage import Storage
from .tempus_client import TempusClient, TempusError

log = logging.getLogger(__name__)

# fewer maps than this in maps/detailedList is a glitch, not Tempus deleting its maps
MIN_MAPS = 100
RETRY_FAILED_JOB_S = 600
MAX_NEW_MAPS = 20
_CONTROL_RE = re.compile(r"[\x00-\x1f\x7f]")

Tiers = Tuple[Optional[int], Optional[int]]


def now() -> float:
    return time.time()


def clean_name(name: Any) -> str:
    return _CONTROL_RE.sub("", str(name)).strip() if name is not None else ""


def _int(v: Any) -> Optional[int]:
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


def parse_server(entry: Any) -> Optional[Dict[str, Any]]:
    """One statusList entry -> server dict, or None for hidden servers (dev, retired, expiring). Raises on garbage."""
    si = entry["server_info"]
    if si.get("hidden"):
        return None
    sid = int(si["id"])
    gi = entry.get("game_info")
    online = isinstance(gi, dict)
    users: List[Dict[str, Any]] = []
    for u in (gi.get("users") or []) if online else []:
        if isinstance(u, dict):
            users.append({"id": _int(u.get("id")) or 0, "name": clean_name(u.get("name"))})
    port = _int(si.get("port"))
    country = clean_name(si.get("country"))
    return {
        "id": sid,
        "shortname": clean_name(si.get("shortname")) or str(sid),
        "name": clean_name(si.get("name")) or (clean_name(gi.get("hostname")) if online else "") or "?",
        "country": country,
        "region": region_for(country),
        "ip": clean_name(si.get("ipAddr")) or None,
        "host": clean_name(si.get("addr")) or None,
        "port": port,
        "online": online,
        "map": clean_name(gi.get("currentMap")) or None if online else None,
        "max_players": _int(gi.get("maxPlayers")) if online else None,
        # users and playerCount agreed in every sample; trust the larger in case names are ever left out
        "players": max(len(users), _int(gi.get("playerCount")) or 0) if online else 0,
        "users": users,
        "map_since": None,
    }


def parse_status(data: Any) -> Tuple[List[Dict[str, Any]], Dict[str, int]]:
    """statusList -> (visible servers, counts). A malformed entry is logged and skipped, never fatal."""
    if not isinstance(data, list):
        raise TempusError(f"statusList is a {type(data).__name__}, not a list")
    servers: List[Dict[str, Any]] = []
    counts = {"visible": 0, "hidden": 0, "offline": 0, "bad": 0}
    for entry in data:
        try:
            s = parse_server(entry)
        except Exception:
            counts["bad"] += 1
            log.warning("Skipping a statusList entry I couldn't read: %.300r", entry, exc_info=True)
            continue
        if s is None:
            counts["hidden"] += 1
            continue
        counts["visible"] += 1
        counts["offline"] += not s["online"]
        servers.append(s)
    return servers, counts


def _tiers(info: Any) -> Tiers:
    """Tier/rating dicts come keyed by class number ("3"/"4") or by name ("soldier"/"demoman")."""
    info = info if isinstance(info, dict) else {}
    return _int(info.get("3", info.get("soldier"))), _int(info.get("4", info.get("demoman")))


def parse_maps(data: Any) -> List[Dict[str, Any]]:
    if not isinstance(data, list):
        raise TempusError(f"maps/detailedList is a {type(data).__name__}, not a list")
    rows = []
    for m in data:
        try:
            ts, td = _tiers(m.get("tier_info"))
            rs, rd = _tiers(m.get("rating_info"))
            authors = [clean_name(a.get("name")) for a in m.get("authors") or [] if isinstance(a, dict) and a.get("name")]
            counts = m.get("zone_counts") if isinstance(m.get("zone_counts"), dict) else {}
            rows.append({"name": str(m["name"]), "id": _int(m.get("id")), "tier_soldier": ts, "tier_demoman": td, "rating_soldier": rs, "rating_demoman": rd,
                         "authors": authors, "zone_counts": counts})
        except Exception:
            log.warning("Skipping a map entry I couldn't read: %.200r", m)
    if len(rows) < MIN_MAPS:
        raise TempusError(f"map list has only {len(rows)} maps")
    return rows


def next_map_state(prev: Optional[Dict[str, Any]], current_map: str, ts: float, gap_s: float) -> Dict[str, Any]:
    """Track since when a server is on its map. exact=1 only when the bot saw the change happen; after a gap
    in polling (bot down) a new map's start time is unknown."""
    if prev is None:
        return {"map": current_map, "since": ts, "exact": 0, "last_seen": ts}
    seen_recently = ts - prev["last_seen"] <= gap_s
    if prev["map"] != current_map:
        return {"map": current_map, "since": ts, "exact": int(seen_recently), "last_seen": ts}
    return {"map": current_map, "since": prev["since"], "exact": prev["exact"] if seen_recently else 0, "last_seen": ts}


class Tracker:
    def __init__(self, cfg: Config, client: TempusClient, store: Storage, ranks: RankService) -> None:
        self.cfg = cfg
        self.client = client
        self.store = store
        self.ranks = ranks
        self.servers: List[Dict[str, Any]] = []
        self.counts: Dict[str, int] = {}
        self.updated_at: Optional[float] = None  # when the last good statusList arrived
        self.tiers: Dict[str, Tiers] = {}
        self.maps_at: Optional[float] = None
        self._maps_due = 0.0
        self._activity_due = 0.0
        self.activity_at: Optional[float] = None
        self._map_state: Dict[int, Dict[str, Any]] = {}
        self.after_cycle: Optional[Callable[[], Awaitable[None]]] = None
        self.on_activity: Optional[Callable[[Any], Awaitable[None]]] = None
        self.on_new_maps: Optional[Callable[[List[Dict[str, Any]]], Awaitable[None]]] = None
        self.health: Dict[str, Any] = {"started_at": now(), "cycles": 0, "consecutive_failures": 0, "last_cycle_start": None, "last_cycle_end": None,
                                       "last_cycle_duration": None, "last_cycle_ok": None, "last_error": None}

    async def load(self) -> None:
        self._map_state = await self.store.server_maps()
        maps = await self.store.maps()
        self.tiers = {m["name"]: (m["tier_soldier"], m["tier_demoman"]) for m in maps}
        if maps:
            self.maps_at = max(m["updated"] for m in maps)
            self._maps_due = self.maps_at + self.cfg.maps_refresh_s

    def stale_since(self) -> Optional[float]:
        """When the last good data is from, if it's older than two polls; else None."""
        if self.updated_at is not None and now() - self.updated_at > 2 * self.cfg.poll_interval_s + 30:
            return self.updated_at
        return None

    async def run_forever(self) -> None:
        while True:
            t0 = time.monotonic()
            self.health["last_cycle_start"] = now()
            try:
                await self.poll()
                self.health["last_cycle_ok"] = True
                self.health["consecutive_failures"] = 0
                self.health["last_error"] = None
            except Exception as e:
                log.exception("Status poll failed")
                self.health["last_cycle_ok"] = False
                self.health["consecutive_failures"] += 1
                self.health["last_error"] = f"{type(e).__name__}: {e}"
            finally:
                self.health["cycles"] += 1
                self.health["last_cycle_end"] = now()
                self.health["last_cycle_duration"] = time.monotonic() - t0
            for job in (self.refresh_maps, self.check_activity):
                try:
                    await job()
                except Exception:
                    log.exception("%s failed", job.__name__)
            if self.after_cycle is not None:
                try:
                    await self.after_cycle()
                except Exception:
                    log.exception("After-cycle hook (status board) failed")
            await asyncio.sleep(max(5.0, self.cfg.poll_interval_s - (time.monotonic() - t0)))

    async def poll(self) -> None:
        data = await self.client.get("servers/statusList")
        servers, counts = parse_status(data)
        if not servers and counts["bad"]:
            raise TempusError(f"no readable servers in statusList ({counts['bad']} bad entries)")
        ts = now()
        await self._track_maps(servers, ts)
        self.servers, self.counts, self.updated_at = servers, counts, ts
        self.ranks.want(u["id"] for s in servers for u in s["users"])

    async def _track_maps(self, servers: Sequence[Dict[str, Any]], ts: float) -> None:
        gap = max(600.0, 3 * self.cfg.poll_interval_s)
        rows = []
        for s in servers:
            if not s["online"] or not s["map"]:
                continue
            state = next_map_state(self._map_state.get(s["id"]), s["map"], ts, gap)
            self._map_state[s["id"]] = state
            s["map_since"] = state["since"] if state["exact"] else None
            rows.append(dict(state, server_id=s["id"]))
        try:
            await self.store.save_server_maps(rows)
        except Exception:
            log.exception("Could not save map start times")

    async def refresh_maps(self) -> None:
        t = now()
        if t < self._maps_due:
            return
        self._maps_due = t + RETRY_FAILED_JOB_S
        rows = parse_maps(await self.client.get("maps/detailedList"))
        known = set(self.tiers)
        await self.store.replace_maps(rows, t)
        self.tiers = {m["name"]: (m["tier_soldier"], m["tier_demoman"]) for m in rows}
        self.maps_at = t
        self._maps_due = t + self.cfg.maps_refresh_s
        new = [m for m in rows if m["name"] not in known]
        log.info("Map list: %d maps, %d new", len(rows), len(new) if known else 0)
        # the first read has nothing to compare with, and a flood of "new" maps is a glitch, not Tempus adding them
        if known and new and self.on_new_maps is not None:
            if len(new) > MAX_NEW_MAPS:
                log.warning("%d new maps at once; not announcing them", len(new))
            else:
                await self.on_new_maps(new)

    async def check_activity(self) -> None:
        if self.on_activity is None:
            return
        t = now()
        if t < self._activity_due:
            return
        self._activity_due = t + self.cfg.activity_interval_s
        data = await self.client.get("activity")
        self.activity_at = t
        await self.on_activity(data)
