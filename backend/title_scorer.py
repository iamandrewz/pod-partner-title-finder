"""
Title Scorer Module - Outlier Algorithm
Calculates outlier scores for YouTube videos to find the best titles to mimic.

OUTLIER SCORE FORMULA:
    outlier_score = (views ÷ subscribers) × recency_bonus
    
    Where recency_bonus:
    - 28 days or less = 2.0x multiplier
    - 29-60 days = 1.5x multiplier  
    - 61-90 days = 1.0x multiplier
    - 91-180 days = 0.5x multiplier
    - 180+ days = 0.25x multiplier

Author: Subagent
Date: 2026-02-17
"""

import os
import math
from datetime import datetime, timedelta
from typing import Dict, List, Any, Optional, Tuple


# YouTube API Configuration
YOUTUBE_API_KEY = os.environ.get('YOUTUBE_API_KEY', 'AIzaSyDZ8BgDX8TjyoA-IpEi02NLocAPeDxC0_8')

# Recency bonus multipliers
RECENCY_BONUS = {
    '28_days': 2.0,     # Within 28 days = 2x
    '60_days': 1.5,     # 29-60 days = 1.5x
    '90_days': 1.0,     # 61-90 days = 1x
    '180_days': 0.5,    # 91-180 days = 0.5x
    'older': 0.25       # 180+ days = 0.25x
}


def calculate_recency_bonus(published_date: str) -> float:
    """
    Calculate recency bonus multiplier based on publish date.
    
    Args:
        published_date: Date string in various formats:
            - ISO format: YYYY-MM-DDTHH:MM:SSZ
            - yt-dlp format: YYYYMMDD
        
    Returns:
        Multiplier between 0.25 and 2.0
    """
    try:
        # Handle yt-dlp format (YYYYMMDD)
        if published_date and len(published_date) == 8 and published_date.isdigit():
            pub_date = datetime(
                int(published_date[0:4]),
                int(published_date[4:6]),
                int(published_date[6:8])
            )
        # Handle ISO format
        elif 'T' in published_date:
            pub_date = datetime.fromisoformat(published_date.replace('Z', '+00:00'))
            pub_date = pub_date.replace(tzinfo=None)
        else:
            pub_date = datetime.strptime(published_date, '%Y-%m-%d')
    except (ValueError, AttributeError):
        return 1.0  # Default to 1x if can't parse
    
    days_ago = (datetime.now() - pub_date).days
    
    if days_ago <= 28:
        return RECENCY_BONUS['28_days']
    elif days_ago <= 60:
        return RECENCY_BONUS['60_days']
    elif days_ago <= 90:
        return RECENCY_BONUS['90_days']
    elif days_ago <= 180:
        return RECENCY_BONUS['180_days']
    else:
        return RECENCY_BONUS['older']


def calculate_outlier_score(views: int, subscribers: int, published_date: str) -> float:
    """
    Calculate the OUTLIER SCORE for a video.
    
    Formula: (views ÷ subscribers) × recency_bonus
    
    Higher score = better candidate to mimic (performed above channel's average)
    
    Args:
        views: Number of video views
        subscribers: Channel subscriber count
        published_date: ISO date string
        
    Returns:
        Outlier score (can exceed 1.0 for viral videos)
    """
    if subscribers <= 0 or views <= 0:
        return 0.0
    
    # Calculate base ratio (views per subscriber)
    ratio = views / subscribers
    
    # Apply recency bonus
    recency_bonus = calculate_recency_bonus(published_date)
    
    outlier_score = ratio * recency_bonus
    
    return outlier_score


def calculate_normalized_score(outlier_score: float) -> float:
    """
    Normalize outlier score to 0-100 scale for display.
    
    Benchmarks:
    - 0-10: Poor (below average performance)
    - 10-30: Average
    - 30-50: Good
    - 50-70: Excellent
    - 70-100: Outstanding (viral)
    
    Args:
        outlier_score: Raw outlier score
        
    Returns:
        Normalized score 0-100
    """
    if outlier_score <= 0:
        return 0.0
    
    # Use logarithmic scale for better distribution
    # A ratio of 1.0 (views = subscribers) with 2x bonus = score of 2.0
    # Normalize so 2.0 = 50 (good), 10.0 = 100 (viral)
    normalized = math.log10(outlier_score * 10 + 1) * 25
    
    return min(100, max(0, normalized))


def score_youtube_results(results: List[Dict[str, Any]], channel_subs: Dict[str, int]) -> List[Dict[str, Any]]:
    """
    Add outlier scores to YouTube search results.
    
    Args:
        results: List of video results from YouTube search
        channel_subs: Dict mapping channel_id to subscriber count
        
    Returns:
        Results with outlier_score and normalized_score added
    """
    scored_results = []
    
    for video in results:
        channel_id = video.get('channel_id', '')
        views = video.get('view_count', 0)
        published = video.get('published_at', '')
        
        # Get subscriber count
        subscribers = channel_subs.get(channel_id, 0)
        
        # Calculate outlier score
        outlier_score = calculate_outlier_score(views, subscribers, published)
        normalized = calculate_normalized_score(outlier_score)
        
        # Add scores to video
        scored_video = {
            **video,
            'subscribers': subscribers,
            'outlier_score': round(outlier_score, 4),
            'normalized_score': round(normalized, 1),
            'recency_bonus': round(calculate_recency_bonus(published), 2)
        }
        
        scored_results.append(scored_video)
    
    # Sort by outlier score (highest first)
    scored_results.sort(key=lambda x: x['outlier_score'], reverse=True)
    
    return scored_results


def get_top_outliers(results: List[Dict[str, Any]], top_n: int = 3) -> List[Dict[str, Any]]:
    """
    Get the top N outlier videos from search results.
    
    Args:
        results: Scored results list
        top_n: Number of top results to return
        
    Returns:
        Top N videos sorted by outlier score
    """
    if not results:
        return []
    
    return results[:min(top_n, len(results))]


def format_score_display(outlier_score: float, normalized_score: float) -> str:
    """
    Format score for display in UI.
    
    Args:
        outlier_score: Raw outlier score
        normalized_score: Normalized 0-100 score
        
    Returns:
        Formatted string like "Score: 45.2 (Excellent)"
    """
    if normalized_score >= 70:
        rating = "🔥 Viral"
    elif normalized_score >= 50:
        rating = "⭐ Excellent"
    elif normalized_score >= 30:
        rating = "✓ Good"
    elif normalized_score >= 10:
        rating = "○ Average"
    else:
        rating = "✗ Poor"
    
    return f"{normalized_score:.1f}/100 ({rating})"


# ============================================================================
# CHANNEL SUBSCRIBER LOOKUP
# ============================================================================

# Cache for channel subscriber counts
_channel_subs_cache: Dict[str, int] = {}


def get_channel_subscribers(channel_id: str, force_refresh: bool = False) -> int:
    """
    Get subscriber count for a channel.
    
    Uses cached value if available, otherwise returns 0 (will be updated later).
    
    Args:
        channel_id: YouTube channel ID
        force_refresh: Force refresh from API
        
    Returns:
        Subscriber count (0 if not available)
    """
    global _channel_subs_cache
    
    if not force_refresh and channel_id in _channel_subs_cache:
        return _channel_subs_cache[channel_id]
    
    # For now, return 0 - will be populated from search results
    # In production, would call YouTube API
    return 0


def update_channel_subscribers(channel_data: Dict[str, int]):
    """
    Update the channel subscriber cache.
    
    Args:
        channel_data: Dict mapping channel_id to subscriber count
    """
    global _channel_subs_cache
    _channel_subs_cache.update(channel_data)


def clear_channel_cache():
    """Clear the channel subscriber cache."""
    global _channel_subs_cache
    _channel_subs_cache = {}


# ============================================================================
# TITLE ANALYSIS
# ============================================================================

def analyze_title_patterns(titles: List[str]) -> Dict[str, Any]:
    """
    Analyze patterns in a list of titles.
    
    Args:
        titles: List of YouTube video titles
        
    Returns:
        Dict with pattern analysis
    """
    patterns = {
        'has_numbers': 0,
        'has_howto': 0,
        'has_why': 0,
        'has_best': 0,
        'has_mistakes': 0,
        'has_secrets': 0,
        'has_emotional': 0,
        'has_vs': 0,
    }
    
    import re
    
    number_pattern = r'\b(\d+)\b'
    howto_pattern = r'\b(how to|how do|how can)\b'
    why_pattern = r'\b(why|reason|reasons)\b'
    best_pattern = r'\b(best|top|ultimate)\b'
    mistakes_pattern = r'\b(mistake|error|wrong|fail)\b'
    secrets_pattern = r'\b(secret|hack|trick|hidden)\b'
    emotional_pattern = r'\b(amazing|incredible|shocking|insane|obsessed|love|hate)\b'
    vs_pattern = r'\b(vs|versus|compared)\b'
    
    for title in titles:
        title_lower = title.lower()
        if re.search(number_pattern, title):
            patterns['has_numbers'] += 1
        if re.search(howto_pattern, title_lower):
            patterns['has_howto'] += 1
        if re.search(why_pattern, title_lower):
            patterns['has_why'] += 1
        if re.search(best_pattern, title_lower):
            patterns['has_best'] += 1
        if re.search(mistakes_pattern, title_lower):
            patterns['has_mistakes'] += 1
        if re.search(secrets_pattern, title_lower):
            patterns['has_secrets'] += 1
        if re.search(emotional_pattern, title_lower):
            patterns['has_emotional'] += 1
        if re.search(vs_pattern, title_lower):
            patterns['has_vs'] += 1
    
    total = len(titles) if titles else 1
    percentages = {k: round((v / total) * 100, 1) for k, v in patterns.items()}
    
    return {
        'counts': patterns,
        'percentages': percentages,
        'total_titles': len(titles)
    }


# ============================================================================
# TESTING
# ============================================================================

if __name__ == '__main__':
    # Test the outlier score calculation
    print("=== Testing Outlier Score Calculator ===\n")
    
    # Test case 1: Recent viral video
    test_cases = [
        {
            'name': 'Recent viral (28 days, 1M views, 100K subs)',
            'views': 1000000,
            'subscribers': 100000,
            'published_date': (datetime.now() - timedelta(days=15)).isoformat()
        },
        {
            'name': 'Old viral (200 days, 500K views, 1M subs)',
            'views': 500000,
            'subscribers': 1000000,
            'published_date': (datetime.now() - timedelta(days=200)).isoformat()
        },
        {
            'name': 'Recent average (10 days, 10K views, 100K subs)',
            'views': 10000,
            'subscribers': 100000,
            'published_date': (datetime.now() - timedelta(days=10)).isoformat()
        }
    ]
    
    for tc in test_cases:
        score = calculate_outlier_score(
            tc['views'],
            tc['subscribers'],
            tc['published_date']
        )
        normalized = calculate_normalized_score(score)
        
        print(f"Test: {tc['name']}")
        print(f"  Outlier Score: {score:.4f}")
        print(f"  Normalized: {normalized:.1f}/100")
        print(f"  Display: {format_score_display(score, normalized)}")
        print()
