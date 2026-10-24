"""HTTP API to control PakoDJ from any external client (language-agnostic).

Runs inside the bot process (same asyncio event loop as discord.py), so it
can read live voice state and invoke playback directly.

SECURITY:
- Login is Discord OAuth only: GET /api/auth/discord/start, complete the
  flow, then use the returned session_token as `Authorization: Bearer <token>`.
- Granular permissions: accounts carry a permission set (view, search, queue,
  queue_manage, playback, manage_users), possibly overridden per guild
  (table user_guild_permissions). Full set for DISCORD_ADMIN_IDS at login;
  admins manage other accounts' permissions via the /api/users endpoints.
- Sessions are server side (api_sessions table), only their SHA-256 is
  stored, they expire (API_SESSION_TTL_DAYS, default 7) and can be revoked
  per user by deleting rows — no shared static secret anywhere.

No port is published by default: reachability is decided by the compose
file, nothing is exposed to the internet.
"""
import os
import time
import json
import secrets
import hashlib
import asyncio
import discord
from datetime import datetime, timezone, timedelta
from typing import Literal, Optional
from urllib.parse import quote, urlencode

from fastapi import Depends, FastAPI, HTTPException, Header, Request
from fastapi.responses import StreamingResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field, model_validator

import db as database


class PlayRequest(BaseModel):
    query: str = Field(min_length=1, max_length=500)


class QueueEnqueueRequest(BaseModel):
    query: str = Field(min_length=1, max_length=500)
    position: Optional[int] = Field(None, ge=0)


class QueueMoveRequest(BaseModel):
    position: int = Field(ge=0)


class QueueReorderRequest(BaseModel):
    order: list[int] = Field(max_length=500)


class SkipRequest(BaseModel):
    all: bool = False


class _ViewBaselineMixin(BaseModel):
    # 'view' is the entry permission: a set without it cannot hold anything
    # else. Invalid sets must never exist, not even transiently (the request
    # is rejected at parse time with a 422).
    permissions: list = Field(max_length=5)

    @model_validator(mode="after")
    def _view_is_baseline(self):
        if self.permissions and 'view' not in self.permissions:
            raise ValueError("'view' is mandatory: no other permission can exist without it")
        return self


class GlobalPermissionsRequest(_ViewBaselineMixin):
    permissions: list[Literal['view', 'queue', 'queue_manage', 'playback', 'manage_users']] = Field(max_length=5)


# Per-guild overrides never carry manage_users: account administration is a
# global-only capability, so the narrower Literal rejects it at the API gate.
class GuildPermissionsRequest(_ViewBaselineMixin):
    permissions: list[Literal['view', 'queue', 'queue_manage', 'playback']] = Field(max_length=4)


class FavoriteAddRequest(BaseModel):
    title: str = Field(min_length=1, max_length=300)
    webpage_url: str = Field(min_length=1, max_length=500)
    search_query: Optional[str] = Field(default=None, max_length=300)


class AccessRequestCreate(BaseModel):
    guild_id: Optional[int] = None
    message: str = Field('', max_length=200)
    message: str = Field('', max_length=200)


class AccessRequestUpdate(BaseModel):
    status: Literal['granted', 'denied']


# ---------------------------------------------------------------------------
# Discord OAuth login (preparation; inert until the env vars are set)
# ---------------------------------------------------------------------------
DISCORD_CLIENT_ID = os.getenv('DISCORD_CLIENT_ID')
DISCORD_CLIENT_SECRET = os.getenv('DISCORD_CLIENT_SECRET')
DISCORD_ADMIN_IDS = {
    s.strip() for s in (os.getenv('DISCORD_ADMIN_IDS') or '').split(',') if s.strip().isdigit()
}
API_PUBLIC_ORIGIN = os.getenv('API_PUBLIC_ORIGIN', '').rstrip('/')

_oauth_states = {}  # CSRF state -> expiry timestamp

# The access panel refreshes the user's shared-guilds snapshot from Discord
# using the stored OAuth pair (stored per login). The lock serializes rare
# refreshes so two concurrent sessions of the same account never grind the
# same refresh token; it also caps Discord API pressure.
_snapshot_refresh_lock = asyncio.Lock()


async def refresh_user_guilds_snapshot(user, bot):
    """Refresh users.guild_ids from Discord with the stored OAuth pair: this
    is what keeps guild joins and leaves accurate without a new login. Like
    the login path it stores only the intersection with the bot's live
    guilds (the snapshot means shared-with-bot). Returns the fresh guild ID
    list, or None when nothing could be fetched (no tokens, revoked/expired
    refresh, Discord hiccup) so the caller falls back to the stored
    snapshot without failing the read."""
    access = user.get('discord_access_token') or ''
    refresh = user.get('discord_refresh_token') or ''
    if not access and not refresh:
        return None
    expires_at = user.get('discord_token_expires')
    import aiohttp  # provided by discord.py
    async with _snapshot_refresh_lock:
        async with aiohttp.ClientSession() as session:
            needs_refresh = (
                refresh and
                expires_at is not None and
                expires_at <= datetime.now(timezone.utc)
            )
            if needs_refresh:
                resp = await session.post('https://discord.com/api/oauth2/token', data={
                    'client_id': DISCORD_CLIENT_ID,
                    'client_secret': DISCORD_CLIENT_SECRET,
                    'grant_type': 'refresh_token',
                    'refresh_token': refresh,
                })
                if resp.status != 200:
                    return None
                tokens = await resp.json()
                access = tokens.get('access_token') or access
                refresh = tokens.get('refresh_token') or refresh
                await database.set_discord_oauth_tokens(
                    user['id'], access, refresh,
                    datetime.fromtimestamp(
                        time.time() + int(tokens.get('expires_in', 0)), tz=timezone.utc
                    ) if tokens.get('expires_in') else None
                )
            if not access:
                return None
            resp = await session.get(
                'https://discord.com/api/users/@me/guilds',
                headers={
                    'Authorization': f"Bearer {access}",
                    'User-Agent': 'PakoDJ Bot (https://github.com/Pako3549/PakoDJ)',
                },
            )
            if resp.status != 200:
                return None
            user_guilds = await resp.json()
        bot_guild_ids = {str(g.id) for g in bot.guilds}
        ids = [str(g.get('id')) for g in user_guilds if g.get('id') and str(g.get('id')) in bot_guild_ids]
    await database.set_user_guilds_snapshot(user['id'], json.dumps(ids))
    return ids
_search_cache: dict = {}  # metadata query -> (expiry timestamp, results), 5-minute TTL


def _row_dict(row):
    """Record mapping with Discord snowflake fields as strings, so the JSON
    survives JavaScript precision (snowflakes exceed Number.MAX_SAFE_INTEGER)."""
    data = dict(row)
    for key in ("guild_id", "user_id", "requester_id", "channel_id"):
        value = data.get(key)
        if isinstance(value, int):
            data[key] = str(value)
    return data


def discord_login_configured() -> bool:
    return bool(DISCORD_CLIENT_ID and DISCORD_CLIENT_SECRET and API_PUBLIC_ORIGIN)


def avatar_url(discord_id, avatar_hash):
    """Discord CDN URL for the account avatar; the generic placeholder for
    accounts logged in before avatar storage (hash refreshed at next login)."""
    if not discord_id:
        return None
    if avatar_hash:
        ext = 'gif' if avatar_hash.startswith('a_') else 'png'
        return f"https://cdn.discordapp.com/avatars/{discord_id}/{avatar_hash}.{ext}?size=64"
    return "https://cdn.discordapp.com/embed/avatars/0.png"


# ---------------------------------------------------------------------------
# Session primitives
# ---------------------------------------------------------------------------

def token_digest(token: str) -> str:
    """Plain session tokens are never stored; only their SHA-256 is."""
    return hashlib.sha256(token.encode()).hexdigest()


# ---------------------------------------------------------------------------
# Live update fan-out (SSE): browsers hold ONE busy-waiting connection and get
# pushed the "granted" event the moment an admin resolves their request — no
# polling loops hammering the API.
# ---------------------------------------------------------------------------
# Stream registry keyed by account id; every entry carries the admin flag
# snapshotted at connect time so broadcasts know who gets queue events.
_stream_queues: dict[int, list[tuple[asyncio.Queue, bool]]] = {}


def _notify_user(user_id: int, event: str, data: str) -> None:
    """Fan-out to every open stream of one account; capped queues drop events
    instead of growing (granted is the only signal the panel reacts to)."""
    for q, _is_admin in _stream_queues.get(user_id, []):
        if q.qsize() < q.maxsize:
            q.put_nowait((event, data))


def _notify_admins(event: str, data: str) -> None:
    """Broadcast to the manage_users streams only (admin queue updates)."""
    for entries in _stream_queues.values():
        for q, is_admin in entries:
            if is_admin and q.qsize() < q.maxsize:
                q.put_nowait((event, data))


def _notify_all(event: str, data: str) -> None:
    """Fan-out to every open stream regardless of the admin flag (playback
    state changes concern the whole music page)."""
    for entries in _stream_queues.values():
        for q, _is_admin in entries:
            if q.qsize() < q.maxsize:
                q.put_nowait((event, data))


def notify_playback(guild_id: int) -> None:
    """Called by the playback pipeline: the music page refetches status and
    queue on this event instead of polling."""
    _notify_all("playback", str(guild_id))


async def resolve_access_request_from_dm(request_id: int, grant: bool, actor: str) -> str:
    """Shared resolver behind the Accept/Deny buttons on the admin DM: the
    same permission flow as the web panel (guild request -> `view` merged
    into that guild's override, generic request -> `view` merged into the
    global set), then the atomic status flip and the usual live-update
    events. `actor` is recorded so any DM copy can show who decided and
    from where. Returns one of 'granted', 'denied', 'already', 'unknown'."""
    row = await database.get_access_request(request_id)
    if row is None:
        return 'unknown'
    if row['status'] != 'pending':
        return 'already'
    if grant and not row['seed']:
        # Seeded accounts are untouchable (and would never need a grant).
        user = await database.get_user_by_id(row['user_id'])
        if user is not None and not user['seed']:
            if row['guild_id'] is not None:
                existing = await database.get_user_guild_permissions(row['user_id'], row['guild_id'])
                perms = database.parse_permissions(existing['permissions']) if existing else []
            else:
                perms = database.parse_permissions(user['permissions'])
            if 'view' not in perms:
                perms = [*perms, 'view']
            if row['guild_id'] is not None:
                await database.set_user_guild_permissions(row['user_id'], row['guild_id'], perms)
            else:
                await database.set_user_permissions(row['user_id'], perms)
    status = 'granted' if grant else 'denied'
    updated = await database.set_access_request_status(request_id, status, actor)
    if updated is None:
        return 'already'
    if grant:
        _notify_user(row['user_id'], "granted", "true")
    _notify_admins("requests", str(request_id))
    return status


class AccessRequestDMView(discord.ui.View):
    """Accept/Deny quick actions on the admin DM. The buttons carry only a
    custom_id convention resolved by the raw on_interaction listener in the
    bot core, so they keep working after a restart (no view registration)."""

    def __init__(self, request_id: int):
        super().__init__(timeout=None)
        self.add_item(discord.ui.Button(
            label="Accept", style=discord.ButtonStyle.success,
            custom_id=f"reqdm:grant:{request_id}",
        ))
        self.add_item(discord.ui.Button(
            label="Deny", style=discord.ButtonStyle.danger,
            custom_id=f"reqdm:deny:{request_id}",
        ))


# ---------------------------------------------------------------------------
# Application factory (no circular imports: main.py injects its live pieces)
# ---------------------------------------------------------------------------

def create_api_app(bot, resolve_audio_info_async, play_audio, play_queued_row, get_server_info, get_server_lock):
    # App-level dependency: everything requires a valid session, except the
    # Discord OAuth flow itself (trailing slashes normalized).
    async def _require_account(request: Request, authorization: Optional[str] = Header(None)) -> dict:
        if request.url.path.rstrip('/').startswith("/api/auth/discord/"):
            return {}
        token = None
        if authorization and authorization.startswith('Bearer '):
            token = authorization[7:].strip()
        if not token:
            raise HTTPException(status_code=401, detail="Missing session token (GET /api/auth/discord/start first)")
        user = await database.get_session_user(token_digest(token))
        if user is None:
            raise HTTPException(status_code=401, detail="Invalid or expired session")
        return dict(user)

    app = FastAPI(title="PakoDJ API", version="1.0", dependencies=[Depends(_require_account)])

    # Granular permission gate. The global permission set applies everywhere;
    # a per-guild override row (user_guild_permissions) replaces it inside
    # that specific guild. Endpoints whose path carries guild_id are scoped.
    async def _effective_permissions(user: dict, guild_id=None) -> list:
        guild_key = int(guild_id) if guild_id is not None else None
        if guild_key is not None:
            override = await database.get_user_guild_permissions(user['id'], guild_key)
            if override:
                return database.parse_permissions(override['permissions'])
        return database.parse_permissions(user.get('permissions'))

    # Without 'view' the account has no access surface at all: the
    # functional gates (music control) implicitly require it on top.
    _FUNCTIONAL_PERMISSIONS = ('queue', 'queue_manage', 'playback')

    def _require_permission(permission: str):
        async def dep(request: Request, user: dict = Depends(_require_account)) -> dict:
            perms = await _effective_permissions(user, request.path_params.get('guild_id'))
            if permission not in perms:
                raise HTTPException(status_code=403, detail=f"Missing '{permission}' permission")
            if permission in _FUNCTIONAL_PERMISSIONS and 'view' not in perms:
                raise HTTPException(status_code=403, detail="Missing 'view' permission")
            return {**user, 'permissions': perms}
        return dep

    # Browser CORS is opt-in only (server-to-server callers don't need it)
    cors_origins = [o.strip() for o in os.getenv('API_CORS_ORIGINS', '').split(',') if o.strip()]
    if cors_origins:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=cors_origins,
            allow_credentials=True,
            allow_methods=["*"],
            allow_headers=["*"],
        )

    @app.on_event("startup")
    async def bootstrap():
        await database.purge_expired_sessions()

    async def _new_session_payload(user) -> dict:
        token = secrets.token_hex(32)
        ttl_days = int(os.getenv('API_SESSION_TTL_DAYS', '7'))
        expires_at = datetime.now(timezone.utc) + timedelta(days=ttl_days)
        await database.create_session(user['id'], token_digest(token), expires_at)
        await database.purge_expired_sessions()
        return {
            "session_token": token,
            "expires_at": expires_at.isoformat(),
            "username": user['username'],
            "permissions": database.parse_permissions(user['permissions']),
        }

    # ------------------------------------------------------------------
    # Auth
    # ------------------------------------------------------------------
    @app.get("/api/auth/me")
    async def me(user: dict = Depends(_require_account)):
        return {
            "id": user['id'],
            "username": user['username'],
            "permissions": database.parse_permissions(user.get('permissions')),
            "avatar_url": avatar_url(user.get('discord_id'), user.get('avatar_hash')),
        }

    @app.post("/api/auth/logout")
    async def logout(user: dict = Depends(_require_account), authorization: Optional[str] = Header(None)):
        if authorization and authorization.startswith('Bearer '):
            await database.delete_session(token_digest(authorization[7:].strip()))
        return {"logged_out": True}

    # ------------------------------------------------------------------
    # Live updates (SSE): one held connection per browser session; the
    # account table is the key: "granted" goes to its target, "requests"
    # broadcasts to every manage_users stream (new access requests).
    # ------------------------------------------------------------------
    @app.get("/api/stream/updates")
    async def stream_updates(user: dict = Depends(_require_account)):
        async def gen():
            queue: asyncio.Queue = asyncio.Queue(maxsize=16)
            # Admin flag snapshot: manage_users streams also receive broadcast
            # queue events (new access requests). Revocation mid-stream keeps
            # pushing harmless events; the next list fetch 403s anyway.
            is_admin = 'manage_users' in database.parse_permissions(user.get('permissions'))
            _stream_queues.setdefault(user['id'], []).append((queue, is_admin))
            try:
                yield "event: connected\ndata: {}\n\n"
                while True:
                    try:
                        event, data = await asyncio.wait_for(queue.get(), timeout=15)
                        yield f"event: {event}\ndata: {data}\n\n"
                    except asyncio.TimeoutError:
                        # Keepalive write: doubles as disconnect detection (a
                        # closed browser connection raises on the next yield).
                        yield ": keepalive\n\n"
            finally:
                open_streams = _stream_queues.get(user['id'], [])
                for entry in open_streams:
                    if entry[0] is queue:
                        open_streams.remove(entry)
                        break
                if not open_streams:
                    _stream_queues.pop(user['id'], None)

        return StreamingResponse(
            gen(),
            media_type="text/event-stream",
            # X-Accel-Buffering: nginx streams this response instead of
            # buffering it (no extra nginx config needed anywhere).
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    # ------------------------------------------------------------------
    # Discord OAuth login (inert until DISCORD_CLIENT_ID/SECRET + origin env)
    # ------------------------------------------------------------------
    @app.get("/api/auth/discord/start")
    async def discord_start():
        if not discord_login_configured():
            raise HTTPException(
                status_code=503,
                detail="Discord login not configured (needs DISCORD_CLIENT_ID, DISCORD_CLIENT_SECRET, API_PUBLIC_ORIGIN)"
            )
        state = secrets.token_hex(16)
        now_ts = time.time()
        for s in [s for s, exp in _oauth_states.items() if exp < now_ts]:
            _oauth_states.pop(s, None)
        _oauth_states[state] = now_ts + 300
        params = urlencode({
            'client_id': DISCORD_CLIENT_ID,
            'redirect_uri': f"{API_PUBLIC_ORIGIN}/api/auth/discord/callback",
            'response_type': 'code',
            'scope': 'identify guilds',
            'state': state,
        })
        return {"authorize_url": f"https://discord.com/oauth2/authorize?{params}"}

    @app.get("/api/auth/discord/callback")
    async def discord_callback(request: Request, code: str = '', state: str = ''):
        # Browser callers must never land on a dead JSON page (a bot restart
        # between /start and the callback invalidates the state, for example):
        # failures bounce back to /login with the reason as a query param.
        async def _fail(detail: str):
            if request.query_params.get('json') != '1':
                from fastapi.responses import RedirectResponse
                return RedirectResponse(
                    url=f"/login?error={quote(detail, safe='')}",
                    status_code=303
                )
            raise HTTPException(status_code=400, detail=detail)

        if not discord_login_configured():
            return await _fail("Discord login not configured")
        expiry = _oauth_states.pop(state, None) if state else None
        if expiry is None or expiry < time.time():
            return await _fail("Login state expired, start again")
        if not code:
            return await _fail("Missing Discord authorization code")

        import aiohttp  # provided by discord.py
        async with aiohttp.ClientSession() as session:
            resp = await session.post('https://discord.com/api/oauth2/token', data={
                'client_id': DISCORD_CLIENT_ID,
                'client_secret': DISCORD_CLIENT_SECRET,
                'grant_type': 'authorization_code',
                'code': code,
                'redirect_uri': f"{API_PUBLIC_ORIGIN}/api/auth/discord/callback",
            })
            if resp.status != 200:
                return await _fail("Discord token exchange failed")
            tokens = await resp.json()
            auth_headers = {
                'Authorization': f"Bearer {tokens['access_token']}",
                'User-Agent': 'PakoDJ Bot (https://github.com/Pako3549/PakoDJ)',
            }
            resp = await session.get('https://discord.com/api/users/@me', headers=auth_headers)
            if resp.status != 200:
                return await _fail("Discord identify failed")
            me = await resp.json()
            resp = await session.get('https://discord.com/api/users/@me/guilds', headers=auth_headers)
            user_guilds = (await resp.json()) if resp.status == 200 else []

        # Login is open to any Discord account: no shared server is required.
        # The API stays closed until an admin grants permissions; accounts
        # that share no server with the bot can ask via the generic request.
        discord_id = str(me['id'])
        env_role = 'admin' if discord_id in DISCORD_ADMIN_IDS else 'nobody'
        user = await database.upsert_discord_user(
            discord_id, me.get('global_name') or me['username'], me.get('avatar'), env_role
        )
        # Snapshot the guilds shared between this user and the bot; the access
        # request flow (GET /api/access/guilds, POST /api/access-requests) reads it.
        user_guild_ids = {str(g.get('id')) for g in user_guilds}
        guild_snapshot = [str(g.id) for g in bot.guilds if str(g.id) in user_guild_ids]
        await database.set_user_guilds_snapshot(user['id'], json.dumps(guild_snapshot))
        # Keep the OAuth pair for the lazy snapshot refresh: the panel can
        # re-check membership later (joins/leaves) without a new login.
        expires_in = tokens.get('expires_in')
        try:
            await database.set_discord_oauth_tokens(
                user['id'], tokens.get('access_token', ''), tokens.get('refresh_token', ''),
                datetime.fromtimestamp(time.time() + int(expires_in), tz=timezone.utc) if expires_in else None
            )
        except (ValueError, TypeError):
            print("WARNING: could not persist Discord OAuth tokens for the snapshot refresh")
        payload = await _new_session_payload(user)
        # Browsers get redirected to the webapp with the token in the URL
        # fragment (client-side only, never hits server logs); API clients
        # that explicitly ask for JSON still get the raw payload.
        if request.query_params.get('json') != '1':
            from fastapi.responses import RedirectResponse
            fragment = urlencode({
                'session_token': payload['session_token'],
                'username': payload['username'],
            })
            return RedirectResponse(
                url=f"/auth/callback#{fragment}",
                status_code=303
            )
        return payload

    def get_guild(guild_id: int):
        guild = bot.get_guild(guild_id)
        if guild is None:
            raise HTTPException(status_code=404, detail="Bot is not in that server")
        return guild

    def get_voice(guild):
        return guild.voice_client

    def really_in_voice(guild):
        """Strict voice check: the voice client must report connected AND the
        gateway voice state of the bot must still point at a channel. A dead
        handshake or a UI disconnect leaves a stale VoiceClient behind that
        still claims to be connected — this catches both."""
        vc = guild.voice_client
        if vc is None or not vc.is_connected():
            return False
        member = guild.get_member(bot.user.id)
        return bool(member and member.voice and member.voice.channel)

    async def drop_stale_voice(guild):
        """Force-disconnect a voice client that claims to be connected while
        the gateway disagrees; the next play/start joins fresh. The leave is
        flagged intentional: the voice-loyalty rejoin must not fight it."""
        vc = guild.voice_client
        if vc is not None and vc.is_connected() and not really_in_voice(guild):
            get_server_info(guild.id)['intentional_leave'] = True
            await vc.disconnect(force=True)
            return True
        return False

    def pick_voice_channel(guild, user):
        """Voice channel to auto-join when the panel starts playback: only the
        one where the requesting account's Discord user is sitting. None when
        the account is not in voice — the bot never picks a channel by guess."""
        discord_id = str(user.get('discord_id') or '')
        for channel in guild.voice_channels:
            for member in channel.members:
                if str(member.id) == discord_id:
                    return channel
        return None

    # ------------------------------------------------------------------
    # Basic info
    # ------------------------------------------------------------------
    @app.get("/api/health")
    async def health():
        return {"status": "ok", "guilds": len(bot.guilds)}

    @app.get("/api/guilds")
    async def guilds(user: dict = Depends(_require_account)):
        # Visibility rule: global 'view' exposes every bot guild; accounts
        # without it see only the guilds carrying a per-guild override with
        # 'view' (that override is how single-server grants work: global set
        # stays empty, view lives inside that one guild). Discord snowflakes
        # exceed JS number precision: always serialize as strings.
        if 'view' in database.parse_permissions(user.get('permissions')):
            return [{"id": str(g.id), "name": g.name} for g in bot.guilds]
        overrides = await database.list_user_guild_permissions(user['id'])
        visible = {
            o['guild_id'] for o in overrides
            if 'view' in database.parse_permissions(o['permissions'])
        }
        return [{"id": str(g.id), "name": g.name} for g in bot.guilds if g.id in visible]

    @app.get("/api/guilds/{guild_id}/status")
    async def status(guild_id: int, user: dict = Depends(_require_permission('view'))):
        guild = get_guild(guild_id)
        vc = get_voice(guild)
        in_voice = really_in_voice(guild)
        now = await database.list_now_playing()
        row = next((r for r in now if r['guild_id'] == guild_id), None)
        # Playback position: elapsed since the mirror's started_at, frozen at
        # the pause instant via the runtime paused_at marker (None when idle
        # or when the row predates the duration tracking).
        position = None
        if row is not None and in_voice:
            started = row['started_at'].timestamp()
            if vc.is_playing():
                position = max(0, int(time.time() - started))
            elif vc.is_paused() and get_server_info(guild_id).get('paused_at'):
                position = max(0, int(get_server_info(guild_id)['paused_at'] - started))
        return {
            "guild_id": str(guild_id),
            "guild_name": guild.name,
            "voice_connected": in_voice,
            "playing": bool(in_voice and vc.is_playing()),
            "paused": bool(in_voice and vc.is_paused()),
            "now_playing": _row_dict(row) if row else None,
            "position": position,
            "queue_count": await database.count_queue(guild_id),
            "loading_in_background": bool(get_server_info(guild_id).get('loading_task')),
        }

    # ------------------------------------------------------------------
    # Data
    # ------------------------------------------------------------------
    @app.get("/api/guilds/{guild_id}/queue")
    async def queue(guild_id: int, user: dict = Depends(_require_permission('view'))):
        get_guild(guild_id)
        rows = await database.list_queue(guild_id, limit=200)
        return [_row_dict(r) for r in rows]

    @app.get("/api/guilds/{guild_id}/history")
    async def history(guild_id: int, offset: int = 0, user: dict = Depends(_require_permission('view'))):
        get_guild(guild_id)
        offset = max(0, min(offset, 1000000))
        rows = await database.list_history(guild_id, limit=20, offset=offset)
        return {"items": [_row_dict(r) for r in rows], "total": await database.count_history(guild_id)}

    @app.delete("/api/guilds/{guild_id}/history")
    async def clear_guild_history(guild_id: int, admin: dict = Depends(_require_permission('queue_manage'))):
        get_guild(guild_id)
        removed = await database.clear_history(guild_id)
        return {"cleared": True, "removed": int(removed.split()[-1])}

    # ------------------------------------------------------------------
    # Personal favorites (any logged account; purely user-owned data)
    # ------------------------------------------------------------------
    @app.get("/api/favorites")
    async def favorites_list(user: dict = Depends(_require_account)):
        rows = await database.list_favorites(user['id'])
        return [_row_dict(r) for r in rows]

    @app.post("/api/favorites")
    async def favorites_add(body: FavoriteAddRequest, user: dict = Depends(_require_account)):
        favorite_id = await database.add_favorite(
            user['id'], body.title.strip(), body.webpage_url,
            (body.search_query or '').strip() or None
        )
        if favorite_id is None:
            raise HTTPException(status_code=409, detail="Already in favorites")
        return {"added": True, "favorite_id": favorite_id}

    @app.delete("/api/favorites/{favorite_id}")
    async def favorites_delete(favorite_id: int, user: dict = Depends(_require_account)):
        if not await database.delete_favorite(user['id'], favorite_id):
            raise HTTPException(status_code=404, detail="Unknown favorite")
        return {"deleted": True}

    @app.get("/api/now-playing")
    async def now_playing(user: dict = Depends(_require_permission('view'))):
        rows = await database.list_now_playing()
        return [_row_dict(r) for r in rows]

    # ------------------------------------------------------------------
    # User management (admin: grant webapp access after first Discord login)
    # ------------------------------------------------------------------
    @app.get("/api/users")
    async def users(admin: dict = Depends(_require_permission('manage_users'))):
        rows = await database.list_users()
        return [
            {
                **dict(r),
                "permissions": database.parse_permissions(r['permissions']),
                # json_agg arrives as a JSON string: parse once, then normalize
                # every override permission set into a real array.
                "guild_overrides": [
                    {**_row_dict(o), "permissions": database.parse_permissions(o['permissions'])}
                    for o in (json.loads(r['guild_overrides']) if r['guild_overrides'] else [])
                ],
                "avatar_url": avatar_url(r['discord_id'], r.get('avatar_hash')),
            }
            for r in rows
        ]

    @app.get("/api/users/{user_id}")
    async def user_detail(user_id: int, admin: dict = Depends(_require_permission('manage_users'))):
        target = await database.get_user_by_id(user_id)
        if target is None:
            raise HTTPException(status_code=404, detail="Unknown user")
        overrides = await database.list_user_guild_permissions(user_id)
        return {
            **dict(target),
            "permissions": database.parse_permissions(target['permissions']),
            "guild_overrides": [
                {**_row_dict(r), "permissions": database.parse_permissions(r['permissions'])}
                for r in overrides
            ],
        }

    @app.patch("/api/users/{user_id}")
    async def set_permissions(
        user_id: int, body: GlobalPermissionsRequest, admin: dict = Depends(_require_permission('manage_users'))
    ):
        target = await database.get_user_by_id(user_id)
        if target is None:
            raise HTTPException(status_code=404, detail="Unknown user")
        if target['seed']:
            # Seeded (env) admins are owned by DISCORD_ADMIN_IDS:
            # nobody, no exception, may change them through the API.
            raise HTTPException(
                status_code=403,
                detail="Seeded admin account managed by DISCORD_ADMIN_IDS only"
            )
        updated = await database.set_user_permissions(user_id, body.permissions)
        if updated is None:
            raise HTTPException(status_code=404, detail="Unknown user")
        _notify_user(user_id, "granted", "true")
        return dict(updated)

    @app.delete("/api/users/{user_id}")
    async def remove_user(user_id: int, admin: dict = Depends(_require_permission('manage_users'))):
        # Login accepts any Discord account, so junk accounts can appear;
        # admins need a removal tool. Seeded (env) admins are untouchable.
        target = await database.get_user_by_id(user_id)
        if target is None:
            raise HTTPException(status_code=404, detail="Unknown user")
        if target['seed']:
            raise HTTPException(
                status_code=403,
                detail="Seeded admin account managed by DISCORD_ADMIN_IDS only"
            )
        await database.delete_discord_user(user_id)
        return {"deleted": user_id}

    @app.put("/api/users/{user_id}/guilds/{guild_id}")
    async def set_guild_permissions(
        user_id: int, guild_id: int, body: GuildPermissionsRequest,
        admin: dict = Depends(_require_permission('manage_users'))
    ):
        target = await database.get_user_by_id(user_id)
        if target is None:
            raise HTTPException(status_code=404, detail="Unknown user")
        if target['seed']:
            raise HTTPException(
                status_code=403,
                detail="Seeded admin account managed by DISCORD_ADMIN_IDS only"
            )
        get_guild(guild_id)  # 404 unless the bot is in that server
        updated = await database.set_user_guild_permissions(user_id, guild_id, body.permissions)
        _notify_user(user_id, "granted", "true")
        return _row_dict(updated)

    @app.delete("/api/users/{user_id}/guilds/{guild_id}")
    async def clear_guild_permissions(
        user_id: int, guild_id: int, admin: dict = Depends(_require_permission('manage_users'))
    ):
        target = await database.get_user_by_id(user_id)
        if target is None:
            raise HTTPException(status_code=404, detail="Unknown user")
        await database.delete_user_guild_permissions(user_id, guild_id)
        return {"removed": True}

    # ------------------------------------------------------------------
    # Access requests (the webapp onboarding for 'nobody' accounts)
    # ------------------------------------------------------------------
    @app.get("/api/access/guilds")
    async def access_guilds(user: dict = Depends(_require_account)):
        """Servers where BOTH the user and the bot are. The snapshot is
        refreshed from Discord right here (stored OAuth pair), so joining or
        leaving a server shows up without any re-login; on a Discord failure
        the last stored snapshot is used, stale but real."""
        try:
            live = await refresh_user_guilds_snapshot(user, bot)
        except Exception:
            live = None
        try:
            snapshot = set(live) if live is not None else set(json.loads(user['guild_ids']))
        except (ValueError, TypeError):
            snapshot = set()
        return [
            {
                "id": str(g.id),
                "name": g.name,
                # Icon CDN URL for the Discord-style server picker; None falls
                # back to the initials placeholder in the panel.
                "icon": str(g.icon.url) if g.icon else None,
            }
            for g in bot.guilds
            if str(g.id) in snapshot
        ]

    @app.get("/api/access-requests")
    async def access_requests(admin: dict = Depends(_require_permission('manage_users'))):
        rows = await database.list_access_requests()
        return [_row_dict(r) for r in rows]

    @app.get("/api/access-requests/mine")
    async def my_pending_access_requests(user: dict = Depends(_require_account)):
        """Pending asks of the CURRENT account only: which guild ids and
        whether a generic request is open. The panel restores the requested
        badges on load so pending asks cannot be re-clicked. Generic and
        admin queues live on the other access-requests routes."""
        guild_ids = await database.list_pending_access_requests_by_user(user['id'])
        # Strings: Discord ids overflow the JS safe integer, and the panel
        # matches them against the picker ids (also strings).
        return {
            "guild_ids": [str(gid) for gid in guild_ids if gid is not None],
            "generic": None in guild_ids,
        }

    @app.post("/api/access-requests")
    async def request_access(body: AccessRequestCreate, user: dict = Depends(_require_account)):
        # Guild request: only a shared guild may be requested, one pending per
        # guild. Generic request (no guild_id): allowed even sharing no server
        # at all, one pending per user. Duplicates are rejected, not silently
        # swallowed, so the web panel can show the "already pending" hint.
        # Snapshot for the guild-request check: refreshed from Discord when
        # possible (join/leave accuracy), last stored one on any failure.
        # Generic requests skip the check entirely (one pending per user).
        if body.guild_id is not None:
            try:
                live = await refresh_user_guilds_snapshot(user, bot)
            except Exception:
                live = None
            try:
                snapshot = set(live) if live is not None else set(json.loads(user['guild_ids']))
            except (ValueError, TypeError):
                snapshot = set()
            if str(body.guild_id) not in snapshot:
                raise HTTPException(status_code=400, detail="Not a guild shared between you and the bot")
            guild_name = next((g.name for g in bot.guilds if g.id == body.guild_id), '')
            request_id = await database.create_access_request(
                user['id'], body.guild_id, guild_name, body.message.strip()
            )
            if request_id is None:
                raise HTTPException(status_code=409, detail="A pending request for this guild already exists")
        else:
            request_id = await database.create_access_request(
                user['id'], None, '', body.message.strip()
            )
            if request_id is None:
                raise HTTPException(status_code=409, detail="A generic request is already pending")

        # Discord DM to every env admin, best effort (closed DMs or unknown
        # ids must never fail the created request; the timeout bounds the
        # latency of a slow Discord API). Seeded admins are exactly the env
        # list, so DMing it reaches whoever owns the deployment.
        embed = discord.Embed(title="Access request", color=0x5865F2)
        embed.add_field(name="User", value=user['username'], inline=True)
        embed.add_field(
            name="Server",
            value=(guild_name if body.guild_id is not None else "Generic") or "Unknown server",
            inline=True,
        )
        if body.message.strip():
            embed.add_field(name="Note", value=body.message.strip()[:1000], inline=False)

        async def _dm_admin(admin_id: str):
            admin_user = bot.get_user(int(admin_id)) or await bot.fetch_user(int(admin_id))
            if admin_user is not None:
                await admin_user.send(embed=embed, view=AccessRequestDMView(request_id))

        try:
            await asyncio.wait_for(
                asyncio.gather(*(_dm_admin(a) for a in DISCORD_ADMIN_IDS), return_exceptions=True),
                timeout=10,
            )
        except asyncio.TimeoutError:
            pass  # notification only; the request row is already stored

        _notify_admins("requests", str(request_id))
        return {"created": True, "request_id": request_id}

    @app.patch("/api/access-requests/{request_id}")
    async def resolve_access_request(request_id: int, body: AccessRequestUpdate, admin: dict = Depends(_require_permission('manage_users'))):
        updated = await database.set_access_request_status(
            request_id, body.status, f"{admin['username']} (web panel)"
        )
        if updated is None:
            raise HTTPException(status_code=404, detail="Unknown or already resolved request")
        if body.status == 'granted':
            _notify_user(updated['user_id'], "granted", "true")
        return dict(updated)

    # ------------------------------------------------------------------
    # Playback controls (permission: playback; per-guild overrides apply)
    # ------------------------------------------------------------------
    @app.post("/api/guilds/{guild_id}/pause")
    async def pause(guild_id: int, admin: dict = Depends(_require_permission('playback'))):
        guild = get_guild(guild_id)
        vc = get_voice(guild)
        if not (really_in_voice(guild) and vc.is_playing()):
            raise HTTPException(status_code=409, detail="No track playing")
        vc.pause()
        # Freeze the progress marker at the pause instant (runtime only).
        get_server_info(guild_id)['paused_at'] = time.time()
        notify_playback(guild_id)
        return {"paused": True}

    @app.post("/api/guilds/{guild_id}/resume")
    async def resume(guild_id: int, admin: dict = Depends(_require_permission('playback'))):
        guild = get_guild(guild_id)
        vc = get_voice(guild)
        if not (really_in_voice(guild) and vc.is_paused()):
            raise HTTPException(status_code=409, detail="No paused track")
        vc.resume()
        get_server_info(guild_id).pop('paused_at', None)
        notify_playback(guild_id)
        return {"resumed": True}

    @app.post("/api/guilds/{guild_id}/queue/start")
    async def start_queue(guild_id: int, admin: dict = Depends(_require_permission('playback'))):
        """Restart the persisted queue from the panel when nothing is playing
        (e.g. after a bot restart): auto-joins the requester's voice channel,
        never a guessed one. The channel check happens BEFORE the claim so a
        409 never swallows a queued row."""
        guild = get_guild(guild_id)
        vc = get_voice(guild)
        if really_in_voice(guild) and (vc.is_playing() or vc.is_paused()):
            raise HTTPException(status_code=409, detail="Already playing")
        voice_channel = None
        if not really_in_voice(guild):
            await drop_stale_voice(guild)  # stale handshake cleanup
            voice_channel = pick_voice_channel(guild, admin)
            if voice_channel is None:
                raise HTTPException(
                    status_code=409,
                    detail="Join a voice channel first: the bot only follows your account, it never picks a channel by guess"
                )
        row = await database.claim_next_track(guild_id)
        if row is None:
            raise HTTPException(status_code=409, detail="Queue is empty")
        played = await play_queued_row(guild_id, row, autopilot=False, voice_channel=voice_channel)
        if not played:
            raise HTTPException(status_code=500, detail="Playback failed")
        return {"started": True, "title": row['title']}

    @app.post("/api/guilds/{guild_id}/skip")
    async def skip(guild_id: int, body: SkipRequest, admin: dict = Depends(_require_permission('playback'))):
        guild = get_guild(guild_id)
        vc = get_voice(guild)
        if not (really_in_voice(guild) and vc.is_playing()):
            raise HTTPException(status_code=409, detail="No track playing")

        server_info = get_server_info(guild_id)
        removed = 0
        if body.all:
            current = server_info.get('current_track') or {}
            collection_id = current.get('collection_id')
            task = server_info.get('loading_task')
            if task and not task.done():
                task.cancel()
            server_info['loading_task'] = None
            if collection_id:
                removed = await database.clear_collection(guild_id, collection_id)
        vc.stop()
        notify_playback(guild_id)
        return {"skipped": True, "extra_tracks_removed": removed}

    @app.post("/api/guilds/{guild_id}/stop")
    async def stop(guild_id: int, admin: dict = Depends(_require_permission('playback'))):
        guild = get_guild(guild_id)
        if not get_voice(guild):
            raise HTTPException(status_code=409, detail="Bot is not in a voice channel")
        server_info = get_server_info(guild_id)
        task = server_info.get('loading_task')
        if task and not task.done():
            task.cancel()
        server_info['loading_task'] = None
        server_info['current_track'] = None
        await database.clear_queue(guild_id)
        await database.clear_now_playing(guild_id)
        get_server_info(guild_id).pop('paused_at', None)
        get_server_info(guild_id)['intentional_leave'] = True  # stay out: Stop means it
        await guild.voice_client.disconnect()
        notify_playback(guild_id)
        return {"stopped": True}

    @app.post("/api/guilds/{guild_id}/play")
    async def play(guild_id: int, body: PlayRequest, admin: dict = Depends(_require_permission('queue'))):
        guild = get_guild(guild_id)
        vc = get_voice(guild)
        stream_url, title, video_url, headers = await resolve_audio_info_async(body.query)
        if stream_url is None:
            raise HTTPException(status_code=400, detail="Unable to retrieve audio stream")

        in_voice = really_in_voice(guild)
        occupied = in_voice and (vc.is_playing() or vc.is_paused())
        if occupied:
            row_id = await database.enqueue_track(
                guild_id=guild_id, title=title, webpage_url=video_url,
                search_query=body.query
            )
            return {"queued": True, "row_id": row_id, "title": title}
        # Not in voice (or a stale/lost handshake): the panel makes the bot
        # join by itself — the requester's voice channel if visible, else it
        # refuses. No more "use !play in Discord first".
        if not in_voice:
            await drop_stale_voice(guild)
            voice_channel = pick_voice_channel(guild, admin)
            if voice_channel is None:
                raise HTTPException(
                    status_code=409,
                    detail="Join a voice channel first: the bot only follows your account, it never picks a channel by guess"
                )
        else:
            voice_channel = None
        played = await play_audio(guild, None, stream_url, title, video_url, headers=headers, voice_channel=voice_channel)
        if not played:
            raise HTTPException(status_code=500, detail="Playback failed")
        return {"playing": True, "title": title}

    @app.post("/api/guilds/{guild_id}/queue")
    async def enqueue_at(guild_id: int, body: QueueEnqueueRequest, admin: dict = Depends(_require_permission('queue'))):
        guild = get_guild(guild_id)
        get_voice(guild)  # 409 if the bot is not connected
        stream_url, title, video_url, headers = await resolve_audio_info_async(body.query)
        if stream_url is None:
            raise HTTPException(status_code=400, detail="Unable to retrieve audio info")
        if body.position is None:
            row_id = await database.enqueue_track(
                guild_id=guild_id, title=title, webpage_url=video_url,
                search_query=body.query
            )
        else:
            queued = await database.count_queue(guild_id)
            if body.position > queued:
                raise HTTPException(status_code=400, detail="Position beyond the queue length")
            row_id = await database.insert_track_at_position(
                guild_id, body.position, title=title, webpage_url=video_url,
                search_query=body.query
            )
        return {"queued": True, "row_id": row_id, "title": title}

    @app.patch("/api/guilds/{guild_id}/queue/{item_id}")
    async def move_queued(guild_id: int, item_id: int, body: QueueMoveRequest, admin: dict = Depends(_require_permission('queue_manage'))):
        get_guild(guild_id)
        moved = await database.move_queue_item(guild_id, item_id, body.position)
        if moved is None:
            raise HTTPException(status_code=404, detail="Queued item not found")
        return dict(moved)

    @app.post("/api/guilds/{guild_id}/queue/reorder")
    async def reorder_queue(
        guild_id: int, body: QueueReorderRequest, admin: dict = Depends(_require_permission('queue_manage'))
    ):
        get_guild(guild_id)
        count = await database.set_queue_order(guild_id, body.order)
        if count == 0:
            raise HTTPException(status_code=404, detail="No queued items matched")
        return {"reordered": count}

    @app.delete("/api/guilds/{guild_id}/queue/{item_id}")
    async def remove_queued(guild_id: int, item_id: int, admin: dict = Depends(_require_permission('queue_manage'))):
        get_guild(guild_id)
        removed = await database.delete_queue_item(guild_id, item_id)
        if removed is None:
            raise HTTPException(status_code=404, detail="Queued item not found")
        return {"removed": True, "title": removed['title']}

    @app.delete("/api/guilds/{guild_id}/queue")
    async def clear_pendings(guild_id: int, admin: dict = Depends(_require_permission('queue_manage'))):
        get_guild(guild_id)
        await database.clear_queue(guild_id)
        return {"cleared": True}

    _search_session_holder: dict = {"session": None}

    def _search_session():
        """Lazily created keep-alive session for the Deezer proxy; lives for
        the whole process (single event loop, single use)."""
        import aiohttp
        session = _search_session_holder["session"]
        if session is None or session.closed:
            session = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=6))
            _search_session_holder["session"] = session
        return session

    @app.get("/api/search")
    async def search(q: str, user: dict = Depends(_require_permission('queue'))):
        """Thin proxy for the free Deezer public metadata API (docs/meta layer:
        no playback permission): title/artist/cover/preview to power the webapp list;
        picking a result just becomes a normal play/queue request."""
        q = q.strip()[:200]
        if not q:
            raise HTTPException(status_code=400, detail="Empty query")
        cached = _search_cache.get(q)
        now_ts = time.time()
        for key, (exp, _) in list(_search_cache.items()):
            if exp < now_ts:
                _search_cache.pop(key, None)
        if cached and cached[0] > now_ts:
            return cached[1]

        # Shared keep-alive session: a per-request session paid a full TCP+TLS
        # handshake to Deezer on every search. Bounded by a timeout so a slow
        # provider answers 502 instead of hanging the panel.
        import aiohttp  # provided by discord.py
        session = _search_session()
        async with session.get('https://api.deezer.com/search/track', params={'q': q, 'limit': 12}) as resp:
            if resp.status != 200:
                raise HTTPException(status_code=502, detail="Metadata provider unavailable")
            raw = await resp.json()
        results = [
            {
                "id": item.get('id'),
                "title": item.get('title'),
                "artist": (item.get('artist') or {}).get('name'),
                "album": (item.get('album') or {}).get('title'),
                "cover": ((item.get('album') or {}).get('cover_medium')),
                "preview": item.get('preview'),
                "duration": item.get('duration'),
                "link": item.get('link'),
            }
            for item in (raw.get('data') or []) if item.get('id') is not None
        ]
        _search_cache[q] = (now_ts + 300, results)
        return results

    return app