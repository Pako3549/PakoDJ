# AGENTS.md — working rules for coding agents

This file is the entry point for any coding agent (or human) working on PakoDJ.
It summarizes the non-negotiable conventions; deeper explanation lives in
`docs/architecture.md` (what the platform is) and `docs/style.md` (the GUI
style for the web frontend). `README.md` documents setup and endpoints for
end users.

## Hard invariants — never break these

1. **English only in artifacts.** Every comment, docstring, log line, print,
   Discord reply, compose comment, README and docs file is written in English,
   with no accented characters (verified by grep in past sweeps). The user
   speaks Italian in conversation: chat replies in Italian, artifacts in English.
2. **All database access goes through `bot/db.py`** (repository layer). No
   SQL in `main.py`, `api.py` or Discord event handlers. Queries must be
   parameterized (`$1, $2...` with asyncpg), never string-formatted.
3. **No playback state in memory.** Queues, history, collections, now-playing
   mirror live in PostgreSQL. Per-guild runtime state is limited to
   `current_track` and `loading_task`. Stream URLs are re-resolved just-in-time
   because they expire; queue rows store canonical data only
   (title, webpage_url, search_query).
4. **The SSRF guard stays.** `is_safe_http_url()` in `bot/main.py` must run
   before every yt-dlp URL extraction. Never bypass or relax it: the API is
   exposed to the internet, so bots must not be tricked into fetching
   `file://`, private IPs, or Docker-internal hostnames.
5. **Nothing is exposed to the internet by default.** Production compose files never
   publish ports; exposure is delegated to Cloudflare Tunnel (dormant `tunnel`
   profile). The ONLY published port allowed is the dev compose's nginx on
   `8080:80` (the webapp entry point for local development). Rate limiting is
   delegated to Cloudflare rules: do not reintroduce in-app throttles that were
   deliberately removed.
6. **Auth is Discord OAuth only.** No password login, no static tokens. The
   seeded admins come from `DISCORD_ADMIN_IDS` and are untouchable via the API
   (`seed = true`: no PATCH can change their role; enforced twice — route check
   and `UPDATE ... WHERE seed = FALSE`). Do not add login mechanisms or CSRF
   bypasses: the only paths reachable without a session are `/api/auth/discord/*`.
7. **Push to `main` deploys to the user's live Raspberry Pi 5** (self-hosted
   runner). Never push without explicit user approval, and keep the repo
   deployable: the workflow rewrites `bot/.env` from GitHub secrets on every
   deploy.

## Code conventions (quick reference)

- Comments explain **why**, never narrate the how. Every env var read by code
  gets one comment line at the read site or in `bot/.env.example`.
- Blocking work (yt-dlp, spotipy, any sync IO) never runs on the loop: route
  it through `run_in_executor` or an existing async client (aiohttp/asyncpg).
- Every in-memory dict/cache gets a cap or TTL — no unbounded growth.
- API: raise `HTTPException` with the narrowest status (409 state conflicts,
  403 role/ownership, 400 bad input, 503 feature disabled by missing env);
  pydantic models carry explicit caps and `Literal[...]` enums; the app owns
  no rate limiting/TLS/IP filtering (Cloudflare's job).
- Ownership rules are enforced twice: in the route and in the repository
  function (e.g. seeded admins: route check + `UPDATE ... WHERE seed = FALSE`).
- Compose: the three compose files stay in sync (same structure and
  comments), secrets flow only via the workflow, every change validated with
  `docker compose config -q` against a placeholder `bot/.env`.
- New endpoint ⇒ README endpoint table with the correct auth column; new env
  variable ⇒ code + `bot/.env.example` + README. Behavioral decisions are
  documented in `docs/architecture.md`.

## Repository map

| Path | What it is |
| --- | --- |
| `bot/main.py` | Bot core: Discord commands, playback pipeline, resolver + caches, SSRF guard, uvicorn API server startup |
| `bot/db.py` | PostgreSQL schema + repository functions (the only place SQL lives) |
| `bot/api.py` | FastAPI app factory, Discord OAuth login, sessions, roles, access requests |
| `bot/.env.example` | Fully commented template of every runtime variable |
| `bot/requirements.txt` | python-dotenv, discord.py[voice], PyNaCl, davey, yt-dlp@master, spotipy, asyncpg, fastapi, uvicorn |
| `compose.yml` | Dev stack (pakodj-dev + web + nginx on 8080 + db; no tunnel service) |
| `deploy/compose.yml` | Production stack pulled from GHCR |
| `deploy/compose.build.yml` | Production stack built on the host |
| `.github/workflows/docker-publish.yml` | Build (multi-arch GHCR) + deploy to the Pi (rewrites `bot/.env` from secrets) |
| `runner/compose.yml` | Self-hosted GitHub Actions runner stack |
| `web/` | Webapp: Next.js + TypeScript + React panel (dev: hot reload, served through nginx) |
| `nginx/` | Webserver configs: `dev.conf` proxies `/` to the Next dev server and `/api/*` to the bot API (one origin) |
| `docs/` | Agent documentation (architecture, permission model, GUI style for the web frontend) |

## Environment variables

Every runtime variable is listed in `bot/.env.example` with defaults and
comments. The workflow injects from GitHub secrets only: `DISCORD_TOKEN`,
`SPOTIFY_CLIENT_ID`/`SPOTIFY_CLIENT_SECRET`, `API_PUBLIC_ORIGIN`,
`DISCORD_CLIENT_ID`/`DISCORD_CLIENT_SECRET`, `DISCORD_ADMIN_IDS`,
`TUNNEL_TOKEN` (into the compose project `.env`). Compose interpolation must
never be fed from `env_file` values (workflow writes `.env` separately for
that reason). If you add a variable, update all three places: code,
`bot/.env.example`, README secrets/env tables.

## Verification checklist (run before claiming done)

```bash
python3 -m py_compile bot/*.py
printf 'token=x\n' > bot/.env                       # placeholder for compose validation
docker compose -f compose.yml config -q
docker compose -f deploy/compose.yml config -q
docker compose -f deploy/compose.build.yml config -q
rm bot/.env
python3 -c "import yaml; yaml.safe_load(open('.github/workflows/docker-publish.yml'))"
```

All green + README/env.example updated = done. Runtime smoke tests need the
user's real token: hand them the step list, do not fake "tested" results.
