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
from spotipy.oauth2 import SpotifyClientCredentials

# Load environmental variables
load_dotenv()

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
bot.remove_command("help")

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
            'playback_history': [],
            'loading_task': None  # Track background loading task
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
        url = re.sub(r'&.*', '', url)
        ydl_opts = {
            'format': 'bestaudio/best',
            'quiet': True,
            'extractor_args': {'youtube': {'innertube_client': ['web', 'tv']}},
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
                'extractor_args': {'youtube': {'player_client': ['tv_embedded']}},
            })
            
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(url, download=True)
                if info is None:
                    raise ValueError("No information retrieved from URL")
                # Return the downloaded file path instead of stream URL
                import glob
                downloaded_file = glob.glob(f"{ram_dir}/{info['title']}.*")
                if downloaded_file:
                    return downloaded_file[0], info['title'], info['webpage_url']
                else:
                    raise ValueError("Downloaded file not found")
        else:
            # Normal streaming for non-SoundCloud URLs
            if os.path.isfile('youtube_cookies.txt'):
                ydl_opts['cookiefile'] = 'youtube_cookies.txt'
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
            'extractor_args': {'youtube': {'innertube_client': ['web', 'tv']}},
        }
        if os.path.isfile('youtube_cookies.txt'):
            ydl_opts['cookiefile'] = 'youtube_cookies.txt'
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            print(f"Searching YouTube for: '{query}'")
            info = ydl.extract_info(query, download=False)
            if info and 'entries' in info and info['entries']:
                entry = info['entries'][0]
                print(f"Found YouTube video: {entry['title']}")
                return entry['url'], entry['title'], entry['webpage_url']
            else:
                print(f"No results found for query: '{query}'")
                return None, None, None
    except Exception as e:
        error_msg = str(e).lower()
        # Check for blocking errors
        if any(keyword in error_msg for keyword in ['sign in', 'authentication', 'challenge', 'cookies required', 'n challenge']):
            print(f"Blocking error for '{query}': Skipping (authentication/challenge required)")
            # Raise special exception to stop fallback attempts
            raise BlockingError(f"Authentication or challenge required: {e}")
        else:
            print(f"Error searching on YouTube for '{query}': {e}")
        return None, None, None

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
        return None, None, None
    
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
            return None, None, None
    
    print(f"All search attempts failed for: '{original_query}'")
    return None, None, None

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
        return None, None, None
    
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
            return None, None, None
                    
    except Exception as e:
        print(f"Error processing Spotify URL '{url}': {e}")
        import traceback
        print(f"Full traceback: {traceback.format_exc()}")
        return None, None, None

# Async function to load playlist tracks in background
async def load_playlist_tracks_async(ctx, tracks_list, collection_name, collection_type, collection_id, status_msg):
    global track_counter
    server_info = get_server_info(ctx.guild.id)
    lock = get_server_lock(ctx.guild.id)
    
    tracks_loaded = 0
    failed_tracks = 0
    first_track_played = False
    
    for i, search_query in enumerate(tracks_list):
        try:
            # Run sync search in thread to avoid blocking
            loop = asyncio.get_event_loop()
            stream_url, title, video_url = await loop.run_in_executor(None, search_youtube_with_fallback, search_query)
            
            if stream_url is None:
                print(f"Failed to find track: {search_query}")
                failed_tracks += 1
                # Update progress even for failed tracks
                try:
                    await status_msg.edit(content=f"Loading {collection_type} **{collection_name}**: {i + 1}/{len(tracks_list)} tracks...")
                except:
                    pass
                continue
            
            async with lock:
                track_counter += 1
                track = {
                    'ctx': ctx,
                    'url': stream_url,
                    'title': title,
                    'video_url': video_url,
                    'track_number': track_counter,
                    'collection_id': collection_id  # Add collection ID for skip all
                }
                
                # If it's the first track and nothing is playing, start immediately
                if not first_track_played and (not ctx.voice_client or not ctx.voice_client.is_playing()):
                    await play_audio(ctx, stream_url, title, video_url)
                    first_track_played = True
                    tracks_loaded += 1
                else:
                    # Add to queue
                    server_info['audio_queue'].append([track])
                    tracks_loaded += 1
            
            # Update progress message
            try:
                await status_msg.edit(content=f"Loading {collection_type} **{collection_name}**: {i + 1}/{len(tracks_list)} tracks...")
            except:
                pass
            
            # Rate limiting to avoid YouTube throttling (increased delay)
            await asyncio.sleep(0.5)
            
        except asyncio.CancelledError:
            # Task was cancelled (skip all was called)
            print(f"Playlist loading cancelled after {tracks_loaded} tracks")
            try:
                await status_msg.edit(content=f"Loading cancelled. Added {tracks_loaded} tracks from {collection_type} **{collection_name}**.")
            except:
                pass
            return
        except Exception as e:
            # Log error but continue with next track
            print(f"Unexpected error loading track '{search_query}': {e}")
            failed_tracks += 1
            # Update progress even on error
            try:
                await status_msg.edit(content=f"Loading {collection_type} **{collection_name}**: {i + 1}/{len(tracks_list)} tracks...")
            except:
                pass
            continue
    
    # Final status update
    try:
        result_msg = f"Added {tracks_loaded} tracks from {collection_type} **{collection_name}** to queue."
        if failed_tracks > 0:
            result_msg += f" ({failed_tracks} track(s) not found on YouTube)"
        await status_msg.edit(content=result_msg)
    except:
        pass
    
    # Clear loading task reference when done
    server_info['loading_task'] = None

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

# Callback function to manage audio reproduction errors
def after_playing(error, guild_id):
    if error:
        print(f"Error during playback: {error}")
    server_info = get_server_info(guild_id)
    lock = get_server_lock(guild_id)
    
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
        async with lock:
            while server_info['audio_queue']:
                group = server_info['audio_queue'][0]
                if group:
                    next_track = group.pop(0)
                    if not group:
                        server_info['audio_queue'].pop(0)
                    await play_audio(next_track['ctx'], next_track['url'], next_track['title'], next_track['video_url'])
                    # Preserve collection_id in current_track
                    if 'collection_id' in next_track:
                        server_info['current_track']['collection_id'] = next_track['collection_id']
                    break
                else:
                    server_info['audio_queue'].pop(0)
            if not server_info['audio_queue']:
                server_info['current_track'] = None
    asyncio.run_coroutine_threadsafe(next_track(), bot.loop)

# Function to play song track in voice channel
async def play_audio(ctx, stream_url, title, video_url):
    try:
        vc = ctx.voice_client
        if vc is None:
            channel = ctx.author.voice.channel
            vc = await channel.connect()

        # Enhanced options for SoundCloud HLS support
        before_options = "-reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5"
        ffmpeg_options = "-vn"
        
        # For SoundCloud files (now local files), use simple options
        is_temp_file = stream_url.startswith('/dev/shm/') or stream_url.startswith('/tmp/')
        if 'sndcdn.com' in video_url or is_temp_file:
            # Simple options for local files
            before_options = ""
            ffmpeg_options = "-vn"
        
        vc.play(FFmpegPCMAudio(source=stream_url, before_options=before_options, options=ffmpeg_options), after=lambda e: after_playing(e, ctx.guild.id))
        await ctx.send(f"I'm playing: **{title}**\n{video_url}")

        # Add to history
        server_info = get_server_info(ctx.guild.id)
        server_info['playback_history'].append({'title': title, 'video_url': video_url})

        # Set current track with temp file info for cleanup
        # Note: collection_id is not passed here but maintained from next_track callback
        track_info = {'title': title, 'video_url': video_url}
        if is_temp_file:
            track_info['temp_file'] = stream_url
        server_info['current_track'] = track_info
    except Exception as e:
        await ctx.send(f"Error: {e}")
        print(f"Error during playback: {e}")

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
    global track_counter
    try:
        if ctx.author.voice:
            # Check if it's a Spotify playlist or album
            if is_spotify_url(query) and ('playlist/' in query or 'album/' in query):
                tracks_result = get_spotify_tracks_list(query)
                
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
                
                # Generate unique collection ID for skip all functionality
                collection_id = str(uuid.uuid4())
                
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
            
            stream_url, title, video_url = get_enhanced_audio_info(query)

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

            server_info = get_server_info(ctx.guild.id)
            lock = get_server_lock(ctx.guild.id)
            async with lock:
                track_counter += 1
                track = {'ctx': ctx, 'url': stream_url, 'title': title, 'video_url': video_url, 'track_number': track_counter}
                if ctx.voice_client and ctx.voice_client.is_playing():
                    server_info['audio_queue'].append([track]) 
                    await ctx.send(f"Track added to queue: **{title}**\n{video_url}")
                else:
                    await play_audio(ctx, stream_url, title, video_url)
        else:
            await ctx.send("You have to be in a voice channel to use this command!")
    except Exception as e:
        await ctx.send(f"Error: {e}")
        print(f"Error: {e}")

# Command to play a track in loop
@bot.command(help="Plays an audio track in loop for n times. Supports YouTube, Spotify, and SoundCloud URLs (use `!skip all` to stop).")
async def repeat(ctx, n: int, *, query: str):
    global track_counter
    try:
        if ctx.author.voice:
            if n < 1:
                await ctx.send("Please choose a number greater than 0.")
                return

            stream_url, title, video_url = get_enhanced_audio_info(query)

            if stream_url is None:
                await ctx.send("Unable to retrieve audio stream.")
                return

            server_info = get_server_info(ctx.guild.id)
            lock = get_server_lock(ctx.guild.id)
            async with lock:
                repeat_group = []
                for i in range(n):
                    track_counter += 1
                    repeat_group.append({
                        'ctx': ctx,
                        'url': stream_url,
                        'title': f"{title} (loop {i+1}/{n})",
                        'video_url': video_url,
                        'track_number': track_counter
                    })
                if ctx.voice_client and ctx.voice_client.is_playing():
                    server_info['audio_queue'].append(repeat_group)  # Add as a group
                    await ctx.send(f"Track added to queue: **{title}** (loop x{n})\n{video_url}")
                else:
                    # Play the first in the group, queue the rest as a group
                    first, rest = repeat_group[0], repeat_group[1:]
                    await play_audio(first['ctx'], first['url'], first['title'], first['video_url'])
                    if rest:
                        server_info['audio_queue'].insert(0, rest)  # Play the rest as a group next
        else:
            await ctx.send("You have to be in a voice channel to use this command!")
    except Exception as e:
        await ctx.send(f"Error: {e}")
        print(f"Error: {e}")

# Command to skip current track
@bot.command(help = "Stops current audio track and plays the next one in the queue. Use '!skip all' to skip the current repeat group.")
async def skip(ctx, arg: str = None):
    server_info = get_server_info(ctx.guild.id)
    lock = get_server_lock(ctx.guild.id)
    async with lock:
        if ctx.voice_client and ctx.voice_client.is_playing():
            if arg == "all":
                current_title = server_info['current_track']['title'] if server_info['current_track'] else None
                current_collection_id = server_info['current_track'].get('collection_id') if server_info['current_track'] else None
                found = False
                
                # Cancel loading task if active
                if server_info['loading_task'] and not server_info['loading_task'].done():
                    server_info['loading_task'].cancel()
                    server_info['loading_task'] = None
                
                # If current track has collection_id, remove all tracks with same collection_id
                if current_collection_id:
                    server_info['audio_queue'] = [
                        group for group in server_info['audio_queue']
                        if not any(t.get('collection_id') == current_collection_id for t in group)
                    ]
                    found = True
                else:
                    # Fallback to old behavior for repeat loops
                    for i, group in enumerate(server_info['audio_queue']):
                        if any(current_title and current_title.split(" (loop")[0] in t['title'] for t in group):
                            server_info['audio_queue'].pop(i)
                            found = True
                            break
                
                ctx.voice_client.stop()
                if found:
                    await ctx.send("Skipped current track and the rest of the playlist/loop.")
                else:
                    await ctx.send("Skipped current track (no playlist/loop found).")
            else:
                ctx.voice_client.stop()
                await ctx.send("Skipped current track.")
        else:
            server_info['current_track'] = None
            await ctx.send("No track to play.")

# Command to pause current track
@bot.command(help = "Pauses currently playing audio track.")
async def pause(ctx):
    if ctx.voice_client and ctx.voice_client.is_playing():
        ctx.voice_client.pause()
        await ctx.send("Track paused.")
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
        for group in server_info['audio_queue']:
            for track in group:
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
        server_info = get_server_info(ctx.guild.id)
        server_info['audio_queue'].clear()
        server_info['current_track'] = None
        if ctx.voice_client and ctx.voice_client.is_connected():
            await ctx.voice_client.disconnect()
            await ctx.send("Track stopped and bot disconnected.")
        else:
            await ctx.send("Bot is not in a voice channel.")
    except Exception as e:
        print(f"Error in stop: {e}")

# Event to log bot's successful connection on console
@bot.event
async def on_ready():
    print(f'Bot connected as {bot.user.name}')

bot.run(os.getenv('token'))
