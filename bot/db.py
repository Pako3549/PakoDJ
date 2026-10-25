"""Persistent storage layer for PakoDJ.

Queues, playback history and background-collection state are stored in
PostgreSQL so they survive bot restarts and can later be shared with a
web platform. Every database access goes through this module (asyncpg
pool); no raw SQL is kept in main.py.
"""
import json
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
ALTER TABLE now_playing ADD COLUMN IF NOT EXISTS duration DOUBLE PRECISION;

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
    discord_id    TEXT,
    avatar_hash   TEXT                    -- Discord avatar hash (avatar CDN URL is rebuilt per request)
);
ALTER TABLE users ADD COLUMN IF NOT EXISTS role TEXT NOT NULL DEFAULT 'nobody';
ALTER TABLE users ADD COLUMN IF NOT EXISTS seed BOOLEAN NOT NULL DEFAULT FALSE;
ALTER TABLE users ADD COLUMN IF NOT EXISTS discord_id TEXT;
ALTER TABLE users ADD COLUMN IF NOT EXISTS guild_ids TEXT NOT NULL DEFAULT '';
ALTER TABLE users ADD COLUMN IF NOT EXISTS avatar_hash TEXT;
-- Stored user OAuth pair: lets the access-panel refresh the shared-guilds
-- snapshot lazily (join/leave detection) instead of only at every login.
ALTER TABLE users ADD COLUMN IF NOT EXISTS discord_access_token TEXT NOT NULL DEFAULT '';
ALTER TABLE users ADD COLUMN IF NOT EXISTS discord_refresh_token TEXT NOT NULL DEFAULT '';
ALTER TABLE users ADD COLUMN IF NOT EXISTS discord_token_expires TIMESTAMPTZ;
-- Granular permissions: JSON array of permission keys over users.
-- Legacy coarse roles are migrated once on load (guard = untouched default).
ALTER TABLE users ADD COLUMN IF NOT EXISTS permissions TEXT NOT NULL DEFAULT '["view"]';
UPDATE users SET permissions =
  '["view","queue","queue_manage","playback","manage_users"]'
  WHERE role = 'admin' AND permissions = '["view"]';
UPDATE users SET permissions = '[]'
  WHERE role = 'nobody' AND permissions = '["view"]';
-- Per-guild permission overrides: when a row exists for (user, guild) it
-- REPLACES the user's global permission set inside that guild.
CREATE TABLE IF NOT EXISTS user_guild_permissions (
    user_id     INT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    guild_id    BIGINT NOT NULL,
    permissions TEXT NOT NULL,               -- JSON array of permission keys
    PRIMARY KEY (user_id, guild_id)
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_users_discord ON users (discord_id);

-- Access requests: 'nobody' users ask for webapp access on specific shared
-- guilds; admins see the pending ones in the user-management panel.
-- status: pending / granted / denied.
CREATE TABLE IF NOT EXISTS access_requests (
    id         SERIAL PRIMARY KEY,
    user_id    INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    guild_id   BIGINT,
    guild_name TEXT NOT NULL DEFAULT '',
    message    TEXT NOT NULL DEFAULT '',
    status     TEXT NOT NULL DEFAULT 'pending',
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
-- One pending request per (user, guild);
-- guild_id IS NULL means a generic request (no shared server at all).
ALTER TABLE access_requests ALTER COLUMN guild_id DROP NOT NULL;
-- Who resolved the request and through which surface, e.g.
-- "Pako (web panel)" or "Pako (DM)": the DM cards read it to show the
-- same "Accepted/Denied by ..." line regardless of where the click landed.
ALTER TABLE access_requests ADD COLUMN IF NOT EXISTS resolved_by TEXT;
CREATE UNIQUE INDEX IF NOT EXISTS idx_access_requests_pending
    ON access_requests (user_id, guild_id) WHERE status = 'pending';
CREATE UNIQUE INDEX IF NOT EXISTS idx_access_requests_pending_generic
    ON access_requests (user_id) WHERE guild_id IS NULL AND status = 'pending';

-- Login sessions: only the SHA-256 of the session token is stored.
CREATE TABLE IF NOT EXISTS api_sessions (
    token_hash TEXT PRIMARY KEY,
    user_id    BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    expires_at TIMESTAMPTZ NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_sessions_expiry ON api_sessions (expires_at);

-- Personal favorites: starred from the queue or the history on the music
-- page. One per (account, track): the webpage_url is the canonical identity
-- and search_query keeps the just-in-time re-resolution cheap.
CREATE TABLE IF NOT EXISTS favorites (
    id           SERIAL PRIMARY KEY,
    user_id      INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    title        TEXT NOT NULL,
    webpage_url  TEXT NOT NULL,
    search_query TEXT,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_favorites_user_url
    ON favorites (user_id, webpage_url);
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
        await _merge_search_into_queue(conn)
    print(f"Database ready ({dsn.split('@')[-1]})")


_CANONICAL_KEYS = ('view', 'queue', 'queue_manage', 'playback', 'manage_users')


async def _merge_search_into_queue(conn):
    """One-time data migration for the retired 'search' key: find+queue music
    is a single permission now. Read-modify-write keeps the stored JSON a
    clean canonical list; the UPDATE is a no-op once every row is migrated."""
    canon_index = {k: i for i, k in enumerate(_CANONICAL_KEYS)}
    for table, key_cols in (("users", "id"), ("user_guild_permissions", "user_id, guild_id")):
        rows = await conn.fetch(
            f"SELECT {key_cols}, permissions AS raw FROM {table} "
            "WHERE permissions LIKE '%\"search\"%'"
        )
        for row in rows:
            try:
                keys = json.loads(row['raw'])
            except (ValueError, TypeError):
                continue
            if not isinstance(keys, list):
                continue
            keys = sorted({_KEY_ALIASES.get(k, k) for k in keys}, key=lambda k: canon_index.get(k, 99))
            merged = json.dumps(keys)
            if table == "users":
                await conn.execute("UPDATE users SET permissions = $2 WHERE id = $1", row['id'], merged)
            else:
                await conn.execute(
                    "UPDATE user_guild_permissions SET permissions = $2 WHERE user_id = $1 AND guild_id = $3",
                    row['user_id'], merged, row['guild_id']
                )


_KEY_ALIASES = {'search': 'queue'}


def _pool_or_raise():
    if _pool is None:
        raise RuntimeError("Database not initialised: init_db() must run first")
    return _pool


# ---------------------------------------------------------------------------
# Queue
# ---------------------------------------------------------------------------

def _notify_playback(guild_id):
    """Queue mutations push the same SSE event the playback pipeline uses,
    so the music page refetches its lists no matter which surface mutated
    them (Discord commands, panel actions). Lazy import: db must not depend
    on the api module at load time."""
    try:
        from api import notify_playback
        notify_playback(guild_id)
    except ImportError:
        pass


async def enqueue_track(guild_id, title, webpage_url, search_query=None,
                        collection_id=None, collection_type=None,
                        requester_id=None, channel_id=None):
    """Append a track to a guild's queue, returns the queue row id.

    Only resolvable, canonical data is stored: the ephemeral stream URL is
    re-resolved just before playback, since YouTube/SoundCloud stream URLs
    expire after a few hours.
    """
    async with _pool_or_raise().acquire() as conn:
        row_id = await conn.fetchval(
            INSERT_TRACK, guild_id, title, webpage_url, search_query,
            collection_id, collection_type, requester_id, channel_id
        )
    _notify_playback(guild_id)
    return row_id


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
            row_id = await conn.fetchval(
                INSERT_TRACK_AT_POSITION, guild_id, title, webpage_url,
                search_query, collection_id, collection_type,
                requester_id, channel_id, position
            )
    _notify_playback(guild_id)
    return row_id


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
            moved = await conn.fetchrow(
                "UPDATE queue_items SET position = $3 "
                "WHERE guild_id = $1 AND id = $2 "
                "RETURNING id, title, position",
                guild_id, item_id, position
            )
            _notify_playback(guild_id)
            return moved


async def swap_queue_positions(guild_id, id_a, id_b):
    """Exchange the positions of two queued rows. Unlike move_queue_item it
    never renumbers the rows in between: every other track keeps its exact
    position. Returns False unless both rows exist in the guild."""
    async with _pool_or_raise().acquire() as conn:
        async with conn.transaction():
            rows = await conn.fetch(
                "SELECT id, position FROM queue_items "
                "WHERE guild_id = $1 AND id = ANY($2::bigint[])",
                guild_id, [id_a, id_b]
            )
            if len(rows) != 2:
                return False
            await conn.execute(
                "UPDATE queue_items SET position = $2 WHERE id = $1",
                rows[0]['id'], rows[1]['position']
            )
            await conn.execute(
                "UPDATE queue_items SET position = $2 WHERE id = $1",
                rows[1]['id'], rows[0]['position']
            )
            return True


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
        if item is not None:
            _notify_playback(guild_id)
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
    _notify_playback(guild_id)


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


async def set_queue_order(guild_id, order):
    """Rewrite positions from the wanted item-id order (drag & drop batch).
    Ids not listed keep their relative order after the listed ones; unknown
    ids are ignored. Returns the number of rows renumbered. Positions are
    plain BIGINTs (no unique constraint), so renumbering cannot clash."""
    async with _pool_or_raise().acquire() as conn:
        listed = await conn.fetch(
            "SELECT id FROM queue_items WHERE guild_id = $1", guild_id
        )
        known = {r['id'] for r in listed}
        wanted = [i for i in order if i in known]
        rest = [r['id'] for r in listed if r['id'] not in set(wanted)]
        final = wanted + rest
        if final:
            await conn.executemany(
                "UPDATE queue_items SET position = $2 WHERE guild_id = $1 AND id = $3",
                [(guild_id, idx, item_id) for idx, item_id in enumerate(final)]
            )
        _notify_playback(guild_id)
        return len(final)


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


async def list_history(guild_id, limit=50, offset=0, q=None):
    where = ("AND title ILIKE $4" if q else "")
    args = [guild_id, limit, offset, f"%{q}%"] if q else [guild_id, limit, offset]
    async with _pool_or_raise().acquire() as conn:
        return await conn.fetch(
            f"SELECT * FROM history WHERE guild_id = $1 {where} ORDER BY id DESC LIMIT $2 OFFSET $3",
            *args
        )


async def count_history(guild_id, q=None):
    where = ("AND title ILIKE $2" if q else "")
    args = [guild_id, f"%{q}%"] if q else [guild_id]
    async with _pool_or_raise().acquire() as conn:
        return await conn.fetchval(
            f"SELECT count(*) FROM history WHERE guild_id = $1 {where}", *args
        )


async def clear_history(guild_id):
    """Wipe one guild's playback history; returns the removed row count."""
    async with _pool_or_raise().acquire() as conn:
        return await conn.execute("DELETE FROM history WHERE guild_id = $1", guild_id)


async def delete_history_item(guild_id, history_id):
    """Remove one history row of a guild; returns True when it existed."""
    async with _pool_or_raise().acquire() as conn:
        status = await conn.execute(
            "DELETE FROM history WHERE guild_id = $1 AND id = $2",
            guild_id, history_id
        )
        return status == 'DELETE 1'


# ---------------------------------------------------------------------------
# Favorites (per web account)
# ---------------------------------------------------------------------------

async def add_favorite(user_id, title, webpage_url, search_query=None):
    """Star a track for one account; idempotent (the unique index makes a
    duplicate a no-op). Returns the row id or None when it already existed."""
    async with _pool_or_raise().acquire() as conn:
        row = await conn.fetchrow(
            "INSERT INTO favorites (user_id, title, webpage_url, search_query) "
            "VALUES ($1, $2, $3, $4) "
            "ON CONFLICT (user_id, webpage_url) DO NOTHING "
            "RETURNING id",
            user_id, title, webpage_url, search_query
        )
        return row['id'] if row else None


async def list_favorites(user_id):
    """All favorites of one account, newest first."""
    async with _pool_or_raise().acquire() as conn:
        return await conn.fetch(
            "SELECT id, title, webpage_url, search_query, created_at "
            "FROM favorites WHERE user_id = $1 ORDER BY id DESC",
            user_id
        )


async def delete_favorite(user_id, favorite_id):
    """Remove one favorite owned by the account; returns True when a row was
    actually deleted (the WHERE is the ownership check)."""
    async with _pool_or_raise().acquire() as conn:
        status = await conn.execute(
            "DELETE FROM favorites WHERE user_id = $1 AND id = $2",
            user_id, favorite_id
        )
        return status == 'DELETE 1'


# ---------------------------------------------------------------------------
# Now playing (real-time mirror for the web API)
# ---------------------------------------------------------------------------

async def set_now_playing(guild_id, title, webpage_url, requester_id=None, duration=None):
    async with _pool_or_raise().acquire() as conn:
        await conn.execute(
            "INSERT INTO now_playing (guild_id, title, webpage_url, requester_id, duration) "
            "VALUES ($1, $2, $3, $4, $5) "
            "ON CONFLICT (guild_id) DO UPDATE SET "
            "title = EXCLUDED.title, webpage_url = EXCLUDED.webpage_url, "
            "requester_id = EXCLUDED.requester_id, started_at = now(), "
            "duration = EXCLUDED.duration",
            guild_id, title, webpage_url, requester_id, duration
        )


async def clear_now_playing(guild_id):
    async with _pool_or_raise().acquire() as conn:
        await conn.execute("DELETE FROM now_playing WHERE guild_id = $1", guild_id)


async def clear_now_playing_all():
    """Wipe the whole playback mirror. Bot startup only: right after a boot
    the bot is in no voice channel, so any surviving row is a dangling
    artifact of a previous run that died mid-playback."""
    async with _pool_or_raise().acquire() as conn:
        await conn.execute("DELETE FROM now_playing")


async def list_now_playing():
    async with _pool_or_raise().acquire() as conn:
        return await conn.fetch("SELECT * FROM now_playing ORDER BY guild_id")


# ---------------------------------------------------------------------------
# Auth accounts & sessions (web API)
# ---------------------------------------------------------------------------

ROLES = ('nobody', 'viewer', 'admin')  # legacy coarse roles, kept for the migration above

# Granular permission keys (JSON arrays on users and user_guild_permissions).
PERMISSIONS = ('view', 'queue', 'queue_manage', 'playback', 'manage_users')
ALL_PERMISSIONS_JSON = '["view","queue","queue_manage","playback","manage_users"]'

# Shared by upsert_discord_user (used twice: original + disambiguated username).
_UPSERT_DISCORD_USER_SQL = (
    "INSERT INTO users (username, discord_id, avatar_hash, password_hash, permissions, seed) "
    "VALUES ($2, $1, $3, '', $4, $5) "
    "ON CONFLICT (discord_id) DO UPDATE SET "
    "username = EXCLUDED.username, avatar_hash = EXCLUDED.avatar_hash, "
    "seed = EXCLUDED.seed, "
    "permissions = CASE "
    "  WHEN EXCLUDED.seed THEN EXCLUDED.permissions "
    "  WHEN users.seed THEN '[]' "
    "  ELSE users.permissions END "
    "RETURNING id, username, permissions, seed"
)


def normalize_permissions(raw):
    """Validate/dedupe a permission list; unknown keys are dropped."""
    received = raw if isinstance(raw, (list, tuple)) else []
    return [p for p in PERMISSIONS if p in received]


def parse_permissions(raw):
    """Decode a permission array into a validated list. Accepts the stored
    JSON string OR an already-decoded list (route handlers pass user dicts
    whose 'permissions' field was decoded by the auth dependency). The
    retired 'search' key aliases to 'queue' so stale legacy rows degrade
    gracefully instead of losing the merged permission."""
    try:
        keys = json.loads(raw) if isinstance(raw, str) else raw
        if isinstance(keys, list):
            keys = ['queue' if k == 'search' else k for k in keys]
        return normalize_permissions(keys)
    except (ValueError, TypeError):
        return []


async def upsert_discord_user(discord_id, username, avatar_hash, env_role):
    """Create or update the Discord-linked account; the Discord ID is the
    identity, username is the visible Discord display name. avatar_hash is the
    raw Discord avatar hash (used to rebuild the CDN URL for the web panel).
    env_role is 'admin' for discord_ids listed in DISCORD_ADMIN_IDS, else
    'nobody'. Rules on login:
    - env admin -> all permissions, seed=TRUE (env owns the account)
    - non env member previously seeded (removed from DISCORD_ADMIN_IDS)
      -> permissions wiped to [] (env revocation, takes effect at this login)
    - non seeded user -> granted permissions are preserved."""
    seed = env_role == 'admin'
    # New accounts (non seeded) start WITHOUT permissions; the access-request
    # flow onboards them. Only env admins inherit the full set at first login.
    initial_permissions = ALL_PERMISSIONS_JSON if seed else '[]'
    async with _pool_or_raise().acquire() as conn:
        try:
            row = await conn.fetchrow(
                _UPSERT_DISCORD_USER_SQL,
                discord_id, username, avatar_hash, initial_permissions, seed
            )
        except asyncpg.UniqueViolationError:
            # Login is open to any Discord account now, so global_name may
            # collide across different Discord ids; disambiguate with the id
            # tail instead of refusing the login.
            row = await conn.fetchrow(
                _UPSERT_DISCORD_USER_SQL,
                discord_id, f"{username}-{discord_id[-4:]}", avatar_hash,
                initial_permissions, seed
            )
    return row


async def list_users():
    """Accounts plus their per-guild overrides in one join (no N+1 reads from
    the panel): overrides ride along so the list can show scoped grants."""
    async with _pool_or_raise().acquire() as conn:
        return await conn.fetch(
            "SELECT u.id, u.username, u.created_at, u.role, u.seed, u.discord_id, "
            "u.avatar_hash, u.permissions, "
            "COALESCE(json_agg(json_build_object("
            "  'guild_id', o.guild_id::text, 'permissions', o.permissions"
            ")) FILTER (WHERE o.user_id IS NOT NULL), '[]'::json) AS guild_overrides, "
            "u.guild_ids "
            "FROM users u LEFT JOIN user_guild_permissions o ON o.user_id = u.id "
            "GROUP BY u.id ORDER BY u.created_at"
        )


async def get_user_by_id(user_id):
    async with _pool_or_raise().acquire() as conn:
        return await conn.fetchrow(
            "SELECT id, username, role, seed, guild_ids, discord_id, permissions FROM users WHERE id = $1",
            user_id
        )


async def set_user_guilds_snapshot(user_id, guild_ids_json):
    """Store the guilds shared between the user and the bot (JSON list),
    refreshed at every Discord login and on every access-panel read."""
    async with _pool_or_raise().acquire() as conn:
        await conn.execute(
            "UPDATE users SET guild_ids = $2 WHERE id = $1",
            user_id, guild_ids_json
        )


async def set_discord_oauth_tokens(user_id, access_token, refresh_token, expires_at):
    """Persist the user OAuth pair from the latest login (or token refresh).
    The access-panel endpoints use it to refresh the shared-guilds snapshot
    lazily, so joining or leaving a server does not need a re-login."""
    async with _pool_or_raise().acquire() as conn:
        await conn.execute(
            "UPDATE users SET discord_access_token = $2, discord_refresh_token = $3, "
            "discord_token_expires = $4 WHERE id = $1",
            user_id, access_token, refresh_token, expires_at
        )


async def create_access_request(user_id, guild_id, guild_name, message):
    """Pending requests are unique per (user, guild); guild_id may be None for
    a generic request (unique per user alone). Returns the new row id or None
    when the equivalent pending request already exists."""
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
            # guild_id is cast to text: Discord ids exceed the JS safe
            # integer range, and the panel re-uses this value in path params.
            "SELECT r.id, r.user_id, u.username, r.guild_id::text, r.guild_name, "
            "r.message, r.status, r.created_at "
            "FROM access_requests r JOIN users u ON u.id = r.user_id "
            "WHERE r.status = 'pending' ORDER BY r.created_at"
        )


async def list_pending_access_requests_by_user(user_id):
    """Pending requests of one account: the pending guild ids plus whether a
    generic request is open. The web panel uses it to restore the already-
    requested state after a reload (no re-click on a pending ask)."""
    async with _pool_or_raise().acquire() as conn:
        rows = await conn.fetch(
            "SELECT guild_id FROM access_requests "
            "WHERE user_id = $1 AND status = 'pending'",
            user_id
        )
        return [r['guild_id'] for r in rows]


async def set_access_request_status(request_id, status, resolved_by=None):
    """Resolve a pending request (granted/denied), recording who decided it
    and through which surface (e.g. "Pako (web panel)"); returns the row or
    None when unknown or already resolved (the WHERE clause makes the
    resolution atomic: a race between two resolvers yields one winner)."""
    if status not in ('granted', 'denied'):
        return None
    async with _pool_or_raise().acquire() as conn:
        return await conn.fetchrow(
            "UPDATE access_requests SET status = $2, resolved_by = $3 "
            "WHERE id = $1 AND status = 'pending' "
            "RETURNING id, user_id, status",
            request_id, status, resolved_by
        )


async def get_access_request(request_id):
    """Full row of one access request (joined username/seed for resolvers);
    None when the id is unknown."""
    async with _pool_or_raise().acquire() as conn:
        return await conn.fetchrow(
            "SELECT r.id, r.user_id, u.username, u.seed, r.guild_id, "
            "r.guild_name, r.message, r.status, r.resolved_by, r.created_at "
            "FROM access_requests r JOIN users u ON u.id = r.user_id "
            "WHERE r.id = $1",
            request_id
        )


async def set_user_permissions(user_id, permissions):
    """Replace a user's global permission set. Seed accounts (owned by
    DISCORD_ADMIN_IDS) are untouchable; returns the updated row or None
    (unknown user / seeded account)."""
    wanted = normalize_permissions(permissions)
    payload = json.dumps(wanted) if wanted else '[]'
    async with _pool_or_raise().acquire() as conn:
        return await conn.fetchrow(
            "UPDATE users SET permissions = $2 WHERE id = $1 AND seed = FALSE "
            "RETURNING id, username, permissions",
            user_id, payload
        )


async def delete_discord_user(user_id):
    """Remove a non-seed account (api_sessions, access_requests and
    user_guild_permissions cascade from the users FK). The WHERE clause is
    the second enforcement of the seeded-admin protection on top of the
    route check; returns True when a row was actually deleted."""
    async with _pool_or_raise().acquire() as conn:
        status = await conn.execute(
            "DELETE FROM users WHERE id = $1 AND seed = FALSE", user_id
        )
        return status == 'DELETE 1'


async def get_user_guild_permissions(user_id, guild_id):
    """Per-guild override row for (user, guild); None when no override."""
    async with _pool_or_raise().acquire() as conn:
        return await conn.fetchrow(
            "SELECT permissions FROM user_guild_permissions "
            "WHERE user_id = $1 AND guild_id = $2",
            user_id, guild_id
        )


async def list_user_guild_permissions(user_id):
    async with _pool_or_raise().acquire() as conn:
        return await conn.fetch(
            "SELECT guild_id, permissions FROM user_guild_permissions "
            "WHERE user_id = $1 ORDER BY guild_id",
            user_id
        )


async def set_user_guild_permissions(user_id, guild_id, permissions):
    """Upsert the per-guild override; it replaces the global set in-guild."""
    wanted = normalize_permissions(permissions)
    payload = json.dumps(wanted) if wanted else '[]'
    async with _pool_or_raise().acquire() as conn:
        return await conn.fetchrow(
            "INSERT INTO user_guild_permissions (user_id, guild_id, permissions) "
            "VALUES ($1, $2, $3) "
            "ON CONFLICT (user_id, guild_id) DO UPDATE SET permissions = EXCLUDED.permissions "
            "RETURNING guild_id, permissions",
            user_id, guild_id, payload
        )


async def delete_user_guild_permissions(user_id, guild_id):
    """Remove the per-guild override; the global set applies again.
    Returns True when a row was actually deleted."""
    async with _pool_or_raise().acquire() as conn:
        result = await conn.execute(
            "DELETE FROM user_guild_permissions WHERE user_id = $1 AND guild_id = $2",
            user_id, guild_id
        )
        # asyncpg returns the "DELETE <count>" status string
        return result.split()[-1] != '0'


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
            "SELECT u.id, u.username, u.permissions, u.avatar_hash, u.discord_id, u.guild_ids, "
            "u.discord_access_token, u.discord_refresh_token, u.discord_token_expires "
            "FROM api_sessions s "
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
