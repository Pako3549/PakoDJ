import os
import discord
from discord.ext import commands
import yt_dlp
import re
import asyncio
import time
import uuid
import random
from discord import FFmpegPCMAudio
from dotenv import load_dotenv
import spotipy
import shutil
from spotipy.oauth2 import SpotifyClientCredentials
import ipaddress
import socket
from urllib.parse import urlparse
import db as database

# Load environmental variables
load_dotenv()

# Log JS runtime availability for yt-dlp
deno_path = shutil.which("deno")
print(f"Deno runtime: {deno_path if deno_path else 'NOT FOUND'}")

# Extract headers from yt-dlp info entries (best-effort)
def _extract_http_headers(info):
    if not info:
        return None
    if isinstance(info, dict):
        headers = info.get('http_headers')
        if headers:
            return headers
        # Sometimes headers live on requested_formats or formats entries
        for key in ('requested_formats', 'formats'):
            formats = info.get(key) or []
            if formats:
                fmt_headers = formats[0].get('http_headers')
                if fmt_headers:
                    return fmt_headers
    return None

# Initialize Spotify client (optional)
spotify_client = None
try:
    client_id = os.getenv('SPOTIFY_CLIENT_ID')
    client_secret = os.getenv('SPOTIFY_CLIENT_SECRET')
    if client_id and client_secret:
        client_credentials_manager = SpotifyClientCredentials(client_id=client_id, client_secret=client_secret)
        spotify_client = spotipy.Spotify(client_credentials_manager=client_credentials_manager)
        print("Spotify integration enabled")
    else:
        print("Spotify credentials not found - Spotify URLs will not be supported")
except Exception as e:
    print(f"Error initializing Spotify client: {e}")
    spotify_client = None

# Intents and bot configs
intents = discord.Intents.default()
intents.messages = True
intents.message_content = True

bot = commands.Bot(command_prefix="!", intents=intents)

# Prefer Android client (no external cookie file is used anymore)
YDL_BASE_HEADERS = {
    "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": "https://www.youtube.com/",
}
YDL_CLIENTS = {"player_client": ["android"], "innertube_client": ["android"]}
YDL_FORMAT = "bestaudio/best"
bot.remove_command("help")

# Per-guild runtime state. Queues and history live in PostgreSQL (see db.py)
# and survive restarts; only "live" playback data stays in memory.
server_playback_info = {}
server_locks = {}

# Function to get playback information for a server
def get_server_info(guild_id):
    if guild_id not in server_playback_info:
        server_playback_info[guild_id] = {
            'current_track': None,
            'loading_task': None  # Background playlist loading task
        }
    return server_playback_info[guild_id]

# Function to lock queue while stopping track
def get_server_lock(guild_id):
    if guild_id not in server_locks:
        server_locks[guild_id] = asyncio.Lock()
    return server_locks[guild_id]

# ---------------------------------------------------------------------------
# URL safety guard (SSRF / local-file access prevention)
# ---------------------------------------------------------------------------
# User-supplied strings can reach yt-dlp. Restrict them to plain http(s)
# URLs pointing at public hosts: no other scheme (file://, ftp://, ...) and
# no host that is private/loopable from inside the Docker network.
_BLACKLISTED_HOSTS = {
    'localhost', 'host.docker.internal', 'metadata',
    'metadata.google.internal', 'metadata.azure.internal',
}

def is_safe_http_url(url):
    if not url or not (url.startswith('http://') or url.startswith('https://')):
        return False
    try:
        host = (urlparse(url).hostname or '').strip().lower().rstrip('.')
    except Exception:
        return False
    if not host or host in _BLACKLISTED_HOSTS:
        return False
    if '.' not in host:
        return False  # single-label host = internal Docker service name (db, redis, ...)
    try:
        ip = ipaddress.ip_address(host)
        if not ip.is_global:
            return False  # loopback/private/link-local/ULA targets
    except ValueError:
        # Domain name: resolve it right here and verify EVERY resolved address,
        # so a public-looking hostname can't redirect into the private network
        try:
            infos = socket.getaddrinfo(host, None, type=socket.SOCK_STREAM)
        except OSError:
            return False
        for info in infos:
            try:
                addr = ipaddress.ip_address(info[4][0])
            except ValueError:
                return False
            if not addr.is_global:
                return False
    except Exception:
        return False
    return True

# Get audio function
def get_audio_stream_url(url):
    if not is_safe_http_url(url):
        print(f"Blocked unsafe URL for audio extraction: {url[:120]}")
        return None, None, None, None
    try:
        url = re.sub(r'&.*', '', url)
        ydl_opts = {
            'format': YDL_FORMAT,
            'quiet': True,
            'extractor_args': {'youtube': YDL_CLIENTS},
            'http_headers': YDL_BASE_HEADERS,
            'js_runtimes': {'deno': {'path': '/usr/local/bin/deno'}},
        }
        
        # Special handling for SoundCloud - use RAM disk to avoid SD card wear
        if is_soundcloud_url(url):
            # Use /dev/shm (RAM disk) instead of /tmp to avoid SD card writes
            
            # Try to use RAM disk first, fallback to /tmp if not available
            ram_dir = '/dev/shm' if os.path.exists('/dev/shm') and os.access('/dev/shm', os.W_OK) else '/tmp'
            
            ydl_opts.update({
                'format': 'bestaudio',
                'outtmpl': f'{ram_dir}/%(title)s.%(ext)s',
                'extractaudio': True,
                'audioformat': 'mp3',
                'audioquality': '128K',  # Reduced quality to save RAM/storage
                'concurrent_fragment_downloads': 4,  # Faster HLS download on the Pi
                'extractor_args': {'youtube': {'player_client': ['tv_embedded']}},
            })
            
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(url, download=True)
                if info is None:
                    raise ValueError("No information retrieved from URL")
                _remember_duration(info)
                # Return the downloaded file path instead of stream URL
                import glob
                downloaded_file = glob.glob(f"{ram_dir}/{info['title']}.*")
                if downloaded_file:
                    return downloaded_file[0], info['title'], info['webpage_url'], None
                else:
                    raise ValueError("Downloaded file not found")
        else:
            # Normal streaming for non-SoundCloud URLs
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(url, download=False)
                if info is None:
                    raise ValueError("No information retrieved from URL")
                _remember_duration(info)
                headers = _extract_http_headers(info)
                return info['url'], info['title'], info['webpage_url'], headers
                
    except Exception as e:
        print(f"Error retrieving audio stream for '{url}': {e}")
        return None, None, None, None

# Duration (seconds) per resolved webpage URL, stashed by the resolvers from
# the same yt-dlp info dict used for the stream; the now-playing mirror reads
# it for the web progress line. Capped: oldest entries drop at 512 keys.
_track_durations: dict[str, float] = {}


def _remember_duration(info):
    try:
        duration = info.get('duration')
        url = info.get('webpage_url')
        if duration and url:
            if len(_track_durations) >= 512:
                _track_durations.pop(next(iter(_track_durations)))
            _track_durations[url] = float(duration)
    except (ValueError, TypeError, AttributeError):
        pass


# Search on youtube using a query written by the user
def search_youtube(query):
    try:
        ydl_opts = {
            'format': YDL_FORMAT,
            'quiet': True,
            'default_search': 'ytsearch',
            'max_downloads': 1,
            'extractor_args': {'youtube': YDL_CLIENTS},
            'http_headers': YDL_BASE_HEADERS,
            'js_runtimes': {'deno': {'path': '/usr/local/bin/deno'}},
        }
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            print(f"Searching YouTube for: '{query}'")
            info = ydl.extract_info(query, download=False)
            if info and 'entries' in info and info['entries']:
                entry = info['entries'][0]
                print(f"Found YouTube video: {entry['title']}")
                _remember_duration(entry)
                headers = _extract_http_headers(entry)
                return entry['url'], entry['title'], entry['webpage_url'], headers
            else:
                print(f"No results found for query: '{query}'")
                return None, None, None, None
    except Exception as e:
        error_msg = str(e).lower()
        # Check for blocking errors
        if any(keyword in error_msg for keyword in ['sign in', 'authentication', 'challenge', 'n challenge']):
            print(f"Blocking error for '{query}': Skipping (authentication/challenge required)")
            # Raise special exception to stop fallback attempts
            raise BlockingError(f"Authentication or challenge required: {e}")
        else:
            print(f"Error searching on YouTube for '{query}': {e}")
        return None, None, None, None

# Custom exception for blocking errors
class BlockingError(Exception):
    pass

# Enhanced search with fallback queries
def search_youtube_with_fallback(original_query):
    try:
        # Try the original query first
        result = search_youtube(original_query)
        if result[0] is not None:
            return result
    except BlockingError:
        # Blocking error, don't try fallbacks
        print(f"Skipping fallback attempts for '{original_query}' due to blocking error")
        return None, None, None, None
    
    # Generate alternative queries with random variations
    fallback_queries = []
    
    if ' - ' in original_query:
        # Try without the dash separator
        fallback_queries.append(original_query.replace(' - ', ' '))
        
        # Try reversing artist and title
        parts = original_query.split(' - ')
        if len(parts) == 2:
            fallback_queries.append(f"{parts[1]} {parts[0]}")
            # Also try with just title
            fallback_queries.append(parts[1])
    
    # Try adding common keywords that might help (randomize order)
    keywords = ['official', 'music video', 'audio', 'lyrics', 'video']
    random.shuffle(keywords)
    for keyword in keywords[:3]:  # Use only 3 random keywords
        fallback_queries.append(f"{original_query} {keyword}")
    
    # Try each fallback query
    for query in fallback_queries:
        try:
            print(f"Trying fallback search: '{query}'")
            result = search_youtube(query)
            if result[0] is not None:
                return result
        except BlockingError:
            # Stop all fallback attempts on blocking error
            print(f"Blocking error encountered, stopping all fallback attempts")
            return None, None, None, None
    
    print(f"All search attempts failed for: '{original_query}'")
    return None, None, None, None

# Function to check if URL is a Spotify URL
def is_spotify_url(url):
    spotify_patterns = [
        r'spotify\.com/(track|album|playlist|artist)/',
        r'open\.spotify\.com/(intl-[a-z]{2}/)?(track|album|playlist|artist)/',
        r'spotify://track/',
        r'spotify://album/',
        r'spotify://playlist/',
        r'spotify://artist/'
    ]
    return any(re.search(pattern, url) for pattern in spotify_patterns)

# Function to extract all tracks from Spotify playlist or album
def get_spotify_tracks_list(url):

    if not spotify_client:
        print("Spotify client not initialized - check SPOTIFY_CLIENT_ID and SPOTIFY_CLIENT_SECRET environment variables")
        return None
    
    try:
        print(f"Processing Spotify URL for multiple tracks: {url}")
        tracks_list = []
        collection_name = ""
        
        if 'album/' in url:
            album_id = url.split('album/')[-1].split('?')[0].split('&')[0]
            print(f"Extracted album ID: {album_id}")
            
            album = spotify_client.album(album_id)
            collection_name = album['name']
            
            for track in album['tracks']['items']:
                artist_names = ', '.join([artist['name'] for artist in track['artists']])
                search_query = f"{artist_names} - {track['name']}"
                tracks_list.append(search_query)
            
            print(f"Found {len(tracks_list)} tracks in album '{collection_name}'")
            return tracks_list, collection_name, 'album'
                
        elif 'playlist/' in url:
            playlist_id = url.split('playlist/')[-1].split('?')[0].split('&')[0]
            print(f"Extracted playlist ID: {playlist_id}")
            
            playlist = spotify_client.playlist(playlist_id)
            collection_name = playlist['name']
            
            # Handle paginated results
            results = playlist['tracks']
            tracks = results['items']
            
            while results['next']:
                results = spotify_client.next(results)
                tracks.extend(results['items'])
            
            for item in tracks:
                track = item['track']
                if track and track['name']:  # Skip null/unavailable tracks
                    artist_names = ', '.join([artist['name'] for artist in track['artists']])
                    search_query = f"{artist_names} - {track['name']}"
                    tracks_list.append(search_query)
            
            print(f"Found {len(tracks_list)} tracks in playlist '{collection_name}'")
            return tracks_list, collection_name, 'playlist'
        
        return None
        
    except Exception as e:
        print(f"Error extracting tracks from Spotify URL '{url}': {e}")
        import traceback
        print(f"Full traceback: {traceback.format_exc()}")
        return None

# Function to extract Spotify track information and search on YouTube
def get_spotify_track_info(url):
    if not spotify_client:
        print("Spotify client not initialized - check SPOTIFY_CLIENT_ID and SPOTIFY_CLIENT_SECRET environment variables")
        return None, None, None, None
    
    try:
        print(f"Processing Spotify URL: {url}")
        
        # Extract track ID from URL
        if 'track/' in url:
            track_id = url.split('track/')[-1].split('?')[0].split('&')[0]
            print(f"Extracted track ID: {track_id}")
            
            track = spotify_client.track(track_id)
            
            # Create search query for YouTube
            artist_names = ', '.join([artist['name'] for artist in track['artists']])
            search_query = f"{artist_names} - {track['name']}"
            
            print(f"Spotify track found: {track['name']} by {artist_names}")
            print(f"Searching YouTube for: {search_query}")
            return search_youtube_with_fallback(search_query)
        else:
            print(f"Unsupported Spotify URL type (use get_spotify_tracks_list for playlists/albums): {url}")
            return None, None, None, None
                    
    except Exception as e:
        print(f"Error processing Spotify URL '{url}': {e}")
        import traceback
        print(f"Full traceback: {traceback.format_exc()}")
        return None, None, None, None

# Function to check if URL is a SoundCloud URL
def is_soundcloud_url(url):
    soundcloud_patterns = [
        r'soundcloud\.com/',
        r'snd\.sc/',
        r'm\.soundcloud\.com/'
    ]
    return any(re.search(pattern, url) for pattern in soundcloud_patterns)

# Enhanced function to get audio stream URL (supports Spotify and SoundCloud)
def get_enhanced_audio_info(query):
    print(f"Processing query: {query}")
    
    if query.startswith("http://") or query.startswith("https://"):
        if is_spotify_url(query):
            print("Detected Spotify URL")
            return get_spotify_track_info(query)
        else:
            print("Detected direct URL (YouTube/SoundCloud/etc.)")
            return get_audio_stream_url(query)
    else:
        print("Detected search query - searching YouTube")
        return search_youtube_with_fallback(query)

# ---------------------------------------------------------------------------
# Non-blocking resolver + caches
# ---------------------------------------------------------------------------
# The bot can serve many servers at once: a yt-dlp search blocks for seconds,
# so every resolution MUST run in the executor and must never be repeated for
# queries already resolved recently (loop iterations, playlist reloads,
# re-plays of the same song in different servers).
SEARCH_CACHE_TTL = 6 * 3600   # search/query results stay valid for hours
STREAM_CACHE_TTL = 10 * 60    # stream URLs expire after a few more hours

search_cache = {}  # query -> (timestamp, title, webpage_url)
stream_cache = {}  # webpage_url -> (timestamp, stream_url, http headers)

def _cache_get(cache, key, ttl):
    entry = cache.get(key)
    if entry is not None:
        if time.time() - entry[0] < ttl:
            return entry
        cache.pop(key, None)
    return None

def _cache_fit(cache):
    if len(cache) > 1024:
        cache.clear()

# Resolve a query string (search text, YouTube/Spotify/SoundCloud URL) to a
# playable stream, without ever blocking the event loop.
async def resolve_audio_info_async(query):
    if not query:
        return None, None, None, None
    loop = asyncio.get_event_loop()

    title = None
    video_url = None
    hit = _cache_get(search_cache, query, SEARCH_CACHE_TTL)
    if hit:
        _, title, video_url = hit
    else:
        stream_url, title, video_url, headers = await loop.run_in_executor(
            None, get_enhanced_audio_info, query
        )
        if stream_url is None or video_url is None:
            return stream_url, title, video_url, headers
        if stream_url.startswith('http'):
            # Warm the stream cache too (works only for http streams; this
            # skips SoundCloud temp files, which are deleted after playback)
            stream_cache[video_url] = (time.time(), stream_url, headers)
        _cache_fit(search_cache)
        search_cache[query] = (time.time(), title, video_url)
        return stream_url, title, video_url, headers

    # Cached search hit: still need a playable stream URL
    stream_hit = _cache_get(stream_cache, video_url, STREAM_CACHE_TTL)
    if stream_hit:
        _, stream_url, headers = stream_hit
        return stream_url, title, video_url, headers

    stream_url, title2, video_url2, headers = await loop.run_in_executor(
        None, get_audio_stream_url, video_url
    )
    if stream_url and stream_url.startswith('http'):
        _cache_fit(stream_cache)
        stream_cache[video_url] = (time.time(), stream_url, headers)
    return stream_url, title2 or title, video_url2 or video_url, headers

# Edit a Discord message ignoring harmless failures (deleted, permissions, ...)
async def safe_edit(msg, content):
    try:
        await msg.edit(content=content)
    except Exception:
        pass

# Async function to load playlist tracks in background
async def load_playlist_tracks_async(ctx, tracks_list, collection_name, collection_type, collection_id, status_msg):
    server_info = get_server_info(ctx.guild.id)
    lock = get_server_lock(ctx.guild.id)
    
    tracks_loaded = 0
    failed_tracks = 0
    first_track_played = False
    
    for i, search_query in enumerate(tracks_list):
        try:
            # Non-blocking resolution (executor + caches)
            stream_url, title, video_url, headers = await resolve_audio_info_async(search_query)
            
            if stream_url is None:
                print(f"Failed to find track: {search_query}")
                failed_tracks += 1
                # Update progress even for failed tracks
                await safe_edit(status_msg, f"Loading {collection_type} **{collection_name}**: {i + 1}/{len(tracks_list)} tracks...")
                continue
            
            async with lock:
                # If it's the first track and nothing is playing, start immediately
                idle = not (ctx.voice_client and (ctx.voice_client.is_playing() or ctx.voice_client.is_paused()))
                if not first_track_played and idle:
                    await play_audio(ctx.guild, ctx.channel, stream_url, title, video_url,
                                     headers=headers, collection_id=collection_id,
                                     voice_channel=ctx.author.voice.channel)
                    first_track_played = True
                    tracks_loaded += 1
                else:
                    # Add to persistent queue
                    await database.enqueue_track(
                        guild_id=ctx.guild.id,
                        title=title,
                        webpage_url=video_url,
                        search_query=search_query,
                        collection_id=collection_id,
                        collection_type=collection_type,
                        requester_id=ctx.author.id,
                        channel_id=ctx.channel.id
                    )
                    tracks_loaded += 1
            
            # Update progress message
            await safe_edit(status_msg, f"Loading {collection_type} **{collection_name}**: {i + 1}/{len(tracks_list)} tracks...")
            
            # Rate limiting to avoid YouTube throttling (increased delay)
            await asyncio.sleep(0.5)
            
        except asyncio.CancelledError:
            # Task was cancelled (skip all was called)
            print(f"Playlist loading cancelled after {tracks_loaded} tracks")
            await safe_edit(status_msg, f"Loading cancelled. Added {tracks_loaded} tracks from {collection_type} **{collection_name}**.")
            return
        except Exception as e:
            # Log error but continue with next track
            print(f"Unexpected error loading track '{search_query}': {e}")
            failed_tracks += 1
            # Update progress even on error
            await safe_edit(status_msg, f"Loading {collection_type} **{collection_name}**: {i + 1}/{len(tracks_list)} tracks...")
            continue
    
    # Final status update
    result_msg = f"Added {tracks_loaded} tracks from {collection_type} **{collection_name}** to queue."
    if failed_tracks > 0:
        result_msg += f" ({failed_tracks} track(s) not found on YouTube)"
    await safe_edit(status_msg, result_msg)
    await database.complete_collection(collection_id, 'complete')
    
    # Clear loading task reference when done
    server_info['loading_task'] = None

# Callback function to manage audio reproduction errors
def after_playing(error, guild_id):
    if error:
        print(f"Error during playback: {error}")
    server_info = get_server_info(guild_id)
    
    # Clean up SoundCloud temporary files
    current_track = server_info.get('current_track')
    if current_track and 'temp_file' in current_track:
        try:
            import os
            if os.path.exists(current_track['temp_file']):
                os.remove(current_track['temp_file'])
                print(f"Cleaned up temporary file: {current_track['temp_file']}")
        except Exception as e:
            print(f"Error cleaning up temporary file: {e}")
    
    async def next_track():
        lock = get_server_lock(guild_id)
        async with lock:
            # The previous mirror (if any) is stale during the resolution gap
            await database.clear_now_playing(guild_id)
            while True:
                row = await database.claim_next_track(guild_id)
                if row is None:
                    server_info['current_track'] = None
                    server_info.pop('paused_at', None)
                    try:
                        from api import notify_playback
                        notify_playback(guild_id)
                    except ImportError:
                        pass
                    break
                played = await play_queued_row(guild_id, row)
                if played:
                    break
                # Row failed to resolve: keep claiming until one plays or queue is empty
    asyncio.run_coroutine_threadsafe(next_track(), bot.loop)

# Pick a text channel to announce playback (falls back to the first available)
def get_notify_channel(guild, channel_id):
    # Tracks queued from the web panel carry no text channel: they stay
    # silent in Discord on purpose (no announcement, no error text there).
    if not channel_id:
        return None
    channel = bot.get_channel(channel_id)
    if channel is not None and hasattr(channel, 'send'):
        return channel
    for text_channel in guild.text_channels:
        return text_channel
    return None

# Resolve and play a queued row.
# Stream URLs are re-resolved just-in-time because they expire after a few
# hours, and it makes the persisted queue shippable across restarts.
async def play_queued_row(guild_id, row, autopilot=True, voice_channel=None):
    guild = bot.get_guild(guild_id)
    if guild is None:
        return False
    
    if autopilot and (guild.voice_client is None or not guild.voice_client.is_connected()):
        # Bot is no longer in voice: nobody can hear the queue, wipe it
        await database.clear_queue(guild_id)
        await database.clear_now_playing(guild_id)
        get_server_info(guild_id)['current_track'] = None
        try:
            from api import notify_playback
            notify_playback(guild_id)
        except ImportError:
            pass
        return True
    
    try:
        # Prefer re-running the original search/query (keeps Spotify fallbacks
        # and hits the resolution caches for loop iterations / re-plays)
        stream_url, title, video_url, headers = await resolve_audio_info_async(
            row['search_query'] or row['webpage_url']
        )
        # Fallback on the canonical webpage URL
        if stream_url is None:
            stream_url, title, video_url, headers = await resolve_audio_info_async(row['webpage_url'])
    except Exception as e:
        print(f"Error resolving queued track {row['id']}: {e}")
        stream_url = None
    
    if stream_url is None:
        channel = get_notify_channel(guild, row['channel_id'])
        if channel:
            try:
                await channel.send(f"Couldn't load the next track **{row['title']}**, skipping it.")
            except Exception:
                pass
        print(f"Queued track {row['id']} failed to resolve, skipping: {row['title']}")
        return False
    
    notify_channel = get_notify_channel(guild, row['channel_id'])
    return await play_audio(
        guild, notify_channel, stream_url, title or row['title'],
        video_url or row['webpage_url'], headers=headers,
        collection_id=row['collection_id'], voice_channel=voice_channel
    )

# Function to play song track in voice channel
async def play_audio(guild, notify_channel, stream_url, title, video_url, headers=None, collection_id=None, voice_channel=None):
    try:
        vc = guild.voice_client
        if (vc is None or not vc.is_connected()):
            if voice_channel is None:
                raise RuntimeError("Bot is not in a voice channel")
            vc = await voice_channel.connect()

        # Enhanced options for SoundCloud HLS support
        before_options = "-reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5"
        ffmpeg_options = "-vn"
        
        # For SoundCloud files (now local files), use simple options
        is_temp_file = stream_url.startswith('/dev/shm/') or stream_url.startswith('/tmp/')
        if 'sndcdn.com' in video_url or is_temp_file:
            # Simple options for local files
            before_options = ""
            ffmpeg_options = "-vn"
        elif headers:
            # Prefer minimal headers to avoid 403s from googlevideo
            user_agent = headers.get('User-Agent') or headers.get('user-agent')
            referer = headers.get('Referer') or headers.get('referer') or video_url
            origin = headers.get('Origin') or headers.get('origin') or "https://www.youtube.com"
            header_lines = []
            if referer:
                header_lines.append(f"Referer: {referer}")
            if origin:
                header_lines.append(f"Origin: {origin}")
            if header_lines:
                headers_str = "\r\n".join(header_lines) + "\r\n"
                headers_str = headers_str.replace('"', '\\"')
                before_options = f'-headers "{headers_str}" {before_options}'
            if user_agent:
                safe_ua = user_agent.replace('"', '\\"')
                before_options = f'-user_agent "{safe_ua}" {before_options}'
        
        vc.play(FFmpegPCMAudio(source=stream_url, before_options=before_options, options=ffmpeg_options), after=lambda e: after_playing(e, guild.id))
        if notify_channel:
            await notify_channel.send(f"I'm playing: **{title}**\n{video_url}")

        # Add to persistent history
        await database.add_history(guild.id, title, video_url)

        # Set current track with temp file info for cleanup
        server_info = get_server_info(guild.id)
        server_info.pop('paused_at', None)  # fresh track: progress restarts
        track_info = {'title': title, 'video_url': video_url}
        if is_temp_file:
            track_info['temp_file'] = stream_url
        if headers:
            track_info['headers'] = headers
        if collection_id:
            track_info['collection_id'] = collection_id
        server_info['current_track'] = track_info
        # Real-time mirror for the web API (see api.py); duration feeds the
        # web progress line when the resolver managed to capture it.
        await database.set_now_playing(
            guild.id, title, video_url, duration=_track_durations.get(video_url)
        )
        try:
            from api import notify_playback
            notify_playback(guild.id)
        except ImportError:
            pass
        return True
    except Exception as e:
        if notify_channel:
            try:
                await notify_channel.send(f"Error: {e}")
            except Exception:
                pass
        print(f"Error during playback: {e}")
        return False

# Command to show help
@bot.command(help="Shows available commands.")
async def help(ctx):
    help_message = """
!join - Joins user's voice channel.
!play <query/url> - Plays an audio track searched by keywords, YouTube link, Spotify URL, or SoundCloud URL (playlists/albums supported).
!repeat - Plays a song in loop for n times. Supports YouTube, Spotify, and SoundCloud URLs (use !skip all to stop the loop).
!skip - Stops current audio track and plays the next one in the queue.
!skip all - Skips the current track and the loop; then it plays the next track in queue.
!pause - Pauses currently playing audio track.
!resume - Resumes paused audio track.
!track - Shows current audio track.
!queue - Shows queued audio tracks.
!history - Shows previously played audio tracks.
!stop - Stops playback and disconnects bot from voice channel.
    """
    await ctx.send(help_message)

# Command to display current audio track
@bot.command(help="Shows current audio track.")
async def track(ctx):
    server_info = get_server_info(ctx.guild.id)
    if server_info['current_track']:
        await ctx.send(
            f"Current track: **{server_info['current_track']['title']}**\n"
            f"{server_info['current_track']['video_url']}"
        )
    else:
        await ctx.send("No track is currently playing.")


# Command to join the user's voice channel
@bot.command(help = "Joins user's voice channel.")
async def join(ctx):
    try:
        if ctx.voice_client and ctx.voice_client.is_connected():
            await ctx.send("Already in a voice channel.")
        elif ctx.author.voice:
            channel = ctx.author.voice.channel
            await channel.connect()
            await ctx.send(f"Joined {channel}")
        else:
            await ctx.send("You need to be in a voice channel to use this command.")
    except Exception as e:
        print(f"Error in join: {e}")

# Command to reproduce audio
@bot.command(help = "Plays an audio track searched by keywords, YouTube link, Spotify URL, or SoundCloud URL. Supports full Spotify playlists and albums (if a song is currently playing, adds the searched song in a queue).")
async def play(ctx, *, query: str):
    try:
        if ctx.author.voice:
            # Check if it's a Spotify playlist or album
            if is_spotify_url(query) and ('playlist/' in query or 'album/' in query):
                # Extracting the track list hits the Spotify API: run it in
                # the executor so other servers' commands are not frozen
                loop = asyncio.get_event_loop()
                tracks_result = await loop.run_in_executor(None, get_spotify_tracks_list, query)
                
                if tracks_result is None:
                    error_msg = "Unable to retrieve playlist/album."
                    if not spotify_client:
                        error_msg += "\nPossible issues:\n- Spotify credentials not configured\n- Check SPOTIFY_CLIENT_ID and SPOTIFY_CLIENT_SECRET environment variables"
                    await ctx.send(error_msg)
                    return
                
                tracks_list, collection_name, collection_type = tracks_result
                
                if not tracks_list:
                    await ctx.send(f"No tracks found in {collection_type}: {collection_name}")
                    return
                
                # Unique collection id groups the whole playlist/album for !skip all
                collection_id = str(uuid.uuid4())
                await database.create_collection(collection_id, ctx.guild.id, collection_name, collection_type, len(tracks_list))
                
                # Create status message
                status_msg = await ctx.send(f"Loading {collection_type} **{collection_name}**: 0/{len(tracks_list)} tracks...")
                
                # Start background task to load tracks and save reference
                server_info = get_server_info(ctx.guild.id)
                server_info['loading_task'] = asyncio.create_task(
                    load_playlist_tracks_async(ctx, tracks_list, collection_name, collection_type, collection_id, status_msg)
                )
                
                return
            
            # Handle single track (original logic)
            # Send a "searching" message for Spotify URLs to give user feedback
            if is_spotify_url(query):
                search_msg = await ctx.send("Searching Spotify track on YouTube...")
            
            # Non-blocking resolution so a slow search doesn't freeze the
            # whole bot (other servers keep responding meanwhile)
            stream_url, title, video_url, headers = await resolve_audio_info_async(query)

            if stream_url is None:
                error_msg = "Unable to retrieve audio stream."
                if is_spotify_url(query):
                    if not spotify_client:
                        error_msg += "\nPossible issues:\n- Spotify credentials not configured\n- Check SPOTIFY_CLIENT_ID and SPOTIFY_CLIENT_SECRET environment variables"
                    else:
                        error_msg += "\nPossible issues:\n- Track not found on YouTube\n- Track may be region-locked\n- Try searching manually with artist and song name"
                
                # Delete the searching message if it exists
                if is_spotify_url(query) and 'search_msg' in locals():
                    try:
                        await search_msg.delete()
                    except:
                        pass
                
                await ctx.send(error_msg)
                return
            
            # Delete the searching message if it exists and we found the track
            if is_spotify_url(query) and 'search_msg' in locals():
                try:
                    await search_msg.delete()
                except:
                    pass

            async with get_server_lock(ctx.guild.id):
                # A paused player occupies the channel too: enqueue instead of
                # silently replacing the paused track
                occupied = ctx.voice_client and (ctx.voice_client.is_playing() or ctx.voice_client.is_paused())
                if occupied:
                    await database.enqueue_track(
                        guild_id=ctx.guild.id,
                        title=title,
                        webpage_url=video_url,
                        search_query=query,
                        requester_id=ctx.author.id,
                        channel_id=ctx.channel.id
                    )
                    await ctx.send(f"Track added to queue: **{title}**\n{video_url}")
                else:
                    await play_audio(ctx.guild, ctx.channel, stream_url, title, video_url,
                                     headers=headers, voice_channel=ctx.author.voice.channel)
        else:
            await ctx.send("You have to be in a voice channel to use this command!")
    except Exception as e:
        await ctx.send(f"Error: {e}")
        print(f"Error: {e}")

# Command to play a track in loop
@bot.command(help="Plays an audio track in loop for n times. Supports YouTube, Spotify, and SoundCloud URLs (use `!skip all` to stop).")
async def repeat(ctx, n: int, *, query: str):
    try:
        if ctx.author.voice:
            if n < 1:
                await ctx.send("Please choose a number greater than 0.")
                return

            # Non-blocking resolution (executor + caches: the loop iterations
            # re-use the query resolution without new YouTube searches)
            stream_url, title, video_url, headers = await resolve_audio_info_async(query)

            if stream_url is None:
                await ctx.send("Unable to retrieve audio stream.")
                return

            # All loop iterations share one collection id for !skip all
            collection_id = str(uuid.uuid4())
            async with get_server_lock(ctx.guild.id):
                occupied = ctx.voice_client and (ctx.voice_client.is_playing() or ctx.voice_client.is_paused())
                async def enqueue_loop(i):
                    await database.enqueue_track(
                        guild_id=ctx.guild.id,
                        title=f"{title} (loop {i}/{n})",
                        webpage_url=video_url,
                        search_query=query,
                        collection_id=collection_id,
                        collection_type='loop',
                        requester_id=ctx.author.id,
                        channel_id=ctx.channel.id
                    )
                if occupied:
                    for i in range(1, n + 1):
                        await enqueue_loop(i)
                    await ctx.send(f"Track added to queue: **{title}** (loop x{n})\n{video_url}")
                else:
                    # Play the first iteration now, queue the rest as a group
                    for i in range(2, n + 1):
                        await enqueue_loop(i)
                    await play_audio(ctx.guild, ctx.channel, stream_url, f"{title} (loop 1/{n})", video_url,
                                     headers=headers, collection_id=collection_id,
                                     voice_channel=ctx.author.voice.channel)
        else:
            await ctx.send("You have to be in a voice channel to use this command!")
    except Exception as e:
        await ctx.send(f"Error: {e}")
        print(f"Error: {e}")

# Command to skip current track
@bot.command(help = "Stops current audio track and plays the next one in the queue. Use '!skip all' to skip the current repeat group.")
async def skip(ctx, arg: str = None):
    server_info = get_server_info(ctx.guild.id)
    async with get_server_lock(ctx.guild.id):
        if ctx.voice_client and ctx.voice_client.is_playing():
            if arg == "all":
                current = server_info.get('current_track') or {}
                collection_id = current.get('collection_id')
                
                # Cancel background playlist loading if still active
                task = server_info.get('loading_task')
                if task and not task.done():
                    task.cancel()
                server_info['loading_task'] = None
                
                ctx.voice_client.stop()
                
                if collection_id:
                    removed = await database.clear_collection(ctx.guild.id, collection_id)
                    if removed > 0:
                        await ctx.send(f"Skipped current track and {removed} more from the same playlist/album/loop.")
                    else:
                        await ctx.send("Skipped current track (no other tracks from that playlist/album/loop).")
                else:
                    await ctx.send("Skipped current track (no playlist/loop found).")
            else:
                ctx.voice_client.stop()
                await ctx.send("Skipped current track.")
        else:
            # Nothing playing: resume a persisted queue (e.g. after a restart)
            async def resume_queue():
                row = await database.claim_next_track(ctx.guild.id)
                if row:
                    await ctx.send("Resuming the persisted queue.")
                    await play_queued_row(ctx.guild.id, row, autopilot=False, voice_channel=ctx.author.voice.channel)
                else:
                    server_info['current_track'] = None
                    await ctx.send("No track to play.")
            if ctx.author.voice:
                await resume_queue()
            else:
                pending = await database.count_queue(ctx.guild.id)
                if pending:
                    await ctx.send("There is a queued track but you must be in a voice channel to resume it.")
                else:
                    server_info['current_track'] = None
                    await ctx.send("No track to play.")

# Command to pause current track
@bot.command(help = "Pauses currently playing audio track.")
async def pause(ctx):
    if ctx.voice_client and ctx.voice_client.is_playing():
        ctx.voice_client.pause()
        # Freeze the progress marker at the pause instant (runtime only).
        get_server_info(ctx.guild.id)['paused_at'] = time.time()
        await ctx.send("Track paused.")
    else:
        await ctx.send("No track playing.")

# Command to resume current track
@bot.command(help = "Resumes paused audio track.")
async def resume(ctx):
    if ctx.voice_client and ctx.voice_client.is_paused():
        ctx.voice_client.resume()
        get_server_info(ctx.guild.id).pop('paused_at', None)
        await ctx.send("Track resumed.")
    else:
        await ctx.send("No paused track.")

# Command to show queued audio tracks
@bot.command(help = "Shows queued audio tracks.")
async def queue(ctx):
    rows = await database.list_queue(ctx.guild.id)
    total = await database.count_queue(ctx.guild.id)
    if not rows:
        await ctx.send("Queue's empty.")
        return
    queue_message = "**Queue:**\n"
    for row in rows:
        line = f"{row['id']}. {row['title']} ({row['webpage_url']})\n"
        if len(queue_message) + len(line) > 1900:
            break
        queue_message += line
    if total > len(rows):
        queue_message += f"...and {total - len(rows)} more."
    await ctx.send(queue_message)

# Command to show previously played audio tracks
@bot.command(help = "Shows previously played audio tracks.")
async def history(ctx):
    rows = await database.list_history(ctx.guild.id)
    if not rows:
        await ctx.send("No tracks have been played yet.")
        return
    history_message = "**Playback History (latest first):**\n"
    for i, row in enumerate(rows, 1):
        line = f"{i}. {row['title']} ({row['webpage_url']})\n"
        if len(history_message) + len(line) > 1900:
            break
        history_message += line
    await ctx.send(history_message)

# Command to stop playback and disconnect PakoDJ
@bot.command(help = "Stops playback and disconnects bot from voice channel.")
async def stop(ctx):
    try:
        server_info = get_server_info(ctx.guild.id)
        # Cancel background playlist loading if still active
        task = server_info.get('loading_task')
        if task and not task.done():
            task.cancel()
        server_info['loading_task'] = None
        server_info['current_track'] = None
        server_info.pop('paused_at', None)
        await database.clear_queue(ctx.guild.id)
        await database.clear_now_playing(ctx.guild.id)
        if ctx.voice_client and ctx.voice_client.is_connected():
            server_info['intentional_leave'] = True  # stay out: !stop means it
            await ctx.voice_client.disconnect()
            await ctx.send("Track stopped and bot disconnected.")
        else:
            await ctx.send("Bot is not in a voice channel.")
    except Exception as e:
        print(f"Error in stop: {e}")

# ---------------------------------------------------------------------------
# Web API (FastAPI inside the bot's event loop; never exposed by default)
# ---------------------------------------------------------------------------
_api_task = None

def _start_api_server():
    try:
        import uvicorn
        from api import create_api_app
    except ImportError as e:
        print(f"WARNING: FastAPI/uvicorn not installed - web API disabled ({e})")
        return None
    # Accounts are created via Discord OAuth login:
    #   DISCORD_CLIENT_ID + DISCORD_CLIENT_SECRET + API_PUBLIC_ORIGIN -> login flow
    #   DISCORD_ADMIN_IDS -> Discord IDs that get the admin flag
    class APIServer(uvicorn.Server):
        def install_signal_handlers(self):
            pass  # the bot process owns the signal handlers
    app = create_api_app(bot, resolve_audio_info_async, play_audio, play_queued_row, get_server_info, get_server_lock)
    config = uvicorn.Config(
        app,
        host=os.getenv('API_HOST', '0.0.0.0'),      # reachability is decided by published ports (none!)
        port=int(os.getenv('API_PORT', '8000')),
        log_level=os.getenv('API_LOG_LEVEL', 'warning'),
    )
    return APIServer(config)

# Event to log bot's successful connection on console (and init the DB)
_boot_mirror_cleaned = False


@bot.event
async def on_ready():
    global _api_task, _boot_mirror_cleaned
    try:
        await database.init_db()
    except Exception as e:
        print(f"WARNING: database initialisation failed ({e}); queue features unavailable")
    if not _boot_mirror_cleaned:
        _boot_mirror_cleaned = True
        # A previous run may have died mid-playback: the surviving mirror row
        # would show a stale "playing" state with an absurd elapsed time. The
        # bot is in no voice channel at boot, so the honest state is "nothing
        # playing" everywhere. Once per process: on_ready can fire again on
        # gateway reconnects and must not wipe a LIVE mirror then.
        try:
            await database.clear_now_playing_all()
        except Exception as e:
            print(f"WARNING: could not clear the playback mirror at boot ({e})")
    if _api_task is None:
        server = _start_api_server()
        if server is not None:
            _api_task = asyncio.create_task(server.serve())
            print(f"Web API listening on port {os.getenv('API_PORT', '8000')} (account auth, internal only)")
    print(f'Bot connected as {bot.user.name}')

# Drop runtime + persisted queue when the bot leaves a server entirely
@bot.event
async def on_guild_remove(guild):
    await database.clear_queue(guild.id)
    await database.clear_now_playing(guild.id)
    get_server_info(guild.id)['current_track'] = None

# Accept/Deny quick actions on the admin access-request DM: the buttons are
# plain custom_id conventions (reqdm:grant|deny:<id>) resolved by this raw
# listener, so they keep working after a restart without view registration.
# bot.listen keeps the default on_interaction dispatch (views, commands) intact.
@bot.listen("on_interaction")
async def _access_request_dm_buttons(interaction):
    if interaction.type != discord.InteractionType.component:
        return
    parts = ((interaction.data or {}).get("custom_id") or "").split(":")
    if len(parts) != 3 or parts[0] != "reqdm" or parts[1] not in ("grant", "deny"):
        return
    try:
        from api import DISCORD_ADMIN_IDS, resolve_access_request_from_dm
    except ImportError:
        await interaction.response.send_message("Web API module unavailable on this bot.", ephemeral=True)
        return
    if str(interaction.user.id) not in DISCORD_ADMIN_IDS:
        await interaction.response.send_message("Only bot admins may resolve access requests.", ephemeral=True)
        return
    try:
        request_id = int(parts[2])
    except ValueError:
        return
    actor = f"{interaction.user.name} (DM)"
    outcome = await resolve_access_request_from_dm(request_id, parts[1] == "grant", actor)
    if outcome == "granted":
        label, color = f"Accepted by {actor}", discord.Color.green()
    elif outcome == "denied":
        label, color = f"Denied by {actor}", discord.Color.red()
    elif outcome == "already":
        # Resolved elsewhere (web panel or another admin's DM copy): mark
        # THIS message with the same "by ..." line using the recorded
        # resolver, instead of scolding whoever clicked the stale button.
        row = await database.get_access_request(request_id)
        if row is None or row['status'] not in ('granted', 'denied'):
            label, color = "Request removed", discord.Color.greyple()
        else:
            verb = "Accepted" if row['status'] == 'granted' else "Denied"
            label = f"{verb} by {row['resolved_by']}" if row['resolved_by'] else verb
            color = discord.Color.green() if row['status'] == 'granted' else discord.Color.red()
    else:
        label, color = "Request removed", discord.Color.greyple()
    if interaction.message.embeds:
        embed = interaction.message.embeds[0]
        embed.colour = color
        embed.add_field(name="Decision", value=label, inline=False)
        await interaction.response.edit_message(embed=embed, view=None)
    else:
        await interaction.response.edit_message(content=f"{label}.", view=None)

# Voice loyalty: remember the last voice channel per guild so an unexpected
# drop (voice timeout, dead handshake, kick) can be recovered. A DELIBERATE
# leave (panel Stop, !stop, stale-handle cleanup) sets server_info's
# 'intentional_leave' flag first: then the bot stays out, no rejoin.
# Keep runtime state consistent if the bot leaves voice unexpectedly
@bot.event
async def on_voice_state_update(member, before, after):
    if member.id != bot.user.id or not member.guild:
        return
    server_info = get_server_info(member.guild.id)
    if after.channel is not None:
        # Joined (or moved): remember where it plays (and clear any leftover
        # intentional-leave flag so future drops are treated as drops).
        server_info['voice_channel_id'] = after.channel.id
        server_info.pop('intentional_leave', None)
        return
    if server_info.pop('intentional_leave', False):
        # Deliberate leave: stay out and clean up, no rejoin.
        task = server_info.get('loading_task')
        if task and not task.done():
            task.cancel()
        server_info['loading_task'] = None
        server_info['current_track'] = None
        server_info.pop('paused_at', None)
        await database.clear_now_playing(member.guild.id)
        try:
            from api import notify_playback
            notify_playback(member.guild.id)
        except ImportError:
            pass
        # The queue stays in the DB: '!skip' can resume it once back in voice
        return
    # UNEXPECTED leave: the connection died on its own. Clean up the mirror,
    # then go back to the last channel after a short grace period and resume
    # the persisted queue - the bot stays with the user, devotedly.
    channel_id = server_info.get('voice_channel_id')
    task = server_info.get('loading_task')
    if task and not task.done():
        task.cancel()
    server_info['loading_task'] = None
    server_info['current_track'] = None
    server_info.pop('paused_at', None)
    await database.clear_now_playing(member.guild.id)
    try:
        from api import notify_playback
        notify_playback(member.guild.id)
    except ImportError:
        pass
    channel = member.guild.get_channel(channel_id) if channel_id else None
    if channel is None:
        return

    async def rejoin():
        try:
            await channel.connect()
            print(f"Voice auto-rejoined #{channel.name} in {member.guild.name}")
            # The drop killed the audio mid-track: resume the persisted queue.
            if not (member.guild.voice_client and member.guild.voice_client.is_playing()):
                async with get_server_lock(member.guild.id):
                    row = await database.claim_next_track(member.guild.id)
                    if row is not None:
                        await play_queued_row(member.guild.id, row, autopilot=False)
        except Exception as e:
            print(f"Voice auto-rejoin failed in {member.guild.name}: {e}")

    await asyncio.sleep(3)
    await rejoin()

bot.run(os.getenv('token'))
