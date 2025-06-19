import os
import discord
from discord.ext import commands
import yt_dlp
import re
import asyncio
from discord import FFmpegPCMAudio
from dotenv import load_dotenv

# Load environmental variables
load_dotenv()

# Intents and bot configs
intents = discord.Intents.default()
intents.messages = True
intents.message_content = True

bot = commands.Bot(command_prefix="!", intents=intents)

# Dictionaries to store playback information for each server
server_playback_info = {}
server_locks = {}

# Global track counter
track_counter = 0

# Function to get playback information for a server
def get_server_info(guild_id):
    if guild_id not in server_playback_info:
        server_playback_info[guild_id] = {
            'current_track': None,
            'audio_queue': [],
            'playback_history': []
        }
    return server_playback_info[guild_id]

# Function to lock queue while stopping track
def get_server_lock(guild_id):
    if guild_id not in server_locks:
        server_locks[guild_id] = asyncio.Lock()
    return server_locks[guild_id]

# Get audio function
def get_audio_stream_url(url):
    try:
        # Remove additional parameters from the URL
        url = re.sub(r'&.*', '', url)
        
        ydl_opts = {
            'format': 'bestaudio/best',
            'quiet': True,
            'cookiefile': 'youtube_cookies.txt'
        }
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=False)
            if info is None:
                raise ValueError("No information retrieved from URL")
            return info['url'], info['title'], info['webpage_url']
    except Exception as e:
        print(f"Error retrieving audio stream: {e}")
        return None, None, None

# Search on youtube using a query written by the user
def search_youtube(query):
    try:
        ydl_opts = {
            'format': 'bestaudio/best',
            'quiet': True,
            'default_search': 'ytsearch',
            'max_downloads': 1,
            'cookiefile': 'youtube_cookies.txt'
        }
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(query, download=False)
            return info['entries'][0]['url'], info['entries'][0]['title'], info['entries'][0]['webpage_url']
    except Exception as e:
        print(f"Error searching on YouTube: {e}")
        return None, None, None

# Callback function to manage audio reproduction errors
def after_playing(error, guild_id):
    if error:
        print(f"Error during playback: {error}")
    server_info = get_server_info(guild_id)
    lock = get_server_lock(guild_id)
    async def next_track():
        async with lock:
            if server_info['audio_queue']:
                next_track = server_info['audio_queue'].pop(0)
                await play_audio(next_track['ctx'], next_track['url'], next_track['title'], next_track['video_url'])
    asyncio.run_coroutine_threadsafe(next_track(), bot.loop)

# Function to play song track in voice channel
async def play_audio(ctx, stream_url, title, video_url):
    vc = ctx.voice_client
    if vc is None:
        channel = ctx.author.voice.channel
        vc = await channel.connect()

    # DO NOT CHANGE SOURCE, OTHERWISE IT'LL STOP WHILE PLAYING
    source = FFmpegPCMAudio(source=stream_url, before_options="-reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5", options="-vn")
    vc.play(source, after=lambda e: after_playing(e, ctx.guild.id))
    await ctx.send(f"I'm playing: **{title}**\n{video_url}")

    # Add to history
    server_info = get_server_info(ctx.guild.id)
    server_info['playback_history'].append({'title': title, 'video_url': video_url})

    # Set current track
    server_info['current_track'] = {'title': title, 'video_url': video_url}

# Command to display current audio track
@bot.command(help = "Shows current audio track.")
async def track(ctx):
    server_info = get_server_info(ctx.guild.id)
    if server_info['current_track']:
        await ctx.send(f"Current track: **{server_info['current_track']['title']}**\n{server_info['current_track']['video_url']}")
    else:
        await ctx.send("No track is currently playing.")

# Command to display custom help message
@bot.command(name='djhelp', help = "Lists all commands for PakoDJ.")
async def custom_help(ctx):
    help_message = """
**PakoDJ Bot Commands:**
- `!join` - Joins user's voice channel
- `!play` - Plays an audio track searched by keywords or link (if a song is currently playing, adds the searched song in a queue).
- `!repeat` - Plays a song in loop for n times (use `!skip` to stop the loop).
- `!skip` - Stops current audio track and plays the next one in the queue.
- `!pause` - Pauses currently playing audio track.
- `!resume` - Resumes paused audio track.
- `!track` - Shows current audio track.
- `!queue` - Shows queued audio tracks.
- `!history` - Shows previously played audio tracks.
- `!stop` - Stops playback and disconnects bot from voice channel.
    """
    await ctx.send(help_message)

# Command to join the user's voice channel
@bot.command(help = "Joins user's voice channel.")
async def join(ctx):
    try:
        if ctx.author.voice:
            channel = ctx.author.voice.channel
            await channel.connect()
            await ctx.send(f"Joined {channel}")
        else:
            await ctx.send("You need to be in a voice channel to use this command.")
    except discord.ClientException as e:
        await ctx.send(f"Error: {e}")
        print(f"ClientException: {e}")
    except discord.InvalidArgument as e:
        await ctx.send(f"Error: {e}")
        print(f"InvalidArgument: {e}")
    except Exception as e:
        await ctx.send(f"An unexpected error occurred: {e}")
        print(f"Unexpected error: {e}")

# Command to reproduce audio
@bot.command(help = "Plays an audio track searched by keywords or link (if a song is currently playing, adds the searched song in a queue).")
async def play(ctx, *, query: str):
    global track_counter
    try:
        if ctx.author.voice:
            # Get the audio stream link, title, and video link
            if query.startswith("http://") or query.startswith("https://"):
                stream_url, title, video_url = get_audio_stream_url(query)
            else:
                stream_url, title, video_url = search_youtube(query)

            if stream_url is None:
                await ctx.send("Unable to retrieve audio stream.")
                return

            server_info = get_server_info(ctx.guild.id)
            lock = get_server_lock(ctx.guild.id)
            async with lock:
                track_counter += 1
                if ctx.voice_client and ctx.voice_client.is_playing():
                    server_info['audio_queue'].append({'ctx': ctx, 'url': stream_url, 'title': title, 'video_url': video_url, 'track_number': track_counter})
                    await ctx.send(f"Track added to queue: **{title}**\n{video_url}")
                else:
                    await play_audio(ctx, stream_url, title, video_url)
        else:
            await ctx.send("You have to be in a voice channel to use this command!")
    except Exception as e:
        await ctx.send(f"Error: {e}")
        print(f"Error: {e}")

# Command to play a track in loop
@bot.command(help="Plays an audio track in loop for n times (use !skip to stop).")
async def repeat(ctx, n: int, *, query: str):
    global track_counter
    try:
        if ctx.author.voice:
            if n < 1:
                await ctx.send("Please choose a number greater than 0.")
                return

            # Get track info
            if query.startswith("http://") or query.startswith("https://"):
                stream_url, title, video_url = get_audio_stream_url(query)
            else:
                stream_url, title, video_url = search_youtube(query)

            if stream_url is None:
                await ctx.send("Unable to retrieve audio stream.")
                return

            server_info = get_server_info(ctx.guild.id)
            lock = get_server_lock(ctx.guild.id)
            async with lock:
                for i in range(n):
                    track_counter += 1
                    track = {
                        'ctx': ctx,
                        'url': stream_url,
                        'title': f"{title} (loop {i+1}/{n})",
                        'video_url': video_url,
                        'track_number': track_counter
                    }
                    if ctx.voice_client and ctx.voice_client.is_playing() or i > 0:
                        server_info['audio_queue'].append(track)
                    else:
                        await play_audio(ctx, stream_url, f"{title} (loop 1/{n})", video_url)
        else:
            await ctx.send("You have to be in a voice channel to use this command!")
    except Exception as e:
        await ctx.send(f"Error: {e}")
        print(f"Error: {e}")

# Command to skip current track
@bot.command(help = "Stops current audio track and plays the next one in the queue.")
async def skip(ctx):
    server_info = get_server_info(ctx.guild.id)
    if ctx.voice_client and ctx.voice_client.is_playing():
        # Remove all iterations from the track in queue
        current = server_info['current_track']
        if current:
            server_info['audio_queue'] = [
                track for track in server_info['audio_queue']
                if track['title'].split(' (loop')[0] != current['title'].split(' (loop')[0]
            ]
        ctx.voice_client.stop()
        if server_info['audio_queue']:
            next_track = server_info['audio_queue'].pop(0)
            await play_audio(next_track['ctx'], next_track['url'], next_track['title'], next_track['video_url'])
        else:
            await ctx.send("Queue's empty. No track to play.")
    else:
        await ctx.send("No track playing.")

# Command to resume current track
@bot.command(help = "Resumes paused audio track.")
async def resume(ctx):
    if ctx.voice_client and ctx.voice_client.is_paused():
        ctx.voice_client.resume()
        await ctx.send("Track resumed.")
    else:
        await ctx.send("No paused track.")

# Command to show queued audio tracks
@bot.command(help = "Shows queued audio tracks.")
async def queue(ctx):
    server_info = get_server_info(ctx.guild.id)
    if server_info['audio_queue']:
        queue_message = "**Queue:**\n"
        for track in server_info['audio_queue']:
            track_info = f"{track['track_number']}. {track['title']} ({track['video_url']})\n"
            if len(queue_message) + len(track_info) <= 2000:
                queue_message += track_info
            else:
                break
        await ctx.send(queue_message)
    else:
        await ctx.send("Queue's empty.")

# Command to show previously played audio tracks
@bot.command(help = "Shows previously played audio tracks.")
async def history(ctx):
    server_info = get_server_info(ctx.guild.id)
    if server_info['playback_history']:
        history_message = "**Playback History (latest first):**\n"
        # Show latest track first
        for i, track in enumerate(reversed(server_info['playback_history']), 1):
            track_info = f"{i}. {track['title']} ({track['video_url']})\n"
            if len(history_message) + len(track_info) <= 2000:
                history_message += track_info
            else:
                break
        await ctx.send(history_message)
    else:
        await ctx.send("No tracks have been played yet.")

# Command to stop playback and disconnect PakoDJ
@bot.command(help = "Stops playback and disconnects bot from voice channel.")
async def stop(ctx):
    try:
        if ctx.voice_client is not None:
            await ctx.voice_client.disconnect()
            await ctx.send("Track stopped and bot disconnected.")
        else:
            await ctx.send("Bot is not in a voice channel.")
    except Exception as e:
        await ctx.send(f"Error: {e}")
        print(f"Error: {e}")

# Event to log bot's successful connection on console
@bot.event
async def on_ready():
    print(f'Bot connected as {bot.user.name}')

bot.run(os.getenv('token'))