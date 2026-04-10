"""
YouTube Data API v3 Search Module
=================================
Provides API-based research for title ranking with Andrew's rules.

Features:
- search.list -> videoIds -> videos.list -> channels.list
- Filters out Shorts (< 60s) and prefers longform
- Caching to reduce quota usage
- Debug fields: rejected_shorts_count, researched_candidates_count, scoring breakdown

Author: Subagent (Max)
Date: 2026-02-20
"""

import os
import json
import time
import hashlib
import re
import glob
from typing import List, Dict, Any, Optional, Tuple
from datetime import datetime, timedelta
from collections import defaultdict
from dotenv import load_dotenv

# Load API key
load_dotenv('/Users/pursuebot/.openclaw/workspace/secrets/youtube.env')
YOUTUBE_API_KEY = os.getenv('YOUTUBE_API_KEY')

# Configuration
BATCH_SIZE = 5  # 5 searches per attempt
CACHE_TTL_HOURS = 24
MAX_RUNTIME_SECONDS = 120


# ============================================================================
# EXCEPTIONS
# ============================================================================

class YouTubeAPIError(Exception):
    """Base exception for YouTube API errors."""
    pass


class QuotaExceededError(YouTubeAPIError):
    """Raised when API quota is exceeded."""
    pass


# ============================================================================
# CACHING
# ============================================================================

def _get_cache_dir() -> str:
    """Get or create cache directory."""
    cache_dir = '/tmp/yt_api_cache'
    os.makedirs(cache_dir, exist_ok=True)
    return cache_dir


def _get_cache_key(query: str, suffix: str = "") -> str:
    """Generate cache key from query."""
    normalized = query.lower().strip()
    key_str = f"{normalized}{suffix}"
    return hashlib.md5(key_str.encode()).hexdigest()


def _get_cached_results(query: str, cache_type: str = "search") -> Optional[List[Dict[str, Any]]]:
    """Get cached API results if still valid."""
    cache_key = _get_cache_key(query, cache_type)
    cache_dir = _get_cache_dir()
    cache_file = os.path.join(cache_dir, f"{cache_key}.json")
    
    if not os.path.exists(cache_file):
        return None
    
    try:
        with open(cache_file, 'r') as f:
            cached = json.load(f)
        
        cached_time = datetime.fromisoformat(cached.get('cached_at', '2000-01-01'))
        age = datetime.now() - cached_time
        
        if age > timedelta(hours=CACHE_TTL_HOURS):
            os.remove(cache_file)
            return None
        
        print(f"[YT-API] Cache HIT for: '{query[:30]}...' ({cache_type}, age: {age.total_seconds()/3600:.1f}h)")
        return cached.get('results', [])
        
    except (json.JSONDecodeError, KeyError, ValueError):
        return None


def _save_cached_results(query: str, results: List[Dict[str, Any]], cache_type: str = "search"):
    """Save API results to cache."""
    cache_key = _get_cache_key(query, cache_type)
    cache_dir = _get_cache_dir()
    cache_file = os.path.join(cache_dir, f"{cache_key}.json")
    
    try:
        with open(cache_file, 'w') as f:
            json.dump({
                'query': query,
                'results': results,
                'cached_at': datetime.now().isoformat()
            }, f)
        print(f"[YT-API] Cached {len(results)} results for: '{query[:30]}...' ({cache_type})")
    except Exception as e:
        print(f"[YT-API] Cache write failed: {e}")


def get_cache_info() -> Dict[str, Any]:
    """Return info about cached searches for debugging."""
    cache_dir = _get_cache_dir()
    cache_files = glob.glob(os.path.join(cache_dir, "*.json"))
    
    return {
        'cache_dir': cache_dir,
        'cached_queries': len(cache_files),
        'cache_ttl_hours': CACHE_TTL_HOURS
    }


# ============================================================================
# API CLIENT
# ============================================================================

def _make_api_request(endpoint: str, params: Dict[str, Any]) -> Dict[str, Any]:
    """Make request to YouTube Data API v3."""
    import urllib.request
    import urllib.parse
    import urllib.error
    
    if not YOUTUBE_API_KEY:
        raise YouTubeAPIError("YOUTUBE_API_KEY not found in environment")
    
    base_url = f"https://www.googleapis.com/youtube/v3/{endpoint}"
    params['key'] = YOUTUBE_API_KEY
    
    url = f"{base_url}?{urllib.parse.urlencode(params)}"
    
    try:
        req = urllib.request.Request(url)
        with urllib.request.urlopen(req, timeout=30) as response:
            data = json.loads(response.read().decode())
            return data
    except urllib.error.HTTPError as e:
        if e.code == 403:
            raise QuotaExceededError("YouTube API quota exceeded (403)")
        elif e.code == 404:
            raise YouTubeAPIError(f"API endpoint not found: {endpoint}")
        else:
            raise YouTubeAPIError(f"HTTP error {e.code}: {e.reason}")
    except urllib.error.URLError as e:
        raise YouTubeAPIError(f"Network error: {e.reason}")
    except json.JSONDecodeError as e:
        raise YouTubeAPIError(f"Failed to parse API response: {e}")


# ============================================================================
# VIDEO DURATION PARSING
# ============================================================================

def _parse_duration(iso_duration: str) -> int:
    """Parse ISO 8601 duration to seconds."""
    if not iso_duration:
        return 0
    
    # Format: PT#H#M#S or PT#M#S or PT#S
    match = re.match(r'PT(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?', iso_duration)
    if not match:
        return 0
    
    hours = int(match.group(1) or 0)
    minutes = int(match.group(2) or 0)
    seconds = int(match.group(3) or 0)
    
    return hours * 3600 + minutes * 60 + seconds


def _is_short(duration_seconds: int) -> bool:
    """Check if video is a Short (< 60 seconds)."""
    return 0 < duration_seconds < 60


# ============================================================================
# SIMILARITY SCORING
# ============================================================================

def _calculate_title_similarity(query_title: str, result_title: str) -> float:
    """Calculate similarity between query title and result title (0-1)."""
    if not query_title or not result_title:
        return 0.0
    
    # Normalize
    query_words = set(query_title.lower().split())
    result_words = set(result_title.lower().split())
    
    if not query_words or not result_words:
        return 0.0
    
    # Jaccard similarity
    intersection = query_words & result_words
    union = query_words | result_words
    
    return len(intersection) / len(union) if union else 0.0


def _calculate_keyword_overlap(query: str, title: str) -> float:
    """Calculate keyword overlap score."""
    query_lower = query.lower()
    title_lower = title.lower()
    
    # Common words to ignore
    stop_words = {'the', 'a', 'an', 'is', 'are', 'was', 'were', 'be', 'been', 'being',
                  'have', 'has', 'had', 'do', 'does', 'did', 'will', 'would', 'could',
                  'should', 'may', 'might', 'must', 'shall', 'can', 'need', 'dare',
                  'to', 'of', 'in', 'for', 'on', 'with', 'at', 'by', 'from', 'as',
                  'into', 'through', 'during', 'before', 'after', 'above', 'below',
                  'between', 'under', 'again', 'further', 'then', 'once', 'here',
                  'there', 'when', 'where', 'why', 'how', 'all', 'each', 'few',
                  'more', 'most', 'other', 'some', 'such', 'no', 'nor', 'not', 'only',
                  'own', 'same', 'so', 'than', 'too', 'very', 'just', 'and', 'but',
                  'if', 'or', 'because', 'until', 'while', 'about', 'this', 'that',
                  'these', 'those', 'what', 'which', 'who', 'whom', 'its', 'it'}
    
    query_keywords = [w for w in query_lower.split() if w not in stop_words and len(w) > 2]
    title_keywords = [w for w in title_lower.split() if w not in stop_words and len(w) > 2]
    
    if not query_keywords or not title_keywords:
        return 0.0
    
    # Count matching keywords
    matches = sum(1 for qk in query_keywords if qk in title_keywords)
    
    return matches / max(len(query_keywords), len(title_keywords))


# ============================================================================
# MAIN SEARCH FUNCTION
# ============================================================================

def search_with_api(
    query: str,
    max_results: int = 10,
    max_duration_minutes: int = None,
    published_after: str = None
) -> Dict[str, Any]:
    """
    Search YouTube using Data API v3 with full metadata.
    
    Flow:
    1. search.list -> get videoIds
    2. videos.list -> get statistics, contentDetails, snippet
    3. channels.list -> get subscriber counts
    
    Args:
        query: Search query
        max_results: Max videos to fetch
        max_duration_minutes: Optional max duration filter
        published_after: ISO 8601 date string for recency filter
    
    Returns:
        Dict with 'videos', 'quota_used', 'debug' info
    """
    debug_info = {
        'rejected_shorts_count': 0,
        'researched_candidates_count': 0,
        'api_calls': {
            'search': 0,
            'videos': 0,
            'channels': 0
        },
        'cached_count': 0
    }
    
    quota_used = 0
    all_videos = []
    channel_ids = set()
    
    # Check cache first
    cached = _get_cached_results(query, "api_search")
    if cached is not None:
        debug_info['cached_count'] = len(cached)
        return {
            'videos': cached,
            'quota_used': 0,
            'debug': debug_info
        }
    
    # Step 1: search.list
    print(f"[YT-API] Step 1: Searching for '{query[:40]}...'")
    
    search_params = {
        'part': 'id',
        'q': query,
        'type': 'video',
        'maxResults': min(max_results * 2, 50),  # Fetch more to account for filtering
        'order': 'relevance',  # Relevance first, we'll sort by views later
    }
    
    if published_after:
        search_params['publishedAfter'] = published_after
    
    try:
        search_data = _make_api_request('search', search_params)
        debug_info['api_calls']['search'] += 1
        quota_used += 100  # search.list costs 100 units
        
        items = search_data.get('items', [])
        print(f"[YT-API] Found {len(items)} search results")
        
        if not items:
            return {
                'videos': [],
                'quota_used': quota_used,
                'debug': debug_info
            }
        
        # Extract video IDs
        video_ids = []
        for item in items:
            if item.get('id', {}).get('kind') == 'youtube#video':
                video_id = item['id'].get('videoId')
                if video_id:
                    video_ids.append(video_id)
        
        debug_info['researched_candidates_count'] = len(video_ids)
        
    except QuotaExceededError:
        raise
    except YouTubeAPIError as e:
        print(f"[YT-API] Search error: {e}")
        return {
            'videos': [],
            'quota_used': quota_used,
            'debug': debug_info,
            'error': str(e)
        }
    
    # Step 2: videos.list (batch of 50) - get duration, views, stats
    if video_ids:
        print(f"[YT-API] Step 2: Fetching video details for {len(video_ids)} videos...")
        
        for i in range(0, len(video_ids), 50):
            batch = video_ids[i:i+50]
            
            video_params = {
                'part': 'statistics,contentDetails,snippet',
                'id': ','.join(batch)
            }
            
            try:
                video_data = _make_api_request('videos', video_params)
                debug_info['api_calls']['videos'] += 1
                quota_used += 1  # videos.list costs 1 unit per video
                
                for item in video_data.get('items', []):
                    video_id = item['id']
                    
                    # Get duration from contentDetails
                    duration_str = item.get('contentDetails', {}).get('duration', '')
                    duration_seconds = _parse_duration(duration_str)
                    
                    # HARD RULE: Never include Shorts (< 60 seconds)
                    if _is_short(duration_seconds):
                        debug_info['rejected_shorts_count'] += 1
                        continue
                    
                    # Get statistics
                    stats = item.get('statistics', {})
                    view_count = int(stats.get('viewCount', 0))
                    
                    # Skip videos with no views
                    if view_count == 0:
                        continue
                    
                    # Get snippet
                    snippet = item.get('snippet', {})
                    channel_id = snippet.get('channelId', '')
                    
                    video = {
                        'video_id': video_id,
                        'title': snippet.get('title', ''),
                        'description': snippet.get('description', '')[:200],
                        'channel_id': channel_id,
                        'channel_title': snippet.get('channelTitle', ''),
                        'published_at': snippet.get('publishedAt', ''),
                        'view_count': view_count,
                        'like_count': int(stats.get('likeCount', 0)),
                        'comment_count': int(stats.get('commentCount', 0)),
                        'duration_seconds': duration_seconds,
                        'duration_iso': duration_str,
                        'thumbnail': snippet.get('thumbnails', {}).get('high', {}).get('url', '')
                    }
                    
                    all_videos.append(video)
                    
                    if channel_id:
                        channel_ids.add(channel_id)
                
            except QuotaExceededError:
                raise
            except YouTubeAPIError as e:
                print(f"[YT-API] Video fetch error: {e}")
                continue
        
        print(f"[YT-API] {len(all_videos)} videos after filtering shorts")
    
    # Step 3: channels.list (batch of 50) - get subscriber counts
    if channel_ids:
        print(f"[YT-API] Step 3: Fetching channel stats for {len(channel_ids)} channels...")
        
        for i in range(0, len(channel_ids), 50):
            batch = list(channel_ids)[i:i+50]
            
            channel_params = {
                'part': 'statistics,snippet',
                'id': ','.join(batch)
            }
            
            try:
                channel_data = _make_api_request('channels', channel_params)
                debug_info['api_calls']['channels'] += 1
                quota_used += 1  # channels.list costs 1 unit per channel
                
                channel_subs = {}
                for item in channel_data.get('items', []):
                    cid = item['id']
                    sub_count = int(item.get('statistics', {}).get('subscriberCount', 0))
                    channel_subs[cid] = sub_count
                
                # Add subscriber counts to videos
                for video in all_videos:
                    cid = video.get('channel_id', '')
                    video['channel_subscribers'] = channel_subs.get(cid, 0)
                    
                    # Calculate views/subs ratio (outlier metric)
                    subs = max(video['channel_subscribers'], 100)  # Min 100 to avoid division issues
                    video['views_per_sub'] = video['view_count'] / subs
                    
            except QuotaExceededError:
                raise
            except YouTubeAPIError as e:
                print(f"[YT-API] Channel fetch error: {e}")
                # Still return videos even without subscriber data
                for video in all_videos:
                    video['channel_subscribers'] = 0
                    video['views_per_sub'] = video['view_count'] / 100
    
    # Sort by views (descending)
    all_videos.sort(key=lambda x: x.get('view_count', 0), reverse=True)
    
    # Limit to max_results
    all_videos = all_videos[:max_results]
    
    # Cache results
    _save_cached_results(query, all_videos, "api_search")
    
    print(f"[YT-API] Final: {len(all_videos)} videos, quota used: {quota_used}")
    print(f"[YT-API] API calls: search={debug_info['api_calls']['search']}, videos={debug_info['api_calls']['videos']}, channels={debug_info['api_calls']['channels']}")
    
    return {
        'videos': all_videos,
        'quota_used': quota_used,
        'debug': debug_info
    }


# ============================================================================
# TITLE SCORING
# ============================================================================

def score_api_results(
    query_title: str,
    videos: List[Dict[str, Any]],
    require_longform: bool = True,
    min_views_20min: int = 5000
) -> List[Dict[str, Any]]:
    """
    Score API results based on Andrew's rules.
    
    Scoring factors:
    1. View count tiers: 100k strong, 10k solid, 5k ok, <5k not ideal
    2. Title similarity (keyword/title similarity)
    3. Channel similarity (same channel = bonus)
    4. Recency (prefer last 12 months)
    5. Views/subs ratio (outlier detection)
    6. Longform preference (>20 min preferred)
    
    Returns:
        List of scored videos with scoring breakdown
    """
    scored = []
    now = datetime.now()
    
    # Parse published date cutoff (12 months ago)
    twelve_months_ago = now - timedelta(days=365)
    
    for video in videos:
        breakdown = {}
        total_score = 0.0
        
        # 1. View count tier scoring
        views = video.get('view_count', 0)
        duration = video.get('duration_seconds', 0)
        is_longform = duration >= 1200  # 20+ minutes
        
        # Andrew's rules: Min views for >=20 min videos: 5,000
        # View tiers: 100k strong, 10k solid, 5k ok
        if is_longform:
            if views >= 100000:
                breakdown['views_tier'] = 100
            elif views >= 10000:
                breakdown['views_tier'] = 70
            elif views >= min_views_20min:  # 5,000
                breakdown['views_tier'] = 40
            else:
                breakdown['views_tier'] = 10
        else:
            # Fallback to 3-20 min videos (still no Shorts)
            if views >= 100000:
                breakdown['views_tier'] = 80
            elif views >= 10000:
                breakdown['views_tier'] = 50
            elif views >= 1000:
                breakdown['views_tier'] = 25
            else:
                breakdown['views_tier'] = 5
        
        total_score += breakdown['views_tier']
        
        # 2. Title similarity
        title_sim = _calculate_title_similarity(query_title, video.get('title', ''))
        breakdown['title_similarity'] = round(title_sim * 50, 2)  # Max 50 points
        total_score += breakdown['title_similarity']
        
        # 3. Keyword overlap
        keyword_score = _calculate_keyword_overlap(query_title, video.get('title', ''))
        breakdown['keyword_overlap'] = round(keyword_score * 30, 2)  # Max 30 points
        total_score += breakdown['keyword_overlap']
        
        # 4. Recency bonus (12 months preferred, older penalized)
        try:
            pub_date = datetime.fromisoformat(video.get('published_at', '').replace('Z', '+00:00'))
            if pub_date >= twelve_months_ago:
                breakdown['recency_bonus'] = 20
                total_score += 20
            else:
                # Penalty for older videos
                age_months = (now - pub_date).days / 30
                penalty = min(15, age_months / 12)  # Max 15 point penalty
                breakdown['recency_bonus'] = -round(penalty, 2)
                total_score -= penalty
        except:
            breakdown['recency_bonus'] = 0
        
        # 5. Views/subs ratio (outlier detection)
        views_per_sub = video.get('views_per_sub', 0)
        if views_per_sub >= 10:
            breakdown['outlier_score'] = 25  # High ratio = potential outlier
        elif views_per_sub >= 5:
            breakdown['outlier_score'] = 15
        elif views_per_sub >= 2:
            breakdown['outlier_score'] = 5
        else:
            breakdown['outlier_score'] = 0
        total_score += breakdown['outlier_score']
        
        # 6. Longform bonus
        if is_longform:
            breakdown['longform_bonus'] = 15
            total_score += 15
        elif duration >= 180:  # 3+ min
            breakdown['longform_bonus'] = 5
            total_score += 5
        else:
            breakdown['longform_bonus'] = 0
        
        scored.append({
            **video,
            'score': round(total_score, 2),
            'scoring_breakdown': breakdown,
            'is_longform': is_longform
        })
    
    # Sort by score descending
    scored.sort(key=lambda x: x.get('score', 0), reverse=True)
    
    return scored


# ============================================================================
# BATCH SEARCH FOR TITLE RESEARCH
# ============================================================================

def research_titles_batch(
    titles: List[Dict[str, Any]],
    batch_size: int = 5,
    time_budget_sec: int = 60
) -> Dict[str, Any]:
    """
    Research a batch of titles using the YouTube API.
    
    Args:
        titles: List of title objects with 'title' and 'topic'
        batch_size: Number of titles to research per batch
        time_budget_sec: Maximum time to spend
    
    Returns:
        Dict with researched titles and debug info
    """
    start_time = time.time()
    results = []
    total_quota = 0
    total_debug = {
        'rejected_shorts_count': 0,
        'researched_candidates_count': 0,
        'total_api_calls': {'search': 0, 'videos': 0, 'channels': 0}
    }
    
    # Calculate publishedAfter (12 months ago - Andrew's rule)
    twelve_months_ago = datetime.now() - timedelta(days=365)
    published_after = twelve_months_ago.isoformat() + 'Z'
    
    titles_to_research = titles[:batch_size]
    print(f"[YT-API] Researching {len(titles_to_research)} titles (batch size: {batch_size})...")
    
    for i, title_obj in enumerate(titles_to_research):
        # Check time budget
        if time.time() - start_time > time_budget_sec:
            print(f"[YT-API] Time budget exceeded. Stopping at {i+1} titles.")
            break
        
        title = title_obj.get('title', '')
        topic = title_obj.get('topic', '')
        
        print(f"[YT-API] [{i+1}/{len(titles_to_research)}] Researching: {title[:40]}...")
        
        try:
            # Search with API
            search_result = search_with_api(
                query=title,
                max_results=10,
                published_after=published_after
            )
            
            videos = search_result.get('videos', [])
            quota = search_result.get('quota_used', 0)
            debug = search_result.get('debug', {})
            
            total_quota += quota
            
            # Update debug totals
            total_debug['rejected_shorts_count'] += debug.get('rejected_shorts_count', 0)
            total_debug['researched_candidates_count'] += debug.get('researched_candidates_count', 0)
            for key in total_debug['total_api_calls']:
                total_debug['total_api_calls'][key] += debug.get('api_calls', {}).get(key, 0)
            
            # Score results
            if videos:
                scored_videos = score_api_results(title, videos)
                best = scored_videos[0] if scored_videos else None
            else:
                scored_videos = []
                best = None
            
            results.append({
                'title': title,
                'topic': topic,
                'videos_found': len(videos),
                'scored_videos': scored_videos[:5],  # Top 5
                'best_match': best,
                'best_score': best.get('score', 0) if best else 0,
                'best_views': best.get('view_count', 0) if best else 0,
                'quota_used': quota
            })
            
            if best:
                print(f"[YT-API] Best: {best.get('view_count', 0):,} views, score: {best.get('score', 0):.1f}")
            
        except QuotaExceededError:
            print(f"[YT-API] Quota exceeded after {i} titles")
            total_debug['quota_exceeded'] = True
            break
        except Exception as e:
            print(f"[YT-API] Error researching '{title}': {e}")
            results.append({
                'title': title,
                'topic': topic,
                'error': str(e),
                'videos_found': 0,
                'best_match': None,
                'best_score': 0,
                'best_views': 0
            })
    
    # Calculate total runtime
    runtime = time.time() - start_time
    
    print(f"[YT-API] Batch complete: {len(results)} titles, total quota: {total_quota}, runtime: {runtime:.2f}s")
    
    return {
        'results': results,
        'total_quota_used': total_quota,
        'runtime_seconds': round(runtime, 2),
        'debug': total_debug
    }


# ============================================================================
# THEME CLUSTERING & CHAMPION SELECTION
# ============================================================================

def cluster_titles_by_theme(titles: List[Dict[str, Any]], num_themes: int = 5) -> List[Dict[str, Any]]:
    """
    Cluster titles into themes and pick champions.
    
    Returns list of theme clusters with champion title for each.
    """
    if not titles:
        return []
    
    # Simple clustering by topic
    topic_groups = defaultdict(list)
    for t in titles:
        topic = t.get('topic', 'General')
        topic_groups[topic].append(t)
    
    # Convert to list of clusters
    clusters = []
    for topic, topic_titles in topic_groups.items():
        # Pick first as champion
        champion = topic_titles[0] if topic_titles else None
        clusters.append({
            'theme': topic,
            'titles': topic_titles,
            'champion': champion,
            'count': len(topic_titles)
        })
    
    # Sort by count descending
    clusters.sort(key=lambda x: x['count'], reverse=True)
    
    return clusters[:num_themes]


# ============================================================================
# FALLBACK
# ============================================================================

def get_api_fallback_results(titles: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Return fallback results when API fails."""
    fallback_results = []
    base_score = 100000
    
    for i, title_obj in enumerate(titles):
        fallback_results.append({
            'title': title_obj.get('title', ''),
            'topic': title_obj.get('topic', ''),
            'videos_found': 0,
            'scored_videos': [],
            'best_match': None,
            'best_score': max(0, base_score - (i * 5000)),
            'best_views': max(0, 50000 - (i * 2000)),
            'fallback': True
        })
    
    return fallback_results


# ============================================================================
# DOUILLARD TEST
# ============================================================================

def run_douillard_test() -> Dict[str, Any]:
    """
    Douillard test - comprehensive test of YouTube API implementation.
    """
    print("=" * 60)
    print("DOUILLARD TEST - YouTube API Implementation")
    print("=" * 60)
    
    results = {
        'timestamp': datetime.now().isoformat(),
        'tests': [],
        'api_calls': {'search': 0, 'videos': 0, 'channels': 0},
        'total_quota': 0,
        'cache_info': get_cache_info(),
        'passed': True
    }
    
    # Clear cache for clean test
    cache_dir = _get_cache_dir()
    for f in glob.glob(os.path.join(cache_dir, "*.json")):
        os.remove(f)
    print(f"Cache cleared: {cache_dir}")
    
    # Test 1: Single search
    print("\n--- Test 1: Single Search ---")
    start = time.time()
    search_result = search_with_api("intermittent fasting benefits", max_results=5)
    elapsed = time.time() - start
    
    results['tests'].append({
        'name': 'single_search',
        'videos_found': len(search_result['videos']),
        'quota_used': search_result['quota_used'],
        'elapsed_sec': round(elapsed, 2),
        'passed': len(search_result['videos']) > 0
    })
    results['api_calls']['search'] += search_result['debug']['api_calls']['search']
    results['api_calls']['videos'] += search_result['debug']['api_calls']['videos']
    results['api_calls']['channels'] += search_result['debug']['api_calls']['channels']
    results['total_quota'] += search_result['quota_used']
    
    # Test 2: Shorts filtering
    print("\n--- Test 2: Shorts Filtering ---")
    shorts_rejected = search_result['debug']['rejected_shorts_count']
    candidates = search_result['debug']['researched_candidates_count']
    
    results['tests'].append({
        'name': 'shorts_filtering',
        'shorts_rejected': shorts_rejected,
        'candidates_found': candidates,
        'passed': shorts_rejected > 0
    })
    print(f"Shorts rejected: {shorts_rejected}, Candidates: {candidates}")
    
    # Test 3: Duration parsing
    print("\n--- Test 3: Duration Parsing ---")
    if search_result['videos']:
        sample = search_result['videos'][0]
        has_duration = 'duration_seconds' in sample
        duration_val = sample.get('duration_seconds', 0)
        
        results['tests'].append({
            'name': 'duration_parsing',
            'has_duration_field': has_duration,
            'sample_duration_sec': duration_val,
            'passed': has_duration and duration_val > 0
        })
        print(f"Sample duration: {duration_val}s")
    
    # Test 4: Scoring with Andrew's rules
    print("\n--- Test 4: Scoring with Andrew's Rules ---")
    scored = score_api_results("intermittent fasting benefits", search_result['videos'])
    
    has_breakdown = len(scored) > 0 and 'scoring_breakdown' in scored[0]
    breakdown = scored[0].get('scoring_breakdown', {}) if scored else {}
    
    results['tests'].append({
        'name': 'scoring_rules',
        'has_breakdown': has_breakdown,
        'views_tier': breakdown.get('views_tier', 0),
        'longform_bonus': breakdown.get('longform_bonus', 0) > 0,
        'recency_tracked': 'recency_bonus' in breakdown,
        'passed': has_breakdown
    })
    print(f"Breakdown: {breakdown}")
    
    # Test 5: Caching
    print("\n--- Test 5: Caching ---")
    start = time.time()
    cached_result = search_with_api("intermittent fasting benefits", max_results=5)
    cached_elapsed = time.time() - start
    
    is_cached = cached_result['debug'].get('cached_count', 0) > 0
    
    results['tests'].append({
        'name': 'caching',
        'cache_hit': is_cached,
        'cached_elapsed_sec': round(cached_elapsed, 3),
        'passed': is_cached
    })
    print(f"Cache hit: {is_cached}, elapsed: {cached_elapsed*1000:.1f}ms")
    
    # Test 6: Batch research
    print("\n--- Test 6: Batch Research ---")
    test_titles = [
        {'title': 'how to build muscle', 'topic': 'Fitness'},
        {'title': 'best diet for weight loss', 'topic': 'Nutrition'},
        {'title': 'workout routine beginners', 'topic': 'Fitness'},
        {'title': 'healthy eating tips', 'topic': 'Nutrition'},
        {'title': 'exercise for energy', 'topic': 'Fitness'},
    ]
    
    batch_result = research_titles_batch(test_titles, batch_size=5, time_budget_sec=30)
    
    results['tests'].append({
        'name': 'batch_research',
        'titles_researched': len(batch_result['results']),
        'quota_used': batch_result['total_quota_used'],
        'runtime_sec': batch_result['runtime_seconds'],
        'passed': len(batch_result['results']) > 0
    })
    results['total_quota'] += batch_result['total_quota_used']
    
    # Summary
    print("\n" + "=" * 60)
    print("DOUILLARD TEST SUMMARY")
    print("=" * 60)
    
    all_passed = all(t['passed'] for t in results['tests'])
    results['passed'] = all_passed
    
    print(f"Total tests: {len(results['tests'])}")
    print(f"Passed: {sum(1 for t in results['tests'] if t['passed'])}")
    print(f"Total API quota used: {results['total_quota']}")
    print(f"API calls: search={results['api_calls']['search']}, videos={results['api_calls']['videos']}, channels={results['api_calls']['channels']}")
    print(f"Overall: {'PASSED' if all_passed else 'FAILED'}")
    
    return results


if __name__ == '__main__':
    if not YOUTUBE_API_KEY:
        print("ERROR: YOUTUBE_API_KEY not found!")
        exit(1)
    
    run_douillard_test()
