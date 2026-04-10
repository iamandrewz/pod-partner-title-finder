"""
YouTube Transcript Extraction Module
=====================================
Extracts transcripts from YouTube videos using a 4-tier fallback chain:

1. Primary: youtube-transcript-api (free)
2. Fallback 1: youtube-transcript-api with retry logic (3 attempts with backoff)
3. Fallback 2: yt-dlp with BgUtils POT (uses POT server on port 4416)
4. Fallback 3: yt-dlp audio download + faster-whisper (local, FREE)

Usage:
    from youtube_transcript import (
        extract_video_id,
        get_transcript,
        get_transcript_with_timestamps,
        get_available_languages,
        extract_transcript
    )

Example:
    video_id = extract_video_id("https://youtu.be/OVVRtX32BcE")
    transcript = get_transcript(video_id)
    print(transcript)
"""

import re
import subprocess
import json
import os
import time
import socket
import threading
from typing import Dict, List, Optional, Any, Tuple

# Primary: youtube-transcript-api
try:
    from youtube_transcript_api._api import YouTubeTranscriptApi
    from youtube_transcript_api._errors import (
        NoTranscriptFound,
        VideoUnavailable,
        TranscriptsDisabled,
        CouldNotRetrieveTranscript,
        YouTubeTranscriptApiException,
        YouTubeRequestFailed,
        AgeRestricted,
        IpBlocked,
        RequestBlocked,
    )
    TooManyRequests = RequestBlocked
    YOUTUBE_TRANSCRIPT_API_AVAILABLE = True
except ImportError:
    YOUTUBE_TRANSCRIPT_API_AVAILABLE = False

# Local Whisper for final fallback
FASTER_WHISPER_AVAILABLE = False
try:
    from faster_whisper import WhisperModel
    FASTER_WHISPER_AVAILABLE = True
except ImportError:
    pass

# Import centralized config for yt-dlp path
try:
    from config import YTDLP_PATH
except ImportError:
    YTDLP_PATH = '/Users/ottis/Library/Python/3.9/bin/yt-dlp'

# Constants
BGUTIL_PORT = 4416
BGUTIL_SERVER_DIR = '/Users/pursuebot/.openclaw/workspace/pursue-segments/backend/bgutil-ytdlp-pot-provider/server'
WHISPER_MODEL_SIZE = 'small'  # small is fast and accurate enough


class TranscriptExtractionError(Exception):
    """Raised when transcript extraction fails."""
    pass


# =============================================================================
# BgUtils Server Management
# =============================================================================

def is_port_open(port: int, host: str = '127.0.0.1') -> bool:
    """Check if a port is open."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.settimeout(1)
    try:
        result = sock.connect_ex((host, port))
        sock.close()
        return result == 0
    except:
        return False


def start_bgutil_server(port: int = BGUTIL_PORT) -> bool:
    """
    Start the BgUtils POT server if not already running.
    Returns True if server is running (either was already or was started).
    """
    if is_port_open(port):
        print(f"[BgUtils] Server already running on port {port}")
        return True
    
    print(f"[BgUtils] Starting POT server on port {port}...")
    
    try:
        # Start the server in background
        proc = subprocess.Popen(
            ['node', 'build/main.js', '--port', str(port)],
            cwd=BGUTIL_SERVER_DIR,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            start_new_session=True
        )
        
        # Wait for server to start
        for _ in range(10):
            time.sleep(0.5)
            if is_port_open(port):
                print(f"[BgUtils] Server started successfully on port {port}")
                return True
        
        print(f"[BgUtils] Warning: Server may not have started properly")
        return is_port_open(port)
        
    except Exception as e:
        print(f"[BgUtils] Failed to start server: {e}")
        return False


def ensure_bgutil_running() -> bool:
    """Ensure BgUtils server is running, start if needed."""
    if is_port_open(BGUTIL_PORT):
        return True
    return start_bgutil_server()


# =============================================================================
# Core Functions
# =============================================================================

def extract_video_id(youtube_url: str) -> Optional[str]:
    """
    Extract video ID from various YouTube URL formats.
    """
    patterns = [
        r'(?:youtube\.com/watch\?v=)([a-zA-Z0-9_-]{11})',
        r'(?:youtu\.be/)([a-zA-Z0-9_-]{11})',
        r'(?:youtube\.com/embed/)([a-zA-Z0-9_-]{11})',
        r'(?:youtube\.com/shorts/)([a-zA-Z0-9_-]{11})',
        r'^([a-zA-Z0-9_-]{11})$'
    ]
    
    for pattern in patterns:
        match = re.search(pattern, youtube_url)
        if match:
            return match.group(1)
    
    return None


def get_transcript(video_id: str, language: str = 'en') -> str:
    """Get full transcript text from a YouTube video."""
    if not YOUTUBE_TRANSCRIPT_API_AVAILABLE:
        raise TranscriptExtractionError(
            "youtube-transcript-api not installed. Install with: pip install youtube-transcript-api"
        )
    
    try:
        api = YouTubeTranscriptApi()
        transcript = api.fetch(video_id, languages=[language])
        transcript_text = ' '.join([snippet.text for snippet in transcript.snippets])
        return transcript_text
        
    except YouTubeTranscriptApiException as e:
        error_type = type(e).__name__
        
        if isinstance(e, NoTranscriptFound):
            raise TranscriptExtractionError(
                f"No transcript found for video {video_id} in language '{language}'. "
                f"Try getting available languages first with get_available_languages()."
            )
        elif isinstance(e, VideoUnavailable):
            raise TranscriptExtractionError(f"Video {video_id} is unavailable or private.")
        elif isinstance(e, (YouTubeRequestFailed, RequestBlocked, IpBlocked)):
            raise TranscriptExtractionError("Rate limited or blocked by YouTube. Please wait before retrying.")
        elif isinstance(e, TranscriptsDisabled):
            raise TranscriptExtractionError(f"Transcripts are disabled for video {video_id}.")
        elif isinstance(e, AgeRestricted):
            raise TranscriptExtractionError(f"Video {video_id} is age-restricted.")
        else:
            raise TranscriptExtractionError(f"API error ({error_type}): {str(e)}")
    except Exception as e:
        raise TranscriptExtractionError(f"Unexpected error: {str(e)}")


def get_transcript_with_timestamps(video_id: str, language: str = 'en') -> List[Dict[str, Any]]:
    """Get transcript with timestamps for chapter generation."""
    if not YOUTUBE_TRANSCRIPT_API_AVAILABLE:
        raise TranscriptExtractionError(
            "youtube-transcript-api not installed. Install with: pip install youtube-transcript-api"
        )
    
    try:
        api = YouTubeTranscriptApi()
        transcript = api.fetch(video_id, languages=[language])
        
        segments = []
        for snippet in transcript.snippets:
            segments.append({
                'text': snippet.text,
                'start': snippet.start,
                'duration': snippet.duration
            })
        return segments
        
    except YouTubeTranscriptApiException as e:
        if isinstance(e, NoTranscriptFound):
            raise TranscriptExtractionError(
                f"No transcript found for video {video_id} in language '{language}'."
            )
        elif isinstance(e, VideoUnavailable):
            raise TranscriptExtractionError(f"Video {video_id} is unavailable or private.")
        elif isinstance(e, TooManyRequests):
            raise TranscriptExtractionError("Rate limited by YouTube. Please wait before retrying.")
        elif isinstance(e, TranscriptsDisabled):
            raise TranscriptExtractionError(f"Transcripts are disabled for video {video_id}.")
        else:
            raise TranscriptExtractionError(f"API error: {str(e)}")
    except Exception as e:
        raise TranscriptExtractionError(f"Unexpected error: {str(e)}")


def get_available_languages(video_id: str) -> List[str]:
    """Get list of available transcript languages for a video."""
    if not YOUTUBE_TRANSCRIPT_API_AVAILABLE:
        raise TranscriptExtractionError("youtube-transcript-api not installed.")
    
    try:
        api = YouTubeTranscriptApi()
        transcript_list = api.list(video_id)
        languages = [t.language_code for t in transcript_list]
        return languages
    except VideoUnavailable:
        raise TranscriptExtractionError(f"Video {video_id} is unavailable.")
    except Exception as e:
        raise TranscriptExtractionError(f"Could not get languages: {str(e)}")


def _get_video_metadata(video_id: str) -> Dict[str, Any]:
    """Get video metadata using yt-dlp (fast, just needs video info)."""
    # Ensure BgUtils is running for yt-dlp
    ensure_bgutil_running()
    
    try:
        result = subprocess.run(
            [YTDLP_PATH, 
             '--extractor-args', f'youtubepot-bgutilhttp:base_url=http://127.0.0.1:{BGUTIL_PORT}',
             '--dump-json', '--no-download', '--no-playlist', 
             f'https://youtu.be/{video_id}'],
            capture_output=True,
            text=True,
            timeout=30
        )
        if result.returncode == 0 and result.stdout:
            data = json.loads(result.stdout.split('\n')[0])
            return {
                'duration': data.get('duration', 0) or 0,
                'title': data.get('title', '') or ''
            }
    except Exception as e:
        print(f"[Transcript] Could not get video metadata: {e}")
    
    return {'duration': 0, 'title': ''}


# =============================================================================
# Fallback Methods
# =============================================================================

def _try_youtube_api_with_retry(video_id: str, language: str = 'en') -> Tuple[bool, List[Dict[str, Any]], str]:
    """
    Try youtube-transcript-api with retry logic.
    
    Returns:
        (success: bool, segments: List, error_message: str)
    """
    if not YOUTUBE_TRANSCRIPT_API_AVAILABLE:
        return False, [], "youtube-transcript-api not available"
    
    # Try with retry logic (3 attempts with exponential backoff)
    for attempt in range(3):
        try:
            transcript_data = get_transcript_with_timestamps(video_id, language)
            return True, transcript_data, ""
        except TranscriptExtractionError as e:
            error_msg = str(e)
            
            # Don't retry for certain errors
            if 'No transcript found' in error_msg and 'language' not in error_msg.lower():
                return False, [], error_msg
            if 'unavailable' in error_msg.lower() or 'private' in error_msg.lower():
                return False, [], error_msg
            
            # Wait before retrying
            if attempt < 2:
                wait_time = (2 ** attempt)  # 1s, 2s
                print(f"[Transcript] Attempt {attempt + 1} failed, retrying in {wait_time}s...")
                time.sleep(wait_time)
    
    return False, [], "All retry attempts failed"


def _try_yt_dlp_subtitles(youtube_url: str, video_id: str, output_dir: str) -> Tuple[bool, Dict[str, Any]]:
    """
    Fallback 2: Use yt-dlp with BgUtils POT to download subtitles.
    
    Returns:
        (success: bool, result_dict: Dict)
    """
    # Ensure BgUtils is running
    if not ensure_bgutil_running():
        return False, {'error': 'BgUtils server not available'}
    
    cmd = [
        YTDLP_PATH,
        '--extractor-args', f'youtubepot-bgutilhttp:base_url=http://127.0.0.1:{BGUTIL_PORT}',
        '--write-auto-sub',
        '--write-subs',
        '--sub-lang', 'en,en-US,en-GB',
        '--skip-download',
        '--output', f'{output_dir}/{video_id}.%(ext)s',
        youtube_url
    ]
    
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=120
        )
        
        if result.returncode != 0:
            return False, {'error': f'yt-dlp failed: {result.stderr}'}
        
        # Find downloaded subtitle file
        subtitle_file = _find_subtitle_file(video_id, output_dir)
        if subtitle_file:
            return True, _parse_subtitle_file(subtitle_file, video_id)
        
        return False, {'error': 'No subtitle file found after download'}
        
    except subprocess.TimeoutExpired:
        return False, {'error': 'Timeout extracting transcript with yt-dlp'}
    except Exception as e:
        return False, {'error': f'yt-dlp error: {str(e)}'}


def _try_whisper_transcription(youtube_url: str, video_id: str, output_dir: str) -> Tuple[bool, Dict[str, Any]]:
    """
    Fallback 3 (LAST RESORT): Download audio with yt-dlp + BgUtils, then transcribe with faster-whisper.
    
    Returns:
        (success: bool, result_dict: Dict)
    """
    if not FASTER_WHISPER_AVAILABLE:
        return False, {'error': 'faster-whisper not installed. Run: pip install faster-whisper'}
    
    # Ensure BgUtils is running
    if not ensure_bgutil_running():
        return False, {'error': 'BgUtils server not available'}
    
    audio_path = os.path.join(output_dir, f"{video_id}.mp3")
    
    # Download audio only
    print(f"[Whisper] Downloading audio for video {video_id}...")
    cmd = [
        YTDLP_PATH,
        '--extractor-args', f'youtubepot-bgutilhttp:base_url=http://127.0.0.1:{BGUTIL_PORT}',
        '-x',
        '--audio-format', 'mp3',
        '--audio-quality', '0',
        '--output', f'{output_dir}/{video_id}.%(ext)s',
        '--no-playlist',
        youtube_url
    ]
    
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=300  # 5 min for download
        )
        
        if result.returncode != 0:
            return False, {'error': f'Audio download failed: {result.stderr}'}
        
        # Check if file was downloaded
        if not os.path.exists(audio_path):
            # Try to find the file with different extension
            import glob
            pattern = os.path.join(output_dir, f"{video_id}.*")
            files = glob.glob(pattern)
            if files:
                audio_path = files[0]
            else:
                return False, {'error': 'Audio file not found after download'}
        
        print(f"[Whisper] Transcribing with faster-whisper ({WHISPER_MODEL_SIZE})...")
        
        # Load Whisper model and transcribe
        model = WhisperModel(WHISPER_MODEL_SIZE, device="cpu", compute_type="int8")
        segments, info = model.transcribe(audio_path, word_timestamps=True)
        
        # Build segments list with word-level timestamps
        transcript_segments = []
        full_text = []
        words_list = []
        
        for segment in segments:
            text = segment.text.strip()
            if text:
                transcript_segments.append({
                    'text': text,
                    'start': segment.start,
                    'duration': segment.end - segment.start
                })
                full_text.append(text)
                
                # Add word-level timestamps if available
                if hasattr(segment, 'words') and segment.words:
                    for word in segment.words:
                        words_list.append({
                            'text': word.word.strip(),
                            'start': word.start,
                            'end': word.end,
                            'index': len(words_list)
                        })
        
        # Calculate duration
        duration = 0
        if transcript_segments:
            last_seg = transcript_segments[-1]
            duration = last_seg['start'] + last_seg['duration']
        
        # Cleanup audio file
        try:
            os.remove(audio_path)
        except:
            pass
        
        return True, {
            'success': True,
            'video_id': video_id,
            'title': None,
            'transcript': ' '.join(full_text),
            'transcript_with_timestamps': transcript_segments,
            'words': words_list,
            'duration': duration,
            'method': 'faster-whisper'
        }
        
    except subprocess.TimeoutExpired:
        return False, {'error': 'Timeout downloading audio'}
    except Exception as e:
        return False, {'error': f'Whisper transcription error: {str(e)}'}


# =============================================================================
# High-Level API with 4-Tier Fallback
# =============================================================================

def extract_transcript(
    youtube_url: str,
    language: str = 'en',
    output_dir: Optional[str] = None
) -> Dict[str, Any]:
    """
    Extract transcript from YouTube video with full 4-tier fallback chain.
    
    Tries in order:
    1. youtube-transcript-api (free)
    2. youtube-transcript-api with retry logic (3 attempts with backoff)
    3. yt-dlp with BgUtils POT (uses POT server on port 4416)
    4. yt-dlp audio download + faster-whisper (local, FREE)
    
    Args:
        youtube_url: YouTube video URL or ID
        language: Preferred language code (default: 'en')
        output_dir: Optional directory for temp files (used by fallback)
        
    Returns:
        Dict with:
            - success: bool
            - video_id: str
            - title: str (if available)
            - transcript: str (full transcript text)
            - transcript_with_timestamps: List[Dict] (segments with start times)
            - duration: float (total duration in seconds)
            - method: str (which method was used)
            - error: str (if failed)
    """
    # Extract video ID
    video_id = extract_video_id(youtube_url)
    if not video_id:
        return {
            'success': False,
            'error': 'Invalid YouTube URL',
            'video_id': None,
            'title': None,
            'transcript': None,
            'transcript_with_timestamps': [],
            'duration': 0,
            'method': 'none'
        }
    
    # Set output directory
    if not output_dir:
        output_dir = '/tmp'
    os.makedirs(output_dir, exist_ok=True)
    
    # Get video metadata first (needed for accurate duration/title)
    video_metadata = _get_video_metadata(video_id)
    video_duration = video_metadata.get('duration', 0)
    video_title = video_metadata.get('title', '')
    
    # ==========================================================================
    # TIER 1: youtube-transcript-api (primary)
    # ==========================================================================
    print(f"[Transcript] Trying Tier 1: youtube-transcript-api...")
    if YOUTUBE_TRANSCRIPT_API_AVAILABLE:
        try:
            transcript_data = get_transcript_with_timestamps(video_id, language)
            
            transcript_text = ' '.join([seg['text'] for seg in transcript_data])
            duration = 0
            if transcript_data:
                last_segment = transcript_data[-1]
                duration = last_segment['start'] + last_segment['duration']
            
            return {
                'success': True,
                'video_id': video_id,
                'title': video_title or None,
                'transcript': transcript_text,
                'transcript_with_timestamps': transcript_data,
                'duration': video_duration if video_duration > 0 else duration,
                'method': 'youtube-transcript-api'
            }
            
        except TranscriptExtractionError as e:
            error_msg = str(e)
            print(f"[Transcript] Tier 1 failed: {error_msg}")
            
            # Try alternative language if no transcript found
            if 'No transcript found' in error_msg:
                try:
                    available = get_available_languages(video_id)
                    if available:
                        alt_lang = available[0]
                        transcript_data = get_transcript_with_timestamps(video_id, alt_lang)
                        transcript_text = ' '.join([seg['text'] for seg in transcript_data])
                        duration = transcript_data[-1]['start'] + transcript_data[-1]['duration'] if transcript_data else 0
                        
                        return {
                            'success': True,
                            'video_id': video_id,
                            'title': video_title or None,
                            'transcript': transcript_text,
                            'transcript_with_timestamps': transcript_data,
                            'duration': video_duration if video_duration > 0 else duration,
                            'method': 'youtube-transcript-api',
                            'language_used': alt_lang
                        }
                except:
                    pass
    
    # ==========================================================================
    # TIER 2: youtube-transcript-api with retry logic
    # ==========================================================================
    print(f"[Transcript] Trying Tier 2: youtube-transcript-api with retry...")
    success, transcript_data, error_msg = _try_youtube_api_with_retry(video_id, language)
    if success and transcript_data:
        transcript_text = ' '.join([seg['text'] for seg in transcript_data])
        duration = transcript_data[-1]['start'] + transcript_data[-1]['duration'] if transcript_data else 0
        
        return {
            'success': True,
            'video_id': video_id,
            'title': video_title or None,
            'transcript': transcript_text,
            'transcript_with_timestamps': transcript_data,
            'duration': video_duration if video_duration > 0 else duration,
            'method': 'youtube-transcript-api-retry'
        }
    print(f"[Transcript] Tier 2 failed: {error_msg}")
    
    # ==========================================================================
    # TIER 3: yt-dlp with BgUtils POT (subtitles)
    # ==========================================================================
    print(f"[Transcript] Trying Tier 3: yt-dlp with BgUtils POT...")
    success, result = _try_yt_dlp_subtitles(youtube_url, video_id, output_dir)
    if success:
        result['title'] = video_title or result.get('title')
        result['duration'] = video_duration if video_duration > 0 else result.get('duration', 0)
        result['method'] = 'yt-dlp-bgutil'
        return result
    print(f"[Transcript] Tier 3 failed: {result.get('error')}")
    
    # ==========================================================================
    # TIER 4: yt-dlp + faster-whisper (LAST RESORT)
    # ==========================================================================
    print(f"[Transcript] Trying Tier 4: yt-dlp + faster-whisper (local)...")
    success, result = _try_whisper_transcription(youtube_url, video_id, output_dir)
    if success:
        result['title'] = video_title or result.get('title')
        result['duration'] = video_duration if video_duration > 0 else result.get('duration', 0)
        return result
    print(f"[Transcript] Tier 4 failed: {result.get('error')}")
    
    # All tiers failed
    return {
        'success': False,
        'error': 'All transcript extraction methods failed',
        'video_id': video_id,
        'title': video_title or None,
        'transcript': None,
        'transcript_with_timestamps': [],
        'duration': video_duration,
        'method': 'none'
    }


# =============================================================================
# Helper Functions
# =============================================================================

def _find_subtitle_file(video_id: str, output_dir: str) -> Optional[str]:
    """Find downloaded subtitle file."""
    import glob
    
    for ext in ['vtt', 'srt', 'txt']:
        path = os.path.join(output_dir, f"{video_id}.{ext}")
        if os.path.exists(path):
            return path
    
    pattern = os.path.join(output_dir, f"{video_id}*.vtt")
    files = glob.glob(pattern)
    if files:
        return files[0]
    
    pattern = os.path.join(output_dir, f"{video_id}*.srt")
    files = glob.glob(pattern)
    if files:
        return files[0]
    
    return None


def _parse_subtitle_file(file_path: str, video_id: str) -> Dict[str, Any]:
    """Parse subtitle file into transcript."""
    try:
        with open(file_path, 'r', encoding='utf-8') as f:
            content = f.read()
        
        if file_path.endswith('.vtt'):
            transcript, segments = _parse_vtt(content)
        elif file_path.endswith('.srt'):
            transcript, segments = _parse_srt(content)
        else:
            transcript = content.strip()
            segments = []
        
        duration = segments[-1]['start'] + segments[-1].get('duration', 0) if segments else 0
        
        return {
            'success': True,
            'video_id': video_id,
            'title': None,
            'transcript': transcript,
            'transcript_with_timestamps': segments,
            'duration': duration,
            'method': 'yt-dlp'
        }
    except Exception as e:
        return {
            'success': False,
            'error': f'Error parsing subtitle: {str(e)}',
            'video_id': video_id,
            'title': None,
            'transcript': None,
            'transcript_with_timestamps': [],
            'duration': 0
        }


def _parse_vtt(content: str) -> Tuple[str, List[Dict]]:
    """Parse VTT subtitle format."""
    lines = content.split('\n')
    transcript = []
    segments = []
    current_text = []
    current_start = 0
    
    for line in lines:
        line = line.strip()
        
        if '-->' in line:
            start_time = _parse_timestamp(line.split('-->')[0].strip())
            current_start = start_time
            
            if current_text:
                text = ' '.join(current_text)
                text = _clean_text(text)
                if text:
                    transcript.append(text)
                    segments.append({'start': current_start, 'text': text, 'duration': 0})
                current_text = []
        elif line and not line.startswith('WEBVTT') and not line.startswith('Kind:'):
            current_text.append(line)
    
    if current_text:
        text = ' '.join(current_text)
        text = _clean_text(text)
        if text:
            transcript.append(text)
            segments.append({'start': current_start, 'text': text, 'duration': 0})
    
    return '\n'.join(transcript), segments


def _parse_srt(content: str) -> Tuple[str, List[Dict]]:
    """Parse SRT subtitle format."""
    lines = content.split('\n')
    transcript = []
    segments = []
    current_text = []
    current_start = 0
    
    for line in lines:
        line = line.strip()
        
        if '-->' in line:
            current_start = _parse_timestamp(line.split('-->')[0].strip())
        elif line.isdigit():
            if current_text:
                text = ' '.join(current_text)
                transcript.append(text)
                segments.append({'start': current_start, 'text': text, 'duration': 0})
                current_text = []
        elif line:
            current_text.append(line)
    
    if current_text:
        text = ' '.join(current_text)
        transcript.append(text)
        segments.append({'start': current_start, 'text': text, 'duration': 0})
    
    return '\n'.join(transcript), segments


def _parse_timestamp(ts: str) -> float:
    """Parse timestamp to seconds."""
    try:
        ts = ts.replace('.', ':').replace(',', '.')
        parts = ts.split(':')
        if len(parts) == 3:
            return int(parts[0]) * 3600 + int(parts[1]) * 60 + float(parts[2])
        elif len(parts) == 2:
            return int(parts[0]) * 60 + float(parts[1])
    except:
        pass
    return 0


def _clean_text(text: str) -> str:
    """Clean subtitle text."""
    text = re.sub(r'<[^>]+>', '', text)
    text = re.sub(r'\s+', ' ', text)
    return text.strip()


# =============================================================================
# Convenience Functions
# =============================================================================

def get_transcript_quick(youtube_url: str, language: str = 'en') -> str:
    """Quick function to get transcript text only."""
    result = extract_transcript(youtube_url, language)
    if result['success']:
        return result['transcript']
    return ""


# =============================================================================
# Test Script
# =============================================================================

if __name__ == '__main__':
    import sys
    
    # Test URL - use a potentially problematic one
    test_url = "https://youtu.be/OVVRtX32BcE"
    
    if len(sys.argv) > 1:
        test_url = sys.argv[1]
    
    print("=" * 60)
    print("YouTube Transcript Extraction Test (4-Tier Fallback)")
    print("=" * 60)
    print(f"\nTest URL: {test_url}")
    print(f"youtube-transcript-api: {'✓' if YOUTUBE_TRANSCRIPT_API_AVAILABLE else '✗'}")
    print(f"faster-whisper: {'✓' if FASTER_WHISPER_AVAILABLE else '✗'}")
    print(f"BgUtils port {BGUTIL_PORT}: {'✓' if is_port_open(BGUTIL_PORT) else '✗ (will start if needed)'}")
    
    # Test extract_video_id
    video_id = extract_video_id(test_url)
    print(f"\n[1] Video ID: {video_id}")
    
    # Test high-level extract_transcript with full fallback
    print("\n--- Running extract_transcript with 4-tier fallback ---")
    start_time = time.time()
    result = extract_transcript(test_url)
    elapsed = time.time() - start_time
    
    print(f"\nSuccess: {result['success']}")
    print(f"Method used: {result.get('method', 'N/A')}")
    print(f"Video ID: {result['video_id']}")
    print(f"Duration: {result.get('duration', 0):.1f}s")
    print(f"Transcript length: {len(result.get('transcript') or '')} chars")
    print(f"Time elapsed: {elapsed:.1f}s")
    
    if result['success']:
        print(f"\nTranscript preview (first 500 chars):")
        print("-" * 40)
        print(result['transcript'][:500])
        print("-" * 40)
    else:
        print(f"Error: {result.get('error')}")
    
    print("\n" + "=" * 60)
    print("Test complete!")
    print("=" * 60)
