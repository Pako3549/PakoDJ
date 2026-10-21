"""Persistent storage layer for PakoDJ.

Queues, playback history and background-collection state are stored in
PostgreSQL so they survive bot restarts and can later be shared with a
web platform. Every database access goes through this module (asyncpg
pool); no raw SQL is kept in main.py.
"""
import os

import asyncpg

# Connection pool, created by init_db() before the bot starts
_pool = None

SCHEMA = """
CREATE TABLE IF NOT EXISTS queue_items (
    id              BIGSERIAL PRIMARY KEY,
    guild_id        BIGINT NOT NULL,
    title           TEXT NOT NULL,
    webpage_url     TEXT NOT NULL,
    search_query    TEXT,
    collection_id   TEXT,
    collection_type TEXT,
    requester_id    BIGINT,
    channel_id      BIGINT,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_queue_guild ON queue_items (guild_id, id);
CREATE INDEX IF NOT EXISTS idx_queue_collection ON queue_items (guild_id, collection_id);

CREATE TABLE IF NOT EXISTS history (
    id           BIGSERIAL PRIMARY KEY,
    guild_id     BIGINT NOT NULL,
    title        TEXT NOT NULL,
    webpage_url  TEXT NOT NULL,
    requester_id BIGINT,
    played_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_history_guild ON history (guild_id, id);

CREATE TABLE IF NOT EXISTS collections (
    id         TEXT PRIMARY KEY,
    guild_id   BIGINT NOT NULL,
    name       TEXT,
    type       TEXT,
    total      INTEGER,
    status     TEXT NOT NULL DEFAULT 'loading',
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
"""

INSERT_TRACK = """
INSERT INTO queue_items
    (guild_id, title, webpage_url, search_query, collection_id, collection_type, requester_id, channel_id)
VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
RETURNING id
"""

CLAIM_NEXT = """
DELETE FROM queue_items
WHERE id = (SELECT id FROM queue_items WHERE guild_id = $1 ORDER BY id LIMIT 1)
RETURNING *
"""


async def init_db():
    """Create the connection pool and schema (idempotent)."""
    global _pool
    if _pool is not None:
        return
    dsn = os.getenv('DATABASE_URL', 'postgres://pakodj:pakodj@db:5432/pakodj')
    _pool = await asyncpg.create_pool(dsn=dsn, min_size=1, max_size=5, command_timeout=15)
    async with _pool.acquire() as conn:
        await conn.execute(SCHEMA)
    print(f"Database ready ({dsn.split('@')[-1]})")


def _pool_or_raise():
    if _pool is None:
        raise RuntimeError("Database not initialised: init_db() must run first")
    return _pool


# ---------------------------------------------------------------------------
# Queue
# ---------------------------------------------------------------------------

async def enqueue_track(guild_id, title, webpage_url, search_query=None,
                        collection_id=None, collection_type=None,
                        requester_id=None, channel_id=None):
    """Append a track to a guild's queue, returns the queue row id.

    Only resolvable, canonical data is stored: the ephemeral stream URL is
    re-resolved just before playback, since YouTube/SoundCloud stream URLs
    expire after a few hours.
    """
    async with _pool_or_raise().acquire() as conn:
        return await conn.fetchval(
            INSERT_TRACK, guild_id, title, webpage_url, search_query,
            collection_id, collection_type, requester_id, channel_id
        )


async def claim_next_track(guild_id):
    """FIFO: atomically remove and return the oldest queued row, or None.

    Returned row: id, title, webpage_url, search_query,
    collection_id, collection_type, requester_id, channel_id.
    """
    async with _pool_or_raise().acquire() as conn:
        return await conn.fetchrow(CLAIM_NEXT, guild_id)


async def clear_collection(guild_id, collection_id):
    """Remove every queued row of one playlist/album/loop group (!skip all)."""
    if not collection_id:
        return 0
    async with _pool_or_raise().acquire() as conn:
        rows = await conn.fetch(
            "DELETE FROM queue_items WHERE guild_id = $1 AND collection_id = $2 RETURNING id",
            guild_id, collection_id
        )
        return len(rows)


async def clear_queue(guild_id):
    async with _pool_or_raise().acquire() as conn:
        await conn.execute("DELETE FROM queue_items WHERE guild_id = $1", guild_id)


async def count_queue(guild_id):
    async with _pool_or_raise().acquire() as conn:
        return await conn.fetchval(
            "SELECT count(*) FROM queue_items WHERE guild_id = $1", guild_id
        )


async def list_queue(guild_id, limit=50):
    async with _pool_or_raise().acquire() as conn:
        return await conn.fetch(
            "SELECT * FROM queue_items WHERE guild_id = $1 ORDER BY id LIMIT $2",
            guild_id, limit
        )


# ---------------------------------------------------------------------------
# History
# ---------------------------------------------------------------------------

async def add_history(guild_id, title, webpage_url, requester_id=None):
    async with _pool_or_raise().acquire() as conn:
        await conn.execute(
            "INSERT INTO history (guild_id, title, webpage_url, requester_id) "
            "VALUES ($1, $2, $3, $4)",
            guild_id, title, webpage_url, requester_id
        )


async def list_history(guild_id, limit=50):
    async with _pool_or_raise().acquire() as conn:
        return await conn.fetch(
            "SELECT * FROM history WHERE guild_id = $1 ORDER BY id DESC LIMIT $2",
            guild_id, limit
        )


# ---------------------------------------------------------------------------
# Collections (playlists / albums / loops being loaded in background)
# ---------------------------------------------------------------------------

async def create_collection(collection_id, guild_id, name, collection_type, total):
    async with _pool_or_raise().acquire() as conn:
        await conn.execute(
            "INSERT INTO collections (id, guild_id, name, type, total, status) "
            "VALUES ($1, $2, $3, $4, $5, 'loading') ON CONFLICT (id) DO NOTHING",
            collection_id, guild_id, name, collection_type, total
        )


async def complete_collection(collection_id, status='complete'):
    async with _pool_or_raise().acquire() as conn:
        await conn.execute(
            "UPDATE collections SET status = $2 WHERE id = $1 AND status = 'loading'",
            collection_id, status
        )
