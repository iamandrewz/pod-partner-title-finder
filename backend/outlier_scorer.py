"""
Outlier Scorer - Episode Optimizer Module

Detects high-performing YouTube videos that are "outliers" relative to their channel size.
Used to identify potential episode candidates from search results.

Score Interpretation:
- 80-100: Exceptional outlier (gold) - Strong episode candidate
- 50-79: Strong performer - Good candidate, check engagement
- 20-49: Decent - May be worth considering
- <20: Skip it - Underperforming relative to channel
"""

import math
from datetime import datetime, timedelta
from typing import Optional
from typing import Dict, List, Any, Tuple


def get_days_since_published(published_at: str) -> float:
    """
    Calculate days since video was published.
    
    Args:
        published_at: ISO format date string (YYYY-MM-DD or YYYY-MM-DDTHH:MM:SSZ)
    
    Returns:
        Number of days since publication (as float for fractional days)
    """
    try:
        # Handle both date-only and datetime formats
        if 'T' in published_at:
            published_date = datetime.fromisoformat(published_at.replace('Z', '+00:00'))
        else:
            published_date = datetime.fromisoformat(published_at)
        
        now = datetime.now(published_date.tzinfo) if published_date.tzinfo else datetime.now()
        delta = now - published_date
        return delta.total_seconds() / 86400  # Convert to days
    except (ValueError, AttributeError):
        # Default to 28+ days if parsing fails
        return 30.0


def get_recency_multiplier(published_at: str) -> float:
    """
    Calculate recency multiplier based on 28-day velocity.
    
    Multipliers:
    - Published today (0 days): 2.0x
    - Published 7 days ago: 1.5x
    - Published 14 days ago: 1.2x
    - Published 28+ days ago: 1.0x
    
    Args:
        published_at: ISO format date string
    
    Returns:
        Recency multiplier (1.0 to 2.0)
    """
    days = get_days_since_published(published_at)
    
    if days <= 1:
        return 2.0
    elif days <= 7:
        return 1.5
    elif days <= 14:
        return 1.2
    elif days <= 28:
        return 1.0
    else:
        return 1.0  # Cap at 28+ days


def get_duration_bonus(duration_seconds: int) -> int:
    """
    Calculate duration bonus for longer content.
    
    Bonuses:
    - 20+ minutes (1200+ seconds): +10 points
    - 10-20 minutes (600-1199 seconds): +5 points
    - Under 10 minutes: 0 points
    
    Args:
        duration_seconds: Video duration in seconds
    
    Returns:
        Duration bonus (0, 5, or 10)
    """
    if duration_seconds is None:
        return 0
    
    if duration_seconds >= 1200:  # 20+ minutes
        return 10
    elif duration_seconds >= 600:  # 10-20 minutes
        return 5
    else:
        return 0


def calculate_base_ratio(view_count: int, subscriber_count: int) -> float:
    """
    Calculate the base ratio: views ÷ subscribers.
    
    This measures views relative to channel size.
    - 10K views / 100 subs = 100 (amazing ratio)
    - 10K views / 1M subs = 0.01 (terrible ratio)
    
    Args:
        view_count: Number of views
        subscriber_count: Number of subscribers
    
    Returns:
        Base ratio as float
    """
    if subscriber_count is None or subscriber_count == 0:
        return 0.0
    
    if view_count is None:
        return 0.0
    
    return view_count / subscriber_count


def calculate_outlier_score(video_data: Dict[str, Any]) -> Tuple[float, Dict[str, Any]]:
    """
    Calculate the outlier score for a YouTube video.
    
    NEW FORMULA (2026-02-17):
    1. IGNORE RULE: If subs > 100K AND views < 5K → score = 0 (underperforming big channel)
    2. ABSOLUTE VIEW BONUS:
       - views > 100K → +40 points
       - views > 50K → +30 points
       - views > 20K → +15 points
       - views > 10K → +10 points
       - views > 5K → +5 points
    3. RATIO BONUS (views ÷ subs):
       - ratio > 50 → +40 points
       - ratio > 20 → +30 points
       - ratio > 10 → +20 points
       - ratio > 5 → +15 points
       - ratio > 2 → +10 points
       - ratio > 1 → +5 points
    4. RECENCY MULTIPLIER (applied to final score):
       - 0-7 days: 2.0x
       - 8-14 days: 1.5x
       - 15-28 days: 1.2x
       - 28+ days: 1.0x
    5. DURATION BONUS:
       - 20+ min: +10
       - 10-20 min: +5
       - <10 min: 0
    CAP AT 100
    
    Args:
        video_data: Dictionary containing:
            - view_count: int (required)
            - subscriber_count: int (required for accurate scoring)
            - published_at: str (ISO format, required for recency)
            - duration_seconds: int (optional, for duration bonus)
            - video_id: str (optional, for reference)
            - title: str (optional, for reference)
    
    Returns:
        Tuple of (score, metadata_dict)
        Score is 0-100, metadata contains breakdown
    """
    metadata = {
        "video_id": video_data.get("video_id"),
        "title": video_data.get("title"),
        "view_count": video_data.get("view_count"),
        "subscriber_count": video_data.get("subscriber_count"),
        "published_at": video_data.get("published_at"),
        "duration_seconds": video_data.get("duration_seconds"),
        "ignored": False,
        "view_bonus": 0,
        "ratio_bonus": 0,
        "recency_multiplier": 1.0,
        "duration_bonus": 0,
        "raw_score": 0.0,
        "score": 0.0,
        "score_label": "Skip it",
        "error": None
    }
    
    # Extract values with defaults
    view_count = video_data.get("view_count")
    subscriber_count = video_data.get("subscriber_count")
    published_at = video_data.get("published_at")
    duration_seconds = video_data.get("duration_seconds")
    
    # Error handling for missing critical data
    if view_count is None:
        metadata["error"] = "Missing view_count"
        return 0.0, metadata
    
    if subscriber_count is None or subscriber_count == 0:
        metadata["error"] = "Missing or zero subscriber_count - cannot calculate ratio"
        metadata["score"] = 0.0
        metadata["score_label"] = "Skip it"
        return 0.0, metadata
    
    # ========== RULE 1: IGNORE RULE ==========
    # If subs > 100K AND views <= 5K → score = 0 (underperforming big channel)
    if subscriber_count > 100000 and view_count <= 5000:
        metadata["ignored"] = True
        metadata["score"] = 0.0
        metadata["score_label"] = "Skip it"
        return 0.0, metadata
    
    # Calculate ratio for bonuses
    ratio = view_count / subscriber_count if subscriber_count > 0 else 0.0
    metadata["ratio"] = ratio
    
    # ========== RULE 2: ABSOLUTE VIEW BONUS ==========
    if view_count > 100000:
        metadata["view_bonus"] = 40
    elif view_count > 50000:
        metadata["view_bonus"] = 30
    elif view_count > 20000:
        metadata["view_bonus"] = 15
    elif view_count > 10000:
        metadata["view_bonus"] = 10
    elif view_count > 5000:
        metadata["view_bonus"] = 5
    else:
        metadata["view_bonus"] = 0
    
    # ========== RULE 3: RATIO BONUS ==========
    if ratio > 50:
        metadata["ratio_bonus"] = 40
    elif ratio > 20:
        metadata["ratio_bonus"] = 30
    elif ratio > 10:
        metadata["ratio_bonus"] = 20
    elif ratio > 5:
        metadata["ratio_bonus"] = 15
    elif ratio > 2:
        metadata["ratio_bonus"] = 10
    elif ratio > 1:
        metadata["ratio_bonus"] = 5
    else:
        metadata["ratio_bonus"] = 0
    
    # ========== RULE 4: RECENCY MULTIPLIER ==========
    if published_at is None:
        metadata["error"] = "Missing published_at - using default recency"
        metadata["recency_multiplier"] = 1.0
    else:
        days = get_days_since_published(published_at)
        metadata["days_since_published"] = days
        if days <= 7:
            metadata["recency_multiplier"] = 2.0
        elif days <= 14:
            metadata["recency_multiplier"] = 1.5
        elif days <= 28:
            metadata["recency_multiplier"] = 1.2
        else:
            metadata["recency_multiplier"] = 1.0
    
    # ========== RULE 5: DURATION BONUS ==========
    metadata["duration_bonus"] = get_duration_bonus(duration_seconds)
    
    # Calculate base score (view bonus + ratio bonus + duration bonus)
    base_score = metadata["view_bonus"] + metadata["ratio_bonus"] + metadata["duration_bonus"]
    metadata["base_score"] = base_score
    
    # Apply recency multiplier
    metadata["raw_score"] = base_score * metadata["recency_multiplier"]
    
    # Cap at 100
    metadata["score"] = min(metadata["raw_score"], 100.0)
    
    # Assign label
    metadata["score_label"] = get_score_label(metadata["score"])
    
    return metadata["score"], metadata


def get_score_label(score: float) -> str:
    """
    Get human-readable label for a score.
    
    Args:
        score: Outlier score (0-100)
    
    Returns:
        Score category label
    """
    if score >= 80:
        return "Exceptional outlier (gold)"
    elif score >= 50:
        return "Strong performer"
    elif score >= 20:
        return "Decent"
    else:
        return "Skip it"


def rank_videos_by_outlier(videos_list: List[Dict[str, Any]], reverse: bool = True) -> List[Dict[str, Any]]:
    """
    Rank a list of videos by their outlier score.
    
    Args:
        videos_list: List of video data dictionaries
        reverse: If True, highest scores first (default)
    
    Returns:
        List of videos sorted by outlier score, each with score metadata added
    """
    scored_videos = []
    
    for video in videos_list:
        score, metadata = calculate_outlier_score(video)
        video_with_score = {**video, **metadata}
        scored_videos.append(video_with_score)
    
    # Sort by score
    scored_videos.sort(key=lambda x: x.get("score", 0), reverse=reverse)
    
    return scored_videos


# ============================================================================
# Channel API Integration (stub for future implementation)
# ============================================================================

def get_channel_subscribers(channel_id: str, api_key: str) -> Optional[int]:
    """
    Fetch subscriber count for a YouTube channel.
    
    This is a stub implementation. In production, this would call the
    YouTube Data API v3 to get channel statistics.
    
    Args:
        channel_id: YouTube channel ID (e.g., "UC...")
        api_key: YouTube Data API key
    
    Returns:
        Subscriber count or None if unavailable
    
    Example API call:
        GET https://www.googleapis.com/youtube/v3/channels
            ?part=statistics
            &id=CHANNEL_ID
            &key=API_KEY
    """
    # Stub: In production, make API call here
    # For now, return None to indicate unavailable
    return None


# ============================================================================
# Test Functions
# ============================================================================

def run_tests():
    """Run test cases with mock data."""
    
    print("=" * 60)
    print("OUTLIER SCORER TESTS")
    print("=" * 60)
    
    # Test Case 1: Gold outlier - small channel, viral video
    test1 = {
        "video_id": "abc123",
        "title": "I made $10K in 24 hours",
        "view_count": 10000,
        "subscriber_count": 100,  # 100:1 ratio = 100
        "published_at": "2026-02-17",  # Today
        "duration_seconds": 1800  # 30 minutes
    }
    score1, meta1 = calculate_outlier_score(test1)
    print(f"\nTest 1 - Gold Outlier:")
    print(f"  Title: {test1['title']}")
    print(f"  Views: {test1['view_count']:,} | Subs: {test1['subscriber_count']:,}")
    print(f"  Days old: {get_days_since_published(test1['published_at']):.1f}")
    print(f"  Base Ratio: {meta1['base_ratio']:.2f}")
    print(f"  Recency: {meta1['recency_multiplier']}x")
    print(f"  Duration Bonus: +{meta1['duration_bonus']}")
    print(f"  RAW: ({meta1['base_ratio']:.2f} × {meta1['recency_multiplier']}) + {meta1['duration_bonus']} = {meta1['raw_score']:.2f}")
    print(f"  → FINAL SCORE: {score1:.1f} [{meta1['score_label']}]")
    assert score1 == 100.0, f"Expected 100, got {score1}"
    
    # Test Case 2: Strong performer - mid-tier
    # Need published_at within last 7 days for 1.5x multiplier
    test2 = {
        "video_id": "def456",
        "title": "Tutorial: Advanced Python Tips",
        "view_count": 100000,
        "subscriber_count": 10000,  # 10:1 ratio = 10
        "published_at": "2026-02-12",  # 5 days ago
        "duration_seconds": 900  # 15 minutes
    }
    score2, meta2 = calculate_outlier_score(test2)
    print(f"\nTest 2 - Strong Performer:")
    print(f"  Title: {test2['title']}")
    print(f"  Views: {test2['view_count']:,} | Subs: {test2['subscriber_count']:,}")
    print(f"  Days old: {get_days_since_published(test2['published_at']):.1f}")
    print(f"  Base Ratio: {meta2['base_ratio']:.2f}")
    print(f"  Recency: {meta2['recency_multiplier']}x")
    print(f"  Duration Bonus: +{meta2['duration_bonus']}")
    print(f"  RAW: ({meta2['base_ratio']:.2f} × {meta2['recency_multiplier']}) + {meta2['duration_bonus']} = {meta2['raw_score']:.2f}")
    print(f"  → FINAL SCORE: {score2:.1f} [{meta2['score_label']}]")
    # 10 * 1.5 + 5 = 15 + 5 = 20 (just at edge of "decent")
    assert 15 <= score2 <= 25, f"Expected ~20, got {score2}"
    
    # Test Case 3: Skip it - large channel, low ratio
    test3 = {
        "video_id": "ghi789",
        "title": "My New Video",
        "view_count": 100000,
        "subscriber_count": 1000000,  # 0.1:1 ratio
        "published_at": "2026-01-15",  # 33 days ago
        "duration_seconds": 300  # 5 minutes
    }
    score3, meta3 = calculate_outlier_score(test3)
    print(f"\nTest 3 - Skip It:")
    print(f"  Title: {test3['title']}")
    print(f"  Views: {test3['view_count']:,} | Subs: {test3['subscriber_count']:,}")
    print(f"  Base Ratio: {meta3['base_ratio']:.4f}")
    print(f"  Recency: {meta3['recency_multiplier']}x")
    print(f"  Duration Bonus: +{meta3['duration_bonus']}")
    print(f"  RAW: ({meta3['base_ratio']:.4f} × {meta3['recency_multiplier']}) + {meta3['duration_bonus']} = {meta3['raw_score']:.2f}")
    print(f"  → FINAL SCORE: {score3:.1f} [{meta3['score_label']}]")
    assert score3 < 20, f"Expected <20, got {score3}"
    
    # Test Case 4: Missing subscriber count
    test4 = {
        "video_id": "jkl012",
        "title": "Video without channel data",
        "view_count": 50000,
        "subscriber_count": None,
        "published_at": "2026-02-17",
        "duration_seconds": 600
    }
    score4, meta4 = calculate_outlier_score(test4)
    print(f"\nTest 4 - Missing Subscriber Data:")
    print(f"  Error: {meta4['error']}")
    print(f"  → FINAL SCORE: {score4:.1f} [{meta4['score_label']}]")
    assert score4 == 0.0, f"Expected 0, got {score4}"
    assert meta4['error'] is not None
    
    # Test Case 5: Ranking function
    print(f"\nTest 5 - Ranking Function:")
    videos = [test1, test2, test3, test4]
    ranked = rank_videos_by_outlier(videos)
    print("  Ranked (highest first):")
    for i, v in enumerate(ranked, 1):
        print(f"    {i}. {v['title'][:40]:<40} → {v['score']:.1f} [{v['score_label']}]")
    
    # Verify ranking order
    assert ranked[0]['score'] == 100.0, "Highest should be test1"
    assert ranked[1]['score'] >= 15, "Second should be decent or better"
    assert ranked[3]['score'] == 0.0, "Lowest should be missing data"
    
    print("\n" + "=" * 60)
    print("ALL TESTS PASSED ✓")
    print("=" * 60)


if __name__ == "__main__":
    run_tests()
