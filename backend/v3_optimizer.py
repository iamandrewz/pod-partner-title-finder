"""
V3 Episode Optimizer - SIMPLIFIED

Simple flow:
1. User pastes YouTube link
2. Extract transcript
3. AI identifies 3 (PRIMARY topic first!)
4.-5 key topics Generate 10-15 titles (weighted toward primary topic)
5. User picks a winning title - DONE

User-driven mimic flow:
1. User finds a successful YouTube title they like
2. Pastes it into "Mimic a Title" input
3. Tool generates 3-5 titles following that pattern

No auto-YouTube search, no broken validation.
"""

import json
import subprocess
import re
from typing import Dict, List, Any, Optional

# Reuse existing modules
from youtube_transcript import extract_transcript as _extract_transcript
from optimizer.ai_client import _call_ai


def _build_context_section(niche: str = "", audience: str = "", focus: str = "") -> str:
    """Build context section only if fields have values."""
    context_lines = []
    if niche and niche.strip():
        context_lines.append(f"PODCAST NICHE: {niche.strip()}")
    if audience and audience.strip():
        context_lines.append(f"TARGET AUDIENCE: {audience.strip()}")
    if focus and focus.strip():
        context_lines.append(f"EPISODE FOCUS: {focus.strip()}")
    
    if context_lines:
        return "\n".join(context_lines) + "\n\n"
    return ""



def search_youtube(query: str, max_results: int = 5) -> List[Dict]:
    """
    Search YouTube and return actual results.
    Uses yt-dlp ytsearch feature.
    """
    try:
        # Use full path to yt-dlp
        ytdlp_path = "/Users/ottis/Library/Python/3.9/bin/yt-dlp"
        cmd = [
            ytdlp_path,
            f"ytsearch{max_results}:{query}",
            "--dump-json",
            "--no-download",
            "--flat-playlist"
        ]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        
        videos = []
        for line in result.stdout.strip().split('\n'):
            if line:
                data = json.loads(line)
                videos.append({
                    "title": data.get("title"),
                    "views": data.get("view_count", 0),
                    "channel": data.get("channel") or data.get("uploader"),
                    "url": f"https://youtube.com/watch?v={data.get('id')}"
                })
        return videos
    except Exception as e:
        print(f"[V3] YouTube search failed: {e}")
        return []
        return []


def extract_transcript(youtube_url: str) -> Dict[str, Any]:
    """Step 1: Extract transcript from YouTube video."""
    print(f"[V3] Extracting transcript from: {youtube_url}")
    result = _extract_transcript(youtube_url)
    
    if result.get('success'):
        print(f"[V3] Transcript: {len(result.get('transcript', ''))} chars")
    else:
        print(f"[V3] Extraction failed: {result.get('error')}")
    
    return result


def extract_topics(transcript: str, niche: str = "", audience: str = "", focus: str = "") -> List[str]:
    """Step 2: AI identifies 3-5 key topics from transcript, PRIMARY topic first."""
    print("[V3] Extracting topics from transcript...")
    
    # Use ENTIRE transcript - MiniMax has 256k token context window
    # No limits, no sampling, no hardcoding - give it everything
    sample = transcript
    
    # Build context section if fields provided
    context_section = _build_context_section(niche, audience, focus)
    
    prompt = f"""{context_section}Analyze this transcript and extract the KEY TOPICS discussed.

CRITICAL RULES:
1. Rank topics by HOW MUCH OF THE EPISODE is about each topic
2. The FIRST topic MUST be the MAIN subject — what the majority of the content is actually about
3. Be SPECIFIC — describe what they're actually discussing

BANNED GENERIC TOPICS (NEVER USE THESE):
- "General Discussion"
- "Various Topics" 
- "Miscellaneous"
- "Conversation"
- "Interview"
- "Podcast Discussion"
- Single words like "Business", "Life", "Work"

GOOD TOPIC EXAMPLES:
- "Why a top performer decided to leave their company"
- "Building a home services business from scratch"
- "The mindset shift that changed his career"
- "How to retain your best employees"

BAD TOPIC EXAMPLES:
- "General Discussion" (TOO VAGUE)
- "Career" (TOO SHORT)
- "Interview with guest" (NOT A TOPIC)

TRANSCRIPT:
{sample}

Return ONLY a JSON array of 3-5 SPECIFIC topic strings:
["Main topic (be specific)", "Secondary topic", "Secondary topic"]"""

    result, model = _call_ai(
        prompt,
        "You are an expert at identifying what content is PRIMARILY about. Focus on the main subject, not tangents.",
        max_tokens=2500,
        prefer_quality=True
    )
    
    if not result:
        print("[V3] Failed to extract topics, retrying with simpler prompt...")
        # Retry with a shorter, simpler prompt
        simple_prompt = f"""What are the 3-5 main topics discussed in this podcast transcript?

{transcript[:4000]}

Return a JSON array of specific topics (NOT generic like "General Discussion"):
["Topic 1", "Topic 2", "Topic 3"]"""
        result, _ = _call_ai(simple_prompt, "Extract podcast topics.", max_tokens=500, prefer_quality=True)
        
    if not result:
        print("[V3] Topic extraction failed completely")
        return ["Career and workplace insights", "Personal growth and decisions"]
    
    # Parse JSON
    try:
        # Clean markdown code blocks
        clean = re.sub(r'```(?:json)?\s*', '', result)
        clean = re.sub(r'```\s*$', '', clean)
        
        match = re.search(r'\[[\s\S]*\]', clean)
        if match:
            topics = json.loads(match.group())
        else:
            topics = json.loads(clean)
        
        # Filter out generic/banned topics
        banned = ['general discussion', 'various topics', 'miscellaneous', 'conversation', 
                  'interview', 'podcast discussion', 'discussion', 'general']
        filtered = [t for t in topics if t.lower().strip() not in banned and len(t) > 10]
        
        if not filtered:
            # All topics were generic - try to salvage by using raw transcript keywords
            print("[V3] All topics were generic, extracting from transcript keywords")
            filtered = ["Career decisions and workplace dynamics", "Personal growth and life changes"]
        
        print(f"[V3] Extracted {len(filtered)} topics (PRIMARY: {filtered[0] if filtered else 'None'}): {filtered}")
        return filtered[:5]
    except json.JSONDecodeError:
        print(f"[V3] Failed to parse topics JSON: {result[:200]}")
        return ["General Discussion"]


def generate_titles(transcript: str, topics: List[str], niche: str = "", audience: str = "", focus: str = "") -> List[Dict[str, Any]]:
    """Step 3: Generate 10-15 titles, heavily weighted toward primary topic."""
    print(f"[V3] Generating titles for {len(topics)} topics...")
    
    # Use first 5000 chars for context
    sample = transcript[:5000] if len(transcript) > 5000 else transcript
    
    # Separate primary from secondary topics
    primary_topic = topics[0] if topics else "General Discussion"
    secondary_topics = topics[1:] if len(topics) > 1 else []
    
    secondary_str = "\n".join(f"- {t}" for t in secondary_topics) if secondary_topics else "(none)"
    
    # Build context section if fields provided
    context_section = _build_context_section(niche, audience, focus)
    
    prompt = f"""{context_section}Generate 12 YouTube title options for this video.

PRIMARY TOPIC (generate 8-10 titles for this):
{primary_topic}

SECONDARY TOPICS (generate only 2-4 titles total for these):
{secondary_str}

CONTENT CONTEXT:
{sample}

IMPORTANT: Most titles (8-10) MUST be about the PRIMARY topic. 
Only 2-4 titles can be about secondary topics.
The PRIMARY topic is what the episode is actually about.

Requirements:
- Each title under 60 characters
- Use power words: Secret, Truth, Nobody, Mistake, How, Why, What
- Create curiosity gaps
- Include numbers when specific (e.g., "3 Ways", "7 Steps")
- Make them clickable and specific to the content

Return as JSON array:
[
  {{"title": "Title 1", "topic": "Which topic this relates to"}},
  {{"title": "Title 2", "topic": "Which topic this relates to"}},
  ...
]"""

    result, model = _call_ai(
        prompt,
        "You are a YouTube title expert. Generate titles that match what the content is PRIMARILY about.",
        max_tokens=1500,
        prefer_quality=True
    )
    
    if not result:
        print("[V3] Failed to generate titles")
        return []
    
    # Parse JSON
    try:
        clean = re.sub(r'```(?:json)?\s*', '', result)
        clean = re.sub(r'```\s*$', '', clean)
        
        match = re.search(r'\[[\s\S]*\]', clean)
        if match:
            titles = json.loads(match.group())
        else:
            titles = json.loads(clean)
        
        # Ensure proper format
        formatted = []
        for t in titles:
            if isinstance(t, str):
                formatted.append({"title": t, "topic": primary_topic})
            elif isinstance(t, dict) and t.get("title"):
                formatted.append({
                    "title": t["title"],
                    "topic": t.get("topic", primary_topic)
                })
        
        print(f"[V3] Generated {len(formatted)} titles")
        return formatted[:15]
    except json.JSONDecodeError:
        print(f"[V3] Failed to parse titles JSON: {result[:300]}")
        return []


def format_titles(titles: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Step 4: Simply format titles for output (no YouTube validation)."""
    print(f"[V3] Formatting {len(titles)} titles for output...")
    
    formatted = []
    for i, title_obj in enumerate(titles):
        title = title_obj.get("title", "")
        topic = title_obj.get("topic", "")
        
        formatted.append({
            "title": title,
            "topic": topic,
        })
    
    print(f"[V3] Formatting complete")
    return formatted


def generate_mimicked_titles(title_to_mimic: str, transcript: str, topics: List[str], niche: str = "", audience: str = "", focus: str = "") -> List[Dict[str, Any]]:
    """
    Generate titles that mimic the pattern of a user-provided successful YouTube title.
    
    Args:
        title_to_mimic: The YouTube title pattern to mimic
        transcript: The episode transcript for context
        topics: Extracted topics from the episode
    
    Returns:
        List of 3-5 titles following the pattern but matching episode content
    """
    print(f"[V3] Generating mimicked titles based on: {title_to_mimic[:40]}...")
    
    # Use first 5000 chars for context
    sample = transcript[:5000] if len(transcript) > 5000 else transcript
    
    primary_topic = topics[0] if topics else "the main topic"
    topics_str = ", ".join(topics[:3]) if topics else "the main topic"
    
    # Build context section if fields provided
    context_section = _build_context_section(niche, audience, focus)
    
    prompt = f"""{context_section}Create 3-5 YouTube titles that MIMIC the STRUCTURE and PATTERN of this successful title,
but apply it to THIS episode's content.

SUCCESSFUL TITLE TO MIMIC (keep the pattern):
{title_to_mimic}

EPISODE TOPICS:
{topics_str}

EPISODE CONTENT:
{sample}

IMPORTANT: The PRIMARY topic is "{primary_topic}". Most titles should be about this topic.

PATTERN ANALYSIS - What makes the original title work:
- Hook style: Does it start with a question, statement, or hook word?
- Structure: Is it list-style, how-to, vs style, comparison?
- Power words: What compelling words are used?
- Numbers: Does it use numbers? (e.g., "3 Ways", "7 Steps")
- Specificity: How specific is it?

Create titles that:
- Follow the SAME structural pattern as the original
- Apply the pattern to our episode's actual content
- Keep the same level of specificity and power
- Under 60 characters
- Make them compelling and clickable

Return as JSON array:
[
  {{"title": "Mimicked Title 1", "topic": "Primary Topic"}},
  {{"title": "Mimicked Title 2", "topic": "Primary Topic"}},
  ...
]"""

    result, model = _call_ai(
        prompt,
        "You are a YouTube title expert. Create titles that mimic successful patterns.",
        max_tokens=1000,
        prefer_quality=True
    )
    
    if not result:
        print("[V3] Failed to generate mimicked titles")
        return []
    
    # Parse JSON
    try:
        clean = re.sub(r'```(?:json)?\s*', '', result)
        clean = re.sub(r'```\s*$', '', clean)
        
        match = re.search(r'\[[\s\S]*\]', clean)
        if match:
            titles = json.loads(match.group())
        else:
            titles = json.loads(clean)
        
        # Ensure proper format
        formatted = []
        for t in titles:
            if isinstance(t, str):
                formatted.append({"title": t, "topic": primary_topic})
            elif isinstance(t, dict) and t.get("title"):
                formatted.append({
                    "title": t["title"],
                    "topic": t.get("topic", primary_topic)
                })
        
        print(f"[V3] Generated {len(formatted)} mimicked titles")
        return formatted[:5]  # Limit to 5
    except json.JSONDecodeError:
        print(f"[V3] Failed to parse mimicked titles JSON: {result[:300]}")
        return []


def optimize(youtube_url: str, niche: str = "", audience: str = "", focus: str = "", manual_transcript: str = None) -> Dict[str, Any]:
    """
    Main function: Full V3 optimization flow.
    
    Returns:
    {
        "success": bool,
        "video_title": str,
        "transcript": str,
        "topics": list[str],
        "titles": list[dict],
        "error": str (if failed)
    }
    """
    print(f"\n[V3] === Starting V3 Optimization ===")
    print(f"[V3] URL: {youtube_url}")
    
    # If manual transcript provided, skip extraction
    if manual_transcript and manual_transcript.strip():
        print(f"[V3] Using manual transcript ({len(manual_transcript)} chars)")
        transcript_result = {
            "success": True,
            "transcript": manual_transcript.strip(),
            "title": "Manual Transcript",
            "video_id": None,
        }
    else:
        # Step 1: Extract transcript
        transcript_result = extract_transcript(youtube_url)
    
    if not transcript_result.get("success"):
        raw_error = transcript_result.get('error', '')
        
        # Clean up specific error messages
        if "processing this video" in raw_error.lower() or "check back later" in raw_error.lower():
            error_msg = "YouTube is still processing your video. Try again once YouTube is done processing."
            error_hint = "Wait until YouTube has added closed captions to your video, then we can help you find titles."
        elif "private" in raw_error.lower():
            error_msg = "This video is private or unavailable."
            error_hint = "Make sure the video is public or unlisted, and that it has closed captions."
        elif "disabled" in raw_error.lower():
            error_msg = "Transcripts are disabled for this video."
            error_hint = "Enable closed captions on your YouTube video, then try again."
        else:
            error_msg = "Failed to extract transcript from this video."
            error_hint = "Make sure the video is public and has closed captions available."
        
        return {
            "success": False,
            "error": error_msg,
            "error_hint": error_hint,
            "video_title": None,
            "transcript": "",
            "topics": [],
            "titles": []
        }
    
    transcript = transcript_result.get("transcript", "")
    video_title = transcript_result.get("title", "Unknown Video")
    
    if len(transcript) < 100:
        return {
            "success": False,
            "error": "Transcript too short (< 100 chars)",
            "video_title": video_title,
            "transcript": "",
            "topics": [],
            "titles": []
        }
    
    # Step 2: Extract topics (PRIMARY topic will be first)
    topics = extract_topics(transcript, niche=niche, audience=audience, focus=focus)
    
    # Generate episode summary
    print("[V3] Generating episode summary...")
    summary_prompt = f"""Based on these topics and transcript, write a 1-2 sentence summary of what this podcast episode is about.

Topics: {topics}
Transcript (first 2000 chars): {transcript[:2000]}

Write ONLY the summary, nothing else. Keep it under 50 words."""

    episode_summary, _ = _call_ai(summary_prompt, "You summarize podcast episodes concisely.", max_tokens=100)
    
    if not episode_summary:
        print("[V3] Failed to generate episode summary, using video title")
        episode_summary = video_title
    
    
    # Step 3: Generate titles (weighted toward primary topic)
    titles = generate_titles(transcript, topics, niche=niche, audience=audience, focus=focus)
    
    if not titles:
        return {
            "success": False,
            "error": "Failed to generate titles",
            "video_title": video_title,
            "transcript": transcript,
            "topics": topics,
            "titles": []
        }
    
    # Step 4: Format titles for output
    formatted_titles = format_titles(titles)
    
    # Step 5: Search YouTube for each generated title
    print("[V3] Searching YouTube for each title...")
    for title_obj in formatted_titles:
        title_text = title_obj.get("title", "")
        youtube_results = search_youtube(title_text, max_results=3)
        title_obj["youtube_matches"] = youtube_results
    
    # Step 6: Search YouTube for each topic too
    print("[V3] Searching YouTube for each topic...")
    topic_searches = {}
    for topic in topics:
        topic_searches[topic] = search_youtube(topic, max_results=3)
    
    print(f"\n[V3] === Optimization Complete ===")
    print(f"[V3] Video: {video_title}")
    print(f"[V3] Topics: {len(topics)} (PRIMARY: {topics[0] if topics else 'None'})")
    print(f"[V3] Titles: {len(formatted_titles)}")
    
    return {
        "success": True,
        "video_title": video_title,
        "transcript": transcript,
        "topics": topics,
        "topic_searches": topic_searches,
        "titles": formatted_titles,
        "episode_summary": episode_summary,
        "error": None
    }


def mimic_title(transcript_summary: str, topics: List[str], title_to_mimic: str, niche: str = "", audience: str = "", focus: str = "") -> Dict[str, Any]:
    """
    Generate titles that mimic a user-provided successful YouTube title.
    
    Args:
        transcript_summary: Summary or excerpt of the episode transcript
        topics: List of topics extracted from the episode
        title_to_mimic: The YouTube title pattern to mimic
    
    Returns:
    {
        "success": bool,
        "titles": list[dict],
        "error": str (if failed)
    }
    """
    print(f"\n[V3] === Starting Mimic Generation ===")
    print(f"[V3] Title to mimic: {title_to_mimic}")
    print(f"[V3] Topics: {topics}")
    
    if not title_to_mimic or not title_to_mimic.strip():
        return {
            "success": False,
            "error": "title_to_mimic is required",
            "titles": []
        }
    
    if not topics:
        return {
            "success": False,
            "error": "topics are required",
            "titles": []
        }
    
    # Generate mimicked titles
    mimicked_titles = generate_mimicked_titles(
        title_to_mimic.strip(),
        transcript_summary,
        topics,
        niche=niche,
        audience=audience,
        focus=focus
    )
    
    if not mimicked_titles:
        return {
            "success": False,
            "error": "Failed to generate mimicked titles",
            "titles": []
        }
    
    print(f"[V3] === Mimic Generation Complete ===")
    print(f"[V3] Generated {len(mimicked_titles)} mimicked titles")
    
    return {
        "success": True,
        "titles": mimicked_titles,
        "error": None
    }


# CLI test
if __name__ == "__main__":
    import sys
    
    if len(sys.argv) < 2:
        print("Usage: python v3_optimizer.py <youtube_url>")
        print("Example: python v3_optimizer.py https://www.youtube.com/watch?v=abc123")
        sys.exit(1)
    
    url = sys.argv[1]
    result = optimize(url)
    
    print("\n" + "="*60)
    print("RESULTS")
    print("="*60)
    
    if result["success"]:
        print(f"\nVideo: {result['video_title']}")
        print(f"\nTopics (PRIMARY first): {', '.join(result['topics'])}")
        print(f"\nTop Titles:")
        for i, t in enumerate(result['titles'][:5], 1):
            print(f"  {i}. {t['title']} (topic: {t['topic']})")
    else:
        print(f"\nError: {result['error']}")
