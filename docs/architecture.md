# PakoDJ — architecture (as built)

PakoDJ is a Discord music bot for a Raspberry Pi 5 (8 GB) that also exposes a
control HTTP API intended to back a future web interface. One Python process
runs both the bot and the API server.

```
                        ┌──────────────────────────────────────────────┐
Discord (gateway/voice) │  bot/main.py (discord.py)                    │
  ▼ commands (!play...) │  - commands, playback pipeline               │
  ─────────────────────►│  - resolver (yt-dlp in executor) + caches    │
                        │  - per-guild runtime state only              │
                        │  - is_safe_http_url() SSRF guard             │
                        │  ├───────────────────────────────────────────┤
                        │  │  bot/api.py (FastAPI, in-process          │
                        │  │  uvicorn on :8000, unpublished)           │
                        │  │  - Discord OAuth login, sessions          │
                        │  │  - roles, users, access requests          │
                        │  └───────────────────────────────────────────┤
                        └───────────────┬──────────────────────────────┘
                                        │ asyncpg pool
                                ┌───────▼───────┐
                                │ PostgreSQL 17 │  (docker db service,
                                │   alpine      │   volume pakodj-pgdata)
                                └───────────────┘
```

## Runtime model

- **Single process, single loop.** `uvicorn.Server.serve()` is started on the
  bot's asyncio loop (`main.py` runs `install_signal_handlers` = pass so the
  bot owns signals). The API factory `create_api_app(bot, ...)` receives live
  bot pieces as arguments to avoid circular imports.
- **Blocking work never blocks the world.** yt-dlp extraction, Spotify
  tracklist calls and CD audio resolution run in the executor
  (`resolve_audio_info_async`); Discord events stay responsive while other
  servers queue tracks.
- **Caches.** `search_cache` (TTL 6 h) for search results, `stream_cache`
  (TTL 10 min) for resolved stream URLs, both bounded (1024 entries). Stream
  URLs expire by nature: DB stores canonical data, playback re-resolves.
- **Per-server fairness.** Each guild queue plays independently
  (per-guild lock/state machine); long background playlist loading is a
  cancelable `loading_task` per guild.

## Data model (bot/db.py)

| Table | Purpose | Notes |
| --- | --- | --- |
| `collections` | Playlist / album / loop groups being loaded in background | lets `!skip all` delete a whole group with one SQL statement |
| `queue_items` | Play queue rows per guild | canonical metadata only (title, webpage_url, search_query, collection_id, position) |
| `history` | Playback history per guild | capped by query, replaces unbounded in-memory lists |
| `now_playing` | DB mirror of what every guild is playing | written by `play_audio`, cleaned by after-playing/stop/guild-remove/voice-leave; powers `GET /api/now-playing` |
| `users` | Discord-linked API accounts | `role` = nobody/viewer/admin, `seed` = env-owned flag, `guild_ids` = JSON snapshot of shared guilds (refreshed at login), `password_hash` kept empty for future needs |
| `api_sessions` | Server-side sessions | only the SHA-256 of the token is stored; TTL default 7 days (`API_SESSION_TTL_DAYS`) |
| `access_requests` | Webapp onboarding: 'nobody' users ask for access | partial unique index ⇒ one pending request per (user, guild); guild_name snapshotted |

Schema policy: `CREATE TABLE IF NOT EXISTS` + `ALTER TABLE ... ADD COLUMN IF NOT EXISTS`
so existing volumes self-migrate at startup. Postgres-only features (partial
indexes) are fine — the DB is Postgres 17-alpine, never SQLite, because the
platform must serve a future web app with concurrent readers.

## Auth and roles

- **Login:** Discord OAuth2 only. Scope `identify guilds`; the callback requires
  sharing at least one guild with the bot, then creates/updates the account and
  returns a session token (`Authorization: Bearer ...` thereafter).
  Redirect URI is always `${API_PUBLIC_ORIGIN}/api/auth/discord/callback`
  (currently `https://dj.pako.uk`) — the domain lives in env, the path in code.
- **Roles:** `nobody` (login only, no API surface), `viewer` (read endpoints),
  `admin` (playback control + user management). Dependencies chain:
  `_require_account` → `_require_viewer` → `_require_admin`.
- **Seeded admins (`DISCORD_ADMIN_IDS`, comma separated):** env is authoritative.
  At login the role is forced to `admin` and `seed=true`. No API call can change
  a seeded account (route returns `403`, plus SQL guard). Removing an ID from
  the env demotes that account to `nobody` at its next login. Granted roles on
  normal accounts survive logins.
- **Access requests:** a `nobody` user calls `GET /api/access/guilds`
  (intersection of the user's snapshot with live bot guilds) and posts
  `POST /api/access-requests` per guild; admins resolve with
  `PATCH /api/access-requests/{id}` and grant roles via `PATCH /api/users/{id}`.

## API surface

Full table lives in README (single source of truth for users); the shape:
health, guilds list, per-guild status/queue/history, global now-playing,
playback control (play/skip/pause/resume/stop, admin-only), auth
(discord/start → discord/callback), auth/me, users list/role patch,
access-guilds + access-requests CRUD-lite. Input validation is pydantic
(max lengths on free text, `Literal` for enums). There is deliberately **no
in-app rate limiting** — a public deployment sits behind Cloudflare rules.

## Security model (what guards what)

| Layer | Owned by |
| --- | --- |
| Ports/TLS/DDoS/rate limits | Cloudflare Tunnel + rules (dormant until used) |
| Session validity, role gates | app-level `_require_account` chain |
| Outbound fetch safety (`file://`, private IPs, Docker names, DNS rebinding) | `is_safe_http_url()` — fail-closed, resolves every FQDN and requires `is_global` on ALL addresses |
| Seeded admin immutability | env + `seed` column (double guarded) |
| Secret material | GitHub secrets → `bot/.env` on deploy; never committed; no static API token exists |

## Deployment

- CI builds the image (`ubuntu-latest`, amd64+arm64) to `ghcr.io/pako3549/pakodj:latest`.
- On push to `main`, a **self-hosted runner on the Pi** checks out the repo,
  rewrites `bot/.env` from GitHub secrets (plus project `.env` for
  `TUNNEL_TOKEN`), then `docker compose -f deploy/compose.yml pull pakodj`
  and `up -d` — the bot restarts but queues/now-playing survive in Postgres.
- Dev convenience: root `compose.yml` (bot built locally), the dormant `tunnel`
  profile lives only in the two deploy compose files — the dev compose has no
  cloudflared service at all.

## Webapp architecture (Next.js + TypeScript + React + nginx)

```
 browser
    │  http://localhost:8080  (dev; prod: yourhostname via Cloudflare Tunnel)
    ▼
  nginx ── one origin for everything:
    ├── /                     → web (Next.js dev / prod server)
    ├── /_next/*              → web
    └── /api/*                → pakodj(-dev):8000  (bot FastAPI, unpublished)
```

- **Framework:** Next.js App Router (TypeScript, `strict`), React 19.
- **nginx is the single entry point**, in DEV and in production. Same-origin
  `/api` calls mean the backend needs **no CORS at all** for the webapp;
  `API_CORS_ORIGINS` stays free for third-party tools.
- **DEV (`compose.yml`):** `web` runs `npm run dev` with the source mounted
  (hot reload) and a dedicated `web-node-modules` volume; nginx publishes
  `8080:80` — the only published port in the whole platform, dev only.
- **Production (planned wiring):** CI builds a `web` image (`next build`
  standalone output; `web/Dockerfile` already set for it), the deploy compose
  runs `web` + `nginx` (no ports) and Cloudflare Tunnel maps the public
  hostname (planned: `https://dj.pako.uk`) to `nginx:80`. The FastAPI port
  stays unpublished in production, exactly as now.
- **Auth flow stays the one defined in `permissions.md`/architecture:** the
  frontend opens `/api/auth/discord/start` (through nginx, same origin),
  stores the session token, and `/api/auth/me` gates the UI (role → buttons).

## Webapp direction (what the API already anticipates)

- Visual and interaction style for the web frontend is defined in
  `docs/style.md` (Tailwind tokens, layout grid, theme system).
- Browser clients: set `API_CORS_ORIGINS` to the webapp origin; server-side
  clients need nothing.
- `/api/auth/discord/start` returns the authorize URL the frontend opens;
  the callback returns the session token as JSON (the frontend decides
  redirects later).
- `/api/auth/me` returns `id/username/role` so the UI can gate buttons.
- `/api/now-playing` exists for a global "what is playing everywhere" view.

## Search and queue management (target design)

Two families of features are planned for the webapp; the permission gates for
both live in `docs/permissions.md`.

### Track info: one free metadata API + direct links

Playback is already provider-agnostic: any entry (pasted URL or free query)
is normalized by yt-dlp resolution into canonical `{title, webpage_url,
search_query}` and played just-in-time. What the webapp adds is only a
*display/suggestion layer* in front of that.

- **Metadata (the one API):** Deezer Public API — free, **no API key, no
  signup**: `https://api.deezer.com/search/track?q=...` returns title,
  artist, album, cover art, preview URL, length. Used by the webapp to show
  track info and offer a "this one" button; picking a result produces a
  normal play request (`{artist} - {title}` query or the track URL), which
  flows through the same resolution as Discord `!play`.
- **Direct links just work:** YouTube, SoundCloud, Bandcamp and every other
  yt-dlp-compatible URL pasted into the UI goes straight to the play/enqueue
  endpoint — no metadata lookup involved.
- Fallback if Deezer ever breaks: MusicBrainz (open, no key) supplies
  metadata while playback keeps resolving through yt-dlp; nothing else in
  the platform depends on a music API.

All URLs (pasted or derived) keep passing `is_safe_http_url()`.

### Queue endpoints [planned]

Existing: `POST /api/guilds/{id}/play` (play-now or enqueue). Planned on top,

| Method + path | Permission |
| --- | --- |
| `GET /api/search?q=` | session (thin proxy for the Deezer metadata lookup: avoids browser CORS quirks and caches popular lookups) |
| `POST /api/guilds/{id}/queue` | `queue.enqueue` (add a specific track/URL at a position) |
| `PATCH /api/guilds/{id}/queue/{item_id}` | `queue.manage` (move to position) |
| `DELETE /api/guilds/{id}/queue/{item_id}` | `queue.manage` (remove one item) |
| `DELETE /api/guilds/{id}/queue` | `queue.manage` (clear all) |

Queue mutations go straight through `bot/db.py` (positions are data, not
memory) and the Discord side keeps working unchanged: the playing pipeline
reads the next row at skip time.
