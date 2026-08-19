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
3. **Configure the `.env` file**: Create a `.env` file in the `bot/` directory and add your bot token:
    ```env
    token=YOUR_BOT_TOKEN
    # Optional: For Spotify URL support
    SPOTIFY_CLIENT_ID=YOUR_SPOTIFY_CLIENT_ID
    SPOTIFY_CLIENT_SECRET=YOUR_SPOTIFY_CLIENT_SECRET
    ```
4. **Run the bot**:
    ```bash
    python bot/main.py
    ```

### 🐳 Docker Management Commands
- **View logs**: `docker-compose -f deploy/compose.yml logs -f pakodj`
- **Stop the bot**: `docker-compose -f deploy/compose.yml down`
- **Restart the bot**: `docker-compose -f deploy/compose.yml restart pakodj`
- **Update image**: `docker-compose -f deploy/compose.yml pull && docker-compose -f deploy/compose.yml up -d`

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

For persistent log permissions on the host, run once:

```bash
sudo chown -R 1000:1000 logs
```

### 3. Runner Management
- **View runner logs**: `docker compose -f runner/compose.yml logs -f`
- **Stop the runner**: `docker compose -f runner/compose.yml down`
- **Restart the runner**: `docker compose -f runner/compose.yml restart`

After a successful push to `main`, check that the `deploy` job ran on your runner and that `pakodj` was restarted with the new image.

## ⚠️ Age-Restricted (+18) YouTube Videos

If you want to play age-restricted (+18) YouTube videos, you must provide your YouTube cookies to yt-dlp.  
Please follow the official yt-dlp guides:

- [How do I pass cookies to yt-dlp?](https://github.com/yt-dlp/yt-dlp/wiki/FAQ#how-do-i-pass-cookies-to-yt-dlp)
- [Exporting YouTube cookies](https://github.com/yt-dlp/yt-dlp/wiki/Extractors#exporting-youtube-cookies)

### For Docker Setup:
After extracting your cookies, save them in a text file named `youtube_cookies.txt` in the `bot/` directory.  
The Docker container will automatically mount this file and make it available to the bot.

### For Manual Python Setup:
After extracting your cookies, save them in a text file named `youtube_cookies.txt` in the same directory as the script that starts the bot (`bot/main.py`).

This is required only for playing age-restricted content; for normal videos, no cookies are needed.

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
