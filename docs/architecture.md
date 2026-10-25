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
| `favorites` | Personal starred tracks per web account (starred from the queue or the history) | unique (user_id, webpage_url); canonical data only (title, webpage_url, search_query); cascades from the account |
| `now_playing` | DB mirror of what every guild is playing | written by `play_audio`, cleaned by after-playing/stop/guild-remove/voice-leave AND wiped once at bot boot (a run that died mid-playback must not leave a dangling "playing" row); powers `GET /api/now-playing` |
| `users` | Discord-linked API accounts | `permissions` = JSON set (view/queue/queue_manage/playback/manage_users), `seed` = env-owned flag, `guild_ids` = JSON snapshot of shared guilds (refreshed at login), `avatar_hash`, `password_hash` kept empty for future needs |
| `api_sessions` | Server-side sessions | only the SHA-256 of the token is stored; TTL default 7 days (`API_SESSION_TTL_DAYS`) |
| `access_requests` | Webapp onboarding: accounts without `view` ask for access | partial unique index ⇒ one pending request per (user, guild); guild_name snapshotted |
| `user_guild_permissions` | Per-guild permission overrides | replaces the user's global set inside that guild only |

Schema policy: `CREATE TABLE IF NOT EXISTS` + `ALTER TABLE ... ADD COLUMN IF NOT EXISTS`
so existing volumes self-migrate at startup. Postgres-only features (partial
indexes) are fine — the DB is Postgres 17-alpine, never SQLite, because the
platform must serve a future web app with concurrent readers.

## Auth and permissions

- **Login:** Discord OAuth2 only. Scope `identify guilds`; the callback requires
  sharing at least one guild with the bot, then creates/updates the account and
  returns a session token (`Authorization: Bearer ...` thereafter).
  Redirect URI is always `${API_PUBLIC_ORIGIN}/api/auth/discord/callback`
  (currently `https://dj.pako.uk`) — the domain lives in env, the path in code.
- **Granular permissions:** accounts carry a JSON permission set — `view`
  (read endpoints), `queue` (find tracks, play/enqueue, search endpoint),
  `queue_manage` (remove/reorder/clear/positioned-inserts/swap), `playback` (pause/resume/skip/stop),
  `manage_users` (accounts + access requests). Endpoints gate on exactly one
  key: `_require_account` → `_require_permission(key)`. The functional keys
  (queue, queue_manage, playback) implicitly require `view`: without it the
  account has no access surface at all. `view` is also enforced at write time
  (shared pydantic validator on both permission requests): a non-empty set
  without `view` is rejected with 422, so an invalid set can never exist. Legacy coarse roles
  (`nobody`/`viewer`/`admin`) are migrated once at load into equivalent sets,
  and the retired `search` key is merged into `queue` by the same load-time
  migration (both account sets and per-guild overrides).
- **Per-guild overrides:** `user_guild_permissions` rows replace the global
  set inside that specific guild only (insert = override, delete = back to
  global). They never carry `manage_users`: account administration is a
  global-only capability, so the override endpoint uses a narrower
  `Literal` model and rejects the key with `422`. Endpoints whose path
  carries `guild_id` resolve the override automatically. `GET /api/guilds`
  encodes the same rule for the dashboard: global `view` lists every bot
  guild; an account without it sees only the guilds whose override carries
  `view` (this is how single-server access requests are honoured with a
  scoped grant instead of a global one).
- **Seeded admins (`DISCORD_ADMIN_IDS`, comma separated):** env is
  authoritative. At login the full permission set is forced and `seed=true`.
  No API call can change a seeded account (route returns `403`, plus SQL
  guard). Removing an ID from the env wipes its permissions at the next
  login. Granted permissions on normal accounts survive logins.
- **Access requests:** an account with no `view` permission calls
  `GET /api/access/guilds` (intersection of the user's snapshot with live bot
  guilds) and posts `POST /api/access-requests` per guild; users with
  `manage_users` resolve with `PATCH /api/access-requests/{id}` and grant
  `["view"]` via `PATCH /api/users/{id}`. A request without `guild_id` is
  **generic**: it is the onboarding path for accounts sharing no server with
  the bot at all, stored with `guild_id IS NULL` (unique pending per user).
  The shared-guilds snapshot is no longer login-only: the OAuth pair from
  the last login is stored on the account and `GET /api/access/guilds`
  (plus the guild-request check) refreshes it from Discord on every read,
  so membership changes reach the panel without a re-login; failures fall
  back to the stored snapshot. Both the admin user detail response and the
  permission endpoints never return these token columns.
- **Live grant push (SSE):** the request-access panel holds ONE
  `GET /api/stream/updates` connection (Bearer-authenticated fetch reader,
  15s keepalive comments, per-account capped queues, no polling).
  `/api/stream/updates` fires `event: granted` the moment `PATCH
  /api/access-requests/{id}` (or a direct `PATCH /api/users/{id}` /
  `PUT /api/users/{id}/guilds/{gid}`) resolves for that account; the panel
  verifies the effective state once (global permissions or the
  visibility-filtered guild list for scoped-only grants) and reloads into
  the dashboard. The admin Users page holds the same stream and receives
  `event: requests` for every newly created access request (broadcast to
  manage_users streams only, flagged at connect), refreshing the queue in
  place. The music page listens for `event: playback` (guild id payload,
  broadcast to every open stream by the pipeline via `notify_playback`:
  track start, pause, resume, skip, stop, queue drain) and refetches status,
  queue and history on it — progress interpolates locally second by second
  between pushes. `X-Accel-Buffering: no` keeps nginx from buffering the
  stream; no extra nginx configuration exists for it.
  Each created request DMs every env admin (best effort, 10 s timeout,
  silent failure) so the panel does not need watching; resolving a request
  clears the row immediately via the follow-up reload in the web UI.
  The admin DM carries Accept/Deny buttons resolved by a raw
  `on_interaction` listener (custom_id convention `reqdm:grant|deny:<id>`)
  through the shared `resolve_access_request_from_dm` helper: same permission
  flow as the panel, atomic pending-only status flip (one winner on races),
  same SSE events, then the message is recolored with a one-line decision.
  Clicks on stale copies (already resolved from the panel or by another
  admin) re-mark that message with the actual outcome; a removed request
  greys it out.
- **Voice loyalty:** the bot never leaves voice on its own. The last voice
  channel is remembered per guild; an unexpected drop (voice timeout, dead
  handshake, kick) triggers a delayed auto-rejoin plus a resume of the
  persisted queue. Deliberate leaves (panel Stop, `!stop`, stale-handle
  cleanup) set an `intentional_leave` flag first so the rejoin stays out.
- **Open login, closed API:** any Discord account can complete the OAuth
  flow (the shared-server gate was dropped to make onboarding possible when
  the bot and the user share nothing yet). A new account starts with an
  empty permission set, so its only surface is `/api/auth/me` plus the two
  access-request endpoints; the pending-request unique index caps one
  generic request per account. Display-name collisions across different
  Discord ids are disambiguated at upsert (`name-####` with the id tail)
  instead of failing the login. Stray accounts can be deleted by managers
  via `DELETE /api/users/{id}` (second `seed = FALSE` guard next to the
  route check).

## API surface

Full table lives in README (single source of truth for users); the shape:
health, guilds list, per-guild status/queue/history, global now-playing,
playback controls gated by granular permissions, auth
(discord/start → discord/callback), auth/me, users list and permission
management (global + per-guild overrides),
access-guilds + access-requests CRUD-lite. Input validation is pydantic
(max lengths on free text, `Literal` for permission keys). There is
deliberately **no in-app rate limiting** — a public deployment sits behind
Cloudflare rules.

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

## Webapp architecture (React + Vite + TypeScript + nginx)

```
 browser
    │  http://localhost:8080  (dev; prod: yourhostname via Cloudflare Tunnel)
    ▼
  nginx ── one origin for everything:
    ├── /                     → web (Vite dev server / static build)
    └── /api/*                → pakodj(-dev):8000  (bot FastAPI, unpublished)
```

- **Framework:** React 19 + Vite + react-router (TypeScript, `strict`).
  The panel is a fully client-side SPA: no SSR, no server runtime of its own,
  and the OAuth token never leaves the browser (localStorage).
- **nginx is the single entry point**, in DEV and in production. Same-origin
  `/api` calls mean the backend needs **no CORS at all** for the webapp;
  `API_CORS_ORIGINS` stays free for third-party tools.
- **DEV (`compose.yml`):** `web` runs `npm run dev` (Vite, hot reload) with
  the source mounted; nginx publishes `8080:80` — the only published port in
  the whole platform, dev only.
- **Production (actual wiring):** CI pushes three GHCR images: `pakodj` (bot +
  API, root `Dockerfile`), `pakodj-web` (static build — `web/Dockerfile`
  `target: prod`, an internal nginx serving `dist/` with SPA fallback) and
  `pakodj-nginx` (the entry-point reverse proxy, `nginx/Dockerfile`, with
  `prod.conf` baked in — no host file mounts, so a failed run can never leave
  stray workspace state behind).
  Both deploy composes run `pakodj` + `db` + `web` + `nginx` (+ `cloudflared`
  under the `tunnel` profile) with **no published ports**; the Tunnel maps
  the public hostname (planned: `https://dj.pako.uk`) to `nginx:80`, so the
  panel and the API share one origin in production too. `nginx/prod.conf`
  mirrors `nginx/dev.conf` with the production service names.
- **Auth flow stays the one defined in `permissions.md`/architecture:** the
  frontend opens `/api/auth/discord/start` (through nginx, same origin),
  stores the session token, and `/api/auth/me` gates the UI (permissions → buttons).

## Webapp direction (what the API already anticipates)

- Visual and interaction style for the web frontend is defined in
  `docs/style.md` (Tailwind tokens, layout grid, theme system).
- Browser clients: set `API_CORS_ORIGINS` to the webapp origin; server-side
  clients need nothing.
- `/api/auth/discord/start` returns the authorize URL the frontend opens;
  the callback redirects browsers to the panel (token in the URL fragment);
  `?json=1` keeps the raw JSON payload for API clients.
- `/api/auth/me` returns `id/username/permissions` so the UI can gate buttons.
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
