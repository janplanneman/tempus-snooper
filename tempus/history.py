"""Players-online history: what's usual for a given day and hour, and when Tempus is busiest.

Works on snapshot rows ({"ts", "players"}) the cog records every few minutes. Days and hours are in UTC, since
Tempus players are spread around the world. Each (day, hour) value is averaged per calendar day first, so a day
with more snapshots (e.g. no bot restarts) doesn't count for more.
"""
from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timezone
from typing import Dict, List, Optional, Sequence, Tuple

DAYS = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")
DAY_NAMES = ("Mondays", "Tuesdays", "Wednesdays", "Thursdays", "Fridays", "Saturdays", "Sundays")
# at least this many different days before a (day, hour) average is shown
MIN_DAYS = 2
SHADES = "-░▒▓█"


def local(ts: float) -> datetime:
    return datetime.fromtimestamp(ts, tz=timezone.utc)


def _per_day_hour(snapshots: Sequence[Dict[str, float]]) -> Dict[Tuple[int, int], Dict[str, List[float]]]:
    """(weekday, hour) -> {date: [players, ...]}"""
    out: Dict[Tuple[int, int], Dict[str, List[float]]] = defaultdict(lambda: defaultdict(list))
    for s in snapshots:
        t = local(s["ts"])
        out[(t.weekday(), t.hour)][t.date().isoformat()].append(s["players"])
    return out


def _average(days: Dict[str, List[float]]) -> Optional[Tuple[float, int]]:
    """Mean of the per-day means, and how many days that is."""
    if len(days) < MIN_DAYS:
        return None
    means = [sum(v) / len(v) for v in days.values()]
    return sum(means) / len(means), len(means)


def usual(snapshots: Sequence[Dict[str, float]], now_ts: float) -> Optional[Tuple[int, str]]:
    """(usual players, "Saturdays") for this weekday and hour, from earlier days only, or None if too little data."""
    t = local(now_ts)
    days = dict(_per_day_hour(snapshots).get((t.weekday(), t.hour), {}))
    days.pop(t.date().isoformat(), None)
    avg = _average(days)
    return (round(avg[0]), DAY_NAMES[t.weekday()]) if avg else None


def grid(snapshots: Sequence[Dict[str, float]]) -> List[List[Optional[float]]]:
    """7 x 24 average players (Monday first, hours in UTC); None where there isn't enough data yet."""
    per = _per_day_hour(snapshots)
    rows: List[List[Optional[float]]] = []
    for day in range(7):
        row = []
        for hour in range(24):
            avg = _average(per.get((day, hour), {}))
            row.append(avg[0] if avg else None)
        rows.append(row)
    return rows


def busiest(cells: List[List[Optional[float]]], count: int = 3) -> List[Tuple[int, int, float]]:
    """The busiest (day, hour, average) slots, most first."""
    slots = [(d, h, v) for d, row in enumerate(cells) for h, v in enumerate(row) if v]
    return sorted(slots, key=lambda x: -x[2])[:count]


def record(snapshots: Sequence[Dict[str, float]]) -> Optional[Tuple[int, float]]:
    """(most players at once, when)"""
    best = max(snapshots, key=lambda s: s["players"], default=None)
    return (int(best["players"]), best["ts"]) if best and best["players"] else None


def chart(cells: List[List[Optional[float]]]) -> List[str]:
    """Text heatmap for a code block: one row per day, one character per hour."""
    top = max((v for row in cells for v in row if v is not None), default=0)
    lines = ["     0     6     12    18    "]
    for day, row in enumerate(cells):
        chars = []
        for v in row:
            if v is None:
                chars.append(" ")
            elif top <= 0 or v < 0.5:
                chars.append(SHADES[0])
            else:
                chars.append(SHADES[min(len(SHADES) - 1, 1 + int((v / top) * (len(SHADES) - 1) - 1e-9))])
        lines.append(f"{DAYS[day]}  {''.join(chars)}")
    return lines


def legend(cells: List[List[Optional[float]]]) -> str:
    top = max((v for row in cells for v in row if v is not None), default=0)
    if top <= 0:
        return f"{SHADES[0]} nobody on"
    steps = len(SHADES) - 1
    return "  ".join([f"{SHADES[0]} 0"] + [f"{SHADES[i]} up to {round(top * i / steps)}" for i in range(1, steps + 1)])
