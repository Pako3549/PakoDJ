import os
import discord
from discord.ext import commands
import yt_dlp
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

# Audio queue
queue = []

# Get audio function
def get_audio_stream_url(url):
    try:
        ydl_opts = {
            'format': 'bestaudio/best',
            'quiet': True
        }
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=False)
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
            'max_downloads': 1
        }
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(query, download=False)
            return info['entries'][0]['url'], info['entries'][0]['title'], info['entries'][0]['webpage_url']
    except Exception as e:
        print(f"Error searching on YouTube: {e}")
        return None, None, None

# Callback function to manage audio reproduction errors
def after_playing(error):
    if error:
        print(f"Error during playback: {error}")
    if queue:
        next_track = queue.pop(0)
        asyncio.run_coroutine_threadsafe(play_audio(next_track['ctx'], next_track['url'], next_track['title'], next_track['video_url']), bot.loop)

# Audio reproduction
async def play_audio(ctx, stream_url, title, video_url):
    vc = ctx.voice_client
    if vc is None:
        channel = ctx.author.voice.channel
        vc = await channel.connect()

    # DO NOT CHANGE SOURCE, OTHERWISE IT'LL STOP WHILE PLAYING
    source = FFmpegPCMAudio(source=stream_url, before_options="-reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5", options="-vn")
    vc.play(source, after=lambda e: after_playing(e))
    await ctx.send(f"I'm playing: **{title}**\n{video_url}")

# Help command
# ...existing code...

# Command to display custom help message
@bot.command(name='djhelp')
async def custom_help(ctx):
    help_message = """
**PakoDJ Bot Commands:**
- `!play` - Plays a song searched by keywords or link (if a song is currently playing, adds the searched song in a queue).
- `!skip` - Stops the current song and plays the next one in the queue.
- `!pause` - Pauses the currently playing track.
- `!resume` - Resumes the paused track.
- `!viewQueue` - Shows the tracks that are queued.
- `!stop` - Stops the playback and disconnects the bot from the voice channel.
    """
    await ctx.send(help_message)

# Command to reproduce audio
@bot.command()
async def play(ctx, *, query: str):
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

            if ctx.voice_client and ctx.voice_client.is_playing():
                queue.append({'ctx': ctx, 'url': stream_url, 'title': title, 'video_url': video_url})
                await ctx.send(f"Track added to queue: **{title}**\n{video_url}")
            else:
                await play_audio(ctx, stream_url, title, video_url)
        else:
            await ctx.send("You have to be in a voice channel to use this command!")
    except Exception as e:
        await ctx.send(f"Error: {e}")
        print(f"Error: {e}")

# Command to skip current track
@bot.command()
async def skip(ctx):
    if ctx.voice_client and ctx.voice_client.is_playing():
        ctx.voice_client.stop()
        if queue:
            next_track = queue.pop(0)
            await play_audio(next_track['ctx'], next_track['url'], next_track['title'], next_track['video_url'])
        else:
            await ctx.send("Queue's empty. No track to play.")
    else:
        await ctx.send("No track playing.")

# Command to pause current track
@bot.command()
async def pause(ctx):
    if ctx.voice_client and ctx.voice_client.is_playing():
        ctx.voice_client.pause()
        await ctx.send("Track paused.")
    else:
        await ctx.send("No track playing.")

# Command to resume current track
@bot.command()
async def resume(ctx):
    if ctx.voice_client and ctx.voice_client.is_paused():
        ctx.voice_client.resume()
        await ctx.send("Track resumed.")
    else:
        await ctx.send("No paused track.")

@bot.command()
async def viewQueue(ctx):
    if queue:
        queue_message = "**Queue:**\n"
        for i, track in enumerate(queue, 1):
            queue_message += f"{i}. {track['title']} ({track['video_url']})\n"
        await ctx.send(queue_message)
    else:
        await ctx.send("Queue's empty.")

# Command to stop playback and disconnect PakoDJ
@bot.command()
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