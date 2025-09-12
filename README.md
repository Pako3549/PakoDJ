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
- **Spotify Integration**: Play music from Spotify URLs (tracks, albums, playlists) by searching on YouTube.
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
    docker-compose up -d
    ```

#### Alternative: Build Locally (Slower)
If you prefer to build the image yourself:
```bash
docker-compose -f docker-compose.build.yml up -d
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
- **View logs**: `docker-compose logs -f pakodj`
- **Stop the bot**: `docker-compose down`
- **Restart the bot**: `docker-compose restart pakodj`
- **Update image**: `docker-compose pull && docker-compose up -d`

#### For Local Build (Alternative):
- **View logs**: `docker-compose -f docker-compose.build.yml logs -f pakodj`
- **Stop the bot**: `docker-compose -f docker-compose.build.yml down`
- **Restart and rebuild**: `docker-compose -f docker-compose.build.yml up -d --build`

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
2. Search for the track on YouTube
3. Play the audio from YouTube (avoiding DRM restrictions)

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
- **Tracks**: `https://open.spotify.com/track/...`
- **Albums**: `https://open.spotify.com/album/...` (plays first track)
- **Playlists**: `https://open.spotify.com/playlist/...` (plays first track)

## 📖 Command

| Command                | Description                                                                                                         |
| ---------------------- | ------------------------------------------------------------------------------------------------------------------- |
| `!play <query>`        | Plays a song searched using keywords, YouTube links, or Spotify URLs. If a song is already playing, adds it to the queue. |
| `!repeat <n> <query>`  | Plays a song in loop for n times. Supports YouTube and Spotify URLs (use `!skip all` to stop the loop).           |
| `!skip`                | Skips the current track and plays the next one in the queue.                                                        |
| `!skip all`            | Skips the current track and the loop; then it plays the next track in queue.     |
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
- **Dynamic Management**: Each server has its own queue and playback history.
- **Automatic Connection**: The bot automatically connects to the voice channel of the user issuing a command.
## 🐛 Troubleshooting
- **Error: `Bot is not in a voice channel`**  
    Ensure the bot is connected to a voice channel and that you're in the same channel.
- **Error during playback**  
    Check if the link or query is valid. For persistent issues, check the console for detailed error messages.
## 📜 License
This project is open-source and available under the GPL-3.0 License. See the [LICENSE](https://github.com/Pako3549/PakoDJ/blob/main/LICENSE) file for more details.
