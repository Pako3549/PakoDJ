<div align="center">

![](https://cdn.discordapp.com/app-icons/1317889379813031946/c3979a312b19ef2fc88e0712716e3077.png?size=512)

# PakoDJ Bot 🎵
![](https://img.shields.io/github/last-commit/Pako3549/PakoDJ?&style=for-the-badge&color=8272a4&logoColor=D9E0EE&labelColor=292324)
![](https://img.shields.io/github/stars/Pako3549/PakoDJ?style=for-the-badge&logo=polestar&color=FFB1C8&logoColor=D9E0EE&labelColor=292324)
![](https://img.shields.io/github/repo-size/Pako3549/PakoDJ?color=CAC992&label=SIZE&logo=files&style=for-the-badge&logoColor=D9E0EE&labelColor=292324)

</div>

**PakoDJ** is a Discord bot written in Python that lets you play music directly in your server's voice channels. With its features, you can search for songs on YouTube, manage music queues, view playback history, and much more!
## 🛠️ Main Features
- **Music Playback**: Search and play songs from YouTube via links or keywords.
- **Spotify Integration**: Play music from Spotify URLs (tracks, albums, playlists) by searching on YouTube. Full playlist and album support with automatic queueing and background loading for instant playback.
- **SoundCloud Support**: Play music directly from SoundCloud URLs with enhanced HLS streaming support.
- **Queue Management**: Add songs to the queue and play them in sequence.
- **Playback Controls**: Pause, resume, or skip tracks.
- **History**: View previously played tracks.
- **Voice Integration**: Automatically connects to the voice channel you're in.
- **Multi-Server Support**: Each server has its own queue and playback history.
## 📋 Prerequisites
### For Docker Setup:
- **Docker** and **Docker Compose** installed
- A Discord bot token (you can obtain one from the [Discord Developer Portal](https://discord.com/developers/applications))

### For Manual Setup:
- **Python 3.8 or higher** installed
- **ffmpeg** installed
- A Discord bot token (you can obtain one from the [Discord Developer Portal](https://discord.com/developers/applications))
- **Optional**: Spotify API credentials for Spotify URL support (get them from [Spotify Developer Dashboard](https://developer.spotify.com/dashboard))
- The following Python libraries installed:
    - `discord.py`
    - `yt-dlp`
    - `python-dotenv`
    - `PyNaCl`
    - `spotipy`
    - `asyncpg`
- A **PostgreSQL** database reachable from the bot (with Docker Compose it is created automatically)
## ⚙️ Setup

### 🐳 Docker Setup (Recommended)
1. **Clone the repository**:
    ```bash
    git clone https://github.com/Pako3549/PakoDJ.git
    cd PakoDJ
    ```
2. **Configure the `.env` file**: Create a `.env` file in the `bot/` directory:
    ```env
    token=YOUR_BOT_TOKEN
    # Optional: For Spotify URL support
    SPOTIFY_CLIENT_ID=YOUR_SPOTIFY_CLIENT_ID
    SPOTIFY_CLIENT_SECRET=YOUR_SPOTIFY_CLIENT_SECRET
    ```
3. **Run the bot**:
    ```bash
    docker-compose -f deploy/compose.yml up -d
    ```

#### Alternative: Build Locally (Slower)
If you prefer to build the image yourself:
```bash
docker-compose -f deploy/compose.build.yml up -d
```

#### 🧪 Development
For local development with live code reload (your `bot/` folder is mounted into the container):
```bash
docker compose up -d --build
docker compose logs -f pakodj-dev
```
The image is always built from the local `Dockerfile` (so `requirements.txt`/Dockerfile changes are picked up); the `./bot` mount lets you tweak Python code without rebuilding. To just rebuild the image without recreating:
```bash
docker compose build
```

### 🐍 Manual Python Setup
1. **Clone the repository**:
    ```bash
    git clone https://github.com/Pako3549/PakoDJ.git
    cd PakoDJ
    ```
2. **Install the dependencies**:
    ```bash
    pip install -r bot/requirements.txt
    ```
3. **Configure the `.env` file**: copy `bot/.env.example` to `bot/.env`, then create a `.env` file in the `bot/` directory and add your bot token (every variable is documented in the example file):
    ```env
    token=YOUR_BOT_TOKEN
    # PostgreSQL connection (required when running without Docker Compose)
    DATABASE_URL=postgres://pakodj:pakodj@localhost:5432/pakodj
    # Web API login via Discord OAuth (needs a public origin, e.g. behind Cloudflare)
    DISCORD_CLIENT_ID=your_discord_app_client_id
    DISCORD_CLIENT_SECRET=your_discord_app_client_secret
    API_PUBLIC_ORIGIN=https://api.yourdomain.com
    # Discord IDs that get the admin flag (put yours here to be the owner)
    DISCORD_ADMIN_IDS=123456789012345678
    # Optional: Cloudflare Tunnel token (activates with: docker compose --profile tunnel up -d)
    TUNNEL_TOKEN=your_cloudflare_tunnel_token
    # Optional: For Spotify URL support
    SPOTIFY_CLIENT_ID=YOUR_SPOTIFY_CLIENT_ID
    SPOTIFY_CLIENT_SECRET=YOUR_SPOTIFY_CLIENT_SECRET
    ```
4. **Run the bot**:
    ```bash
    python bot/main.py
    ```

## 🗄️ Database Persistence

Queues, playback history and background-loading playlists are stored in **PostgreSQL** instead of memory. This means:
- **Queues survive bot restarts** (e.g. when the CI/CD pipeline redeploys the container). The queue is resumed automatically with the next `!play` / `!skip`.
- **History is capped** by query: no more unbounded memory growth.
- `!skip all` removes a whole playlist/album/loop group with a single SQL delete (no more fragile title matching).
- Future web platform: a self-hosted PostgreSQL is ready to be read by an external app.

With Docker Compose the database is started and connected automatically (`pakodj-db` service, persistent volume `pakodj-pgdata`, credentials `pakodj/pakodj`). Connection settings are injected via the `DATABASE_URL` environment variable:
```env
DATABASE_URL=postgres://user:password@host:5432/dbname
```

## 🔌 Web API

The bot embeds an **HTTP API** (FastAPI) in its own process on port **8000**. It is a plain REST interface: any external client (web app, script, mobile app — in any language) can consume it.

**Every endpoint requires a login session, and the only login method is Discord OAuth:**
1. `GET /api/auth/discord/start` → returns the Discord authorize URL (open it, approve).
2. Discord redirects back to `GET /api/auth/discord/callback?code=...&state=...` → the account (linked to the Discord user) is created and the **browser is redirected to the panel** (`/auth/callback`, session data travels in the URL fragment so it never hits server logs). API clients that want the raw JSON add `?json=1`.
3. Every other call needs the header `Authorization: Bearer <session_token>`.

Sessions are stored server side (only their SHA-256), can be revoked by deleting the row or via `POST /api/auth/logout`. **When the API is exposed through Cloudflare, rate limiting is delegated to Cloudflare rules.**

**Setup requirements:** set `DISCORD_CLIENT_ID`, `DISCORD_CLIENT_SECRET` and `API_PUBLIC_ORIGIN` (e.g. `https://api.yourdomain.com`) in `bot/.env`, and register the redirect URI `${API_PUBLIC_ORIGIN}/api/auth/discord/callback` in your Discord application (discord.com/developers → OAuth2). Users must share a server with the bot to log in. Discord IDs in `DISCORD_ADMIN_IDS=<id>,...` are always admin — put your own ID there and your account is the owner-admin from the first login. Until those env vars are set, the Discord endpoints answer `503` and every other endpoint answers `401` (no anonymous surface).

**Roles:** every login auto-creates the account, but with the `nobody` role (no API surface: only `/api/auth/me` and the access-request endpoints). The owner-admin then grants access with the user-management endpoints:
- `GET /api/users` — list accounts (id, username, role, seed flag, created_at, discord_id)
- `PATCH /api/users/{id}` `{"role": "viewer"|"admin"|"nobody"}` — change a role

`viewer` gets read-only access; `admin` gets playback control and user management. **Seeded admins are untouchable:** accounts whose Discord ID is in `DISCORD_ADMIN_IDS` carry the `seed` flag and are exclusively owned by the env — no API call and no other admin can ever change or demote them (`403`), and they are re-forced to `admin` at every login. Removing an ID from `DISCORD_ADMIN_IDS` revokes the seeded admin (that account falls back to `nobody` and becomes changeable at its next login). A role granted by an admin to a normal account survives logins.

**Access requests (webapp onboarding):** a `nobody` user asks for access directly from the webapp:
1. `GET /api/access/guilds` — servers where BOTH the user and the bot are (snapshot refreshed at every login)
2. `POST /api/access-requests {"guild_id": ..., "message": "why I want access"}` — one pending request per guild, validated against that same list
3. admins see the queue with `GET /api/access-requests` (username, guild, message) and resolve each one with `PATCH /api/access-requests/{id} {"status": "granted"|"denied"}` — granting the actual role is a separate `PATCH /api/users/{id}` call

**Local webapp panel (development):** the root `compose.yml` also runs the web panel (React + Vite, `web/`) behind **nginx**, the only published port of the platform: open `http://localhost:8080` and the UI talks to the API through the same origin (`/api/*` proxy, no CORS). Production exposure remains Cloudflare-only.

**No ports are published** in the production compose files: the API is reachable only from the Docker network (`http://pakodj:8000`) and the web panel is served internally by nginx. To expose the platform to the internet, the recommended path is **Cloudflare Tunnel** — see the next section. If you ever publish a port directly, bind it to localhost only (`127.0.0.1:8000:8000`, commented block in the compose files) and keep the public caller behind a reverse proxy.

**Production stack layout (both deploy composes):** `pakodj` (bot + API), `web` (static React/Vite build served by an internal nginx), `nginx` (single entry point: `/` → web, `/api/*` → pakodj), optional `cloudflared` under the `tunnel` profile. Point the Tunnel hostname at `http://nginx:80` in the Cloudflare dashboard and the whole platform (panel + API) is live with zero published ports.

| Method | Endpoint | Auth | Description |
| ------ | -------- | ---- | ----------- |
| `GET` | `/api/auth/discord/start` | — | Returns the Discord OAuth authorize URL |
| `GET` | `/api/auth/discord/callback` | — | Discord OAuth callback → session token |
| `GET` | `/api/auth/me` | session | Current account (id, username, role) |
| `POST` | `/api/auth/logout` | session | Revoke the session |
| `GET` | `/api/users` | 🛠️ admin | List accounts and their roles |
| `PATCH` | `/api/users/{id}` | 🛠️ admin | Change a role (`nobody`/`viewer`/`admin`) |
| `GET` | `/api/access/guilds` | session | Guilds shared between the account and the bot |
| `GET` | `/api/access-requests` | 🛠️ admin | Pending access requests (panel queue) |
| `POST` | `/api/access-requests` | session | Submit an access request for a shared guild |
| `PATCH` | `/api/access-requests/{id}` | 🛠️ admin | Resolve a request (`granted`/`denied`) |
| `GET` | `/api/guilds` | viewer+ | Servers the bot is in |
| `GET` | `/api/guilds/{id}/status` | viewer+ | Now playing + queue count + voice state |
| `GET` | `/api/now-playing` | viewer+ | What every connected server is playing |
| `GET` | `/api/guilds/{id}/queue` | viewer+ | Queue of a server |
| `GET` | `/api/search?q=` | viewer+ | Track metadata (free Deezer public API, server-cached) |
| `POST` | `/api/guilds/{id}/queue` | 🛠️ admin | `{ "query": "...", "position": 0? }` — enqueue (append or at a position) |
| `PATCH` | `/api/guilds/{id}/queue/{item_id}` | 🛠️ admin | `{ "position": n }` — reorder the queue |
| `DELETE` | `/api/guilds/{id}/queue/{item_id}` | 🛠️ admin | Remove one queued item |
| `DELETE` | `/api/guilds/{id}/queue` | 🛠️ admin | Clear the queue |
| `GET` | `/api/guilds/{id}/history` | viewer+ | History of a server |
| `POST` | `/api/guilds/{id}/play` | 🛠️ admin | `{ "query": "..." }` — play or enqueue |
| `POST` | `/api/guilds/{id}/skip` | 🛠️ admin | `{ "all": false }` — skip / skip all |
| `POST` | `/api/guilds/{id}/pause` · `/resume` · `/stop` | 🛠️ admin | Playback controls |

Optional: set `API_CORS_ORIGINS=https://your-webapp.example` in `bot/.env` if a browser-based frontend will call the API cross-origin (server-to-server clients don't need it).

### Exposing to the internet (Cloudflare)
When you put a web page in front of the API via Cloudflare Tunnel, **nothing in the app** needs to change: ports stay unpublished forever (cloudflared connects outbound to Cloudflare), TLS/DDoS/rate-limits are offloaded, and CORS only needs the real origin in `API_CORS_ORIGINS`. The in-app session auth and the outbound URL guard stay in place — see the security section.

### 🐳 Docker Management Commands
- **View logs**: `docker-compose -f deploy/compose.yml logs -f pakodj`
- **Stop the bot**: `docker-compose -f deploy/compose.yml down`
- **Restart the bot**: `docker-compose -f deploy/compose.yml restart pakodj`
- **Update image**: `docker-compose -f deploy/compose.yml pull && docker-compose -f deploy/compose.yml up -d`

Web panel on production: `web` is a static build served inside the stack and
fronted by `nginx`; image updates land with the same `pull && up -d` command
above. Logs for the webserver: `logs -f nginx`.

#### For Local Build (Alternative):
- **View logs**: `docker-compose -f deploy/compose.build.yml logs -f pakodj`
- **Stop the bot**: `docker-compose -f deploy/compose.build.yml down`
- **Restart and rebuild**: `docker-compose -f deploy/compose.build.yml up -d --build`

## 🚀 Auto-Deploy with a Self-Hosted Runner

On every push to `main`, the CI workflow (`.github/workflows/docker-publish.yml`) builds and pushes the PakoDJ image to GHCR (`ghcr.io/pako3549/pakodj:latest`). A second job (`deploy`) then runs on a machine of yours with a **self-hosted GitHub Actions runner**: the runner pulls the fresh image from GHCR and restarts the bot with `docker compose`.

### 1. Set Up the Runner (once per machine)
Create a `.env` file in the `runner/` directory with your GitHub token:

```env
ACCESS_TOKEN=github_pat_xxx   # classic PAT with "repo" scope (or fine-grained with Actions read/write)
RUNNER_NAME=pakodj-runner
RUNNER_LABELS=self-hosted
```

Start the runner:

```bash
docker compose -f runner/compose.yml up -d
```

The runner registers itself against the `Pako3549/PakoDJ` repository and automatically picks up deploy jobs tagged `self-hosted`.

> ⚠️ The runner runs inside the docker socket, so it needs a machine with Docker installed. `runner/compose.yml` is the same tried-and-tested setup as `myoung34/github-runner`, which registers the runner each restart.

### 2. Configure the Deployment
The `deploy` job writes `bot/.env` on the runner from GitHub **repository secrets** (Settings → Secrets and variables → Actions):

| Secret | Required | Description |
| ------ | -------- | ----------- |
| `DISCORD_TOKEN` | Yes | Bot token used at runtime |
| `SPOTIFY_CLIENT_ID` | No | Spotify Integration |
| `SPOTIFY_CLIENT_SECRET` | No | Spotify Integration |
| `DISCORD_CLIENT_ID` | No | Web API: Discord OAuth login |
| `DISCORD_CLIENT_SECRET` | No | Web API: Discord OAuth login |
| `API_PUBLIC_ORIGIN` | No | Web API: public origin, e.g. https://api.yourdomain.com (with it, register the redirect `${API_PUBLIC_ORIGIN}/api/auth/discord/callback` in the Discord app) |
| `DISCORD_ADMIN_IDS` | No | Web API: comma separated Discord IDs that get the admin flag (yours = owner) |
| `TUNNEL_TOKEN` | No | Cloudflare Tunnel token (only with the `tunnel` compose profile) |

For persistent log permissions on the host, run once:

```bash
sudo chown -R 1000:1000 logs
```

### 3. Runner Management
- **View runner logs**: `docker compose -f runner/compose.yml logs -f`
- **Stop the runner**: `docker compose -f runner/compose.yml down`
- **Restart the runner**: `docker compose -f runner/compose.yml restart`

After a successful push to `main`, check that the `deploy` job ran on your runner and that `pakodj` was restarted with the new image.

## 🎵 Spotify Integration

PakoDJ supports playing music from Spotify URLs! When you provide a Spotify track, album, or playlist URL, the bot will:
1. Extract the track information from Spotify
2. Search for the track(s) on YouTube
3. Play the audio from YouTube (avoiding DRM restrictions)

**Full Playlist & Album Support**: When you provide a Spotify playlist or album URL, the bot will automatically start playing the first track immediately while loading the remaining tracks in the background. This ensures instant playback without delays, even for large playlists!

### Setting Up Spotify Integration:
1. Go to [Spotify Developer Dashboard](https://developer.spotify.com/dashboard)
2. Create a new app (or use an existing one)
3. Copy your **Client ID** and **Client Secret**
4. Add them to your `.env` file:
   ```env
   SPOTIFY_CLIENT_ID=your_client_id_here
   SPOTIFY_CLIENT_SECRET=your_client_secret_here
   ```

**Note**: Spotify integration is optional. The bot works perfectly fine without it, but you won't be able to use Spotify URLs.

### Supported Spotify URLs:
- **Tracks**: `https://open.spotify.com/track/...` - plays the single track
- **Albums**: `https://open.spotify.com/album/...` - plays all tracks from the album
- **Playlists**: `https://open.spotify.com/playlist/...` - plays all tracks from the playlist

## 🎵 SoundCloud Support

PakoDJ also supports playing music directly from SoundCloud! The bot automatically detects SoundCloud URLs and uses enhanced streaming technology to handle HLS audio formats.

### Supported SoundCloud URLs:
- **Tracks**: `https://soundcloud.com/artist/track-name`
- **Short URLs**: `https://snd.sc/...`
- **Mobile URLs**: `https://m.soundcloud.com/...`

**Note**: SoundCloud support works out of the box - no additional configuration needed! The bot automatically optimizes FFmpeg settings for SoundCloud's HLS streaming format.

## 📖 Command

| Command                | Description                                                                                                         |
| ---------------------- | ------------------------------------------------------------------------------------------------------------------- |
| `!play <query>`        | Plays a song searched using keywords, YouTube links, Spotify URLs, or SoundCloud URLs. If a song is already playing, adds it to the queue. |
| `!repeat <n> <query>`  | Plays a song in loop for n times. Supports YouTube, Spotify, and SoundCloud URLs (use `!skip all` to stop the loop). |
| `!skip`                | Skips the current track and plays the next one in the queue.                                                        |
| `!skip all`            | Skips the current track and all remaining tracks from the same playlist/album or loop, then plays the next track in queue. |
| `!pause`               | Pauses the currently playing track.                                                                                  |
| `!resume`              | Resumes the paused track.                                                                                           |
| `!track`               | Shows the currently playing track.                                                                                   |
| `!queue`               | Displays the queue of tracks.                                                                                       |
| `!history`             | Shows the history of previously played tracks.                                                                      |
| `!stop`                | Stops playback and disconnects the bot from the voice channel.                                                      |
| `!djhelp`              | Displays the list of available commands.                                                                            |                                                      |

## 🛠️ How It Works
- **Search and Playback**: The bot uses `yt-dlp` to fetch the best available audio from YouTube.
- **Spotify Integration**: When a Spotify URL is provided, the bot extracts track information and searches for it on YouTube.
- **SoundCloud Support**: Direct streaming from SoundCloud with optimized FFmpeg settings for HLS audio formats.
- **Dynamic Management**: Each server has its own queue and playback history.
- **Automatic Connection**: The bot automatically connects to the voice channel of the user issuing a command.
## 🐛 Troubleshooting
- **Error: `Bot is not in a voice channel`**  
    Ensure the bot is connected to a voice channel and that you're in the same channel.
- **Error during playback**  
    Check if the link or query is valid. For persistent issues, check the console for detailed error messages.
## 📜 License
This project is open-source and available under the GPL-3.0 License. See the [LICENSE](https://github.com/Pako3549/PakoDJ/blob/main/LICENSE) file for more details.
