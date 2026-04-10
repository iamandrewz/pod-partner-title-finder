"""
YouTube Search Integration for Episode Optimizer - YT-DLP VERSION (NO API)
Provides title search and competition analysis capabilities using yt-dlp (FREE, NO QUOTAS)

Features:
- 24-hour caching of search results to avoid repeated hits
- Query variants (full, shortened, keyword-only) for better success rate
- Reliable yt-dlp options for stability
"""

import os
import subprocess
import json
import re
import time
import hashlib
import fcntl
from typing import List, Dict, Any, Optional
from datetime import datetime, timedelta


# Import centralized config for yt-dlp path
try:
    from config import YTDLP_PATH
except ImportError:
    # Fallback for backwards compatibility - add to PATH
    YTDLP_PATH = 'yt-dlp'
    _yt_dlp_paths = [
        "/Users/ottis/Library/Python/3.9/bin",
        "/usr/local/bin",
        "/usr/bin",
    ]
    for _path in _yt_dlp_paths:
        if _path not in os.environ.get("PATH", ""):
            os.environ["PATH"] = _path + ":" + os.environ.get("PATH", "")

class YouTubeSearchError(Exception):
    """Custom exception for YouTube search errors."""
    pass


class QuotaExceededError(YouTubeSearchError):
    """Not used with yt-dlp, but kept for compatibility."""
    pass


# Configuration
MAX_SEARCH_TIME = 60  # seconds per search (long timeout for parallel searches)
MAX_RETRIES = 0       # No retries for speed (verification searches)
RETRY_DELAY = 3       # seconds between retries
CACHE_TTL_HOURS = 24  # Cache TTL in hours


# ============================================================================
# CACHING
# ============================================================================

def _get_cache_dir() -> str:
    """Get or create cache directory."""
    cache_dir = '/tmp/yt_search_cache'
    os.makedirs(cache_dir, exist_ok=True)
    return cache_dir


def _get_cache_key(query: str) -> str:
    """Generate cache key from query."""
    # Normalize query for consistent caching
    normalized = query.lower().strip()
    return hashlib.md5(normalized.encode()).hexdigest()


def _get_cached_results(query: str) -> Optional[List[Dict[str, Any]]]:
    """Get cached search results if still valid."""
    cache_key = _get_cache_key(query)
    cache_dir = _get_cache_dir()
    cache_file = os.path.join(cache_dir, f"{cache_key}.json")
    
    if not os.path.exists(cache_file):
        return None
    
    try:
        with open(cache_file, 'r') as f:
            cached = json.load(f)
        
        # Check if cache is still valid
        cached_time = datetime.fromisoformat(cached.get('cached_at', '2000-01-01'))
        age = datetime.now() - cached_time
        
        if age > timedelta(hours=CACHE_TTL_HOURS):
            # Cache expired
            os.remove(cache_file)
            return None
        
        print(f"[YT-DLP] Cache HIT for: '{query[:30]}...' (age: {age.total_seconds()/3600:.1f}h)")
        return cached.get('results', [])
        
    except (json.JSONDecodeError, KeyError, ValueError):
        return None


def _save_cached_results(query: str, results: List[Dict[str, Any]]):
    """Save search results to cache."""
    cache_key = _get_cache_key(query)
    cache_dir = _get_cache_dir()
    cache_file = os.path.join(cache_dir, f"{cache_key}.json")
    
    try:
        with open(cache_file, 'w') as f:
            json.dump({
                'query': query,
                'results': results,
                'cached_at': datetime.now().isoformat()
            }, f)
        print(f"[YT-DLP] Cached {len(results)} results for: '{query[:30]}...'")
    except Exception as e:
        print(f"[YT-DLP] Cache write failed: {e}")


# ============================================================================
# QUERY VARIANTS
# ============================================================================

def _generate_query_variants(base_query: str) -> List[str]:
    """Generate query variants to try in order of likelihood.
    
    Returns up to 3 variants:
    1. Full query (sanitized)
    2. Shortened (first half, ~40 chars)
    3. Keyword-only (extract key terms)
    """
    variants = []
    
    # 1. Full sanitized query
    sanitized = _sanitize_query(base_query)
    if sanitized:
        variants.append(sanitized)
    
    # 2. Shortened version (~40 chars, word-boundary)
    if len(sanitized) > 40:
        shortened = sanitized[:40]
        # Try to cut at word boundary
        last_space = shortened.rfind(' ')
        if last_space > 20:
            shortened = shortened[:last_space]
        variants.append(shortened)
    
    # 3. Keyword-only: extract 3-5 significant words
    words = sanitized.split()
    if len(words) > 5:
        # Keep words with significant meaning (longer, not common)
        significant = [w for w in words if len(w) > 3 and w.lower() not in 
                     {'this', 'that', 'with', 'from', 'have', 'been', 'will', 
                      'they', 'what', 'when', 'your', 'more', 'some', 'into',
                      'over', 'such', 'than', 'them', 'then', 'there', 'these'}]
        if significant:
            keywords = ' '.join(significant[:5])
            if keywords and keywords not in variants:
                variants.append(keywords)
    
    # Dedupe and return
    seen = set()
    unique = []
    for v in variants:
        if v.lower() not in seen:
            seen.add(v.lower())
            unique.append(v)
    
    return unique[:3]


def _sanitize_query(query: str) -> str:
    """Sanitize and shorten query for more reliable yt-dlp searching."""
    # Remove extra whitespace
    query = ' '.join(query.split())
    # Truncate to 80 chars (yt-dlp handles long queries poorly)
    if len(query) > 80:
        query = query[:80].rsplit(' ', 1)[0]
    # Remove special chars that might cause issues
    query = re.sub(r'[^\w\s\-:|#]', '', query)
    return query.strip()


# ============================================================================
# SEARCH IMPLEMENTATION
# ============================================================================

def search_titles(query: str, max_results: int = 10) -> List[Dict[str, Any]]:
    """
    Search YouTube for videos matching a query using yt-dlp (FREE, NO API QUOTAS)
    
    Features:
    - 24-hour caching to avoid repeated hits
    - Query variants for better success rate
    - DURATION FILTERS (Andrew's process):
      - Primary: 20+ minutes (duration > 1200 seconds)
      - Fallback: 3-20 minutes if primary returns <3 results
      - NEVER: Under 3 minutes (shorts excluded)
    
    Args:
        query: Search query string
        max_results: Maximum number of results to return (default: 10)
    
    Returns:
        List of video dictionaries with title, view count, thumbnail, channel info
    """
    # Check cache first
    cached = _get_cached_results(query)
    if cached is not None:
        return cached
    
    query = _sanitize_query(query)
    print(f"[YT-DLP] Searching: '{query}' (max_results={max_results})")
    
    # Generate variants to try
    variants = _generate_query_variants(query)
    print(f"[YT-DLP] Query variants: {variants}")
    
    last_error = None
    
    # DURATION FILTER STRATEGY:
    # Search for ALL videos 3+ minutes (excludes YouTube Shorts)
    # Let the relevance filter handle quality, not duration
    duration_strategies = [
        {'min_duration': 3, 'label': '3+ minutes'}
    ]
    
    for variant_idx, variant in enumerate(variants):
        if variant_idx > 0:
            print(f"[YT-DLP] Trying variant {variant_idx + 1}/{len(variants)}: '{variant[:40]}...'")
        
        all_results = []
        
        for strategy in duration_strategies:
            min_dur = strategy['min_duration']
            label = strategy['label']
            
            for attempt in range(MAX_RETRIES + 1):
                if attempt > 0:
                    print(f"[YT-DLP] Retry {attempt}/{MAX_RETRIES} after {RETRY_DELAY}s...")
                    time.sleep(RETRY_DELAY)
                
                try:
                    print(f"[YT-DLP] Trying duration filter: {label}")
                    results = _search_impl(variant, max_results, min_duration=min_dur)
                    
                    if results:
                        all_results.extend(results)
                        print(f"[YT-DLP] Got {len(results)} results with {label}")
                        
                        # Got results, continue to get more if available
                        pass
                    else:
                        print(f"[YT-DLP] No results with {label}")
                        
                except Exception as e:
                    last_error = e
                    print(f"[YT-DLP] Attempt {attempt+1} failed: {e}")
                    continue
            
            # Continue with all results
            pass
        
        # Deduplicate results
        if all_results:
            seen_ids = set()
            unique_results = []
            for r in all_results:
                vid = r.get('video_id')
                if vid and vid not in seen_ids:
                    seen_ids.add(vid)
                    unique_results.append(r)
            
            print(f"[YT-DLP] Total unique results: {len(unique_results)}")
            _save_cached_results(query, unique_results)
            return unique_results
    
    # All variants and retries failed - cache empty result briefly (5 min)
    print(f"[YT-DLP] All variants failed for: {query[:30]}... Error: {last_error}")
    _save_cached_results(query, [])  # Cache empty for shorter time
    return []


def _search_impl(query: str, max_results: int, min_duration: int = 1) -> List[Dict[str, Any]]:
    """
    Implementation of YouTube search with timeout and reliable options.
    
    Args:
        query: Search query string
        max_results: Maximum results to return
        min_duration: Minimum video duration in minutes (default: 1)
                      Use 1 to exclude only true Shorts (~60s)
                      Values <1 will be treated as 1
    """
    # ISSUE 4 FIX: Only exclude true YouTube Shorts (60 seconds), not all short videos
    # Changed from 180 (3 min) to 60 seconds to allow normal short/medium videos
    min_duration_seconds = max(min_duration * 60, 60)  # At least 60 seconds to exclude Shorts
    
    process = None
    try:
        # Use yt-dlp with duration filter to exclude shorts
        # Filter excludes only true YouTube Shorts (duration < 60 seconds)
        # Allows all normal videos including short (1-10 min) and medium (10-20 min) videos
        duration_filter = f"duration > {min_duration_seconds}"
        
        cmd = [
            YTDLP_PATH,
            f'ytsearch{max_results}:{query}',
            '--dump-single-json',
            '--no-warnings',
            '--socket-timeout', '15',
            '--match-filter', duration_filter,
            # Prefer web client for reliable results
            '--extractor-args', 'youtube:player_client=web',
        ]
        
        print(f"[YT-DLP] Running with duration filter: {duration_filter}")
        
        # Use Popen with start_new_session=True to create a new process group
        process = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            start_new_session=True
        )
        
        try:
            stdout, stderr = process.communicate(timeout=MAX_SEARCH_TIME)
        except subprocess.TimeoutExpired:
            # Kill the entire process group
            try:
                os.killpg(os.getpgid(process.pid), 9)
            except ProcessLookupError:
                pass
            print(f"[YT-DLP] Search timeout for query: {query[:30]}")
            raise TimeoutError(f"Search timed out after {MAX_SEARCH_TIME}s")
        
        if process.returncode != 0:
            err_msg = stderr[:200] if stderr else "Unknown error"
            # Don't retry on certain errors
            if "Unable to extract" in err_msg or "not found" in err_msg.lower():
                print(f"[YT-DLP] Non-retryable error: {err_msg}")
                return []
            raise Exception(err_msg)
        
        # Parse results - with error handling for non-JSON output
        try:
            data = json.loads(stdout)
        except json.JSONDecodeError as e:
            # Check if stdout contains an error message instead of JSON
            print(f"[YT-DLP] Failed to parse yt-dlp output as JSON: {e}")
            print(f"[YT-DLP] stdout preview: {stdout[:200]}")
            # Check if it looks like an error message
            if stdout and len(stdout) < 500:
                error_indicators = ['error', 'internal server error', 'not found', 'unavailable', 'rate limit', 'failed', 'exception', 'unable']
                if any(stdout.strip().lower().startswith(indicator) for indicator in error_indicators):
                    print(f"[YT-DLP] yt-dlp returned error: {stdout[:200]}")
                    return []
            # Return empty results instead of crashing
            return []
        
        entries = data.get('entries', [])
        
        results = []
        for entry in entries:
            video_id = entry.get('id')
            if not video_id:
                continue
            
            # Get view count - try multiple fields
            view_count = entry.get('view_count', 0)
            if not view_count:
                view_count = entry.get('views', 0)
            if not view_count:
                view_count = entry.get('play_count', 0)
            
            # Convert to int if string
            if isinstance(view_count, str):
                view_count_str = view_count.replace(',', '').replace(' views', '').strip()
                try:
                    view_count = int(view_count_str)
                except:
                    view_count = 0
            
            # Get subscriber count from channel_follower_count (yt-dlp field)
            subscriber_count = entry.get('channel_follower_count', 0)
            if not subscriber_count:
                subscriber_count = entry.get('subscriber_count', 0)
            
            # Convert subscriber count to int if string
            if isinstance(subscriber_count, str):
                try:
                    subscriber_count = int(subscriber_count.replace(',', ''))
                except:
                    subscriber_count = 0
            
            results.append({
                'video_id': video_id,
                'title': entry.get('title', ''),
                'description': entry.get('description', '')[:200] if entry.get('description') else '',
                'thumbnail': f"https://i.ytimg.com/vi/{video_id}/mqdefault.jpg",
                'published_at': entry.get('upload_date', ''),
                'view_count': view_count or 0,
                'subscriber_count': subscriber_count or 0,
                'channel_id': entry.get('channel_id', ''),
                'channel_title': entry.get('channel', entry.get('uploader', '')),
                'duration': entry.get('duration', 0),
                # FIXED: Add duration_minutes and is_short for proper validation
                # duration from yt-dlp is in SECONDS
                'duration_minutes': round((entry.get('duration', 0) or 0) / 60, 1),
                # YouTube Shorts are < 60 seconds (some say < 90s, but official is 60s)
                'is_short': (entry.get('duration', 0) or 0) < 60
            })
        
        print(f"[YT-DLP] Found {len(results)} results")
        for r in results[:3]:
            print(f"  {r['view_count']:,} views - {r['title'][:50]}...")
        
        return results
        
    except TimeoutError:
        raise
    except subprocess.TimeoutExpired:
        raise TimeoutError("Subprocess timeout")
    except json.JSONDecodeError as e:
        raise Exception(f"Failed to parse yt-dlp output: {e}")
    except Exception as e:
        raise Exception(f"Search error: {e}")


def get_channel_stats(channel_id: str) -> Dict[str, Any]:
    """Get channel statistics - yt-dlp version returns minimal info."""
    return {
        'subscriber_count': 0,
        'video_count': 0,
        'view_count': 0
    }


def extract_video_id(url: str) -> Optional[str]:
    """Extract video ID from YouTube URL."""
    patterns = [
        r'(?:youtube\.com/watch\?v=|youtu\.be/|youtube\.com/embed/)([a-zA-Z0-9_-]{11})',
        r'youtube\.com/shorts/([a-zA-Z0-9_-]{11})',
    ]
    
    for pattern in patterns:
        match = re.search(pattern, url)
        if match:
            return match.group(1)
    
    return None


def get_fallback_results(titles: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """
    Fallback Mode B: Return fake YouTube results when search fails completely.
    Assigns decreasing scores to simulate ranking for titles that couldn't be searched.
    This ensures we still return 12+ titles with Gold/Silver/Bronze.
    """
    fallback_results = []
    # Assign fake "outlier" scores in descending order for ranking
    base_score = 100000
    for i, title_obj in enumerate(titles):
        fallback_results.append({
            'title': title_obj.get('title', ''),
            'topic': title_obj.get('topic', ''),
            'youtube_results': [],
            'top_outliers': [],
            'best_outlier': None,
            'score': max(0, base_score - (i * 5000)),
            'view_count': max(0, 50000 - (i * 2000)),
            'fallback': True
        })
    return fallback_results


# Test function
if __name__ == '__main__':
    print("Testing yt-dlp search with caching and variants...")
    
    # First call - should search
    print("\n=== First call (no cache) ===")
    results = search_titles("nutrition advice health", max_results=5)
    print(f"\nTotal: {len(results)} results")
    for r in results[:3]:
        print(f"  {r['view_count']:,} views - {r['title'][:60]}")
    
    # Second call - should hit cache
    print("\n=== Second call (should hit cache) ===")
    results2 = search_titles("nutrition advice health", max_results=5)
    print(f"\nTotal: {len(results2)} results (cached)")
    
    # Test query variants
    print("\n=== Testing query variants ===")
    variants = _generate_query_variants("The Truth About Intermittent Fasting That Nobody Tells You")
    for i, v in enumerate(variants):
        print(f"  Variant {i+1}: '{v}'")
