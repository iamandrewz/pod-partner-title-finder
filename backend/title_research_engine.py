"""
Title Research Engine - Core Tool for YouTube Title Research
==============================================================

A fast, efficient research tool that:
1. Extracts real topics from a transcript
2. Generates title variations (SPP framework)
3. Searches YouTube for real competition data
4. Returns raw data (no scores)

USAGE:
    from title_research_engine import research_episode
    
    results = research_episode("https://youtu.be/OVVRtX32BcE")
    
    # Results contains:
    # - 5 topics extracted from transcript
    # - 3 title options per topic (15 total)
    # - 3-5 YouTube videos per title with accurate view counts

REQUIREMENTS:
    - YOUTUBE_API_KEY environment variable
    - youtube-transcript-api

Run test:
    python title_research_engine.py
"""

import os
import json
import time
from typing import List, Dict, Any, Optional
from concurrent.futures import ThreadPoolExecutor, as_completed

# Import existing utilities
from youtube_transcript import extract_transcript, extract_video_id, get_transcript_with_timestamps
from youtube_search import search_titles


# =============================================================================
# 1. TRANSCRIPT → TOPICS
# =============================================================================

def extract_topics(transcript: str, num_topics: int = 5) -> List[Dict[str, Any]]:
    """
    Find 5 REAL moments from the episode transcript.
    
    Args:
        transcript: Full transcript text
        num_topics: Number of topics to extract (default: 5)
    
    Returns:
        List of dicts: [{"topic": str, "quote": str, "timestamp": str}]
    """
    import re
    
    if not transcript:
        return _generate_fallback_topics(num_topics)
    
    # Clean transcript - remove common YouTube artifacts
    transcript = re.sub(r'^Language: \w+\s*', '', transcript)
    transcript = transcript.strip()
    
    # Split into segments (roughly every 150 words = ~1 minute)
    words = transcript.split()
    segment_size = 150
    segments = []
    
    for i in range(0, len(words), segment_size):
        segment_words = words[i:i + segment_size]
        segment_text = ' '.join(segment_words)
        # Estimate timestamp: ~2.5 words per second
        estimated_seconds = (i * 60) // 150
        minutes = estimated_seconds // 60
        seconds = estimated_seconds % 60
        timestamp = f"{minutes:02d}:{seconds:02d}"
        segments.append({'text': segment_text, 'timestamp': timestamp})
    
    # Key phrases that indicate valuable content - refined patterns
    key_indicators = [
        # Numbers/milestones
        (r'\b(\d+ years old|at age \d+|when I was \d+|turning \d+)\b', 'Age Milestone'),
        (r'\b(\$[\d,]+|make \$\d+|earned \$\d+)\b', 'Money'),
        
        # Action words
        (r'\b(I learned|I realized|I discovered|I found out|I decided)\b', 'Insight'),
        (r'\b(the truth|the secret|honestly|real talk|here\'s the thing)\b', 'Truth'),
        (r'\b(never|always|everyone|nobody|most people)\b', 'Universal'),
        
        # Transformation
        (r'\b(changed|transformed|improved|better than ever|best shape)\b', 'Transformation'),
        (r'\b(shredded|lean|fit|jacked|muscle|physique)\b', 'Physique'),
        
        # Advice indicators
        (r'\b(here\'s what|this is how|this is why|if you want to|my advice)\b', 'How-To'),
        (r'\b(lesson|tip|strategy|method|system|framework|blueprint)\b', 'Strategy'),
        
        # Life/death, significant moments
        (r'\b(rock bottom|moment that changed|turning point|decision|chose|commit)\b', 'Turning Point'),
        
        # Competition/results
        (r'\b(competition|contest|stage|prepare|prep|peak|off season)\b', 'Competition'),
    ]
    
    topic_candidates = []
    
    for seg in segments:
        text_lower = seg['text'].lower()
        for pattern, topic_type in key_indicators:
            if re.search(pattern, text_lower):
                # Extract a meaningful quote - find the actual sentence
                match = re.search(r'[^.!?]*' + pattern + r'[^.!?]*', seg['text'], re.IGNORECASE)
                if match:
                    quote = match.group(0).strip()[:120]
                    if len(quote) > 30:  # Only meaningful quotes
                        topic_candidates.append({
                            'topic': topic_type,
                            'quote': quote + ('...' if len(quote) == 120 else ''),
                            'timestamp': seg['timestamp']
                        })
                        break
    
    # Deduplicate by topic type - keep first occurrence
    seen_types = set()
    unique_topics = []
    for t in topic_candidates:
        if t['topic'] not in seen_types:
            seen_types.add(t['topic'])
            unique_topics.append(t)
    
    # Fill in if we don't have enough - use evenly spaced segments
    if len(unique_topics) < num_topics:
        segment_step = max(1, len(segments) // num_topics)
        for i in range(0, len(segments), segment_step):
            if len(unique_topics) >= num_topics:
                break
            # Check if this segment's topic is already covered
            seg_text = segments[i]['text'][:50].lower()
            already_covered = any(seg_text in t['quote'].lower() for t in unique_topics)
            if not already_covered:
                unique_topics.append({
                    'topic': f'Key Moment {len(unique_topics)+1}',
                    'quote': segments[i]['text'][:120] + ('...' if len(segments[i]['text']) > 120 else ''),
                    'timestamp': segments[i]['timestamp']
                })
    
    return unique_topics[:num_topics]


def _generate_fallback_topics(num_topics: int = 5) -> List[Dict[str, Any]]:
    """Generate generic topics when no transcript available."""
    return [
        {'topic': 'Introduction', 'quote': 'Opening segment', 'timestamp': '00:00'},
        {'topic': 'Main Content', 'quote': 'Core discussion', 'timestamp': '05:00'},
        {'topic': 'Key Strategy', 'quote': 'Important strategy', 'timestamp': '10:00'},
        {'topic': 'Results', 'quote': 'Results discussion', 'timestamp': '15:00'},
        {'topic': 'Conclusion', 'quote': 'Final thoughts', 'timestamp': '20:00'},
    ][:num_topics]


# =============================================================================
# 2. TOPICS → TITLE OPTIONS
# =============================================================================

def generate_titles(topic: Dict[str, Any]) -> List[str]:
    """
    Generate 3 title variations per topic following SPP framework:
    - No first names
    - Power words
    - Curiosity gaps
    
    Args:
        topic: Dict with 'topic' key
    
    Returns:
        List of 3 title strings
    """
    topic_type = topic.get('topic', 'General')
    quote = topic.get('quote', '')
    
    # Extract key terms from quote for contextual titles
    import re
    numbers = re.findall(r'\b(\d+)\b', quote)
    age_refs = re.findall(r'\b(age \d+|years old|\d+ years)\b', quote.lower())
    
    # SPP title templates by topic type - refined for SPP format
    title_templates = {
        'Age Milestone': [
            "The Truth About Getting Lean After {age}",
            "What Nobody Tells You About {age}",
            "Getting Fit at {age}: The Real Story"
        ],
        'Money': [
            "How to Make ${amount} With This Strategy",
            "The Real Reason People Are Making ${amount}",
            "${amount}: The Exact Blueprint"
        ],
        'Insight': [
            "I Wish I Knew This Earlier",
            "The Truth Nobody Tells You",
            "What Nobody Tells You About This"
        ],
        'Truth': [
            "The Real Reason for Success",
            "The Truth About This (From Someone Who's Done It)",
            "What Top Performers Don't Know"
        ],
        'Universal': [
            "The #1 Mistake Everyone Makes",
            "Why Most People Fail (And How To Succeed)",
            "The Secret Elite Performers Know"
        ],
        'Transformation': [
            "I Transformed My Body In {timeframe}",
            "The Exact Method That Changed Everything",
            "From Out of Shape To Lean: My Journey"
        ],
        'Physique': [
            "The {age} Year Old Shredded Physique",
            "How I Got Lean At {age}",
            "My Body Transformation At {age}"
        ],
        'How-To': [
            "The Exact Strategy That Works",
            "How To Get Results (Step By Step)",
            "Here's The System That Actually Works"
        ],
        'Strategy': [
            "The Strategy That Changed Everything",
            "My Exact Blueprint For Success",
            "{timeframe} To Results: Complete Guide"
        ],
        'Turning Point': [
            "The Moment Everything Changed",
            "The Decision That Transformed My Life",
            "My Turning Point: The Real Story"
        ],
        'Competition': [
            "My Competition Prep: Exact Details",
            "The Truth About Competition Prep",
            "How I Got Stage Ready"
        ],
        'Key Moment': [
            "The Moment Everything Changed",
            "What I Learned From This",
            "The Most Important Lesson"
        ],
        'Introduction': [
            "My Story: From Average To Elite",
            "Everything Changed When I Did This",
            "This Is My Journey"
        ],
        'Main Content': [
            "The Exact Strategy That Works",
            "How To Get The Same Results",
            "Here's What Actually Works"
        ],
        'Results': [
            "These Are The Real Results",
            "The Numbers Don't Lie",
            "What Success Looks Like"
        ],
        'Conclusion': [
            "Here's What To Do Next",
            "The Final Takeaway",
            "What Matters Most"
        ]
    }
    
    templates = title_templates.get(topic_type, title_templates['Key Moment'])
    
    # Fill in templates with contextual values
    import random
    values = {
        'age': age_refs[0].replace('years old', 'Years Old').replace('age ', '') if age_refs else random.choice(['30', '40', '50']),
        'amount': random.choice(['100k', '10k', '50k', '1M']),
        'timeframe': random.choice(['6 months', '1 year', '90 days', '30 days']),
        'result': random.choice(['lean', 'shredded', 'jacked', 'fit']),
        'topic': topic_type,
    }
    
    titles = []
    for template in templates[:3]:
        title = template
        for key, val in values.items():
            title = title.replace(f'{{{key}}}', val)
        titles.append(title)
    
    return titles


# =============================================================================
# 3. TITLES → YOUTUBE SEARCH
# =============================================================================

def search_youtube(title: str, api_key: str, max_results: int = 5) -> List[Dict[str, Any]]:
    """
    Search YouTube and return top videos with accurate view counts.
    
    Args:
        title: Search query/title
        api_key: YouTube API key
        max_results: Max videos to return (default: 5)
    
    Returns:
        List of video dicts: {title, views, channel, thumbnail, video_id}
    """
    if not title or not api_key:
        return []
    
    try:
        results = search_titles(title, max_results=max_results)
        
        # Format results - just the raw data, no scoring
        videos = []
        for r in results:
            videos.append({
                'title': r.get('title', ''),
                'views': r.get('view_count', 0),
                'channel': r.get('channel_title', ''),
                'thumbnail': r.get('thumbnail', ''),
                'video_id': r.get('video_id', '')
            })
        
        # Rate limit to conserve quota
        time.sleep(0.1)
        
        return videos
        
    except Exception as e:
        print(f"Search error for '{title}': {e}")
        return []


def _search_batch(titles: List[str], api_key: str) -> List[Dict[str, Any]]:
    """
    Search multiple titles in parallel for speed.
    
    Args:
        titles: List of title queries
        api_key: YouTube API key
    
    Returns:
        List of search results per title
    """
    results = []
    
    with ThreadPoolExecutor(max_workers=5) as executor:
        future_to_title = {
            executor.submit(search_youtube, title, api_key): title 
            for title in titles
        }
        
        for future in as_completed(future_to_title):
            title = future_to_title[future]
            try:
                videos = future.result()
                results.append({
                    'title': title,
                    'videos': videos
                })
            except Exception as e:
                results.append({
                    'title': title,
                    'videos': []
                })
    
    return results


def _search_with_ytdlp_batch(topic_results: List[Dict], all_titles: List[Dict]) -> None:
    """
    Search YouTube using yt-dlp (free, no API needed).
    Updates topic_results in place.
    
    Args:
        topic_results: List of topic dicts to update in place
        all_titles: List of {title, topic} dicts
    """
    result_idx = 0
    
    for topic_data in topic_results:
        for j in range(len(topic_data['titles'])):
            if result_idx < len(all_titles):
                title = all_titles[result_idx]['title']
                try:
                    videos = search_titles(title, max_results=5)
                    # Format results
                    formatted_videos = []
                    for v in videos:
                        formatted_videos.append({
                            'title': v.get('title', ''),
                            'views': v.get('view_count', 0),
                            'channel': v.get('channel_title', ''),
                            'thumbnail': v.get('thumbnail', ''),
                            'video_id': v.get('video_id', '')
                        })
                    topic_data['titles'][j]['videos'] = formatted_videos
                except Exception as e:
                    print(f"  Error searching '{title}': {e}")
                    topic_data['titles'][j]['videos'] = []
                result_idx += 1
                # Small delay to be respectful
                time.sleep(0.2)


# =============================================================================
# 4. MAIN FUNCTION
# =============================================================================

def research_episode(youtube_url: str, api_key: Optional[str] = None) -> Dict[str, Any]:
    """
    Full research pipeline for a YouTube episode.
    
    Args:
        youtube_url: YouTube video URL
        api_key: YouTube API key (optional, uses env YOUTUBE_API_KEY if not provided)
    
    Returns:
        Dict containing:
        - video_id: str
        - url: str  
        - transcript_available: bool
        - topics: List[Dict] - 5 topics with quotes and timestamps
            - Each topic has: titles (List[Dict])
            - Each title has: title (str), videos (List[Dict])
    
    Example output structure:
    {
        "video_id": "OVVRtX32BcE",
        "url": "https://youtu.be/OVVRtX32BcE",
        "transcript_available": True,
        "topics": [
            {
                "topic": "Insight",
                "quote": "I realized something important...",
                "timestamp": "03:24",
                "titles": [
                    {
                        "title": "I Wish I Knew This At 40",
                        "videos": [
                            {"title": "...", "views": 150000, "channel": "...", "video_id": "...", "thumbnail": "..."}
                        ]
                    },
                    ...
                ]
            },
            ...
        ]
    }
    """
    # Get API key from env if not provided
    if not api_key:
        api_key = os.environ.get('YOUTUBE_API_KEY', '')
    
    # Extract video ID
    video_id = extract_video_id(youtube_url)
    if not video_id:
        return {
            'error': 'Invalid YouTube URL',
            'video_id': None,
            'topics': []
        }
    
    print(f"🔍 Researching: {youtube_url}")
    
    # Step 1: Get transcript
    transcript_data = extract_transcript(youtube_url)
    transcript = transcript_data.get('transcript', '') if transcript_data.get('success') else ''
    
    print(f"📝 Transcript: {'✓ Available' if transcript else '✗ Not available'}")
    
    # Step 2: Extract 5 topics
    topics = extract_topics(transcript, num_topics=5)
    print(f"📌 Topics found: {len(topics)}")
    
    # Step 3: For each topic, generate titles and search YouTube
    all_titles = []
    topic_results = []
    
    for topic in topics:
        # Generate 3 title options
        title_options = generate_titles(topic)
        
        # Collect all titles for batch search
        for title in title_options:
            all_titles.append({
                'title': title,
                'topic': topic['topic']
            })
        
        # Store topic with empty titles (will fill after batch search)
        topic_results.append({
            'topic': topic['topic'],
            'quote': topic['quote'],
            'timestamp': topic['timestamp'],
            'titles': [{'title': t, 'videos': []} for t in title_options]
        })
    
    # Step 4: Batch search YouTube for all titles (parallel)
    # Use yt-dlp fallback if no API key - it's free and has no quotas
    if api_key:
        print(f"🔎 Searching YouTube for {len(all_titles)} titles (API)...")
        search_results = _search_batch([t['title'] for t in all_titles], api_key)
        
        # Map results back to topics
        result_idx = 0
        for i, topic_data in enumerate(topic_results):
            for j in range(len(topic_data['titles'])):
                if result_idx < len(search_results):
                    topic_data['titles'][j]['videos'] = search_results[result_idx].get('videos', [])
                    result_idx += 1
        
        print(f"✓ YouTube searches complete")
    else:
        # Use yt-dlp fallback - it's FREE and has no quotas!
        print(f"🔎 Searching YouTube for {len(all_titles)} titles (yt-dlp)...")
        _search_with_ytdlp_batch(topic_results, all_titles)
        print(f"✓ YouTube yt-dlp searches complete")
    
    return {
        'video_id': video_id,
        'url': youtube_url,
        'transcript_available': bool(transcript),
        'topics': topic_results
    }


# =============================================================================
# UTILITIES
# =============================================================================

def format_views(views: Any) -> str:
    """Format view count for display."""
    try:
        views = int(views)
    except (ValueError, TypeError):
        return "0"
    if views >= 1_000_000:
        return f"{views/1_000_000:.1f}M"
    elif views >= 1_000:
        return f"{views/1_000:.1f}K"
    return str(views)


def print_results(results: Dict) -> None:
    """Print research results in readable format."""
    print("\n" + "=" * 70)
    print("YOUTUBE TITLE RESEARCH RESULTS")
    print("=" * 70)
    
    print(f"\n📹 Video: {results.get('url')}")
    print(f"📝 Transcript: {'✓ Available' if results.get('transcript_available') else '✗'}")
    
    for i, topic in enumerate(results.get('topics', []), 1):
        print(f"\n{'─' * 70}")
        print(f"TOPIC {i}: {topic['topic']}")
        print(f"📍 Timestamp: {topic['timestamp']}")
        print(f"💬 \"{topic['quote'][:80]}{'...' if len(topic['quote']) > 80 else ''}\"")
        
        for j, title_data in enumerate(topic.get('titles', []), 1):
            print(f"\n  Title {j}: {title_data['title']}")
            videos = title_data.get('videos', [])
            
            if videos:
                for v in videos[:3]:
                    print(f"    🎬 {v['title'][:50]}")
                    print(f"       👁 {format_views(v['views'])} views | {v['channel']}")
            else:
                print(f"    (No videos found)")
    
    print("\n" + "=" * 70)


# =============================================================================
# TEST
# =============================================================================

if __name__ == "__main__":
    # Test target: SPP Jeff episode
    test_url = "https://youtu.be/OVVRtX32BcE"
    
    print("🚀 Running Title Research Engine Test")
    print(f"📌 Target: {test_url}\n")
    
    start_time = time.time()
    
    # Run research
    results = research_episode(test_url)
    
    elapsed = time.time() - start_time
    
    # Print results
    print_results(results)
    
    # Summary stats
    total_titles = sum(len(t['titles']) for t in results.get('topics', []))
    total_videos = sum(
        len(title.get('videos', []))
        for t in results.get('topics', [])
        for title in t.get('titles', [])
    )
    
    print(f"\n⏱️  Completed in {elapsed:.2f}s")
    print(f"📊 Stats: {len(results.get('topics', []))} topics | {total_titles} titles | {total_videos} videos")
    
    # Output JSON for programmatic use
    print("\n📄 JSON Output:")
    print(json.dumps(results, indent=2)[:2000] + "...")
