PRAGMA journal_mode = WAL;

-- the map each server is on and since when (exact = the bot saw the map change)
CREATE TABLE IF NOT EXISTS server_maps (
    server_id INTEGER PRIMARY KEY,
    map       TEXT    NOT NULL,
    since     REAL    NOT NULL,
    exact     INTEGER NOT NULL,
    last_seen REAL    NOT NULL
);

-- tiers per map name, from maps/detailedList; kept so a restart without Tempus still shows tiers
CREATE TABLE IF NOT EXISTS maps (
    name           TEXT PRIMARY KEY,
    id             INTEGER,
    tier_soldier   INTEGER,
    tier_demoman   INTEGER,
    rating_soldier INTEGER,
    rating_demoman INTEGER,
    updated        REAL NOT NULL
);

-- overall rank per Tempus player id; rank is NULL for players Tempus doesn't rank (no points, or 404)
CREATE TABLE IF NOT EXISTS player_ranks (
    player_id    INTEGER PRIMARY KEY,
    rank         INTEGER,
    points       REAL,
    total_ranked INTEGER,
    country      TEXT,
    fetched_at   REAL NOT NULL
);

-- records already posted (or already there when the feed started), so each is posted once
CREATE TABLE IF NOT EXISTS seen_records (
    record_id INTEGER PRIMARY KEY,
    kind      TEXT NOT NULL,
    seen_at   REAL NOT NULL
);

-- history: players online every few minutes, and the busy servers at that moment
CREATE TABLE IF NOT EXISTS snapshots (
    ts      REAL PRIMARY KEY,
    players INTEGER NOT NULL,
    servers INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS server_snapshots (
    ts        REAL    NOT NULL,
    server_id INTEGER NOT NULL,
    region    TEXT    NOT NULL,
    map       TEXT,
    players   INTEGER NOT NULL,
    PRIMARY KEY (ts, server_id)
);
CREATE INDEX IF NOT EXISTS idx_server_snapshots_region ON server_snapshots (region, ts);

CREATE TABLE IF NOT EXISTS kv (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
