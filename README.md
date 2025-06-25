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
- **Queue Management**: Add songs to the queue and play them in sequence.
- **Playback Controls**: Pause, resume, or skip tracks.
- **History**: View previously played tracks.
- **Voice Integration**: Automatically connects to the voice channel you're in.
- **Multi-Server Support**: Each server has its own queue and playback history.
## 📋 Prerequisites
Before starting, make sure you have:
- **Python 3.8 or higher** installed.
- **ffmpeg** installed.
- A Discord bot token (you can obtain one from the [Discord Developer Portal](https://discord.com/developers/applications)).
- The following Python libraries installed:
    - `discord.py`
    - `yt-dlp`
    - `python-dotenv`
    - `PyNaCl`
## ⚙️ Setup
1. **Clone the repository**:
    ```bash
    git clone https://github.com/Pako3549/PakoDJ.git
    ```
2. **Install the dependencies**:
    ```bash
    pip install -r requirements.txt
    ```
3. **Configure the `.env` file**: Create a `.env` file in the main directory and add your bot token:
    ```env
    token=YOUR_BOT_TOKEN
    ```
1. **Run the bot**:
    ```bash
    python main.py
    ```

## ⚠️ Age-Restricted (+18) YouTube Videos

If you want to play age-restricted (+18) YouTube videos, you must provide your YouTube cookies to yt-dlp.  
Please follow the official yt-dlp guides:

- [How do I pass cookies to yt-dlp?](https://github.com/yt-dlp/yt-dlp/wiki/FAQ#how-do-i-pass-cookies-to-yt-dlp)
- [Exporting YouTube cookies](https://github.com/yt-dlp/yt-dlp/wiki/Extractors#exporting-youtube-cookies)

After extracting your cookies, save them in a text file named `youtube_cookies.txt` in the same directory as the script that starts the bot (`main.py`).  
This is required only for playing age-restricted content; for normal videos, no cookies are needed.

## 📖 Command

| Command                | Description                                                                                                         |
| ---------------------- | ------------------------------------------------------------------------------------------------------------------- |
| `!play <query>`        | Plays a song searched using keywords or a link. If a song is already playing, adds it to the queue.                 |
| `!repeat <n> <query>`  | Plays a song in loop for n times (use `!skip` to stop the loop).                                                    |
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
- **Dynamic Management**: Each server has its own queue and playback history.
- **Automatic Connection**: The bot automatically connects to the voice channel of the user issuing a command.
## 🐛 Troubleshooting
- **Error: `Bot is not in a voice channel`**  
    Ensure the bot is connected to a voice channel and that you're in the same channel.
- **Error during playback**  
    Check if the link or query is valid. For persistent issues, check the console for detailed error messages.
## 📜 License
This project is open-source and available under the GPL-3.0 License. See the [LICENSE](https://github.com/Pako3549/PakoDJ/blob/main/LICENSE) file for more details.
