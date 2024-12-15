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
        print(f"Errore nel recuperare il flusso audio: {e}")
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
        print(f"Errore nella ricerca su YouTube: {e}")
        return None, None, None

# Callback function to manage audio reproduction errors
def after_playing(error):
    if error:
        print(f"Errore durante la riproduzione: {error}")
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
    await ctx.send(f"Sto riproducendo: **{title}**\n{video_url}")

# Command to reproduce audio
@bot.command()
async def play(ctx, *, query: str):
    try:
        if ctx.author.voice:
            # Ottieni il link del flusso audio, il titolo e il link del video
            if query.startswith("http://") or query.startswith("https://"):
                stream_url, title, video_url = get_audio_stream_url(query)
            else:
                stream_url, title, video_url = search_youtube(query)

            if stream_url is None:
                await ctx.send("Impossibile recuperare il flusso audio.")
                return

            if ctx.voice_client and ctx.voice_client.is_playing():
                queue.append({'ctx': ctx, 'url': stream_url, 'title': title, 'video_url': video_url})
                await ctx.send(f"Traccia aggiunta alla coda: **{title}**\n{video_url}")
            else:
                await play_audio(ctx, stream_url, title, video_url)
        else:
            await ctx.send("Devi essere in un canale vocale per usare questo comando!")
    except Exception as e:
        await ctx.send(f"Errore: {e}")
        print(f"Errore: {e}")

# Command to skip current track
@bot.command()
async def skip(ctx):
    if ctx.voice_client and ctx.voice_client.is_playing():
        ctx.voice_client.stop()
        if queue:
            next_track = queue.pop(0)
            await play_audio(next_track['ctx'], next_track['url'], next_track['title'], next_track['video_url'])
        else:
            await ctx.send("La coda è vuota. Nessuna traccia da riprodurre.")
    else:
        await ctx.send("Nessuna traccia in riproduzione.")

# Command to pause current track
@bot.command()
async def pause(ctx):
    if ctx.voice_client and ctx.voice_client.is_playing():
        ctx.voice_client.pause()
        await ctx.send("Traccia messa in pausa.")
    else:
        await ctx.send("Nessuna traccia in riproduzione.")

# Command to resume current track
@bot.command()
async def resume(ctx):
    if ctx.voice_client and ctx.voice_client.is_paused():
        ctx.voice_client.resume()
        await ctx.send("Traccia ripresa.")
    else:
        await ctx.send("Nessuna traccia in pausa.")

# Command to stop reproduction and to disconnect PakoDJ
@bot.command()
async def stop(ctx):
    try:
        if ctx.voice_client is not None:
            await ctx.voice_client.disconnect()
            await ctx.send("Riproduzione fermata e bot disconnesso dal canale vocale.")
        else:
            await ctx.send("Il bot non è in un canale vocale.")
    except Exception as e:
        await ctx.send(f"Errore: {e}")
        print(f"Errore: {e}")

# Event to log bot's successfull connection on console
@bot.event
async def on_ready():
    print(f'Bot connesso come {bot.user.name}')

bot.run(os.getenv('token'))