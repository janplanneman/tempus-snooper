from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Optional, Tuple

from dotenv import load_dotenv


class ConfigError(RuntimeError):
    pass


def _str(name: str, default: Optional[str] = None, required: bool = False) -> Optional[str]:
    value = os.getenv(name, "").strip()
    if not value:
        if required:
            raise ConfigError(f"{name} is required (see .env.example)")
        return default
    return value


def _int(name: str, default: Optional[int]) -> Optional[int]:
    raw = _str(name)
    if raw is None:
        return default
    try:
        return int(raw)
    except ValueError:
        raise ConfigError(f"{name} must be an integer, got {raw!r}") from None


def _float(name: str, default: float) -> float:
    raw = _str(name)
    if raw is None:
        return default
    try:
        return float(raw)
    except ValueError:
        raise ConfigError(f"{name} must be a number, got {raw!r}") from None


def _bool(name: str, default: bool) -> bool:
    raw = _str(name)
    if raw is None:
        return default
    return raw.lower() in ("1", "true", "yes", "on")


def _csv(name: str) -> Tuple[str, ...]:
    raw = _str(name) or ""
    return tuple(p.strip().lower() for p in raw.split(",") if p.strip())


@dataclass(frozen=True)
class Config:
    discord_token: str = field(repr=False)
    guild_id: Optional[int] = None
    status_channel_id: Optional[int] = None
    status_channel_name: str = "tempus-status"
    # new world records are posted in every server with this channel; None turns the feed off
    records_channel_name: Optional[str] = "tempus-records"
    # also post new top-10 map times, not only world records
    records_include_tops: bool = False
    # page that opens steam://connect/<addr> (Discord won't link steam:// itself); empty hides the links
    connect_url: Optional[str] = "https://janplanneman.github.io/tempus-snooper/connect/#{addr}"
    admin_user_ids: Tuple[int, ...] = ()
    # who to contact about the bot, sent to Tempus in the User-Agent
    contact: Optional[str] = None

    # statusList changes on most 15 s polls (measured 2026-10-16), so polling faster only shows the same churn sooner
    poll_interval_s: int = 30
    maps_refresh_s: int = 24 * 3600
    activity_interval_s: int = 300
    # rank lookups are one request per player; they share the 1 request/s budget with everything else
    rank_ttl_s: int = 24 * 3600
    rank_lookup_interval_s: float = 2.0
    request_interval_s: float = 1.0
    request_timeout_s: float = 20.0

    # history snapshots (players online over time)
    snapshot_interval_s: int = 300
    history_keep_days: int = 120

    db_path: str = "tempus.db"
    log_level: str = "INFO"
    log_file: Optional[str] = None

    @property
    def user_agent(self) -> str:
        return f"tempus-snooper/0.1 ({self.contact})" if self.contact else "tempus-snooper/0.1"


def load_config(env_file: Optional[str] = None) -> Config:
    load_dotenv(env_file)
    admin_ids = tuple(int(x) for x in _csv("ADMIN_USER_IDS") if x.isdigit())
    records = os.getenv("RECORDS_CHANNEL_NAME", "tempus-records").strip().lstrip("#")
    cfg = Config(
        discord_token=_str("DISCORD_TOKEN", required=True),
        guild_id=_int("GUILD_ID", None),
        status_channel_id=_int("STATUS_CHANNEL_ID", None),
        status_channel_name=(_str("STATUS_CHANNEL_NAME", "tempus-status") or "tempus-status").lstrip("#"),
        records_channel_name=records or None,
        records_include_tops=_bool("RECORDS_INCLUDE_TOPS", False),
        connect_url=os.getenv("CONNECT_URL", "https://janplanneman.github.io/tempus-snooper/connect/#{addr}").strip() or None,
        admin_user_ids=admin_ids,
        contact=_str("CONTACT"),
        poll_interval_s=_int("POLL_INTERVAL_SECONDS", 30),
        maps_refresh_s=_int("MAPS_REFRESH_HOURS", 24) * 3600,
        activity_interval_s=_int("ACTIVITY_INTERVAL_MINUTES", 5) * 60,
        rank_ttl_s=_int("RANK_TTL_HOURS", 24) * 3600,
        rank_lookup_interval_s=_float("RANK_LOOKUP_SECONDS", 2.0),
        request_interval_s=_float("REQUEST_INTERVAL_SECONDS", 1.0),
        request_timeout_s=_float("REQUEST_TIMEOUT_SECONDS", 20.0),
        snapshot_interval_s=_int("SNAPSHOT_INTERVAL_MINUTES", 5) * 60,
        history_keep_days=_int("HISTORY_KEEP_DAYS", 120),
        db_path=_str("DB_PATH", "tempus.db"),
        log_level=(_str("LOG_LEVEL", "INFO") or "INFO").upper(),
        log_file=_str("LOG_FILE"),
    )
    if cfg.poll_interval_s < 15:
        raise ConfigError("POLL_INTERVAL_SECONDS below 15 is not supported (the data doesn't change faster than that)")
    if cfg.request_interval_s < 1.0:
        raise ConfigError("REQUEST_INTERVAL_SECONDS must be at least 1 (Tempus asks for at most 1 request per second)")
    if cfg.rank_lookup_interval_s < cfg.request_interval_s:
        raise ConfigError("RANK_LOOKUP_SECONDS can't be below REQUEST_INTERVAL_SECONDS")
    if cfg.connect_url and "{addr}" not in cfg.connect_url:
        raise ConfigError("CONNECT_URL must contain {addr}")
    return cfg
