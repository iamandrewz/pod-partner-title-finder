"""
Title Research Tool - YouTube Title Research Without Scoring

A simple, powerful research tool that:
1. Extracts real topics from a transcript
2. Generates title variations
3. Searches YouTube for real data on those titles
4. Returns raw data (no scoring, just information)

USAGE:
    from title_research_tool import research_episode_titles
    
    results = research_episode_titles(
        youtube_url="https://youtu.be/OVVRtX32BcE",
        api_key="YOUR_YOUTUBE_API_KEY"  # Optional - without it, searches won't work
    )

REQUIREMENTS:
    - YouTube API key (optional but recommended for search functionality)
    - youtube-transcript-api (for fetching transcripts)
    
    Install: pip install youtube-transcript-api requests
"""

import re
import json
import os
import requests
from typing import List, Dict, Optional
from urllib.parse import urlparse, parse_qs


# ============================================================================
# 1. EXTRACT REAL TOPICS FROM TRANSCRIPT
# ============================================================================

def extract_topics(transcript: str, num_topics: int = 5) -> List[Dict]:
    """
    Parse transcript and find REAL moments/topics that were discussed.
    
    Returns list of topic dicts with:
    - topic: Short topic name
    - quote: Key quote or phrase
    - timestamp: Where it appears in video (estimated)
    """
    if not transcript:
        return []
    
    # Split into sentences/segments
    sentences = re.split(r'[.!?\n]+', transcript)
    sentences = [s.strip() for s in sentences if len(s.strip()) > 30]
    
    topics = []
    
    # Look for key phrases that indicate important topics
    key_patterns = [
        (r'\b(I\'ve been|I am in|been in)\s+(\w+\s+prep|prep mode|preparation)\b', 'Prep Mode'),
        (r'\b(years? old|at age|turning \d+)\b', 'Age Milestone'),
        (r'\b(secret|truth|real|honest)\s+(reason|way|how|what)\b', 'Secret/Truth'),
        (r'\b(shredded|lean|fit|jacked|muscular)\b', 'Fitness Results'),
        (r'\b(never|stopping|quit|give up|consistent)\b', 'Consistency'),
        (r'\b(diet|nutrition|eating|food|meal)\b', 'Diet/Nutrition'),
        (r'\b(workout|training|exercise|routine)\b', 'Training'),
        (r'\b(mindset|mental|discipline|focus)\b', 'Mindset'),
        (r'\b(life|living|journey|story)\b', 'Life Story'),
        (r'\b(age|aging|older|younger)\b', 'Aging'),
    ]
    
    # Find sentences matching patterns
    topic_candidates = []
    for i, sentence in enumerate(sentences):
        sentence_lower = sentence.lower()
        for pattern, topic_label in key_patterns:
            if re.search(pattern, sentence_lower):
                # Estimate timestamp (assuming ~150 words/min, ~2.5 words/sec)
                estimated_time = f"{int((i * 150) / 60):02d}:{int((i * 150) % 60):02d}"
                topic_candidates.append({
                    'topic': topic_label,
                    'quote': sentence[:100] + '...' if len(sentence) > 100 else sentence,
                    'timestamp': estimated_time,
                    'match': pattern
                })
                break
    
    # Remove duplicates and get unique topics
    seen_topics = set()
    unique_topics = []
    for t in topic_candidates:
        if t['topic'] not in seen_topics:
            seen_topics.add(t['topic'])
            unique_topics.append(t)
    
    # Fill in with generic topic extraction if needed
    if len(unique_topics) < num_topics:
        for i in range(len(unique_topics), num_topics):
            idx = i % len(sentences) if sentences else 0
            estimated_time = f"{int((idx * 150) / 60):02d}:{int((idx * 150) % 60):02d}"
            unique_topics.append({
                'topic': f'Key Moment {i+1}',
                'quote': sentences[idx][:100] + '...' if sentences and len(sentences[idx]) > 100 else (sentences[idx] if sentences else ''),
                'timestamp': estimated_time
            })
    
    return unique_topics[:num_topics]


# ============================================================================
# 2. GENERATE TITLE OPTIONS
# ============================================================================

def generate_title_options(topic: Dict) -> List[str]:
    """
    Generate 3 title variations for a topic:
    - Direct quote style
    - Curiosity gap style  
    - Power word style
    """
    topic_name = topic.get('topic', '')
    quote = topic.get('quote', '')
    
    # Extract meaningful words from quote
    words = [w for w in quote.split() if len(w) > 3][:6]
    key_phrase = ' '.join(words[:3]) if words else 'This'
    
    # Map topics to better title templates
    title_templates = {
        'Prep Mode': [
            f"I've Been in Perma Prep Mode Since 50",
            f"Perma Prep at 50: The Truth About Staying Lean",
            f"Why I Never Stop Prepping (Even at My Age)"
        ],
        'Age Milestone': [
            f"How I Got Lean After Turning 50",
            f"What Nobody Tells You About Getting Fit After 50",
            f"After 50, Here's My Exact Fitness Strategy"
        ],
        'Secret/Truth': [
            f"The Real Reason I Stay Lean",
            f"What Nobody Tells You About Fitness Over 50",
            f"The Truth About Getting Lean At Any Age"
        ],
        'Fitness Results': [
            f"I Got Lean At 50 - Here's How",
            f"How I Transformed My Body After 50",
            f"My Fitness Journey At 50: Real Results"
        ],
        'Consistency': [
            f"Why I Never Skip a Day (And You Should Too)",
            f"The Secret to Consistency After 50",
            f"How I Stay Consistent With Fitness"
        ],
        'Diet/Nutrition': [
            f"What I Eat to Stay Lean",
            f"My Diet for Getting (and Staying) Lean",
            f"Nutrition Secrets That Work After 50"
        ],
        'Training': [
            f"My Training Routine at 50",
            f"What My Workouts Look Like",
            f"The Exact Training That Got Me Lean"
        ],
        'Mindset': [
            f"The Mindset That Changed Everything",
            f"How I Think About Fitness",
            f"Mental Strategies for Long-Term Success"
        ],
        'Life Story': [
            f"My Fitness Journey",
            f"What I've Learned Getting Lean",
            f"How I Got Here: My Story"
        ],
        'Aging': [
            f"Fitness After 50: What Actually Works",
            f"What Changes When You're Over 50",
            f"How to Get Lean No Matter Your Age"
        ]
    }
    
    # Get templates for this topic or use defaults
    templates = title_templates.get(topic_name, [
        f"What I Learned About {topic_name}",
        f"How I Approach {topic_name}",
        f"Everything About {topic_name}"
    ])
    
    return templates[:3]


# ============================================================================
# 3. SEARCH YOUTUBE
# ============================================================================

def get_video_id(url: str) -> Optional[str]:
    """Extract video ID from YouTube URL."""
    if not url:
        return None
    
    # Handle various YouTube URL formats
    patterns = [
        r'(?:youtube\.com/watch\?v=)([a-zA-Z0-9_-]{11})',
        r'(?:youtu\.be/)([a-zA-Z0-9_-]{11})',
        r'(?:youtube\.com/embed/)([a-zA-Z0-9_-]{11})',
        r'(?:youtube\.com/v/)([a-zA-Z0-9_-]{11})',
    ]
    
    for pattern in patterns:
        match = re.search(pattern, url)
        if match:
            return match.group(1)
    
    return None


def get_transcript_from_youtube(video_id: str) -> str:
    """Fetch transcript from YouTube video using yt-dlp."""
    import subprocess
    import json
    
    try:
        # Use yt-dlp to get transcript
        cmd = [
            'yt-dlp',
            '--write-auto-sub',
            '--skip-download',
            '--sub-langs', 'en',
            '--dump-json',
            f'https://youtube.com/watch?v={video_id}'
        ]
        
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        
        if result.returncode == 0:
            # Try to get automatic captions
            cmd_transcript = [
                'yt-dlp',
                '--write-auto-sub',
                '--skip-download',
                '--sub-langs', 'en',
                '-o', f'/tmp/{video_id}',
                f'https://youtube.com/watch?v={video_id}'
            ]
            subprocess.run(cmd_transcript, capture_output=True, timeout=30)
            
            # Read the subtitle file if it exists
            subtitle_file = f'/tmp/{video_id}.en.vtt'
            if os.path.exists(subtitle_file):
                with open(subtitle_file, 'r') as f:
                    content = f.read()
                # Clean up VTT format
                lines = content.split('\n')
                text_lines = []
                for line in lines:
                    if line and not line.startswith('WEBVTT') and not line.startswith('00:') and '-->' not in line:
                        text_lines.append(line.strip())
                return ' '.join(text_lines)
        
        # Fallback: try using the youtube_transcript module with yt-dlp fallback
        from youtube_transcript import extract_transcript
        result = extract_transcript(f'https://youtube.com/watch?v={video_id}')
        if result.get('success'):
            return result.get('transcript', '')
        
        return ""
    except Exception as e:
        print(f"Could not fetch transcript: {e}")
        return ""


def search_youtube(query: str, api_key: str, max_results: int = 5) -> Dict:
    """
    Search YouTube for videos matching the query.
    Returns dict with search results.
    """
    if not query:
        return {
            "search_query": query,
            "results_found": 0,
            "top_videos": []
        }
    
    # If no API key, try web search fallback
    if not api_key:
        return search_youtube_fallback(query, max_results)
    
    url = "https://www.googleapis.com/youtube/v3/search"
    params = {
        "part": "snippet",
        "q": query,
        "type": "video",
        "maxResults": max_results,
        "key": api_key
    }
    
    try:
        response = requests.get(url, params=params, timeout=10)
        data = response.json()
        
        if 'error' in data:
            print(f"YouTube API error: {data['error']}")
            return {
                "search_query": query,
                "results_found": 0,
                "top_videos": []
            }
        
        videos = []
        for item in data.get('items', []):
            video_id = item['id', {}].get('videoId', '')
            snippet = item.get('snippet', {})
            
            # Get view count (requires additional API call)
            view_count = None
            try:
                stats_url = f"https://www.googleapis.com/youtube/v3/videos?part=statistics&id={video_id}&key={api_key}"
                stats_response = requests.get(stats_url, timeout=5)
                stats_data = stats_response.json()
                if stats_data.get('items'):
                    view_count = stats_data['items'][0]['statistics'].get('viewCount', '0')
            except:
                pass
            
            videos.append({
                "title": snippet.get('title', ''),
                "channel": snippet.get('channelTitle', ''),
                "published": snippet.get('publishedAt', '')[:10],
                "thumbnail": snippet.get('thumbnails', {}).get('high', {}).get('url', ''),
                "video_id": video_id,
                "views": int(view_count) if view_count else 0
            })
        
        # Sort by views
        videos.sort(key=lambda x: x['views'], reverse=True)
        
        return {
            "search_query": query,
            "results_found": data.get('pageInfo', {}).get('totalResults', len(videos)),
            "top_videos": videos
        }
        
    except Exception as e:
        print(f"YouTube search error: {e}")
        return {
            "search_query": query,
            "results_found": 0,
            "top_videos": []
        }


def search_youtube_fallback(query: str, max_results: int = 5) -> Dict:
    """
    Fallback search using web scraping when no API key is available.
    Uses YouTube's search page to find videos.
    """
    import re
    import json
    
    try:
        # Search YouTube directly via URL
        search_url = f"https://www.youtube.com/results?search_query={requests.utils.quote(query)}"
        headers = {
            'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
        }
        
        response = requests.get(search_url, headers=headers, timeout=15)
        html = response.text
        
        videos = []
        
        # Try to find JSON data in the page
        # YouTube embeds initial data in a script tag
        json_pattern = r'var ytInitialData = ({.*?});'
        match = re.search(json_pattern, html, re.DOTALL)
        
        if match:
            try:
                data = json.loads(match.group(1))
                
                # Navigate to video results
                contents = data.get('contents', {}).get('twoColumnSearchResultsRenderer', {})
                results = contents.get('primaryContents', {}).get('sectionListRenderer', {})
                items = results.get('contents', [])
                
                for item in items:
                    video_renderer = item.get('videoRenderer', {})
                    if not video_renderer:
                        continue
                    
                    video_id = video_renderer.get('videoId', '')
                    title_obj = video_renderer.get('title', {})
                    title = ''
                    for run in title_obj.get('runs', []):
                        title += run.get('text', '')
                    
                    channel_obj = video_renderer.get('shortBylineText', {})
                    channel = ''
                    for run in channel_obj.get('runs', []):
                        channel += run.get('text', '')
                    
                    # View count
                    view_obj = video_renderer.get('viewCountText', {})
                    views_str = view_obj.get('simpleText', '0').replace(',', '')
                    views = int(views_str) if views_str.isdigit() else 0
                    
                    # Published time
                    pub_obj = video_renderer.get('publishedTimeText', {})
                    published = pub_obj.get('simpleText', '')
                    
                    if video_id and title:
                        videos.append({
                            "title": title,
                            "channel": channel,
                            "published": published,
                            "thumbnail": f"https://img.youtube.com/vi/{video_id}/hqdefault.jpg",
                            "video_id": video_id,
                            "views": views
                        })
                        
                        if len(videos) >= max_results:
                            break
                            
            except json.JSONDecodeError as je:
                print(f"JSON parse error: {je}")
        
        # Sort by views
        videos.sort(key=lambda x: x['views'], reverse=True)
        
        return {
            "search_query": query,
            "results_found": len(videos),
            "top_videos": videos
        }
        
    except Exception as e:
        print(f"Web search fallback error: {e}")
        return {
            "search_query": query,
            "results_found": 0,
            "top_videos": []
        }


def research_title_on_youtube(title: str, api_key: str) -> Dict:
    """
    Search YouTube for a specific title concept.
    """
    return search_youtube(title, api_key, max_results=5)


# ============================================================================
# 4. MAIN RESEARCH FUNCTION
# ============================================================================

def research_episode_titles(youtube_url: str, api_key: str) -> Dict:
    """
    Full pipeline:
    1. Extract transcript from YouTube
    2. Find 5 real topics from transcript
    3. For each topic:
       - Generate 3 title options
       - Search YouTube for each
       - Get top 5 videos per search
    4. Return everything
    """
    video_id = get_video_id(youtube_url)
    if not video_id:
        return {"error": "Invalid YouTube URL", "topics": []}
    
    print(f"Researching video: {video_id}")
    
    # Get transcript
    transcript = get_transcript_from_youtube(video_id)
    if not transcript:
        # Fallback: use empty transcript, will generate generic topics
        print("Could not fetch transcript, using fallback topics")
        transcript = ""
    
    # Extract topics
    topics = extract_topics(transcript, num_topics=5)
    
    # For each topic, generate titles and search YouTube
    results = {
        "video_id": video_id,
        "youtube_url": youtube_url,
        "transcript_available": bool(transcript),
        "topics": []
    }
    
    for topic in topics:
        titles = generate_title_options(topic)
        
        # Search YouTube for each title option
        topic_result = {
            "topic": topic['topic'],
            "quote": topic['quote'],
            "timestamp": topic['timestamp'],
            "titles": []
        }
        
        for title in titles:
            youtube_results = research_title_on_youtube(title, api_key)
            topic_result["titles"].append({
                "title": title,
                "youtube_results": youtube_results
            })
        
        results["topics"].append(topic_result)
    
    return results


# ============================================================================
# 5. UTILITY FUNCTIONS FOR UI
# ============================================================================

def format_view_count(views: int) -> str:
    """Format view count with commas."""
    if views >= 1000000:
        return f"{views / 1000000:.1f}M"
    elif views >= 1000:
        return f"{views / 1000:.1f}K"
    return str(views)


def render_topic_results(results: Dict) -> str:
    """
    Render research results in the specified UI format.
    """
    output = []
    output.append("=" * 60)
    output.append("YOUTUBE TITLE RESEARCH RESULTS")
    output.append("=" * 60)
    output.append("")
    
    video_id = results.get('video_id', '')
    output.append(f"Video ID: {video_id}")
    output.append(f"Transcript: {'✓ Available' if results.get('transcript_available') else '✗ Not Available'}")
    output.append("")
    
    for i, topic in enumerate(results.get('topics', []), 1):
        output.append(f"TOPIC {i}: {topic['topic']}")
        output.append("━" * 60)
        
        quote = topic.get('quote', '')
        if quote:
            output.append(f"📝 Quote: \"{quote[:80]}{'...' if len(quote) > 80 else ''}\"")
            output.append(f"   Timestamp: {topic.get('timestamp', 'N/A')}")
            output.append("")
        
        for j, title_option in enumerate(topic.get('titles', [])):
            letter = chr(65 + j)  # A, B, C
            title = title_option.get('title', '')
            yt_results = title_option.get('youtube_results', {})
            
            output.append(f"Title Option {letter}: \"{title}\"")
            output.append("YouTube Results:")
            
            videos = yt_results.get('top_videos', [])
            if videos:
                for video in videos[:5]:
                    views_str = format_view_count(video.get('views', 0))
                    channel = video.get('channel', 'Unknown')
                    title = video.get('title', 'Untitled')
                    output.append(f"  🎬 {title[:50]}")
                    output.append(f"     {views_str} views | {channel}")
            else:
                output.append("  (No results found)")
            
            output.append("")
        
        output.append("")
    
    return "\n".join(output)


# ============================================================================
# TEST RUN
# ============================================================================

if __name__ == "__main__":
    import os
    
    # Test with the provided YouTube URL
    test_url = "https://youtu.be/OVVRtX32BcE"
    
    # Try to get API key from environment
    api_key = os.environ.get('YOUTUBE_API_KEY', '')
    
    if not api_key:
        print("WARNING: No YOUTUBE_API_KEY found in environment")
        print("The tool will run but YouTube searches will return empty results")
        print("")
    
    # Run research
    print("Starting title research...")
    results = research_episode_titles(test_url, api_key)
    
    # Render output
    output = render_topic_results(results)
    print(output)
    
    # Also output as JSON for programmatic use
    print("\n" + "=" * 60)
    print("JSON Output:")
    print("=" * 60)
    print(json.dumps(results, indent=2))
