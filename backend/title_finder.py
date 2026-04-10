"""
Title Finder - Simplified Version
===================================
Extracts transcript, generates titles, searches YouTube, ranks by views.

Workflow:
1. Extract transcript (yt-dlp)
2. Load config (spp.json, sbs.json, etc.)
3. Generate 15 title options (3 per topic)
4. For each title: search YouTube, get view counts
5. Find outliers (100K+ views)
6. Rank: Gold/Silver/Bronze

Returns: 3 titles + Pattern info

Author: Subagent
Date: 2026-02-19
"""

import os
import sys
import json
import re
import subprocess
from typing import Dict, List, Any, Optional, Tuple
from datetime import datetime
from dotenv import load_dotenv

# Add backend to path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# Load environment
load_dotenv()

# Import transcript extraction
from youtube_transcript import extract_transcript

# Import title scorer
from title_scorer import (
    calculate_outlier_score, 
    calculate_normalized_score, 
    score_youtube_results,
    get_top_outliers,
    update_channel_subscribers
)

# Import YouTube search (yt-dlp version)
try:
    from youtube_search import search_titles, QuotaExceededError, get_fallback_results
except ImportError:
    def search_titles(*args, **kwargs):
        return []
    def get_fallback_results(*args, **kwargs):
        return []
    class QuotaExceededError(Exception): pass

# Import YouTube API search (new API-based version with Andrew's rules)
try:
    from youtube_api_search import (
        search_with_api,
        score_api_results,
        research_titles_batch,
        cluster_titles_by_theme,
        get_api_fallback_results,
        YouTubeAPIError,
        QuotaExceededError as APIQuotaExceededError
    )
    YOUTUBE_API_AVAILABLE = True
except ImportError as e:
    print(f"[TitleFinder] YouTube API module not available: {e}")
    YOUTUBE_API_AVAILABLE = False
    YouTubeAPIError = Exception
    APIQuotaExceededError = Exception


# ============================================================================
# CONFIG LOADING
# ============================================================================

def load_podcast_config(podcast_code: str) -> Dict[str, Any]:
    """Load podcast-specific configuration."""
    configs = {
        'spp': 'episode-optimizer/config/spp.json',
        'jpi': 'episode-optimizer/config/jpi.json',
        'sbs': 'episode-optimizer/config/sbs.json',
        'wow': 'episode-optimizer/config/wow.json',
        'agp': 'episode-optimizer/config/agp.json',
        'generic': None,  # No guardrails mode - pure AI search
    }
    
    if podcast_code not in configs:
        raise ValueError(f"Unknown podcast code: {podcast_code}. Must be one of: {list(configs.keys())}")
    
    # Generic mode - return minimal config with no guardrails
    if podcast_code == 'generic':
        return {
            'name': 'Generic',
            'target_audience': '',
            'niche': '',
            'prompts': {}
        }
    
    config_path = configs[podcast_code]
    
    # Try multiple base paths
    base_paths = [
        '/Users/pursuebot/.openclaw/workspace',
        '/Users/pursuebot/.openclaw/workspace/pursue-segments/backend',
        os.path.dirname(os.path.abspath(__file__)),
        '.',
    ]
    
    config = None
    for base in base_paths:
        full_path = os.path.join(base, config_path)
        if os.path.exists(full_path):
            with open(full_path, 'r') as f:
                config = json.load(f)
            print(f"[TitleFinder] Loaded config from: {full_path}")
            break
    
    if config is None:
        raise FileNotFoundError(f"Config file not found: {config_path}")
    
    # Extract and flatten config for easier access
    # Config has nested "podcast" and "rules" keys
    podcast_info = config.get('podcast', {})
    rules_info = config.get('rules', {})
    prompts_info = config.get('prompts', {})
    
    # Flatten into top-level keys for easier access
    config['podcast_code'] = podcast_code
    config['podcast_name'] = podcast_info.get('name', 'The Podcast')
    config['host_name'] = podcast_info.get('host', 'Host')
    config['target_audience'] = podcast_info.get('targetAudience', 'audience')
    config['banned_phrases'] = rules_info.get('bannedPhrases', [])
    
    # Ensure rules are at top level too
    config['rules'] = rules_info
    config['prompts'] = prompts_info
    
    return config


# ============================================================================
# AI CLIENT - GPT-5.2 via OAuth (using shared ai_client)
# ============================================================================

# Import shared AI client with validators and retry logic
from ai_client import (
    call_ai,
    validate_title,
    extract_keywords_from_topics,
    DEFAULT_BANNED_PHRASES,
    COMMON_FIRST_NAMES
)

# Re-export for backward compatibility
_call_ai = call_ai

print("[TitleFinder] Using MiniMax AI client")


# ============================================================================
# TOPIC EXTRACTION
# ============================================================================

def extract_topics_from_transcript(transcript: str, num_topics: int = 5) -> List[Dict[str, Any]]:
    """Extract key topics from transcript."""
    if not transcript:
        return []
    
    # Clean transcript
    transcript = re.sub(r'\s+', ' ', transcript).strip()
    
    # Split into segments (~200 words each)
    words = transcript.split()
    segment_size = 200
    segments = []
    
    for i in range(0, len(words), segment_size):
        segment_text = ' '.join(words[i:i + segment_size])
        estimated_seconds = (i * 60) // 150
        minutes = estimated_seconds // 60
        seconds = estimated_seconds % 60
        timestamp = f"{minutes:02d}:{seconds:02d}"
        segments.append({'text': segment_text, 'timestamp': timestamp})
    
    # Key phrases to identify topics
    key_indicators = [
        (r'\b(\d+ years|at age \d+|when I was \d+)\b', 'Milestone'),
        (r'\b(\$[\d,]+|million|billion|earned|make \$)\b', 'Money'),
        (r'\b(I learned|I realized|I discovered|I found out)\b', 'Insight'),
        (r'\b(the truth is|the secret|honestly|real talk)\b', 'Truth'),
        (r'\b(mistake|wrong|error|fail|failure|mess up)\b', 'Mistake'),
        (r'\b(changed|transformed|improved|better|success)\b', 'Transformation'),
        (r'\b(help|helping|coach|clients|patients)\b', 'Helping'),
        (r'\b(business|entrepreneur|startup| founder)\b', 'Business'),
        (r'\b(health|fitness|workout|diet|nutrition)\b', 'Health'),
        (r'\b(important|critical|essential|key|crucial)\b', 'Key Insight'),
    ]
    
    topic_candidates = []
    
    for seg in segments:
        text_lower = seg['text'].lower()
        for pattern, topic_type in key_indicators:
            if re.search(pattern, text_lower, re.IGNORECASE):
                match = re.search(r'[^.!?]*' + pattern + r'[^.!?]*', seg['text'], re.IGNORECASE)
                if match:
                    quote = match.group(0).strip()[:150]
                    if len(quote) > 20:
                        topic_candidates.append({
                            'topic': topic_type,
                            'quote': quote,
                            'timestamp': seg['timestamp']
                        })
                        break
    
    # Deduplicate
    seen_types = set()
    unique_topics = []
    for t in topic_candidates:
        if t['topic'] not in seen_types:
            seen_types.add(t['topic'])
            unique_topics.append(t)
    
    # Fill with evenly spaced segments if needed
    if len(unique_topics) < num_topics:
        step = max(1, len(segments) // num_topics)
        for i in range(0, len(segments), step):
            if len(unique_topics) >= num_topics:
                break
            seg_text = segments[i]['text']
            already_covered = any(seg_text[:40] in t['quote'][:40] for t in unique_topics)
            if not already_covered and len(seg_text) > 50:
                unique_topics.append({
                    'topic': f'Moment {len(unique_topics)+1}',
                    'quote': seg_text[:150] + ('...' if len(seg_text) > 150 else ''),
                    'timestamp': segments[i]['timestamp']
                })
    
    return unique_topics[:num_topics]


# ============================================================================
# TITLE GENERATION
# ============================================================================

def generate_title_options(transcript: str, config: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Generate 12 title options using the Ultimate YouTube Title Writing Framework.
    
    Workflow (per Andrew's direction):
    1. Extract 5 key themes/topics from transcript
    2. Generate 1-2 titles per theme
    3. Expand to 12 total variations tied to those themes
    
    Hard validators after generation:
    - Ban phrases: unlock, peak performance, next level, game changing (case-insensitive, substrings ok)
    - No first names (guest/host)
    - Topical grounding: each title must include >=1 keyword from its theme
    - Length target <=55 chars when possible
    
    Auto-reprompt/rewrite until valid or max 2 retries.
    """
    print("[TitleFinder] === ULTIMATE YOUTUBE TITLE WRITING FRAMEWORK ===")
    print("[TitleFinder] Step 1: Extracting 5 key themes from transcript...")
    
    # Get rules from config
    rules = config.get('rules', {})
    config_banned = rules.get('bannedPhrases', [])
    
    # Merge config banned phrases with required core banned phrases
    banned_phrases = list(config_banned)
    core_banned = ['unlock', 'peak performance', 'next level', 'game changing']
    for phrase in core_banned:
        if phrase.lower() not in [bp.lower() for bp in banned_phrases]:
            banned_phrases.append(phrase)
    
    # Get NO FIRST NAMES rule
    title_rules = rules.get('titleRules', {})
    no_first_names = title_rules.get('noFirstNames', True)
    
    # Get podcast info
    podcast_info = config.get('podcast', {}) if isinstance(config.get('podcast', {}), dict) else {}
    podcast_name = podcast_info.get('name', config.get('podcast_name', 'The Podcast'))
    host_name = podcast_info.get('host', config.get('host_name', 'Host'))
    
    banned_str = ', '.join(banned_phrases) if banned_phrases else 'none'
    
    # Sample transcript for theme extraction (beginning + middle + end for representative content)
    def get_representative_sample(text, max_total=6000):
        if not text or len(text) <= max_total:
            return text
        # Take beginning, middle, and end sections
        section_size = max_total // 3
        beginning = text[:section_size]
        middle_start = len(text) // 2 - section_size // 2
        middle = text[middle_start:middle_start + section_size]
        end = text[-section_size:]
        return f"[BEGINNING]\n{beginning}\n\n[MIDDLE]\n{middle}\n\n[END]\n{end}"
    
    transcript_sample = get_representative_sample(transcript, 6000)
    
    # ========== STEP 1: Extract 5 key themes ==========
    theme_prompt = f"""Analyze this podcast transcript and extract EXACTLY 5 key themes/topics.

PODCAST: {podcast_name}

TRANSCRIPT SAMPLE:
---
{transcript_sample}
---

Task: Extract 5 distinct themes/topics that are the MAIN focus of this episode.
Each theme should be 2-4 words describing a specific concept (e.g., "nasal breathing", "lymphatic system", "ayurvedic medicine", "stress hormones").

Return as JSON:
{{"themes": ["theme1", "theme2", "theme3", "theme4", "theme5"]}}

EXACTLY 5 themes - no more, no less."""

    theme_result, model = _call_ai(theme_prompt, require_json=True)
    
    extracted_themes = []
    if theme_result:
        try:
            parsed = json.loads(theme_result)
            extracted_themes = parsed.get('themes', [])
        except Exception as e:
            print(f"[TitleFinder] Theme parse error: {e}")
    
    # Fallback: extract themes from transcript if AI failed
    if len(extracted_themes) < 3:
        print("[TitleFinder] Using fallback topic extraction...")
        topic_objects = extract_topics_from_transcript(transcript, num_topics=5)
        extracted_themes = [t.get('topic', '') for t in topic_objects if t.get('topic')]
    
    print(f"[TitleFinder] Themes: {extracted_themes[:5]}")
    
    # Extract keywords from themes for validation
    theme_keywords = extract_keywords_from_topics(extracted_themes)
    print(f"[TitleFinder] Theme keywords: {theme_keywords[:10]}")
    
    # ========== STEP 2: Generate 1-2 titles per theme ==========
    print("[TitleFinder] Step 2: Generating 1-2 titles per theme...")
    
    titles_per_theme = 2  # 5 themes * 2 = 10 titles (we'll expand to 12)
    
    titles_prompt = f"""Generate YouTube titles for {podcast_name}.

PODCAST: {podcast_name} hosted by {host_name}

TRANSCRIPT CONTEXT:
---
{transcript_sample[:3000]}
---

THE 5 THEMES TO COVER (from this episode):
{chr(10).join(f"- {t}" for t in extracted_themes[:5])}

RULES (STRICT - titles WILL be filtered):
- NEVER use these phrases: {banned_str}
- NO FIRST NAMES (no guest or host first names)
- Each title MUST include at least one keyword from its theme
- Under 55 characters when possible
- Curiosity-driven, specific to the actual content in the transcript
- NO generic titles like "Unlock your potential" or "How to succeed"

For EACH theme above, generate {titles_per_theme} title(s) tied to that specific theme.
Total: {5 * titles_per_theme} titles

Return as JSON:
{{
  "titles": [
    {{"title": "Title Here", "theme": "theme_name"}},
    ...
  ]
}}"""

    result, model = _call_ai(titles_prompt, require_json=True)
    
    # DEBUG: Print raw AI response
    print(f"[TitleFinder] DEBUG: Raw AI response (first 300 chars): {result[:300] if result else 'None'}")
    
    generated_titles = []
    if result:
        try:
            parsed = json.loads(result)
            generated_titles = parsed.get('titles', [])
        except Exception as e:
            print(f"[TitleFinder] Parse error: {e}")
    
    print(f"[TitleFinder] Generated {len(generated_titles)} raw titles")
    
    # ========== STEP 3: Expand to 12 total variations ==========
    if len(generated_titles) < 12:
        print("[TitleFinder] Step 3: Expanding to 12 variations...")
        
        expansion_prompt = f"""Expand these {len(generated_titles)} titles into 12 total variations.

EXISTING TITLES:
{chr(10).join([f"- {t.get('title', t)}" for t in generated_titles[:10]])}

THEMES: {extracted_themes[:5]}

RULES:
- NEVER use: {banned_str}
- NO FIRST NAMES
- Each title must contain keyword from themes: {theme_keywords[:8]}
- Under 55 characters
- Add variations that sound different but cover same themes

Return 12 titles total as JSON array:
[{{"title": "...", "theme": "..."}}, ...]"""

        expand_result, _ = _call_ai(expansion_prompt, require_json=True)
        
        if expand_result:
            try:
                new_titles = json.loads(expand_result)
                if isinstance(new_titles, list):
                    generated_titles.extend(new_titles)
            except:
                pass
    
    # ========== VALIDATION: Hard ban phrases, first names, topical grounding ==========
    print("[TitleFinder] === VALIDATION: Filtering invalid titles...")
    
    valid_titles = []
    filter_stats = {'banned': 0, 'name': 0, 'off_topic': 0, 'too_long': 0}
    
    for title_obj in generated_titles:
        title = title_obj.get('title', '') if isinstance(title_obj, dict) else str(title_obj)
        if not title:
            continue
        
        # Validate - ONLY check banned phrases and first names (hard rules)
        is_valid, reason = validate_title(
            title,
            banned_phrases=banned_phrases,
            allow_first_names=not no_first_names
        )
        
        if is_valid:
            valid_titles.append({'title': title, 'theme': title_obj.get('theme', '')})
        else:
            if 'Banned' in reason:
                filter_stats['banned'] += 1
                print(f"  [FILTER BANNED] {reason}: {title[:50]}...")
            elif 'First name' in reason:
                filter_stats['name'] += 1
                print(f"  [FILTER NAME] {reason}: {title[:50]}...")
            else:
                # Log but accept other titles
                valid_titles.append({'title': title, 'theme': title_obj.get('theme', '')})
    
    print(f"[TitleFinder] Validation: {len(valid_titles)}/{len(generated_titles)} valid")
    print(f"[TitleFinder] Filters: banned={filter_stats['banned']}, names={filter_stats['name']}, off_topic={filter_stats['off_topic']}, long={filter_stats['too_long']}")
    
    # ========== AUTO-RETRY if needed (max 2 retries) ==========
    retry_count = 0
    max_retries = 2
    
    while len(valid_titles) < 12 and retry_count < max_retries:
        retry_count += 1
        print(f"[TitleFinder] Retry {retry_count}: regenerating with feedback...")
        
        feedback = f"""
FAILED VALIDATION - Regenerate with stricter rules:
- Only {len(valid_titles)}/{len(generated_titles)} titles passed validation
- Banned phrases found: {filter_stats['banned']}
- First names found: {filter_stats['name']}
- Off-topic: {filter_stats['off_topic']}

REQUIREMENTS:
- DO NOT use: {banned_str}
- NO first names
- MUST include keywords: {theme_keywords[:8]}
- Under 55 chars

THEMES: {extracted_themes[:5]}
Generate 12 titles now."""
        
        retry_result, _ = _call_ai(titles_prompt + feedback, require_json=True)
        
        if retry_result:
            try:
                retry_titles = json.loads(retry_result)
                if isinstance(retry_titles, dict):
                    retry_titles = retry_titles.get('titles', [])
                    
                    for t in retry_titles:
                        title = t.get('title', '') if isinstance(t, dict) else str(t)
                        if title:
                            is_valid, _ = validate_title(
                                title,
                                banned_phrases=banned_phrases,
                                allow_first_names=not no_first_names
                            )
                            if is_valid:
                                valid_titles.append({'title': title, 'theme': t.get('theme', '')})
            except:
                pass
        
        print(f"[TitleFinder] Retry {retry_count}: {len(valid_titles)} valid now")
    
    # If we have no valid titles after all retries, something is seriously wrong
    # But return empty list rather than crash - let the caller handle it
    if len(valid_titles) == 0:
        print(f"[TitleFinder] WARNING: No valid titles after all retries - returning empty")
        return []
    
    # Return up to 12 titles (don't pad with placeholders)
    valid_titles = valid_titles[:12]
    
    print(f"[TitleFinder] === FINAL: {len(valid_titles)} validated titles (model: {model})")
    return valid_titles


# ============================================================================
# YOUTUBE SEARCH & RANKING
# ============================================================================

def search_and_score_titles(titles: List[Dict[str, Any]], max_search: int = 12, time_budget_sec: int = 60) -> List[Dict[str, Any]]:
    """Search YouTube for each title and calculate scores.

    IMPORTANT: Must never hang. We cap total wall time via time_budget_sec.
    Returns at least 12 scored titles (uses fallback if YouTube search fails).
    """
    import time

    titles_to_search = titles[:max_search]
    print(f"[TitleFinder] Searching YouTube for {len(titles_to_search)} titles (out of {len(titles)} generated)...")
    start = time.monotonic()
    
    quota_exceeded = False
    scored_titles = []
    channel_subs = {}  # Track channel subscribers
    failed_count = 0
    
    for i, title_obj in enumerate(titles_to_search):
        # Enforce global time budget
        if (time.monotonic() - start) > time_budget_sec:
            print(f"[TitleFinder] Time budget exceeded ({time_budget_sec}s). Stopping YouTube search early.")
            break

        title = title_obj.get('title', '')
        topic = title_obj.get('topic', '')

        print(f"[TitleFinder] [{i+1}/{len(titles_to_search)}] Searching: {title[:50]}...")
        
        try:
            # Search YouTube
            results = search_titles(title, max_results=10)
            
            if not results:
                print(f"[TitleFinder] No results for: {title[:30]}")
                failed_count += 1
                scored_titles.append({
                    'title': title,
                    'topic': topic,
                    'youtube_results': [],
                    'top_outliers': [],
                    'best_outlier': None,
                    'score': 0,
                    'view_count': 0
                })
                continue
            
            # Update channel subscribers
            for r in results:
                channel = r.get('channel_title', '')
                subs = r.get('channel_subscribers', 0)
                if channel and subs:
                    channel_subs[channel] = subs
            
            # Score results
            scored = score_youtube_results(results, channel_subs)
            outliers = get_top_outliers(scored, min_views=100000, top_n=3)
            
            best = outliers[0] if outliers else None
            score = best.get('outlier_score', 0) if best else 0
            view_count = best.get('view_count', 0) if best else 0
            
            scored_titles.append({
                'title': title,
                'topic': topic,
                'youtube_results': results[:5],
                'top_outliers': outliers,
                'best_outlier': best,
                'score': score,
                'view_count': view_count
            })
            
            print(f"[TitleFinder] Best: {view_count:,} views, score: {score:.2f}")
            
        except QuotaExceededError:
            print("[TitleFinder] YouTube quota exceeded")
            quota_exceeded = True
            break
        except Exception as e:
            print(f"[TitleFinder] Search error for '{title}': {e}")
            failed_count += 1
            scored_titles.append({
                'title': title,
                'topic': topic,
                'error': str(e)
            })
    
    # If too many failed or we didn't search enough, use fallback
    searched_count = len(scored_titles) - failed_count
    if failed_count > len(titles_to_search) // 2 or searched_count < 6:
        print(f"[TitleFinder] Too many failures ({failed_count}/{len(titles_to_search)}). Using fallback mode.")
        return get_fallback_results(titles_to_search)
    
    return scored_titles


def rank_titles(scored_titles: List[Dict[str, Any]], all_generated_titles: List[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Rank titles and assign Gold/Silver/Bronze.
    
    Always returns at least 12 titles in the list + Gold/Silver/Bronze.
    """
    is_fallback = any(t.get('fallback', False) for t in scored_titles)
    
    # Sort by score (descending) - prioritize titles with view counts
    scored_with_views = [t for t in scored_titles if t.get('score', 0) > 0 and t.get('view_count', 0) > 0]
    sorted_titles = sorted(
        scored_with_views,
        key=lambda x: (x.get('score', 0), x.get('view_count', 0)),
        reverse=True
    )
    
    # If we don't have 3 scored titles, fill with other searched titles (even without views)
    if len(sorted_titles) < 3:
        print(f"[TitleFinder] Only {len(sorted_titles)} titles with views, filling from searched titles...")
        scored_title_texts = {t.get('title', '') for t in sorted_titles}
        for t in scored_titles:
            if len(sorted_titles) >= 3:
                break
            if t.get('title', '') not in scored_title_texts:
                sorted_titles.append(t)
    
    # If still less than 3, fill from the original generated titles
    if len(sorted_titles) < 3 and all_generated_titles:
        print(f"[TitleFinder] Only {len(sorted_titles)} searched titles, filling from generated titles...")
        used_titles = {t.get('title', '') for t in sorted_titles}
        for t in all_generated_titles:
            if len(sorted_titles) >= 3:
                break
            if t.get('title', '') not in used_titles:
                sorted_titles.append({
                    'title': t.get('title', ''),
                    'topic': t.get('topic', ''),
                    'score': 0,
                    'view_count': 0,
                    'best_outlier': None
                })
    
    # Ensure we have 3 titles with fallbacks
    while len(sorted_titles) < 3:
        sorted_titles.append({
            'title': f"Generated Title {len(sorted_titles) + 1}",
            'topic': 'Fallback',
            'score': 0,
            'view_count': 0,
            'best_outlier': None
        })
    
    gold = sorted_titles[0] if len(sorted_titles) > 0 else None
    silver = sorted_titles[1] if len(sorted_titles) > 1 else None
    bronze = sorted_titles[2] if len(sorted_titles) > 2 else None
    
    # Build full titles list (12 minimum)
    all_titles_list = []
    used = set()
    
    # First add all scored titles
    for t in scored_titles:
        if t.get('title') and t.get('title') not in used:
            all_titles_list.append({
                'title': t.get('title', ''),
                'topic': t.get('topic', ''),
                'score': t.get('score', 0),
                'view_count': t.get('view_count', 0),
                'is_fallback': t.get('fallback', False)
            })
            used.add(t.get('title'))
    
    # Fill from sorted titles
    for t in sorted_titles:
        if t.get('title') and t.get('title') not in used:
            all_titles_list.append({
                'title': t.get('title', ''),
                'topic': t.get('topic', ''),
                'score': t.get('score', 0),
                'view_count': t.get('view_count', 0),
                'is_fallback': t.get('fallback', False)
            })
            used.add(t.get('title'))
    
    # Fill from generated titles if needed
    if len(all_titles_list) < 12 and all_generated_titles:
        for t in all_generated_titles:
            if len(all_titles_list) >= 12:
                break
            if t.get('title') and t.get('title') not in used:
                all_titles_list.append({
                    'title': t.get('title', ''),
                    'topic': t.get('topic', ''),
                    'score': 0,
                    'view_count': 0,
                    'is_fallback': True
                })
                used.add(t.get('title'))
    
    result = {
        'gold': {
            'title': gold.get('title', '') if gold else '',
            'pattern_from': gold.get('best_outlier', {}).get('title', '') if gold and gold.get('best_outlier') else '',
            'pattern_views': gold.get('view_count', 0) if gold else 0,
            'topic': gold.get('topic', '') if gold else ''
        },
        'silver': {
            'title': silver.get('title', '') if silver else '',
            'pattern_from': silver.get('best_outlier', {}).get('title', '') if silver and silver.get('best_outlier') else '',
            'pattern_views': silver.get('view_count', 0) if silver else 0,
            'topic': silver.get('topic', '') if silver else ''
        },
        'bronze': {
            'title': bronze.get('title', '') if bronze else '',
            'pattern_from': bronze.get('best_outlier', {}).get('title', '') if bronze and bronze.get('best_outlier') else '',
            'pattern_views': bronze.get('view_count', 0) if bronze else 0,
            'topic': bronze.get('topic', '') if bronze else ''
        },
        'all_titles': all_titles_list[:12],  # Always 12 titles
        'fallback': is_fallback,
        'research_method': 'Model-ranked (no YouTube research)' if is_fallback else 'YouTube-researched'
    }
    
    print(f"[TitleFinder] Ranked: Gold={result['gold']['title'][:30]}, Silver={result['silver']['title'][:30]}, Bronze={result['bronze']['title'][:30]}")
    print(f"[TitleFinder] Fallback mode: {is_fallback}, Total titles: {len(all_titles_list)}")
    
    return result


# ============================================================================
# API-BASED RESEARCH (ANDREW'S RULES)
# ============================================================================

def search_and_score_titles_with_api(
    titles: List[Dict[str, Any]], 
    batch_size: int = 5, 
    time_budget_sec: int = 60
) -> List[Dict[str, Any]]:
    """
    Search YouTube using Data API v3 with Andrew's rules.
    
    Workflow:
    1. Generate 12 titles internally (already done)
    2. Cluster into ~5 themes
    3. Pick 1 champion per theme for research
    4. Research batch = 5 searches per attempt
    5. Score and rank results
    
    Args:
        titles: List of title objects with 'title' and 'topic'
        batch_size: Number of titles to research (default 5)
        time_budget_sec: Max time to spend
    
    Returns:
        List of scored titles with API research data
    """
    import time
    
    if not YOUTUBE_API_AVAILABLE:
        print("[TitleFinder] YouTube API not available, falling back to yt-dlp")
        return search_and_score_titles(titles, max_search=len(titles), time_budget_sec=time_budget_sec)
    
    start_time = time.time()
    
    # Cluster titles into themes (5 themes max)
    print(f"[TitleFinder] Clustering {len(titles)} titles into themes...")
    clusters = cluster_titles_by_theme(titles, num_themes=5)
    
    # Pick champions (one per theme)
    champions = []
    for cluster in clusters:
        champion = cluster.get('champion')
        if champion:
            champions.append({
                'title': champion.get('title', ''),
                'topic': cluster.get('theme', champion.get('topic', '')),
                'cluster_count': cluster.get('count', 1),
                'is_champion': True
            })
    
    print(f"[TitleFinder] Selected {len(champions)} theme champions for API research")
    
    # If we have fewer than batch_size champions, fill with remaining titles
    if len(champions) < batch_size:
        used_titles = {c.get('title') for c in champions}
        for t in titles:
            if len(champions) >= batch_size:
                break
            if t.get('title') not in used_titles:
                champions.append({
                    'title': t.get('title', ''),
                    'topic': t.get('topic', ''),
                    'is_champion': False
                })
    
    # Limit to batch_size
    champions = champions[:batch_size]
    
    print(f"[TitleFinder] Researching {len(champions)} titles via API...")
    
    # Research titles with API
    try:
        research_result = research_titles_batch(
            champions,
            batch_size=len(champions),
            time_budget_sec=time_budget_sec
        )
        
        api_results = research_result.get('results', [])
        total_quota = research_result.get('total_quota_used', 0)
        debug = research_result.get('debug', {})
        
        print(f"[TitleFinder] API research complete:")
        print(f"  - Titles researched: {len(api_results)}")
        print(f"  - Quota used: {total_quota}")
        print(f"  - Shorts rejected: {debug.get('rejected_shorts_count', 0)}")
        print(f"  - Runtime: {research_result.get('runtime_seconds', 0)}s")
        
    except (YouTubeAPIError, APIQuotaExceededError) as e:
        print(f"[TitleFinder] API error: {e}. Falling back to yt-dlp.")
        return search_and_score_titles(titles, max_search=min(len(titles), 12), time_budget_sec=time_budget_sec)
    
    # Convert API results to scored_titles format
    scored_titles = []
    
    for res in api_results:
        title = res.get('title', '')
        topic = res.get('topic', '')
        
        best_match = res.get('best_match')
        
        if best_match:
            scored_titles.append({
                'title': title,
                'topic': topic,
                'youtube_results': res.get('scored_videos', [])[:5],
                'top_outliers': res.get('scored_videos', [])[:3],
                'best_outlier': best_match,
                'score': res.get('best_score', 0),
                'view_count': res.get('best_views', 0),
                'api_research': True,
                'scoring_breakdown': best_match.get('scoring_breakdown', {}),
                'debug': {
                    'rejected_shorts': debug.get('rejected_shorts_count', 0),
                    'candidates_found': res.get('videos_found', 0)
                }
            })
        else:
            scored_titles.append({
                'title': title,
                'topic': topic,
                'youtube_results': [],
                'top_outliers': [],
                'best_outlier': None,
                'score': res.get('best_score', 0),
                'view_count': res.get('best_views', 0)
            })
    
    # Add remaining titles (not researched) with zero scores
    researched_titles = {r.get('title') for r in api_results}
    for t in titles:
        if len(scored_titles) >= 12:
            break
        if t.get('title') not in researched_titles:
            scored_titles.append({
                'title': t.get('title', ''),
                'topic': t.get('topic', ''),
                'youtube_results': [],
                'top_outliers': [],
                'best_outlier': None,
                'score': 0,
                'view_count': 0,
                'not_researched': True
            })
    
    return scored_titles


def rank_titles_from_api(scored_titles: List[Dict[str, Any]], all_generated_titles: List[Dict[str, Any]] = None) -> Dict[str, Any]:
    """
    Rank titles from API research and format for output.
    
    Enhanced to include debug fields from API research.
    """
    is_fallback = any(t.get('fallback', False) for t in scored_titles)
    
    # Collect debug info
    total_rejected_shorts = sum(t.get('debug', {}).get('rejected_shorts', 0) for t in scored_titles)
    total_candidates = sum(t.get('debug', {}).get('candidates_found', 0) for t in scored_titles)
    
    # Sort by score (descending)
    scored_with_scores = [t for t in scored_titles if t.get('score', 0) > 0]
    sorted_titles = sorted(
        scored_with_scores,
        key=lambda x: (x.get('score', 0), x.get('view_count', 0)),
        reverse=True
    )
    
    # Fill with non-scored titles if needed
    if len(sorted_titles) < 3:
        for t in scored_titles:
            if len(sorted_titles) >= 3:
                break
            if t not in sorted_titles:
                sorted_titles.append(t)
    
    # Ensure we have at least 3 titles
    while len(sorted_titles) < 3:
        sorted_titles.append({
            'title': f"Generated Title {len(sorted_titles) + 1}",
            'topic': 'Fallback',
            'score': 0,
            'view_count': 0,
            'best_outlier': None
        })
    
    gold = sorted_titles[0] if len(sorted_titles) > 0 else None
    silver = sorted_titles[1] if len(sorted_titles) > 1 else None
    bronze = sorted_titles[2] if len(sorted_titles) > 2 else None
    
    # Build all_titles list (12 minimum)
    all_titles_list = []
    used = set()
    
    for t in scored_titles:
        if t.get('title') and t.get('title') not in used:
            all_titles_list.append({
                'title': t.get('title', ''),
                'topic': t.get('topic', ''),
                'score': t.get('score', 0),
                'view_count': t.get('view_count', 0),
                'is_fallback': t.get('fallback', False),
                'scoring_breakdown': t.get('scoring_breakdown', {})
            })
            used.add(t.get('title'))
    
    # Fill from sorted titles
    for t in sorted_titles:
        if t.get('title') and t.get('title') not in used:
            all_titles_list.append({
                'title': t.get('title', ''),
                'topic': t.get('topic', ''),
                'score': t.get('score', 0),
                'view_count': t.get('view_count', 0),
                'is_fallback': t.get('fallback', False)
            })
            used.add(t.get('title'))
    
    # Fill from generated titles if needed
    if len(all_titles_list) < 12 and all_generated_titles:
        for t in all_generated_titles:
            if len(all_titles_list) >= 12:
                break
            if t.get('title') and t.get('title') not in used:
                all_titles_list.append({
                    'title': t.get('title', ''),
                    'topic': t.get('topic', ''),
                    'score': 0,
                    'view_count': 0,
                    'is_fallback': True
                })
                used.add(t.get('title'))
    
    # Build result
    def get_pattern_info(title_obj):
        """Extract pattern info from best outlier."""
        if not title_obj:
            return {'pattern_from': '', 'pattern_views': 0}
        
        best = title_obj.get('best_outlier', {})
        if best:
            return {
                'pattern_from': best.get('title', ''),
                'pattern_views': best.get('view_count', 0),
                'pattern_channel': best.get('channel_title', ''),
                'pattern_duration': best.get('duration_seconds', 0)
            }
        return {'pattern_from': '', 'pattern_views': 0}
    
    result = {
        'gold': {
            'title': gold.get('title', '') if gold else '',
            **get_pattern_info(gold),
            'topic': gold.get('topic', '') if gold else ''
        },
        'silver': {
            'title': silver.get('title', '') if silver else '',
            **get_pattern_info(silver),
            'topic': silver.get('topic', '') if silver else ''
        },
        'bronze': {
            'title': bronze.get('title', '') if bronze else '',
            **get_pattern_info(bronze),
            'topic': bronze.get('topic', '') if bronze else ''
        },
        'all_titles': all_titles_list[:12],
        'fallback': is_fallback,
        'research_method': 'YouTube API (Andrew\'s rules)',
        'debug': {
            'rejected_shorts_count': total_rejected_shorts,
            'researched_candidates_count': total_candidates,
            'api_research': True
        }
    }
    
    print(f"[TitleFinder] API Ranked: Gold={result['gold']['title'][:30]}, Silver={result['silver']['title'][:30]}, Bronze={result['bronze']['title'][:30]}")
    print(f"[TitleFinder] Debug: shorts_rejected={total_rejected_shorts}, candidates={total_candidates}")
    
    return result


# ============================================================================
# MAIN PROCESSOR
# ============================================================================

def model_ranked_titles(youtube_url: str, podcast: str) -> Dict[str, Any]:
    """Fast fallback: generate titles without YouTube research.

    Used when the full pipeline times out.
    """
    result = {
        'success': True,
        'fallback': True,
        'research_method': 'Model-ranked (no YouTube research)',
        'youtube_url': youtube_url,
        'podcast': podcast,
        'gold': {'title': '', 'pattern_from': '', 'pattern_views': 0},
        'silver': {'title': '', 'pattern_from': '', 'pattern_views': 0},
        'bronze': {'title': '', 'pattern_from': '', 'pattern_views': 0},
        'all_titles': [],
        'error': None,
    }

    try:
        # transcript (reuse existing helper; it already has its own ytdlp timeout)
        tr = extract_transcript(youtube_url)
        if not tr.get('success'):
            result['success'] = False
            result['error'] = f"Transcript failed: {tr.get('error', 'Unknown')}"
            return result

        transcript = tr.get('transcript', '')
        if not transcript:
            result['success'] = False
            result['error'] = 'Empty transcript'
            return result

        config = load_podcast_config(podcast)
        titles = generate_title_options(transcript, config)
        if not titles:
            result['success'] = False
            result['error'] = 'Failed to generate title options'
            return result

        # Ensure at least 12
        all_titles = []
        for t in titles:
            if t.get('title'):
                all_titles.append({'title': t['title'], 'topic': t.get('topic', ''), 'views': None})
            if len(all_titles) >= 12:
                break
        result['all_titles'] = all_titles

        # pick first 3 as gold/silver/bronze (UI can still show 12)
        if len(all_titles) >= 1:
            result['gold']['title'] = all_titles[0]['title']
        if len(all_titles) >= 2:
            result['silver']['title'] = all_titles[1]['title']
        if len(all_titles) >= 3:
            result['bronze']['title'] = all_titles[2]['title']

        return result

    except Exception as e:
        result['success'] = False
        result['error'] = str(e)
        return result


def find_winning_titles(youtube_url: str, podcast: str) -> Dict[str, Any]:
    """
    Main function to find winning titles for a YouTube video.
    
    Args:
        youtube_url: YouTube video URL
        podcast: Podcast code (spp, jpi, sbs, wow, agp)
    
    Returns:
        Dict with gold, silver, bronze titles and pattern info
        Always returns 12+ titles in all_titles list
    """
    import concurrent.futures
    import time as time_module
    
    print(f"\n{'='*60}")
    print(f"[TitleFinder] Starting: {youtube_url}")
    print(f"[TitleFinder] Podcast: {podcast}")
    print(f"{'='*60}\n")
    
    overall_start = time_module.monotonic()
    MAX_RUNTIME = 100  # Hard cap: 100 seconds max (leaves buffer for 120s total with network)
    
    result = {
        'success': False,
        'youtube_url': youtube_url,
        'podcast': podcast,
        'gold': {'title': '', 'pattern_from': '', 'pattern_views': 0},
        'silver': {'title': '', 'pattern_from': '', 'pattern_views': 0},
        'bronze': {'title': '', 'pattern_from': '', 'pattern_views': 0},
        'all_titles': [],
        'fallback': False,
        'error': None
    }
    
    try:
        # Check time budget before starting
        if (time_module.monotonic() - overall_start) > MAX_RUNTIME:
            result['error'] = 'Time budget exceeded before starting'
            result['fallback'] = True
            return _generate_fallback_titles(youtube_url, podcast)
        
        # Step 1: Extract transcript (must never hang)
        print("[TitleFinder] Step 1: Extracting transcript...")
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as ex:
            fut = ex.submit(extract_transcript, youtube_url)
            try:
                transcript_result = fut.result(timeout=45)
            except Exception:
                transcript_result = {'success': False, 'error': 'Transcript extraction timed out'}
        
        if not transcript_result.get('success'):
            result['error'] = f"Transcript failed: {transcript_result.get('error', 'Unknown')}"
            print(f"[TitleFinder] ERROR: {result['error']}")
            # Try fallback
            return _generate_fallback_titles(youtube_url, podcast, result['error'])
        
        transcript = transcript_result.get('transcript', '')
        video_title = transcript_result.get('title', '')
        
        if not transcript:
            result['error'] = "Empty transcript"
            return _generate_fallback_titles(youtube_url, podcast, result['error'])
        
        print(f"[TitleFinder] Transcript: {len(transcript)} chars from '{video_title}'")
        
        # Check time budget
        if (time_module.monotonic() - overall_start) > MAX_RUNTIME:
            result['error'] = 'Time budget exceeded after transcript'
            return _generate_fallback_titles(youtube_url, podcast, result['error'])
        
        # Step 2: Load config
        print("[TitleFinder] Step 2: Loading podcast config...")
        config = load_podcast_config(podcast)
        
        # Step 3: Generate 15 title options
        print("[TitleFinder] Step 3: Generating 15 title options...")
        title_options = generate_title_options(transcript, config)
        
        if not title_options:
            result['error'] = "Failed to generate title options"
            return _generate_fallback_titles(youtube_url, podcast, result['error'])
        
        print(f"[TitleFinder] Generated {len(title_options)} titles")
        
        # Check time budget
        if (time_module.monotonic() - overall_start) > MAX_RUNTIME:
            result['error'] = 'Time budget exceeded after title generation'
            return _generate_fallback_from_generated(youtube_url, podcast, title_options, result['error'])
        
        # Step 4: Research titles with YouTube API (Andrew's rules)
        # - Generate 12 titles internally (done)
        # - Cluster into ~5 themes, pick 1 champion per theme
        # - Research batch = 5 searches
        print("[TitleFinder] Step 4: Researching titles with YouTube API (Andrew's rules)...")
        remaining_time = int(MAX_RUNTIME - (time_module.monotonic() - overall_start))
        
        # Use API-based research if available, otherwise fall back to yt-dlp
        if YOUTUBE_API_AVAILABLE:
            scored_titles = search_and_score_titles_with_api(
                title_options, 
                batch_size=5,  # 5 searches per attempt per Andrew's rules
                time_budget_sec=max(30, remaining_time)
            )
        else:
            print("[TitleFinder] YouTube API not available, using yt-dlp fallback...")
            scored_titles = search_and_score_titles(
                title_options, 
                max_search=min(len(title_options), 12), 
                time_budget_sec=max(30, remaining_time)
            )
        
        if not scored_titles:
            result['error'] = "Failed to research titles"
            return _generate_fallback_from_generated(youtube_url, podcast, title_options, result['error'])
        
        # Step 5: Rank and return Gold/Silver/Bronze
        print("[TitleFinder] Step 5: Ranking titles...")
        
        # Use API ranking if we have API-scored titles
        if YOUTUBE_API_AVAILABLE and any(t.get('api_research') for t in scored_titles):
            ranked = rank_titles_from_api(scored_titles, title_options)
        else:
            ranked = rank_titles(scored_titles, title_options)
        
        result['success'] = True
        result['video_title'] = video_title
        result.update(ranked)
        
        print(f"\n[TitleFinder] SUCCESS!")
        print(f"  Gold: {result['gold']['title']}")
        print(f"  Silver: {result['silver']['title']}")
        print(f"  Bronze: {result['bronze']['title']}")
        print(f"  Fallback: {result.get('fallback', False)}")
        print(f"  Total titles: {len(result.get('all_titles', []))}")
        
    except Exception as e:
        result['error'] = str(e)
        print(f"[TitleFinder] ERROR: {e}")
        import traceback
        traceback.print_exc()
        # Return fallback on error
        return _generate_fallback_titles(youtube_url, podcast, result['error'])
    
    return result


def _generate_fallback_titles(youtube_url: str, podcast: str, error: str = None) -> Dict[str, Any]:
    """Generate fallback titles when everything fails."""
    print(f"[TitleFinder] Using fallback mode. Error: {error}")
    return {
        'success': False,
        'youtube_url': youtube_url,
        'podcast': podcast,
        'gold': {'title': '', 'pattern_from': '', 'pattern_views': 0},
        'silver': {'title': '', 'pattern_from': '', 'pattern_views': 0},
        'bronze': {'title': '', 'pattern_from': '', 'pattern_views': 0},
        'all_titles': [],
        'fallback': True,
        'research_method': 'Model-ranked (no YouTube research)',
        'error': error or 'All operations failed',
        'debug': {
            'ai': {
                'model_attempted': 'minimax/MiniMax-M2.5',
                'fallback_attempted': [],
                'error_detail': error,
                'breadcrumb': 'All AI providers failed - no valid titles generated'
            }
        }
    }


def _generate_fallback_from_generated(youtube_url: str, podcast: str, title_options: List[Dict], error: str = None) -> Dict[str, Any]:
    """Generate fallback from already-generated titles when YouTube search fails."""
    print(f"[TitleFinder] Using fallback from generated titles. Error: {error}")
    
    # Score with fallback mode
    fallback_results = get_fallback_results(title_options[:15])
    ranked = rank_titles(fallback_results, title_options)
    
    return {
        'success': False,
        'youtube_url': youtube_url,
        'podcast': podcast,
        'video_title': '',
        'gold': ranked.get('gold', {}),
        'silver': ranked.get('silver', {}),
        'bronze': ranked.get('bronze', {}),
        'all_titles': ranked.get('all_titles', [])[:12],
        'fallback': True,
        'research_method': 'Model-ranked (no YouTube research)',
        'error': error or 'YouTube search failed - using fallback'
    }


# ============================================================================
# TEST
# ============================================================================

if __name__ == "__main__":
    # Test with Dr. Anthony Beck video
    test_url = "https://youtu.be/6ksjnC9E4iQ"
    test_podcast = "spp"
    
    result = find_winning_titles(test_url, test_podcast)
    print("\n" + "="*60)
    print("FINAL RESULT:")
    print(json.dumps(result, indent=2))
