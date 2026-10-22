"""HTTP API to control PakoDJ from any external client (language-agnostic).

Runs inside the bot process (same asyncio event loop as discord.py), so it
can read live voice state and invoke playback directly.

SECURITY:
- Login is Discord OAuth only: GET /api/auth/discord/start, complete the
  flow, then use the returned session_token as `Authorization: Bearer <token>`.
- Accounts get a role: nobody (login only, no API surface), viewer
  (read-only) or admin (playback control + user management). Admins come
  from DISCORD_ADMIN_IDS at login; admins grant other roles via the
  /api/users endpoints.
- Sessions are server side (api_sessions table), only their SHA-256 is
  stored, they expire (API_SESSION_TTL_DAYS, default 7) and can be revoked
  per user by deleting rows — no shared static secret anywhere.
- Future roles/permissions plug into the same dependencies without touching
  the endpoint surface.

No port is published by default: reachability is decided by the compose
file, nothing is exposed to the internet.
"""
import os
import time
import json
import secrets
import hashlib
from datetime import datetime, timezone, timedelta
from typing import Literal, Optional
from urllib.parse import urlencode

from fastapi import Depends, FastAPI, HTTPException, Header, Request
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

import db as database


class PlayRequest(BaseModel):
    query: str = Field(min_length=1, max_length=500)


class QueueEnqueueRequest(BaseModel):
    query: str = Field(min_length=1, max_length=500)
    position: Optional[int] = Field(None, ge=0)


class QueueMoveRequest(BaseModel):
    position: int = Field(ge=0)


class SkipRequest(BaseModel):
    all: bool = False


class UserRoleRequest(BaseModel):
    role: Literal['nobody', 'viewer', 'admin']


class AccessRequestCreate(BaseModel):
    guild_id: int
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


# ---------------------------------------------------------------------------
# Session primitives
# ---------------------------------------------------------------------------

def token_digest(token: str) -> str:
    """Plain session tokens are never stored; only their SHA-256 is."""
    return hashlib.sha256(token.encode()).hexdigest()


# ---------------------------------------------------------------------------
# Application factory (no circular imports: main.py injects its live pieces)
# ---------------------------------------------------------------------------

def create_api_app(bot, resolve_audio_info_async, play_audio, get_server_info, get_server_lock):
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

    # Read endpoints require at least the viewer role ('nobody' accounts exist
    # because login auto-creates the account; an admin must grant access).
    def _require_viewer(user: dict = Depends(_require_account)) -> dict:
        if user['role'] == 'nobody':
            raise HTTPException(status_code=403, detail="Account without access yet (ask an admin)")
        return user

    # Playback control and user management additionally require admin.
    def _require_admin(user: dict = Depends(_require_viewer)) -> dict:
        if user['role'] != 'admin':
            raise HTTPException(status_code=403, detail="Admin account required")
        return user

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
            "role": user['role'],
        }

    # ------------------------------------------------------------------
    # Auth
    # ------------------------------------------------------------------
    @app.get("/api/auth/me")
    async def me(user: dict = Depends(_require_account)):
        return {"id": user['id'], "username": user['username'], "role": user['role']}

    @app.post("/api/auth/logout")
    async def logout(user: dict = Depends(_require_account), authorization: Optional[str] = Header(None)):
        if authorization and authorization.startswith('Bearer '):
            await database.delete_session(token_digest(authorization[7:].strip()))
        return {"logged_out": True}

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
        if not discord_login_configured():
            raise HTTPException(status_code=503, detail="Discord login not configured")
        expiry = _oauth_states.pop(state, None) if state else None
        if expiry is None or expiry < time.time():
            raise HTTPException(status_code=400, detail="Invalid or expired OAuth state")
        if not code:
            raise HTTPException(status_code=400, detail="Missing Discord authorization code")

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
                raise HTTPException(status_code=400, detail="Discord token exchange failed")
            tokens = await resp.json()
            auth_headers = {
                'Authorization': f"Bearer {tokens['access_token']}",
                'User-Agent': 'PakoDJ Bot (https://github.com/Pako3549/PakoDJ)',
            }
            resp = await session.get('https://discord.com/api/users/@me', headers=auth_headers)
            if resp.status != 200:
                raise HTTPException(status_code=400, detail="Discord identify failed")
            me = await resp.json()
            resp = await session.get('https://discord.com/api/users/@me/guilds', headers=auth_headers)
            user_guilds = (await resp.json()) if resp.status == 200 else []

        # Only members of a server the bot is in may log in
        bot_guild_ids = {str(g.id) for g in bot.guilds}
        if not any(str(g.get('id')) in bot_guild_ids for g in user_guilds):
            raise HTTPException(status_code=403, detail="You must share a server with the bot")

        discord_id = str(me['id'])
        env_role = 'admin' if discord_id in DISCORD_ADMIN_IDS else 'nobody'
        user = await database.upsert_discord_user(
            discord_id, me.get('global_name') or me['username'], env_role
        )
        # Snapshot the guilds shared between this user and the bot; the access
        # request flow (GET /api/access/guilds, POST /api/access-requests) reads it.
        user_guild_ids = {str(g.get('id')) for g in user_guilds}
        shared = [str(g.id) for g in bot.guilds if str(g.id) in user_guild_ids]
        await database.set_user_guilds_snapshot(user['id'], json.dumps(shared))
        payload = await _new_session_payload(user)
        # Browsers get redirected to the webapp with the token in the URL
        # fragment (client-side only, never hits server logs); API clients
        # that explicitly ask for JSON still get the raw payload.
        if request.query_params.get('json') != '1':
            from fastapi.responses import RedirectResponse
            fragment = urlencode({
                'session_token': payload['session_token'],
                'username': payload['username'],
                'role': payload['role'],
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

    # ------------------------------------------------------------------
    # Basic info
    # ------------------------------------------------------------------
    @app.get("/api/health")
    async def health():
        return {"status": "ok", "guilds": len(bot.guilds)}

    @app.get("/api/guilds")
    async def guilds(user: dict = Depends(_require_viewer)):
        # Discord snowflakes exceed JS number precision: always serialize as strings
        return [{"id": str(g.id), "name": g.name} for g in bot.guilds]

    @app.get("/api/guilds/{guild_id}/status")
    async def status(guild_id: int, user: dict = Depends(_require_viewer)):
        guild = get_guild(guild_id)
        vc = get_voice(guild)
        now = await database.list_now_playing()
        row = next((r for r in now if r['guild_id'] == guild_id), None)
        return {
            "guild_id": str(guild_id),
            "voice_connected": bool(vc and vc.is_connected()),
            "playing": bool(vc and vc.is_playing()),
            "paused": bool(vc and vc.is_paused()),
            "now_playing": _row_dict(row) if row else None,
            "queue_count": await database.count_queue(guild_id),
            "loading_in_background": bool(get_server_info(guild_id).get('loading_task')),
        }

    # ------------------------------------------------------------------
    # Data
    # ------------------------------------------------------------------
    @app.get("/api/guilds/{guild_id}/queue")
    async def queue(guild_id: int, user: dict = Depends(_require_viewer)):
        get_guild(guild_id)
        rows = await database.list_queue(guild_id, limit=200)
        return [_row_dict(r) for r in rows]

    @app.get("/api/guilds/{guild_id}/history")
    async def history(guild_id: int, user: dict = Depends(_require_viewer)):
        get_guild(guild_id)
        rows = await database.list_history(guild_id, limit=100)
        return [_row_dict(r) for r in rows]

    @app.get("/api/now-playing")
    async def now_playing(user: dict = Depends(_require_viewer)):
        rows = await database.list_now_playing()
        return [_row_dict(r) for r in rows]

    # ------------------------------------------------------------------
    # User management (admin: grant webapp access after first Discord login)
    # ------------------------------------------------------------------
    @app.get("/api/users")
    async def users(admin: dict = Depends(_require_admin)):
        rows = await database.list_users()
        return [dict(r) for r in rows]

    @app.patch("/api/users/{user_id}")
    async def set_role(user_id: int, body: UserRoleRequest, admin: dict = Depends(_require_admin)):
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
        updated = await database.set_user_role(user_id, body.role)
        if updated is None:
            raise HTTPException(status_code=404, detail="Unknown user")
        return dict(updated)

    # ------------------------------------------------------------------
    # Access requests (the webapp onboarding for 'nobody' accounts)
    # ------------------------------------------------------------------
    @app.get("/api/access/guilds")
    async def access_guilds(user: dict = Depends(_require_account)):
        """Servers where BOTH the user and the bot are (snapshot from the last
        login). A 'nobody' account uses this list to build its request."""
        try:
            snapshot = set(json.loads(user['guild_ids']))
        except (ValueError, TypeError):
            snapshot = set()
        return [
            {"id": str(g.id), "name": g.name} for g in bot.guilds if str(g.id) in snapshot
        ]

    @app.get("/api/access-requests")
    async def access_requests(admin: dict = Depends(_require_admin)):
        rows = await database.list_access_requests()
        return [_row_dict(r) for r in rows]

    @app.post("/api/access-requests")
    async def request_access(body: AccessRequestCreate, user: dict = Depends(_require_account)):
        # Only a shared guild may be requested, and only while the request
        # is unanswered; duplicates are silently rejected.
        try:
            snapshot = set(json.loads(user['guild_ids']))
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
        return {"created": True, "request_id": request_id}

    @app.patch("/api/access-requests/{request_id}")
    async def resolve_access_request(request_id: int, body: AccessRequestUpdate, admin: dict = Depends(_require_admin)):
        updated = await database.set_access_request_status(request_id, body.status)
        if updated is None:
            raise HTTPException(status_code=404, detail="Unknown request or invalid status")
        return dict(updated)

    # ------------------------------------------------------------------
    # Playback controls (admin only: DISCORD_ADMIN_IDS owners)
    # ------------------------------------------------------------------
    @app.post("/api/guilds/{guild_id}/pause")
    async def pause(guild_id: int, admin: dict = Depends(_require_admin)):
        vc = get_voice(get_guild(guild_id))
        if not (vc and vc.is_playing()):
            raise HTTPException(status_code=409, detail="No track playing")
        vc.pause()
        return {"paused": True}

    @app.post("/api/guilds/{guild_id}/resume")
    async def resume(guild_id: int, admin: dict = Depends(_require_admin)):
        vc = get_voice(get_guild(guild_id))
        if not (vc and vc.is_paused()):
            raise HTTPException(status_code=409, detail="No paused track")
        vc.resume()
        return {"resumed": True}

    @app.post("/api/guilds/{guild_id}/skip")
    async def skip(guild_id: int, body: SkipRequest, admin: dict = Depends(_require_admin)):
        guild = get_guild(guild_id)
        vc = get_voice(guild)
        if not (vc and vc.is_playing()):
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
        return {"skipped": True, "extra_tracks_removed": removed}

    @app.post("/api/guilds/{guild_id}/stop")
    async def stop(guild_id: int, admin: dict = Depends(_require_admin)):
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
        await guild.voice_client.disconnect()
        return {"stopped": True}

    @app.post("/api/guilds/{guild_id}/play")
    async def play(guild_id: int, body: PlayRequest, admin: dict = Depends(_require_admin)):
        guild = get_guild(guild_id)
        vc = get_voice(guild)
        stream_url, title, video_url, headers = await resolve_audio_info_async(body.query)
        if stream_url is None:
            raise HTTPException(status_code=400, detail="Unable to retrieve audio stream")

        occupied = vc and (vc.is_playing() or vc.is_paused())
        if occupied:
            row_id = await database.enqueue_track(
                guild_id=guild_id, title=title, webpage_url=video_url,
                search_query=body.query
            )
            return {"queued": True, "row_id": row_id, "title": title}
        if vc is None:
            raise HTTPException(
                status_code=409,
                detail="Bot is not in a voice channel in this server; use !play in Discord first"
            )
        played = await play_audio(guild, None, stream_url, title, video_url, headers=headers)
        if not played:
            raise HTTPException(status_code=500, detail="Playback failed")
        return {"playing": True, "title": title}

    @app.post("/api/guilds/{guild_id}/queue")
    async def enqueue_at(guild_id: int, body: QueueEnqueueRequest, admin: dict = Depends(_require_admin)):
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
    async def move_queued(guild_id: int, item_id: int, body: QueueMoveRequest, admin: dict = Depends(_require_admin)):
        get_guild(guild_id)
        moved = await database.move_queue_item(guild_id, item_id, body.position)
        if moved is None:
            raise HTTPException(status_code=404, detail="Queued item not found")
        return dict(moved)

    @app.delete("/api/guilds/{guild_id}/queue/{item_id}")
    async def remove_queued(guild_id: int, item_id: int, admin: dict = Depends(_require_admin)):
        get_guild(guild_id)
        removed = await database.delete_queue_item(guild_id, item_id)
        if removed is None:
            raise HTTPException(status_code=404, detail="Queued item not found")
        return {"removed": True, "title": removed['title']}

    @app.delete("/api/guilds/{guild_id}/queue")
    async def clear_pendings(guild_id: int, admin: dict = Depends(_require_admin)):
        get_guild(guild_id)
        await database.clear_queue(guild_id)
        return {"cleared": True}

    @app.get("/api/search")
    async def search(q: str, user: dict = Depends(_require_account)):
        """Thin proxy for the free Deezer public metadata API (docs/meta layer:
        no playback role): title/artist/cover/preview to power the webapp list;
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

        import aiohttp  # provided by discord.py
        async with aiohttp.ClientSession() as session:
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
            }
            for item in (raw.get('data') or []) if item.get('id') is not None
        ]
        _search_cache[q] = (now_ts + 300, results)
        return results

    return app