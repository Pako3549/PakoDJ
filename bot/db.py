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
    position        BIGINT,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);
-- position powers queue reordering (web panel); NULL rows are legacy rows
-- already stored before the column existed.
ALTER TABLE queue_items ADD COLUMN IF NOT EXISTS position BIGINT;
UPDATE queue_items SET position = id WHERE position IS NULL;
CREATE INDEX IF NOT EXISTS idx_queue_guild ON queue_items (guild_id, position);
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

-- Real-time mirror of what is playing right now (for the web API).
-- One row per guild, rewritten at every track start.
CREATE TABLE IF NOT EXISTS now_playing (
    guild_id     BIGINT PRIMARY KEY,
    title        TEXT NOT NULL,
    webpage_url  TEXT NOT NULL,
    requester_id BIGINT,
    started_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Web API accounts: created exclusively via Discord OAuth login.
-- password_hash is kept for future needs (e.g. service accounts); Discord
-- accounts always have it empty.
-- role: nobody = logged in but no API surface yet, viewer = read-only,
-- admin = playback control + user management. Admins are normally granted
-- by their Discord ID (DISCORD_ADMIN_IDS) and refreshed at every login.
-- seed = TRUE marks accounts created/owned by DISCORD_ADMIN_IDS: they are
-- managed by the env only, no API call may ever change them.
CREATE TABLE IF NOT EXISTS users (
    id            SERIAL PRIMARY KEY,
    username      TEXT NOT NULL UNIQUE,
    password_hash TEXT NOT NULL,
    role          TEXT NOT NULL DEFAULT 'nobody',
    seed          BOOLEAN NOT NULL DEFAULT FALSE,
    guild_ids     TEXT NOT NULL DEFAULT '',   -- JSON list: guilds shared with the bot, snapshot updated at each login
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    discord_id    TEXT
);
ALTER TABLE users ADD COLUMN IF NOT EXISTS role TEXT NOT NULL DEFAULT 'nobody';
ALTER TABLE users ADD COLUMN IF NOT EXISTS seed BOOLEAN NOT NULL DEFAULT FALSE;
ALTER TABLE users ADD COLUMN IF NOT EXISTS discord_id TEXT;
ALTER TABLE users ADD COLUMN IF NOT EXISTS guild_ids TEXT NOT NULL DEFAULT '';
CREATE UNIQUE INDEX IF NOT EXISTS idx_users_discord ON users (discord_id);

-- Access requests: 'nobody' users ask for webapp access on specific shared
-- guilds; admins see the pending ones in the user-management panel.
-- status: pending / granted / denied.
CREATE TABLE IF NOT EXISTS access_requests (
    id         SERIAL PRIMARY KEY,
    user_id    INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    guild_id   BIGINT NOT NULL,
    guild_name TEXT NOT NULL DEFAULT '',
    message    TEXT NOT NULL DEFAULT '',
    status     TEXT NOT NULL DEFAULT 'pending',
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_access_requests_pending
    ON access_requests (user_id, guild_id) WHERE status = 'pending';

-- Login sessions: only the SHA-256 of the session token is stored.
CREATE TABLE IF NOT EXISTS api_sessions (
    token_hash TEXT PRIMARY KEY,
    user_id    BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    expires_at TIMESTAMPTZ NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_sessions_expiry ON api_sessions (expires_at);
"""

INSERT_TRACK = """
INSERT INTO queue_items
    (guild_id, title, webpage_url, search_query, collection_id, collection_type, requester_id, channel_id, position)
VALUES ($1, $2, $3, $4, $5, $6, $7, $8, COALESCE((SELECT max(position) FROM queue_items WHERE guild_id = $1), 0) + 1)
RETURNING id
"""

INSERT_TRACK_AT_POSITION = """
INSERT INTO queue_items
    (guild_id, title, webpage_url, search_query, collection_id, collection_type, requester_id, channel_id, position)
VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9)
RETURNING id
"""

CLAIM_NEXT = """
DELETE FROM queue_items
WHERE id = (SELECT id FROM queue_items WHERE guild_id = $1 ORDER BY position, id LIMIT 1)
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
    """FIFO on positions: atomically remove and return the next queued row."""
    async with _pool_or_raise().acquire() as conn:
        return await conn.fetchrow(CLAIM_NEXT, guild_id)


async def get_queue_item(guild_id, item_id):
    """Fetch one queued row of a guild (does not remove it)."""
    async with _pool_or_raise().acquire() as conn:
        return await conn.fetchrow(
            "SELECT * FROM queue_items WHERE guild_id = $1 AND id = $2",
            guild_id, item_id
        )


async def insert_track_at_position(guild_id, position, title, webpage_url,
                                   search_query=None, collection_id=None,
                                   collection_type=None, requester_id=None,
                                   channel_id=None):
    """Insert a track at a 0-based position, shifting later rows down;
    returns the queue row id."""
    async with _pool_or_raise().acquire() as conn:
        async with conn.transaction():
            await conn.execute(
                "UPDATE queue_items SET position = position + 1 "
                "WHERE guild_id = $1 AND position >= $2",
                guild_id, position
            )
            return await conn.fetchval(
                INSERT_TRACK_AT_POSITION, guild_id, title, webpage_url,
                search_query, collection_id, collection_type,
                requester_id, channel_id, position
            )


async def move_queue_item(guild_id, item_id, position):
    """Change a queued row's position and renumber its neighbours;
    returns the moved row or None if the guild/item pair is unknown."""
    async with _pool_or_raise().acquire() as conn:
        async with conn.transaction():
            item = await conn.fetchrow(
                "SELECT id, position FROM queue_items WHERE guild_id = $1 AND id = $2",
                guild_id, item_id
            )
            if item is None:
                return None
            old = item['position']
            if position == old:
                return item
            if position < old:
                await conn.execute(
                    "UPDATE queue_items SET position = position + 1 "
                    "WHERE guild_id = $1 AND position >= $2 AND position < $3",
                    guild_id, position, old
                )
            else:
                await conn.execute(
                    "UPDATE queue_items SET position = position - 1 "
                    "WHERE guild_id = $1 AND position > $2 AND position <= $3",
                    guild_id, old, position
                )
            return await conn.fetchrow(
                "UPDATE queue_items SET position = $3 "
                "WHERE guild_id = $1 AND id = $2 "
                "RETURNING id, title, position",
                guild_id, item_id, position
            )


async def delete_queue_item(guild_id, item_id):
    """Remove one queued row and close the gap; returns the deleted row."""
    async with _pool_or_raise().acquire() as conn:
        async with conn.transaction():
            item = await conn.fetchrow(
                "DELETE FROM queue_items WHERE guild_id = $1 AND id = $2 RETURNING *",
                guild_id, item_id
            )
            if item is not None:
                await conn.execute(
                    "UPDATE queue_items SET position = position - 1 "
                    "WHERE guild_id = $1 AND position > $2",
                    guild_id, item['position']
                )
            return item


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
            "SELECT * FROM queue_items WHERE guild_id = $1 ORDER BY position, id LIMIT $2",
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
# Now playing (real-time mirror for the web API)
# ---------------------------------------------------------------------------

async def set_now_playing(guild_id, title, webpage_url, requester_id=None):
    async with _pool_or_raise().acquire() as conn:
        await conn.execute(
            "INSERT INTO now_playing (guild_id, title, webpage_url, requester_id) "
            "VALUES ($1, $2, $3, $4) "
            "ON CONFLICT (guild_id) DO UPDATE SET "
            "title = EXCLUDED.title, webpage_url = EXCLUDED.webpage_url, "
            "requester_id = EXCLUDED.requester_id, started_at = now()",
            guild_id, title, webpage_url, requester_id
        )


async def clear_now_playing(guild_id):
    async with _pool_or_raise().acquire() as conn:
        await conn.execute("DELETE FROM now_playing WHERE guild_id = $1", guild_id)


async def list_now_playing():
    async with _pool_or_raise().acquire() as conn:
        return await conn.fetch("SELECT * FROM now_playing ORDER BY guild_id")


# ---------------------------------------------------------------------------
# Auth accounts & sessions (web API)
# ---------------------------------------------------------------------------

ROLES = ('nobody', 'viewer', 'admin')


async def upsert_discord_user(discord_id, username, env_role):
    """Create or update the Discord-linked account; the Discord ID is the
    identity, username is the visible Discord display name.
    env_role is 'admin' for discord_ids listed in DISCORD_ADMIN_IDS, else
    'nobody'. Rules on login:
    - env admin -> always role 'admin', seed=TRUE (env owns the account)
    - non env member previously seeded (removed from DISCORD_ADMIN_IDS)
      -> demoted to 'nobody' (env revocation, takes effect at this login)
    - non seeded user -> granted role is preserved."""
    seed = env_role == 'admin'
    async with _pool_or_raise().acquire() as conn:
        return await conn.fetchrow(
            "INSERT INTO users (username, discord_id, password_hash, role, seed) "
            "VALUES ($2, $1, '', $3, $4) "
            "ON CONFLICT (discord_id) DO UPDATE SET "
            "username = EXCLUDED.username, seed = EXCLUDED.seed, "
            "role = CASE "
            "  WHEN EXCLUDED.seed THEN 'admin' "
            "  WHEN users.seed THEN 'nobody' "
            "  ELSE users.role END "
            "RETURNING id, username, role, seed",
            discord_id, username, env_role, seed
        )


async def list_users():
    async with _pool_or_raise().acquire() as conn:
        return await conn.fetch(
            "SELECT id, username, created_at, role, seed, discord_id FROM users "
            "ORDER BY created_at"
        )


async def get_user_by_id(user_id):
    async with _pool_or_raise().acquire() as conn:
        return await conn.fetchrow(
            "SELECT id, username, role, seed, guild_ids, discord_id FROM users WHERE id = $1",
            user_id
        )


async def set_user_guilds_snapshot(user_id, guild_ids_json):
    """Store the guilds shared between the user and the bot (JSON list),
    refreshed at every Discord login."""
    async with _pool_or_raise().acquire() as conn:
        await conn.execute(
            "UPDATE users SET guild_ids = $2 WHERE id = $1",
            user_id, guild_ids_json
        )


async def create_access_request(user_id, guild_id, guild_name, message):
    """Pending requests are unique per (user, guild); returns the new row id
    or None when a pending request for the same guild already exists."""
    async with _pool_or_raise().acquire() as conn:
        row = await conn.fetchrow(
            "INSERT INTO access_requests (user_id, guild_id, guild_name, message) "
            "VALUES ($1, $2, $3, $4) "
            "ON CONFLICT DO NOTHING "
            "RETURNING id",
            user_id, guild_id, guild_name, message
        )
        return row['id'] if row else None


async def list_access_requests():
    async with _pool_or_raise().acquire() as conn:
        return await conn.fetch(
            "SELECT r.id, r.user_id, u.username, r.guild_id, r.guild_name, "
            "r.message, r.status, r.created_at "
            "FROM access_requests r JOIN users u ON u.id = r.user_id "
            "WHERE r.status = 'pending' ORDER BY r.created_at"
        )


async def set_access_request_status(request_id, status):
    """Resolve a request (granted/denied); returns the row or None."""
    if status not in ('granted', 'denied'):
        return None
    async with _pool_or_raise().acquire() as conn:
        return await conn.fetchrow(
            "UPDATE access_requests SET status = $2 WHERE id = $1 "
            "RETURNING id, user_id, status",
            request_id, status
        )


async def set_user_role(user_id, role):
    """Change a granted user's role; returns the updated row, None if
    unknown id. Seeded accounts are never updated through this call."""
    if role not in ROLES:
        return None
    async with _pool_or_raise().acquire() as conn:
        return await conn.fetchrow(
            "UPDATE users SET role = $2 WHERE id = $1 AND seed = FALSE "
            "RETURNING id, username, role",
            user_id, role
        )


async def create_session(user_id, token_hash, expires_at):
    async with _pool_or_raise().acquire() as conn:
        await conn.execute(
            "INSERT INTO api_sessions (token_hash, user_id, expires_at) VALUES ($1, $2, $3)",
            token_hash, user_id, expires_at
        )


async def get_session_user(token_hash):
    """Resolve a session token hash to its user; None if unknown/expired."""
    async with _pool_or_raise().acquire() as conn:
        return await conn.fetchrow(
            "SELECT u.id, u.username, u.role FROM api_sessions s "
            "JOIN users u ON u.id = s.user_id "
            "WHERE s.token_hash = $1 AND s.expires_at > now()",
            token_hash
        )


async def delete_session(token_hash):
    async with _pool_or_raise().acquire() as conn:
        await conn.execute("DELETE FROM api_sessions WHERE token_hash = $1", token_hash)


async def purge_expired_sessions():
    async with _pool_or_raise().acquire() as conn:
        await conn.execute("DELETE FROM api_sessions WHERE expires_at < now()")


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
