"""
Episode Optimizer V3 - FIXED VERSION
CRITICAL FIX: Uses FULL prompts from config files only - NO FALLBACKS

Features:
1. Output Selection (only generate what user selects)
2. Podcast Dropdown with config loading (SPP/JPI/SBS/WOW/AGP)
3. Medal System for Top 3 Titles (Gold/Silver/Bronze)
4. Uses FULL prompts from config files ONLY
5. Gemini first, then OpenAI fallback

Author: Subagent
Date: 2026-02-18
"""

import os
import json
import re
import difflib
from typing import Dict, List, Any, Optional, Tuple
from datetime import datetime
from dotenv import load_dotenv
from concurrent.futures import ThreadPoolExecutor, as_completed
import threading

# Similarity threshold for title matching (PHASE 1 safety gate)
# Titles with best_outlier similarity below this threshold are marked low-confidence
TITLE_SIMILARITY_THRESHOLD = 0.45

# REMOVED: All hard-coded content filters per Andrew's directive
# Everything must come from the TRANSCRIPT, not from preset blacklists
# The only allowed presets are: duration > 3 min, views > 1k, and podcast config

# def is_irrelevant_content - REMOVED: No hard-coded blacklists

# Load environment variables
load_dotenv()

# Import local modules
from youtube_transcript import extract_transcript, TranscriptExtractionError
from title_scorer import (
    calculate_outlier_score, 
    calculate_normalized_score, 
    score_youtube_results,
    get_top_outliers,
    format_score_display,
    update_channel_subscribers
)

# Import YouTube search
try:
    from youtube_search import search_titles, QuotaExceededError
except ImportError as e:
    print(f"[ERROR] Failed to import youtube_search: {e}")
    def search_titles(*args, **kwargs):
        raise RuntimeError(f"youtube_search module not available. Install required dependencies. Original error: {e}")
    class QuotaExceededError(Exception):
        def __init__(self, msg="YouTube API quota exceeded"):
            super().__init__(msg)

# Import OpenAI
try:
    from openai import OpenAI
    OPENAI_AVAILABLE = True
except ImportError as e:
    print(f"[WARNING] Failed to import OpenAI: {e}")
    OPENAI_AVAILABLE = False

# Import AI client from optimizer module
from optimizer.ai_client import (
    _check_gemini_available,
    _ensure_openai_configured,
    _call_gemini,
    _call_openai,
    _call_ai,
)


# ============================================================================
# CONFIG LOADING
# ============================================================================

def load_podcast_config(podcast_code: str) -> Dict[str, Any]:
    """
    Load podcast-specific configuration.
    
    Args:
        podcast_code: One of 'spp', 'jpi', 'sbs', 'wow', 'agp'
    
    Returns:
        Config dictionary with podcast-specific settings
    
    Raises:
        FileNotFoundError: If config file doesn't exist
        KeyError: If required prompts are missing
    """
    configs = {
        'spp': 'episode-optimizer/config/spp.json',
        'jpi': 'episode-optimizer/config/jpi.json',
        'sbs': 'episode-optimizer/config/sbs.json',
        'wow': 'episode-optimizer/config/wow.json',
        'agp': 'episode-optimizer/config/agp.json',
    }
    
    if podcast_code not in configs:
        raise ValueError(f"Unknown podcast code: {podcast_code}. Must be one of: {list(configs.keys())}")
    
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
            print(f"[Optimizer] Loaded config from: {full_path}")
            break
    
    if config is None:
        raise FileNotFoundError(f"Config file not found: {config_path}")
    
    # Validate required prompts exist
    prompts = config.get('prompts', {})
    required_prompts = ['chapters', 'description', 'tags']  # thumbnailText is optional
    missing_prompts = [p for p in required_prompts if not prompts.get(p)]
    
    if missing_prompts:
        raise KeyError(f"Config for {podcast_code} is missing required prompts: {missing_prompts}")
    
    # Add podcast_code to config for reference
    config['podcast_code'] = podcast_code
    
    return config


def update_podcast_config(podcast_code: str, updates: Dict[str, Any]) -> bool:
    """
    Update podcast-specific configuration with new values.
    Particularly useful for updating youtubeSearchKeywords from niche detection.
    
    Args:
        podcast_code: One of 'spp', 'jpi', 'sbs', 'wow', 'agp'
        updates: Dictionary of values to update (e.g., {'youtubeSearchKeywords': [...]})
    
    Returns:
        True if successful, False otherwise
    """
    configs = {
        'spp': 'episode-optimizer/config/spp.json',
        'jpi': 'episode-optimizer/config/jpi.json',
        'sbs': 'episode-optimizer/config/sbs.json',
        'wow': 'episode-optimizer/config/wow.json',
        'agp': 'episode-optimizer/config/agp.json',
    }
    
    if podcast_code not in configs:
        print(f"[Optimizer] Unknown podcast code: {podcast_code}")
        return False
    
    config_path = configs[podcast_code]
    
    # Try multiple base paths
    base_paths = [
        '/Users/pursuebot/.openclaw/workspace',
        '/Users/pursuebot/.openclaw/workspace/pursue-segments/backend',
        os.path.dirname(os.path.abspath(__file__)),
        '.',
    ]
    
    full_path = None
    for base in base_paths:
        candidate = os.path.join(base, config_path)
        if os.path.exists(candidate):
            full_path = candidate
            break
    
    if not full_path:
        print(f"[Optimizer] Config file not found: {config_path}")
        return False
    
    try:
        # Load existing config
        with open(full_path, 'r') as f:
            config = json.load(f)
        
        # Apply updates
        for key, value in updates.items():
            config[key] = value
        
        # Save updated config
        with open(full_path, 'w') as f:
            json.dump(config, f, indent=2)
        
        print(f"[Optimizer] Updated config for {podcast_code}: {list(updates.keys())}")
        return True
        
    except Exception as e:
        print(f"[Optimizer] Error updating config: {e}")
        return False


# ============================================================================
# HUMANIZER SKILL
# ============================================================================

class Humanizer:
    """
    Removes AI writing patterns to make text sound natural and human.
    Applied to ALL outputs: titles, descriptions, thumbnail text.
    """
    
    AI_WORDS = [
        'additionally', 'align with', 'crucial', 'delve', 'emphasizing',
        'enduring', 'enhance', 'fostering', 'garner', 'highlight',
        'interplay', 'intricate', 'key', 'landscape', 'pivotal',
        'showcase', 'tapestry', 'testament', 'underscore', 'valuable',
        'vibrant', 'underscores', 'highlights', 'serves as', 'stands as',
        'testament to', 'reminder of', 'crucial role', 'vital role',
        'significant', 'pivotal moment', 'broader', 'symbolizing',
        'contributing to', 'setting the stage', 'marking', 'shaping',
        'represents a shift', 'turning point', 'evolving landscape',
        'focal point', 'indelible mark', 'deeply rooted'
    ]
    
    FILLER_PHRASES = {
        'in order to': 'to',
        'due to the fact that': 'because',
        'at this point in time': 'now',
        'in the event that': 'if',
        'has the ability to': 'can',
        'it is important to note that': '',
        'it should be noted that': '',
        'as a matter of fact': '',
    }
    
    @classmethod
    def humanize(cls, text: str) -> str:
        """Remove AI patterns from text - for descriptions."""
        if not text:
            return text
        
        # Replace filler phrases
        for phrase, replacement in cls.FILLER_PHRASES.items():
            text = re.sub(r'\b' + re.escape(phrase) + r'\b', replacement, text, flags=re.IGNORECASE)
        
        # Remove "-ing" superficial analyses
        text = re.sub(r',\s*(highlighting|underscoring|emphasizing|reflecting|symbolizing|contributing to|cultivating|fostering|encompassing|showcasing)[^,]*', '', text, flags=re.IGNORECASE)
        
        # Replace inflated significance words
        replacements = {
            r'\bobserves as a testament to\b': 'shows',
            r'\bstands as a reminder of\b': 'reminds us of',
            r'\bmarks a pivotal moment\b': 'is important',
            r'\bplays a crucial role\b': 'is important',
            r'\bplays a vital role\b': 'is important',
            r'\bunderscores\b': ' shows',
            r'\bhighlights\b': ' shows',
        }
        
        for pattern, replacement in replacements.items():
            text = re.sub(pattern, replacement, text, flags=re.IGNORECASE)
        
        # Clean up
        text = re.sub(r'\s+', ' ', text)
        text = re.sub(r'\.+', '.', text)
        
        return text.strip()
    
    # Corporate jargon phrases to filter from titles
    # Andrew's banned list (from voice notes 2026-02-25):
    # "Digital success sounds so corporate, like that doesn't sound anything like me"
    # "Mastering Podcast Success, Good God, shoot me now. I would never put that out there"
    # "Revenue operations - we are never using that ever"
    CORPORATE_JARGON = [
        'digital success',           # Andrew: "sounds like junior college course"
        'revenue operations',        # Andrew: "never using that ever"
        'mastering',                 # Andrew: "sounds robotic"
        'synergy',
        'leverage',
        'optimize your',
        'maximize your',
        'streamline',
        'holistic approach',
        'paradigm',
        'scalable solutions',
        'actionable insights',
        'best practices',
        'thought leader',
        'game-changing',
        'cutting-edge',
        'next-level',
        'world-class',
        'industry-leading',
        'innovative solutions',
        'strategic alignment',
        'core competencies',
        'value proposition',
        'mission-critical',
    ]
    
    @classmethod
    def has_corporate_jargon(cls, title: str) -> bool:
        """Check if title contains banned corporate phrases."""
        if not title:
            return False
        title_lower = title.lower()
        for phrase in cls.CORPORATE_JARGON:
            if phrase in title_lower:
                return True
        return False
    
    @classmethod
    def humanize_title(cls, title: str) -> str:
        """Make titles punchy and natural - removes AI patterns."""
        if not title:
            return title
        
        # Run base humanization
        title = cls.humanize(title)
        
        # Remove corporate jargon
        for phrase in cls.CORPORATE_JARGON:
            pattern = r'\b' + re.escape(phrase) + r'\b'
            title = re.sub(pattern, '', title, flags=re.IGNORECASE)
        
        # Remove excessive punctuation
        title = re.sub(r'!+', '!', title)
        title = re.sub(r'\?+', '?', title)
        
        # Ensure it doesn't sound clickbaity
        clickbait_words = ['shocking', "you won't believe", 'doctors hate', 'this one trick']
        for word in clickbait_words:
            if re.search(word, title, re.IGNORECASE):
                title = re.sub(word, '', title, flags=re.IGNORECASE)
        
        return title.strip()
    
    @classmethod
    def humanize_thumbnail(cls, text: str) -> str:
        """Make thumbnail text punchy and human."""
        if not text:
            return text
        
        # Remove AI patterns
        text = cls.humanize(text)
        
        # Make punchy - short, uppercase, impactful
        text = text.upper()
        
        # Keep only key words (first meaningful words)
        words = text.split()
        if len(words) > 6:
            words = words[:6]
        
        return ' '.join(words)


# ============================================================================
# AI CLIENTS
# ============================================================================


_gemini_available = None





# ============================================================================
# DESCRIPTION TRUNCATION HELPER
# ============================================================================

def _truncate_at_sentence_boundary(text: str, max_length: int = 900) -> str:
    """
    Truncate text at the last complete sentence before max_length.
    
    NEVER mid-sentence. Finds sentence boundaries (. ! ?) with end-of-string detection.
    Hard cap at max_length and never cut mid-sentence.
    
    Args:
        text: The text to truncate
        max_length: Maximum allowed length (hard cap)
    
    Returns:
        Text truncated at sentence boundary, with trailing period
    """
    if len(text) <= max_length:
        return text
    
    # Look for sentence endings: . ! ?
    # Strategy: Find all sentence-ending punctuation followed by space or end of string
    # Must have at least one character after punctuation to be complete
    
    # Common abbreviations to skip (not exhaustive)
    abbreviations = {'mr', 'dr', 'ms', 'mrs', 'sr', 'jr', 'vs', 'etc', 'eg', 'ie', 'al'}
    
    sentence_ends = []
    
    # Find all sentence-ending patterns within max_length
    search_range = min(max_length, len(text))
    
    i = 0
    while i < search_range:
        char = text[i]
        
        if char in '.!?':
            # Check what follows the punctuation
            next_idx = i + 1
            
            # Skip if followed by another punctuation (e.g., "...")
            if next_idx < search_range and text[next_idx] in '.!?':
                i += 1
                continue
            
            # Check for abbreviation before this punctuation
            # Look back 2-3 chars for common abbreviations
            start_check = max(0, i - 4)
            prev_text = text[start_check:i].lower().strip()
            
            # If previous text is a single word that's an abbreviation, skip
            if prev_text in abbreviations:
                i += 1
                continue
            
            # This is a valid sentence end if:
            # 1. It's followed by a space, OR
            # 2. It's at the end of text (end of string), OR  
            # 3. It's followed by a newline
            if next_idx >= search_range:  # End of string
                sentence_ends.append(i + 1)
            elif text[next_idx] in ' \n\t':  # Space or whitespace after
                sentence_ends.append(i + 1)
        
        i += 1
    
    if sentence_ends:
        # Take the LAST complete sentence boundary (closest to max_length)
        truncate_at = max(sentence_ends)
        
        # Ensure we don't go past max_length
        truncate_at = min(truncate_at, max_length)
        
        result = text[:truncate_at].strip()
        
        # Ensure it ends with punctuation
        if result and result[-1] not in '.!?':
            result += '.'
        
        return result
    
    # No sentence boundary found - try to find a word boundary near max_length
    # Look for space within 50 chars of max_length
    search_start = max(0, max_length - 50)
    last_space = text.rfind(' ', search_start, max_length)
    
    if last_space > search_start:
        result = text[:last_space].strip()
        if result and result[-1] not in '.!?':
            result += '.'
        return result
    
    # No good boundary found - just hard truncate at max_length
    # But still try to end at a word boundary if possible
    last_word_break = text.rfind(' ', max(0, max_length - 20), max_length)
    if last_word_break > search_start:
        result = text[:last_word_break].strip()
        if result and result[-1] not in '.!?':
            result += '.'
        return result
    
    # Absolute fallback - hard truncate
    return text[:max_length].strip() + '...'


# ============================================================================
# JPI CTA HELPER
# ============================================================================

def _generate_cta_line1_for_jpi(title: str, transcript_sample: str) -> str:
    """
    Generate an episode-relevant first line for JPI Fitness Authority Academy CTA.
    
    Creates a snappy question that relates to the episode content.
    Uses keywords from title to make it relevant.
    
    Args:
        title: The episode title
        transcript_sample: Brief transcript for context
    
    Returns:
        A snappy CTA line 1 (question format)
    """
    # Extract key topics from title
    title_lower = title.lower()
    
    # Map keywords to relevant CTAs
    keyword_to_cta = {
        'fitness': "Want to build a fitness brand that stands out?",
        'gym': "Want to stop chasing clients and build a fitness empire?",
        'coaching': "Ready to scale your coaching business to 6-figures?",
        'nutrition': "Want to help more people with nutrition coaching?",
        'online': "Ready to build a profitable online fitness business?",
        'client': "Want to attract high-ticket fitness clients?",
        'scale': "Ready to scale your fitness business beyond limits?",
        'marketing': "Want to fill your calendar with ideal clients?",
        'personal brand': "Ready to build a powerful fitness personal brand?",
        'money': "Want to double your fitness business revenue?",
        'entrepreneur': "Ready to build a fitness business that runs itself?",
        'success': "Want the secret to fitness business success?",
        'growth': "Ready to 10x your fitness business growth?",
        'content': "Want to create content that converts followers to clients?",
        'social media': "Ready to dominate social media as a fitness pro?",
        'youtube': "Want to grow on YouTube as a fitness creator?",
        'podcast': "Ready to use your podcast to grow your fitness business?",
        'niche': "Want help niching down and building fitness authority?",
    }
    
    # Find matching keyword
    for keyword, cta in keyword_to_cta.items():
        if keyword in title_lower:
            return cta
    
    # Default CTA if no keyword matches
    return "Want to build your fitness authority and grow your business?"




# ============================================================================
# NAME FILTER - POST-GENERATION FILTER FOR TOOL-GENERATED OUTPUTS
# ============================================================================
# Filters personal names from ALL tool-generated outputs:
# - Generated titles list
# - Copy options
# - Description
# - Tags
# - Thumbnail text
# - Timestamps (if applicable)
#
# IMPORTANT: Successful-match titles from YouTube validation MAY contain names
# (that's OK - we're copying what already worked). But our AI-generated outputs
# must NOT contain personal names.


class NameFilter:
    """
    Robust post-generation filter to remove personal names from tool-generated outputs.
    
    Uses:
    1. Known name/handle list (Gary Vee, Joe Rogan, etc.)
    2. Heuristic: 2+ TitleCase words not at start (likely "First Last")
    3. Allowlist of common non-names
    
    This runs AFTER generation to catch any names that slip through.
    """
    
    # Known person names/handles to filter (celebrities, influencers, podcast guests)
    KNOWN_NAMES = {
        # Gary Vaynerchuk variants
        'gary vee', 'gary vaynerchuk', 'gary vaynerchuk', 'vaynerchuk', 'vayner',
        # Joe Rogan
        'joe rogan', 'rogan',
        # Elon Musk
        'elon musk', 'elon',
        # Andrew Tate
        'andrew tate', 'tate',
        # Mark Manson
        'mark manson', 'manson',
        # Tim Ferriss
        'tim ferriss', 'ferriss',
        # Lewis Howes
        'lewis howes', 'howes',
        # Tony Robbins
        'tony robbins', 'robbins',
        # Grant Cardone
        'grant cardone', 'cardone',
        # Dan Lok
        'dan lok', 'lok',
        # Jordan Belfort
        'jordan belfort', 'belfort',
        # Robert Kiyosaki
        'robert kiyosaki', 'kiyosaki',
        # Dave Ramsey
        'dave ramsey', 'ramsey',
        # Suze Orman
        'suze orman', 'orman',
        # Oprah
        'oprah', 'oprah winfrey',
        # Elon
        'musk',
        # Tech influencers
        'gary v', 'garyvee',
        # Generic influencer patterns to flag
        'mrb',
    }
    
    # Common words that might look like names but aren't
    ALLOWLIST = {
        # Business terms
        'business', 'coach', 'coaching', 'expert', 'mentor', 'guru',
        # General terms
        'people', 'person', 'someone', 'everybody', 'anybody', 'nobody',
        # Podcast/platform terms
        'podcast', 'youtube', 'instagram', 'tiktok', 'content', 'creator',
        # Common adjectives
        'good', 'great', 'best', 'new', 'old', 'young', 'real', 'true',
        # Time/person references
        'today', 'tomorrow', 'yesterday', 'morning', 'night', 'week', 'month', 'year',
        # Numbers as words
        'one', 'two', 'three', 'four', 'five', 'six', 'seven', 'eight', 'nine', 'ten',
        # Common compound words that might look like names
        'anyone', 'someone', 'everyone', 'testimonial', 'spotlight',
        # Generic titles that might appear in content
        'guest', 'host', 'speaker', 'interviewer', 'expert', 'founder', 'ceo',
        # More business/lifestyle terms
        'money', 'success', 'health', 'fitness', 'wealth', 'growth', 'impact',
    }
    
    # Common first names (for heuristic detection)
    COMMON_FIRST_NAMES = {
        'jeff', 'mike', 'robert', 'andrew', 'ali', 'kevin', 'john', 'chris',
        'dave', 'steve', 'matt', 'alex', 'james', 'ryan', 'daniel', 'brian',
        'mark', 'tom', 'jason', 'tim', 'nick', 'sam', 'ben', 'adam', 'tony',
        'peter', 'paul', 'mary', 'sarah', 'jessica', 'emily', 'susan', 'lisa',
        'betty', 'nancy', 'karen', 'linda', 'ashley', 'amanda', 'dorothy', 'sharon',
        'carlos', 'jose', 'miguel', 'luis', 'juan', 'carlos', 'antonio',
        'michael', 'david', 'joseph', 'william', 'richard', 'charles', 'christopher',
        'anthony', 'daniel', 'matthew', 'anthony', 'donald', 'steven', 'paul',
        'seth', 'jonah', 'leah', 'rachel', 'noah', 'isaac', 'jacob', 'moses',
        'abraham', 'eva', 'eve', 'adam', 'goddess',
    }
    
    @classmethod
    def contains_name(cls, text: str) -> Tuple[bool, str]:
        """
        Check if text contains a personal name.
        
        Returns:
            (contains_name: bool, detected_name: str)
        """
        if not text:
            return False, ""
        
        text_lower = text.lower()
        
        # Check known names/handles (exact match or partial)
        for name in cls.KNOWN_NAMES:
            # Use word boundary for longer names, partial for handles
            if len(name) > 3:
                # Use word boundaries for proper names
                pattern = r'\b' + re.escape(name) + r'\b'
                if re.search(pattern, text_lower):
                    return True, name
            else:
                # Short handles might need partial matching
                if name in text_lower:
                    return True, name
        
        # Heuristic: Check for "First Last" pattern (2+ TitleCase words not at start)
        # This catches unknown names like "John Smith", "Jane Doe"
        words = text.split()
        if len(words) >= 2:
            # Check pairs of consecutive words
            for i in range(len(words) - 1):
                word1 = words[i]
                word2 = words[i + 1]
                
                # Skip if either word is in allowlist
                if word1.lower() in cls.ALLOWLIST or word2.lower() in cls.ALLOWLIST:
                    continue
                
                # Check if both look like proper names (TitleCase, not all caps, not numbers)
                if cls._looks_like_name(word1) and cls._looks_like_name(word2):
                    # Additional check: avoid common phrases that aren't names
                    phrase = f"{word1} {word2}".lower()
                    if phrase not in cls.ALLOWLIST and not cls._is_common_phrase(phrase):
                        return True, f"{word1} {word2}"
        
        # Check for possessives: "John's", "Smith's"
        for name in cls.COMMON_FIRST_NAMES:
            possessive = f"{name}'s"
            if possessive in text_lower:
                return True, possessive
        
        return False, ""
    
    @classmethod
    def _looks_like_name(cls, word: str) -> bool:
        """Check if a word looks like a name (TitleCase, alphabetic)."""
        if not word:
            return False
        
        # Must be alphabetic (or hyphenated for names like "Mary-Jane")
        word_clean = word.replace('-', '').replace("'", "")
        if not word_clean.isalpha():
            return False
        
        # Must start with uppercase
        if not word[0].isupper():
            return False
        
        # Shouldn't be all caps (that's a different pattern)
        if word.isupper():
            return False
        
        # Shouldn't be too long (probably not a name)
        if len(word) > 15:
            return False
        
        return True
    
    @classmethod
    def _is_common_phrase(cls, phrase: str) -> bool:
        """Check if phrase is a common non-name phrase."""
        common_phrases = {
            'digital marketing', 'content marketing', 'social media', 'email marketing',
            'online business', 'side hustle', 'passive income', 'personal development',
            'self improvement', 'time management', 'goal setting', 'habit formation',
            'weight loss', 'muscle gain', 'healthy eating', 'fitness journey',
            'business growth', 'revenue growth', 'customer acquisition', 'lead generation',
            'podcast growth', 'youtube growth', 'instagram growth', 'brand building',
            'thought leadership', 'personal brand', 'business model', 'revenue model',
            'income stream', 'multiple streams', 'financial freedom', 'early retirement',
            'lifestyle design', 'work life', 'workout routine', 'morning routine',
            'night routine', 'daily habits', 'success habits', 'winning mindset',
            'abundance mindset', 'growth mindset', 'entrepreneur mindset', 'ceo mindset',
        }
        return phrase.lower() in common_phrases
    
    @classmethod
    def filter_text(cls, text: str, replacement: str = "The Expert") -> str:
        """
        Remove personal names from text, replacing with generic term.
        
        Args:
            text: The text to filter
            replacement: What to replace names with (default: "The Expert")
        
        Returns:
            Filtered text with names replaced
        """
        if not text:
            return text
        
        result = text
        
        # Replace known names
        for name in cls.KNOWN_NAMES:
            if len(name) > 3:
                pattern = r'\b' + re.escape(name) + r'\b'
                result = re.sub(pattern, replacement, result, flags=re.IGNORECASE)
            else:
                # For short names/handles, be more careful
                if name in result.lower():
                    # Replace with word boundaries where possible
                    result = re.sub(r'\b' + re.escape(name) + r'\b', replacement, result, flags=re.IGNORECASE)
        
        # Replace common first names
        for name in cls.COMMON_FIRST_NAMES:
            # Replace standalone names and possessives
            patterns = [
                r'\b' + re.escape(name) + r"'?s?\b",  # name, name's, names
            ]
            for pattern in patterns:
                result = re.sub(pattern, replacement, result, flags=re.IGNORECASE)
        
        # Replace "First Last" patterns (TitleCase pairs)
        words = result.split()
        filtered_words = []
        skip_next = False
        
        for i, word in enumerate(words):
            if skip_next:
                skip_next = False
                continue
            
            # Check if this word and next word form a name pattern
            if i < len(words) - 1:
                word1 = word
                word2 = words[i + 1]
                
                phrase = f"{word1} {word2}".lower()
                
                # Skip if either is in allowlist or it's a common phrase
                if (word1.lower() in cls.ALLOWLIST or word2.lower() in cls.ALLOWLIST or
                    cls._is_common_phrase(phrase)):
                    filtered_words.append(word)
                    continue
                
                # Check if both look like names
                if cls._looks_like_name(word1) and cls._looks_like_name(word2):
                    filtered_words.append(replacement)
                    skip_next = True
                    continue
            
            filtered_words.append(word)
        
        result = ' '.join(filtered_words)
        
        # Clean up extra spaces
        result = re.sub(r'\s+', ' ', result)
        
        return result.strip()
    
    @classmethod
    def filter_output(cls, output: Dict[str, Any]) -> Dict[str, Any]:
        """
        Filter all relevant fields in an output dict.
        
        Args:
            output: Dict with keys like 'title', 'description', 'tags', 'thumbnail_text', 'copies'
        
        Returns:
            Same dict with names filtered from all relevant fields
        
        NOTE: Descriptions are NOT filtered because they MUST contain:
        - Podcast name (e.g., "Just Pursue It Podcast")
        - Host name (e.g., "Andrew Zaragoza")
        - Guest name (when applicable)
        These are REQUIRED per Andrew's prompts, not forbidden.
        """
        if not output:
            return output
        
        # Filter title
        if 'title' in output and output['title']:
            output['title'] = cls.filter_text(output['title'])
        
        # NOTE: Descriptions are NOT filtered - they MUST contain podcast name, host name, etc.
        # The NameFilter was incorrectly replacing "Andrew Zaragoza" with "The Expert Zaragoza"
        # and "Just Pursue" with "The Expert" (detected as name pattern).
        # Descriptions need these names per the config requirements.
        
        # NOTE: Tags are NOT filtered - they MUST contain host name, podcast name for SEO
        # Tags like "Andrew Zaragoza" and "Just Pursue It Podcast" are required
        # if 'tags' in output and isinstance(output['tags'], list):
        #     output['tags'] = [cls.filter_text(tag) for tag in output['tags']]
        
        # Filter thumbnail text
        if 'thumbnail_text' in output:
            if isinstance(output['thumbnail_text'], list):
                output['thumbnail_text'] = [cls.filter_text(t) for t in output['thumbnail_text']]
            elif isinstance(output['thumbnail_text'], str):
                output['thumbnail_text'] = cls.filter_text(output['thumbnail_text'])
        
        # Filter copy options
        if 'copies' in output and isinstance(output['copies'], list):
            output['copies'] = [
                {**c, 'title': cls.filter_text(c.get('title', ''))} 
                if isinstance(c, dict) else cls.filter_text(str(c))
                for c in output['copies']
            ]
        
        # Filter copy_options (different key name used in some places)
        if 'copy_options' in output and isinstance(output['copy_options'], list):
            for opt in output['copy_options']:
                if isinstance(opt, dict) and 'title' in opt:
                    opt['title'] = cls.filter_text(opt['title'])
                if isinstance(opt, dict) and 'copy' in opt:
                    opt['copy'] = cls.filter_text(opt['copy'])
        
        return output
    
    @classmethod
    def validate_output(cls, output: Dict[str, Any], raise_on_name: bool = False) -> Tuple[bool, List[str]]:
        """
        Validate that output doesn't contain personal names.
        
        Args:
            output: The output dict to validate
            raise_on_name: If True, raises exception on name detection
        
        Returns:
            (is_valid: bool, detected_names: List[str])
        
        Raises:
            ValueError: If raise_on_name is True and names are detected
        """
        detected_names = []
        
        # Check title
        if 'title' in output and output['title']:
            has_name, name = cls.contains_name(output['title'])
            if has_name:
                detected_names.append(f"title: {name}")
        
        # NOTE: Descriptions are NOT checked for names - they MUST contain:
        # - Podcast name (e.g., "Just Pursue It Podcast")
        # - Host name (e.g., "Andrew Zaragoza")
        # - Guest name (when applicable)
        # These are REQUIRED, not violations.
        
        # Check tags
        if 'tags' in output and isinstance(output['tags'], list):
            for tag in output['tags']:
                has_name, name = cls.contains_name(tag)
                if has_name:
                    detected_names.append(f"tag: {name}")
        
        # Check thumbnail text
        if 'thumbnail_text' in output:
            text = output['thumbnail_text']
            if isinstance(text, list):
                for t in text:
                    has_name, name = cls.contains_name(t)
                    if has_name:
                        detected_names.append(f"thumbnail: {name}")
            elif isinstance(text, str):
                has_name, name = cls.contains_name(text)
                if has_name:
                    detected_names.append(f"thumbnail: {name}")
        
        # Check copies
        for key in ['copies', 'copy_options']:
            if key in output and isinstance(output[key], list):
                for opt in output[key]:
                    title = opt.get('title', '') if isinstance(opt, dict) else str(opt)
                    has_name, name = cls.contains_name(title)
                    if has_name:
                        detected_names.append(f"{key}: {name}")
        
        is_valid = len(detected_names) == 0
        
        if not is_valid and raise_on_name:
            raise ValueError(f"Output contains personal names: {detected_names}")
        
        return is_valid, detected_names


# ============================================================================
# TITLE GENERATION - USES EXACT CONFIG PROMPTS ONLY
# ============================================================================
# CRITICAL: Title generation uses ONLY the prompts.titles from config files
# NO FALLBACK TEMPLATES - If config prompt is missing, it raises an error
# This ensures consistency with Telegram workflow


# ============================================================================
# MAIN OPTIMIZER CLASS
# ============================================================================

# YouTube Validation Filters (configurable thresholds)
# Primary ranking is OUTLIER SCORE (views/subscribers), these are just safety nets
YOUTUBE_MIN_VIEWS = 1000           # Skip videos with <1K views (very lenient)
YOUTUBE_MAX_AGE_DAYS = 1825        # Accept videos up to 5 years old (1825 days)
YOUTUBE_MIN_SUBSCRIBERS = 500      # Skip channels with <500 subscribers


class EpisodeOptimizerV3:
    """
    Enhanced Episode Optimizer with:
    - Configurable podcast settings
    - Output selection (title, timestamps, description, tags, thumbnail)
    - Medal ranking system for titles
    - Uses FULL prompts from config files ONLY
    - Gemini first, then OpenAI fallback
    """
    
    def __init__(self):
        self.client = None
        self.model = None
        self.config = None
        self._initialized = False
        self.custom_keywords = None  # Custom keywords from questionnaire/niche detection
        self._job_id = None  # Job ID for progress tracking
    
    def _ensure_initialized(self):
        """Lazy initialization."""
        if self._initialized:
            return
        
        _ensure_openai_configured()
        
        global _openai_client, _openai_model
        self.client = _openai_client
        self.model = _openai_model
        
        if self.client:
            print("[EpisodeOptimizerV3] OpenAI GPT-4o ready")
        
        self._initialized = True
    
    def is_available(self) -> bool:
        """Check if optimizer is ready."""
        self._ensure_initialized()
        return _check_gemini_available() or (self.client is not None and self.model is not None)
    
    # =========================================================================
    # STEP 1: EXTRACT TRANSCRIPT
    # =========================================================================
    
    def extract_transcript(self, youtube_url: str) -> Dict[str, Any]:
        """Step 1: Extract transcript from YouTube video."""
        print(f"[Optimizer] Extracting transcript from: {youtube_url}")
        
        try:
            result = extract_transcript(youtube_url)
            
            if result['success']:
                print(f"[Optimizer] Transcript extracted: {len(result.get('transcript', ''))} chars")
            else:
                print(f"[Optimizer] Transcript extraction failed: {result.get('error')}")
            
            return result
            
        except Exception as e:
            print(f"[Optimizer] Transcript error: {e}")
            return {
                'success': False,
                'error': str(e),
                'video_id': None,
                'title': None,
                'transcript': None,
                'duration': 0
            }
    
    # =========================================================================
    # STEP 2: EXTRACT TOPICS FROM TRANSCRIPT
    # =========================================================================
    
    def _extract_topics_from_transcript(self, transcript: str, num_topics: int = 5) -> List[Dict[str, Any]]:
        """
        Extract real topics from transcript using key phrase detection.
        This mimics the manual process that works so well.
        
        Args:
            transcript: Full transcript text
            num_topics: Number of topics to extract (default: 5)
        
        Returns:
            List of dicts: [{"topic": str, "quote": str, "timestamp": str}]
        """
        if not transcript:
            return self._generate_fallback_topics(num_topics)
        
        # Clean transcript - remove duplicates and artifacts
        import re
        transcript = re.sub(r'^Language: \w+\s*', '', transcript)
        # Remove HTML entities and repeated lines (common in YouTube transcripts)
        transcript = re.sub(r'&gt;&gt;|&gt;|&lt;', '', transcript)
        # Remove duplicate consecutive lines
        lines = transcript.split('\n')
        cleaned_lines = []
        prev_line = None
        for line in lines:
            line = line.strip()
            if line and line != prev_line:
                cleaned_lines.append(line)
                prev_line = line
        transcript = ' '.join(cleaned_lines)
        transcript = re.sub(r'\s+', ' ', transcript).strip()
        
        # Split into segments (roughly every 200 words = ~80 seconds)
        words = transcript.split()
        segment_size = 200
        segments = []
        
        for i in range(0, len(words), segment_size):
            segment_words = words[i:i + segment_size]
            segment_text = ' '.join(segment_words)
            # Estimate timestamp
            estimated_seconds = (i * 60) // 150
            minutes = estimated_seconds // 60
            seconds = estimated_seconds % 60
            timestamp = f"{minutes:02d}:{seconds:02d}"
            segments.append({'text': segment_text, 'timestamp': timestamp})
        
        # Enhanced key phrases for podcast content
        key_indicators = [
            # Numbers/milestones
            (r'\b(\d+ years old|at age \d+|when I was \d+|turning \d+)\b', 'Age Milestone'),
            (r'\b(\$[\d,]+|make \$\d+|earned \$\d+|million|billion)\b', 'Money'),
            
            # Action/insight words
            (r'\b(I learned|I realized|I discovered|I found|I decided|I chose)\b', 'Insight'),
            (r'\b(the truth is|the secret|honestly|real talk|here\'s the thing)\b', 'Truth'),
            (r'\b(never|always|everyone|nobody|most people)\b', 'Universal'),
            
            # Transformation/results
            (r'\b(changed|transformed|improved|better|success|achieved|results)\b', 'Transformation'),
            (r'\b(help|helping|coach|clients|patients|people)\b', 'Helping Others'),
            
            # Business/career
            (r'\b(business|company|startup|entrepreneur|founder)\b', 'Business'),
            (r'\b(marketing|sales|revenue|profit|growth)\b', 'Business Growth'),
            
            # Health/fitness
            (r'\b(health|fitness|workout|diet|nutrition|wellness)\b', 'Health'),
            (r'\b(weight|lose|gain|muscle|fat|body)\b', 'Physical Results'),
            
            # Advice/lessons
            (r'\b(here\'s what|this is how|if you want|my advice|recommend)\b', 'How-To'),
            (r'\b(lesson|tip|strategy|method|system|framework)\b', 'Strategy'),
            (r'\b(mistake|wrong|error|fail|failure)\b', 'Mistakes'),
            
            # Important moments
            (r'\b(rock bottom|turning point|decision|commit|dedicated)\b', 'Turning Point'),
            (r'\b(important|critical|essential|key|crucial)\b', 'Key Insight'),
        ]
        
        topic_candidates = []
        
        for seg in segments:
            text_lower = seg['text'].lower()
            for pattern, topic_type in key_indicators:
                if re.search(pattern, text_lower):
                    # Extract meaningful quote - find the sentence containing the match
                    match = re.search(r'[^.!?]*' + pattern + r'[^.!?]*', seg['text'], re.IGNORECASE)
                    if match:
                        quote = match.group(0).strip()[:150]
                        if len(quote) > 20:
                            topic_candidates.append({
                                'topic': topic_type,
                                'quote': quote + ('...' if len(quote) == 150 else ''),
                                'timestamp': seg['timestamp']
                            })
                            break  # Only take first match per segment
        
        # Deduplicate by topic type
        seen_types = set()
        unique_topics = []
        for t in topic_candidates:
            if t['topic'] not in seen_types:
                seen_types.add(t['topic'])
                unique_topics.append(t)
        
        # Fill in with evenly spaced segments if we don't have enough
        if len(unique_topics) < num_topics:
            segment_step = max(1, len(segments) // num_topics)
            for i in range(0, len(segments), segment_step):
                if len(unique_topics) >= num_topics:
                    break
                seg_text = segments[i]['text'][:80].lower()
                # Check if this segment is already covered
                already_covered = any(seg_text[:40] in t['quote'].lower()[:40] for t in unique_topics)
                if not already_covered and len(segments[i]['text']) > 50:
                    unique_topics.append({
                        'topic': f'Key Moment {len(unique_topics)+1}',
                        'quote': segments[i]['text'][:150] + ('...' if len(segments[i]['text']) > 150 else ''),
                        'timestamp': segments[i]['timestamp']
                    })
        
        print(f"[Optimizer] Found {len(unique_topics)} unique topics from {len(topic_candidates)} candidates")
        return unique_topics[:num_topics]
    
    def _generate_fallback_topics(self, num_topics: int = 5) -> List[Dict[str, Any]]:
        """Generate generic topics when no transcript available."""
        return [
            {'topic': 'Introduction', 'quote': 'Opening segment', 'timestamp': '00:00'},
            {'topic': 'Main Content', 'quote': 'Core discussion', 'timestamp': '05:00'},
            {'topic': 'Key Strategy', 'quote': 'Important strategy', 'timestamp': '10:00'},
            {'topic': 'Results', 'quote': 'Results discussion', 'timestamp': '15:00'},
            {'topic': 'Conclusion', 'quote': 'Final thoughts', 'timestamp': '20:00'},
        ][:num_topics]
    
    # =========================================================================
    # STEP 2: GENERATE TITLES - ANDREW'S WORKFLOW
    # =========================================================================
    
    # Banned phrases from titles
    BANNED_TITLE_PHRASES = [
        'digital success',
        'revenue operations',
        'mastering',
        'unlock',
    ]
    
    # Common first names to ban
    BANNED_NAMES = [
        'jeff', 'mike', 'robert', 'andrew', 'ali', 'kevin', 'john', 'chris', 
        'dave', 'steve', 'matt', 'alex', 'james', 'ryan', 'daniel', 'brian',
        'mark', 'tom', 'jason', 'tim', 'nick', 'sam', 'ben', 'adam', 'tony',
    ]
    
    def generate_title_options(
        self, 
        transcript: str, 
        config: Dict[str, Any],
        timeout_seconds: int = 300,
        progress_callback: callable = None
    ) -> List[Dict[str, Any]]:
        """
        ANDREW'S FLOW - Extract topics from transcript, then find successful YouTube titles to COPY.
        
        Flow:
        1. Extract 5 main topics from THIS episode's transcript (specific to episode)
        2. For each topic, generate 3-5 title options
        3. Search YouTube for each title
        4. Find best performing video in same niche
        5. Generate 3 COPY options (minimal, medium, more changed)
        6. Return titles with YouTube data and copy options
        
        Each title object includes:
        - original_title: The AI-generated title (from topic)
        - youtube_results: Videos found when searching that title
        - best_outlier: The highest scoring video to copy
        - copy_options: 3 variations of copying the successful title
        - search_query: What was searched
        
        Args:
            transcript: The transcript text
            config: Podcast configuration
            timeout_seconds: Maximum time for title generation (default 5 minutes)
            progress_callback: Optional callback for progress updates (jid, step, message, percent)
        """
        def _log_progress(step: str, message: str, percent: int):
            """Log and optionally report progress."""
            print(f"[Optimizer] [{percent}%] {step}: {message}")
            if progress_callback:
                try:
                    progress_callback(self._job_id if hasattr(self, '_job_id') else 'unknown', step, message, percent)
                except Exception as e:
                    print(f"[Optimizer] Progress callback error: {e}")
        
        _log_progress("START", "Beginning title generation with timeout", 0)
        
        # Check if we have AI available
        if not self.is_available():
            raise RuntimeError("No AI available - Gemini and OpenAI both failed")
        
        # Get podcast context
        podcast_section = config.get('podcast', {})
        podcast_name = podcast_section.get('name', 'The Podcast')
        host_name = podcast_section.get('host', 'Host')
        target_audience = podcast_section.get('targetAudience', 'audience')
        
        # Step 1: Extract 5 main topics from THIS episode's transcript
        _log_progress("EXTRACT_TOPICS", "Extracting main topics from transcript...", 10)
        print("\n[Optimizer] STEP 1: Extracting 5 main topics from THIS episode's transcript...")
        topics = self._extract_topics_for_title_generation(
            transcript=transcript,
            podcast_name=podcast_name,
            host_name=host_name,
            target_audience=target_audience
        )
        
        if not topics:
            print("[Optimizer] WARNING: Could not extract topics, using fallback")
            _log_progress("FALLBACK", "Using fallback titles (no topics extracted)", 30)
            return self._generate_fallback_titles(transcript, config)
        
        _log_progress("TOPICS_EXTRACTED", f"Extracted {len(topics)} topics", 20)
        print(f"[Optimizer] Extracted {len(topics)} topics:")
        for i, t in enumerate(topics, 1):
            print(f"  {i}. {t.get('topic_name', 'Unknown')}")
        
        # =========================================================================
        # PHASE 2: Search YouTube with niche keywords FIRST to find top outliers
        # =========================================================================
        _log_progress("NICHE_SEARCH", "Finding top performing videos in niche...", 22)
        
        # Get youtubeSearchKeywords from config
        niche_keywords = config.get('youtubeSearchKeywords', [])
        if not niche_keywords:
            podcast_section = config.get('podcast', {})
            niche_keywords = podcast_section.get('youtubeSearchKeywords', [])
        
        pattern_titles = []  # Titles generated from YouTube patterns
        
        if niche_keywords:
            print(f"[Optimizer] PHASE 2: Searching YouTube with niche keywords: {niche_keywords[:3]}...")
            
            # Search YouTube for top performing videos in the niche
            top_outliers = self._search_youtube_for_outliers(niche_keywords)
            
            if top_outliers:
                print(f"[Optimizer] PHASE 2: Found {len(top_outliers)} top outliers in niche")
                
                # PHASE 2: Generate titles by mimicking successful YouTube patterns
                _log_progress("PATTERN_TITLES", "Generating titles from YouTube patterns...", 25)
                print(f"[Optimizer] PHASE 2: Generating titles from {len(top_outliers)} outlier examples...")
                
                pattern_titles = self._generate_titles_from_youtube_patterns(
                    youtube_videos=top_outliers,
                    transcript=transcript,
                    config=config
                )
                
                print(f"[Optimizer] PHASE 2: Generated {len(pattern_titles)} titles from YouTube patterns")
            else:
                print("[Optimizer] PHASE 2: No outliers found, falling back to topic-based generation")
        else:
            print("[Optimizer] PHASE 2: No youtubeSearchKeywords in config, skipping pattern-based generation")
        
        # =========================================================================
        # Step 2: For each topic, generate 3-5 title options
        # =========================================================================
        _log_progress("GENERATE_TITLES", f"Generating title options for {len(topics)} topics...", 25)
        print("\n[Optimizer] STEP 2: Generating 3-5 title options per topic...")
        all_title_candidates = []
        
        for topic_idx, topic in enumerate(topics):
            topic_name = topic.get('topic_name', '')
            topic_context = topic.get('context', '')
            
            _log_progress("GENERATING_TOPIC_TITLES", f"Generating titles for topic {topic_idx+1}/{len(topics)}: {topic_name[:30]}...", 25 + (topic_idx * 5))
            
            titles_for_topic = self._generate_titles_for_topic(
                topic_name=topic_name,
                topic_context=topic_context,
                transcript=transcript,
                config=config
            )
            
            all_title_candidates.extend(titles_for_topic)
            print(f"[Optimizer] Generated {len(titles_for_topic)} titles for topic: {topic_name}")
        
        if not all_title_candidates:
            print("[Optimizer] WARNING: No titles generated, falling back")
            return self._generate_fallback_titles(transcript, config)
        
        # PHASE 2: Add pattern-based titles to the candidates
        if pattern_titles:
            print(f"[Optimizer] PHASE 2: Adding {len(pattern_titles)} pattern-based titles to candidates")
            # Mark these as coming from YouTube patterns
            for pt in pattern_titles:
                pt['from_youtube_pattern'] = True
                pt['topic_name'] = pt.get('topic_name', 'YouTube Pattern')
            all_title_candidates.extend(pattern_titles)
        
        # Validate and clean titles
        all_title_candidates = self._validate_and_clean_titles(all_title_candidates)
        print(f"[Optimizer] After validation: {len(all_title_candidates)} title candidates")
        _log_progress("TITLES_GENERATED", f"Generated {len(all_title_candidates)} title candidates", 35)
        
        # Step 3-5: Search YouTube for each title, find best to copy, generate copy options
        print("\n[Optimizer] STEP 3-5: Searching YouTube and generating copy options...")
        _log_progress("YOUTUBE_SEARCH", f"Searching YouTube for {len(all_title_candidates)} titles...", 40)
        
        final_titles = []
        start_time = datetime.now()
        
        for i, title_candidate in enumerate(all_title_candidates):
            original_title = title_candidate.get('original_title', '')
            
            # Progress: 40% to 80% for YouTube searches
            search_percent = 40 + int((i / len(all_title_candidates)) * 40)
            _log_progress("YOUTUBE_SEARCH_PROGRESS", f"Searching YouTube ({i+1}/{len(all_title_candidates)}): {original_title[:40]}...", search_percent)
            
            # Check timeout
            elapsed = (datetime.now() - start_time).total_seconds()
            if elapsed > timeout_seconds:
                _log_progress("TIMEOUT", f"Title generation timed out after {elapsed:.0f}s - completing with {len(final_titles)} titles", 80)
                print(f"[Optimizer] WARNING: Timeout after {elapsed:.0f}s, returning {len(final_titles)} titles processed so far")
                break
            
            print(f"[Optimizer] [{i+1}/{len(all_title_candidates)}] Processing: {original_title[:50]}...")
            
            # Search YouTube with the actual generated title
            search_query = original_title
            youtube_results = self._search_youtube(search_query, min_duration=1)
            
            if not youtube_results:
                # Try shorter search
                youtube_results = self._search_youtube(search_query, min_duration=3)
            
            title_candidate['search_query'] = search_query
            title_candidate['youtube_results'] = youtube_results
            
            # Find best performing video to copy
            if youtube_results:
                channel_subs = self._get_channel_subscribers(youtube_results)
                scored = score_youtube_results(youtube_results, channel_subs)
                top_outliers = get_top_outliers(scored, top_n=3)
                
                title_candidate['top_outliers'] = top_outliers
                
                if top_outliers:
                    best_outlier = top_outliers[0]
                    
                    # PHASE 1 SAFETY GATE: Check similarity between generated title and outlier
                    similarity_check = self._check_similarity_threshold(
                        generated_title=original_title,
                        outlier_title=best_outlier.get('title', '')
                    )
                    
                    # Store similarity metadata
                    title_candidate['best_outlier'] = best_outlier
                    title_candidate['similarity_check'] = similarity_check
                    title_candidate['score'] = best_outlier.get('normalized_score', 0)
                    
                    # If similarity is too low, mark as low-confidence (PHASE 1)
                    if not similarity_check['is_valid']:
                        title_candidate['is_low_confidence'] = True
                        title_candidate['low_confidence_reason'] = similarity_check['warning']
                        # Clear best_outlier to avoid showing "successful match"
                        title_candidate['best_outlier'] = None
                        title_candidate['score'] = 0  # Downgrade score
                        print(f"[Optimizer]   -> WARNING: Low similarity ({similarity_check['similarity_score']:.3f}) - marking low confidence")
                    else:
                        title_candidate['is_low_confidence'] = False
                        # Step 5: Generate 3 COPY options from the successful video (only if similarity passes)
                        copy_options = self._generate_copy_options(
                            original_title=original_title,
                            successful_youtube_title=best_outlier.get('title', ''),
                            topic_name=title_candidate.get('topic_name', ''),
                            config=config
                        )
                        title_candidate['copy_options'] = copy_options
                        print(f"[Optimizer]   -> Found best outlier: {best_outlier.get('title', '')[:40]}... ({best_outlier.get('view_count', 0):,} views) [similarity: {similarity_check['similarity_score']:.2f}]")
                else:
                    title_candidate['score'] = 0
            else:
                title_candidate['score'] = 0
            
            final_titles.append(title_candidate)
            
            # Progress update after each title
            _log_progress("TITLE_VALIDATED", f"Validated title {i+1}/{len(all_title_candidates)}: {original_title[:30]}...", search_percent + 5)
        
        # Sort by score
        final_titles.sort(key=lambda x: x.get('score', 0), reverse=True)
        
        print(f"[Optimizer] Final: {len(final_titles)} titles with YouTube validation")
        _log_progress("TITLES_COMPLETE", f"Title generation complete: {len(final_titles)} titles validated", 85)
        
        # Return top 25 (5 topics x 5 titles)
        return final_titles[:25]
    
    def _extract_topics_for_title_generation(
        self, 
        transcript: str, 
        podcast_name: str,
        host_name: str,
        target_audience: str
    ) -> List[Dict[str, Any]]:
        """
        Extract 5 main topics from THIS episode's transcript.
        Uses AI to analyze the actual content, not generic keywords.
        """
        # Get transcript sample (first 8000 chars for topic extraction)
        transcript_sample = transcript[:8000] if len(transcript) > 8000 else transcript
        
        prompt = f"""Analyze this podcast episode transcript and extract the 5 MAIN TOPICS discussed.

PODCAST CONTEXT:
- Podcast: {podcast_name}
- Host: {host_name}
- Target Audience: {target_audience}

TRANSCRIPT:
{transcript_sample}

Your task:
1. Read through the ENTIRE transcript above
2. Identify the 5 MOST SIGNIFICANT topics (not categories - specific topics from this episode)
3. For each topic, provide:
   - topic_name: A clear 2-5 word name for the topic
   - context: 1-2 sentences explaining what specifically was discussed about this topic

Return as JSON array with exactly 5 topics:
[
  {{
    "topic_name": "Topic Name",
    "context": "What was specifically said about this topic in the episode"
  }},
  ... (4 more)
]

IMPORTANT: 
- Topics must be SPECIFIC to this episode's content, not generic categories
- If episode discusses "how to build a coaching business", topic_name should be "Building Coaching Business", not just "Business"
- Do NOT include guest name or host name in topics"""

        result, model_used = _call_ai(
            prompt,
            "You are an expert at identifying key topics from podcast content.",
            max_tokens=1500,
            prefer_quality=True
        )
        
        if not result:
            print("[Optimizer] Failed to extract topics from transcript")
            return []
        
        # Parse JSON response
        try:
            # Clean up response
            clean = re.sub(r'```(?:json)?\s*', '', result)
            clean = re.sub(r'```\s*$', '', clean)
            
            json_match = re.search(r'\[[\s\S]*\]', clean)
            if json_match:
                topics = json.loads(json_match.group())
            else:
                topics = json.loads(clean)
        except json.JSONDecodeError as e:
            print(f"[Optimizer] Failed to parse topics JSON: {e}")
            return []
        
        if not isinstance(topics, list) or len(topics) < 3:
            print(f"[Optimizer] Invalid topics format: {topics[:200] if topics else 'empty'}")
            return []
        
        return topics
    

    def _search_youtube_for_outliers(self, keywords: List[str], min_views: int = 10000, max_results: int = 10) -> List[Dict[str, Any]]:
        """
        PHASE 2: Search YouTube using niche keywords to find top performing videos (outliers).
        
        This is used to find successful titles in the podcast's niche BEFORE generating
        new titles, so we can pattern after proven winners.
        
        Args:
            keywords: List of YouTube search keywords from config
            min_views: Minimum view count to consider (default 10,000)
            max_results: Maximum results per keyword
            
        Returns:
            List of top performing videos sorted by view count
        """
        print(f"[Optimizer] PHASE 2: Searching YouTube for niche outliers using {len(keywords)} keywords...")
        
        all_results = []
        
        for keyword in keywords[:5]:  # Limit to first 5 keywords
            print(f"[Optimizer]   Searching keyword: {keyword}")
            
            # Search YouTube with this keyword
            results = self._search_youtube(keyword, min_duration=1)
            
            if results:
                # Score and filter
                for video in results:
                    views = video.get('view_count', 0)
                    if views >= min_views:
                        all_results.append(video)
        
        # Sort by view count and deduplicate
        all_results.sort(key=lambda x: x.get('view_count', 0), reverse=True)
        
        # Remove duplicates based on title
        seen_titles = set()
        unique_results = []
        for video in all_results:
            title_lower = video.get('title', '').lower()
            if title_lower not in seen_titles:
                seen_titles.add(title_lower)
                unique_results.append(video)
        
        print(f"[Optimizer]   Found {len(unique_results)} unique high-performing videos")
        
        # Get top outliers using the outlier scoring
        if unique_results:
            channel_subs = self._get_channel_subscribers(unique_results)
            scored = score_youtube_results(unique_results, channel_subs)
            top_outliers = get_top_outliers(scored, top_n=5)
            print(f"[Optimizer]   Top outliers by score: {len(top_outliers)}")
            return top_outliers
        
        return []


    def _generate_titles_for_topic(
        self,
        topic_name: str,
        topic_context: str,
        transcript: str,
        config: Dict[str, Any]
    ) -> List[Dict[str, Any]]:
        """Generate 3-5 title options for a specific topic from this episode."""
        
        podcast_section = config.get('podcast', {})
        target_audience = podcast_section.get('targetAudience', 'listeners')
        
        # Get transcript sample relevant to this topic (first 3000 chars)
        transcript_sample = transcript[:3000] if len(transcript) > 3000 else transcript
        
        prompt = f"""Generate 5 YouTube title options for a podcast episode about this topic.

TOPIC: {topic_name}
CONTEXT: {topic_context}
TARGET AUDIENCE: {target_audience}

EPISODE CONTENT (use this to make titles specific):
{transcript_sample}

Requirements for each title:
- MUST be specific to THIS episode's content (from transcript above)
- Include power words: Secret, Truth, Nobody, Mistake, Learned, How, Why, What
- Use numbers when specific (e.g., "3 Ways", "7 Steps")
- Create curiosity gaps
- Under 60 characters
- NEVER include ANY person's name in titles (no Gary Vee, no Joe Rogan, no experts, no guests, etc.) Focus on topics and concepts, not people.
- STRICTLY AVOID: "Digital Success", "Mastering", "Revenue Operations", "Unlock" (corporate jargon)

Generate 5 titles that would work for YouTube:
[
  {{"title": "Title 1"}},
  {{"title": "Title 2"}},
  {{"title": "Title 3"}},
  {{"title": "Title 4"}},
  {{"title": "Title 5"}}
]"""

        result, model_used = _call_ai(
            prompt,
            "You are a YouTube title expert. Generate specific, clickable titles.",
            max_tokens=800,
            prefer_quality=True
        )
        
        if not result:
            return []
        
        # Parse titles
        try:
            clean = re.sub(r'```(?:json)?\s*', '', result)
            clean = re.sub(r'```\s*$', '', clean)
            
            json_match = re.search(r'\[[\s\S]*\]', clean)
            if json_match:
                titles = json.loads(json_match.group())
            else:
                titles = json.loads(clean)
        except json.JSONDecodeError:
            # Try regex extraction
            titles = []
            for match in re.findall(r'"title":\s*"([^"]+)"', result):
                titles.append({'title': match})
        
        if not isinstance(titles, list):
            return []
        
        # Add topic info to each title
        for t in titles:
            t['topic_name'] = topic_name
            t['topic_context'] = topic_context
            t['original_title'] = t.get('title', '')
        
        return titles
    
    def _validate_and_clean_titles(self, titles: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Validate titles don't contain banned phrases or names."""
        # Placeholder title patterns to filter out
        PLACEHOLDER_PATTERNS = [
            r'^topic\s*\d+',
            r'deep\s*dive',
            r'episode\s*\d+',
            r'part\s*\d+',
            r'segment\s*\d+',
            r'chapter\s*\d+',
            r'section\s*\d+',
            r'^intro',
            r'^conclusion',
            r'^summary',
            r'^q&a',
            r'^discussion',
            r'^analysis',
        ]
        
        validated = []
        
        for title_item in titles:
            title = title_item.get('original_title', '') or title_item.get('title', '')
            if not title:
                continue
            
            title_lower = title.lower()
            
            # Check for banned phrases
            has_banned = False
            for phrase in self.BANNED_TITLE_PHRASES:
                if phrase in title_lower:
                    print(f"[Optimizer] Skipping banned phrase '{phrase}': {title[:50]}")
                    has_banned = True
                    break
            
            if has_banned:
                continue
            
            # Check for placeholder title patterns
            for pattern in PLACEHOLDER_PATTERNS:
                if re.search(pattern, title_lower):
                    print(f"[Optimizer] Skipping placeholder title: {title[:50]}")
                    has_banned = True
                    break
            
            if has_banned:
                continue
            
            # Check for banned names using the robust NameFilter
            has_name, detected_name = NameFilter.contains_name(title)
            if has_name:
                print(f"[Optimizer] Name detected '{detected_name}': {title[:50]} - filtering")
                # Use NameFilter to clean the title
                title = NameFilter.filter_text(title)
                title_item['original_title'] = title.strip()
                title_item['title'] = title.strip()
            
            # Must have at least 15 chars
            if len(title) < 15:
                continue
            
            # Ensure required fields
            title_item['original_title'] = title_item.get('original_title', title)
            title_item['title'] = title
            title_item['youtube_results'] = []
            title_item['top_outliers'] = []
            title_item['best_outlier'] = None
            title_item['copy_options'] = []
            title_item['score'] = 0
            
            validated.append(title_item)
        
        return validated
    
    def _generate_copy_options(
        self,
        original_title: str,
        successful_youtube_title: str,
        topic_name: str,
        config: Dict[str, Any]
    ) -> List[Dict[str, Any]]:
        """
        Generate 3 copy options from a successful YouTube title.
        
        Copy 1 (minimal): Change almost nothing, just fit episode
        Copy 2 (medium): Slightly more customization  
        Copy 3 (more): Add niche specificity (e.g., "for coaches")
        """
        
        prompt = f"""You have a successful YouTube title to COPY and adapt for this podcast episode.

SUCCESSFUL YOUTUBE TITLE (this got high views - COPY its pattern):
"{successful_youtube_title}"

ORIGINAL AI TITLE (what we generated from episode content):
"{original_title}"

TOPIC: {topic_name}

Create 3 copy options that copy the successful title's pattern but apply it to this episode:

COPY OPTION 1 (MINIMAL): 
- Change almost nothing from the successful title
- Just adapt it slightly to fit this episode's content
- Keep structure, word order, power words exactly the same

COPY OPTION 2 (MEDIUM):
- Slightly more customization
- Change a word or two to be more specific to this episode
- Still clearly copy the successful title's pattern

COPY OPTION 3 (MORE):
- Add more niche specificity
- E.g., add "for coaches", "in 2024", "in the fitness industry"
- More customization while keeping the pattern

Return as JSON array:
[
  {{
    "copy_type": "minimal",
    "title": "Copied title (minimal changes)",
    "changes_made": "Brief description of what was changed"
  }},
  {{
    "copy_type": "medium", 
    "title": "Copied title (medium changes)",
    "changes_made": "Brief description of what was changed"
  }},
  {{
    "copy_type": "more",
    "title": "Copied title (more changes with niche specificity)",
    "changes_made": "Brief description of what was changed"
  }}
]

IMPORTANT:
- Each copied title must be UNDER 60 characters
- NEVER include ANY person's name (no experts, no guests, no content creators, etc.) Focus on topics and concepts, not people.
- Copy the PATTERN, not just the words"""

        result, model_used = _call_ai(
            prompt,
            "You are a YouTube title expert. Copy successful title patterns.",
            max_tokens=600,
            prefer_quality=True
        )
        
        if not result:
            return []
        
        # Parse copy options
        try:
            clean = re.sub(r'```(?:json)?\s*', '', result)
            clean = re.sub(r'```\s*$', '', clean)
            
            json_match = re.search(r'\[[\s\S]*\]', clean)
            if json_match:
                copy_options = json.loads(json_match.group())
            else:
                copy_options = json.loads(clean)
        except json.JSONDecodeError:
            return []
        
        if not isinstance(copy_options, list):
            return []
        
        # Validate and clean each option using NameFilter
        validated = []
        for opt in copy_options:
            title = opt.get('title', '')
            if title and len(title) >= 15:
                # Check for banned phrases
                title_lower = title.lower()
                if any(phrase in title_lower for phrase in self.BANNED_TITLE_PHRASES):
                    continue
                
                # Use NameFilter to check and clean names
                has_name, detected = NameFilter.contains_name(title)
                if has_name:
                    print(f"[Optimizer] Copy option contains name '{detected}': {title[:40]} - filtering")
                    title = NameFilter.filter_text(title)
                    opt['title'] = title
                
                validated.append(opt)
        
        return validated
    
    def _generate_fallback_titles(self, transcript: str, config: Dict[str, Any]) -> List[Dict[str, Any]]:
        """
        Fallback: Generate titles the OLD way (AI first, then YouTube validation).
        Used when YouTube search fails or returns no results.
        """
        print("[Optimizer] Using fallback title generation (AI-first)...")
        
        # Get prompts from config
        prompts = config.get('prompts', {})
        titles_prompt_template = prompts.get('titles', '')
        
        if not titles_prompt_template:
            raise KeyError("Config is missing 'titles' prompt. Cannot generate titles.")
        
        podcast_section = config.get('podcast', {})
        podcast_name = podcast_section.get('name', 'The Podcast')
        host_name = podcast_section.get('host', 'Host')
        target_audience = podcast_section.get('targetAudience', 'audience')
        
        niche_keywords = config.get('youtubeSearchKeywords', [])
        if not niche_keywords:
            niche_keywords = podcast_section.get('youtubeSearchKeywords', [])
        
        guest_name = getattr(self, 'guest_name', '')
        episode_number = getattr(self, 'episode_number', '')
        
        niche_instruction = ""
        if niche_keywords:
            keywords_str = ", ".join(niche_keywords[:6])
            niche_instruction = f"""
            
NICHE-SPECIFIC REQUIREMENTS (CRITICAL):
- Target audience: {target_audience}
- Niche keywords to INCLUDE in titles: {keywords_str}
- Use niche-specific terms
"""
        
        prompt = titles_prompt_template + niche_instruction
        prompt = prompt.replace('{guest_name}', guest_name or 'the guest')
        prompt = prompt.replace('{episode_number}', episode_number or '')
        
        transcript_sample = transcript[:20000] if len(transcript) > 20000 else transcript
        if '{transcript}' in prompt:
            prompt = prompt.replace('{transcript}', transcript_sample)
        else:
            prompt = prompt + "\n\n--- EPISODE TRANSCRIPT ---\n" + transcript_sample + "\n--- END TRANSCRIPT ---"
        
        result, model_used = _call_ai(
            prompt,
            "You are a YouTube title expert. Follow the prompt EXACTLY as written.",
            max_tokens=2000,
            prefer_quality=True
        )
        
        if not result:
            raise RuntimeError("AI generation failed for titles")
        
        titles = self._parse_titles_from_response(result, config)
        
        if not titles:
            raise RuntimeError("Could not parse titles from AI response")
        
        titles = self._validate_no_names(titles, guest_name, host_name)
        
        print(f"[Optimizer] Fallback generated {len(titles)} titles")
        return titles
    
    def _generate_titles_from_youtube_patterns(
        self, 
        youtube_videos: List[Dict], 
        transcript: str, 
        config: Dict
    ) -> List[Dict[str, Any]]:
        """
        Extract patterns from successful YouTube titles and apply to this episode.
        
        This is the KEY FUNCTION that makes the new flow work:
        - Takes TOP successful YouTube videos as examples
        - Analyzes their patterns/structures
        - Generates NEW titles by copying those patterns but using THIS episode's content
        - Links each new title to the YouTube video it mimics
        """
        
        # Build prompt with YouTube examples
        youtube_examples = []
        for i, video in enumerate(youtube_videos[:5], 1):
            title = video.get('title', '')
            views = video.get('view_count', 0)
            youtube_examples.append(f"{i}. \"{title}\" ({views:,} views)")
        
        youtube_examples_str = "\n".join(youtube_examples)
        
        podcast_section = config.get('podcast', {})
        target_audience = podcast_section.get('targetAudience', 'listeners')
        
        guest_name = getattr(self, 'guest_name', '')
        episode_number = getattr(self, 'episode_number', '')
        
        prompt = f"""You are analyzing successful YouTube titles to create SIMILAR titles for THIS episode.

TARGET AUDIENCE: {target_audience}

SUCCESSFUL YOUTUBE TITLES (these videos got HIGH views - study their patterns):
{youtube_examples_str}

YOUR TASK:
1. Analyze the PATTERNS in these successful titles:
   - Word structures (e.g., "How I Made $X", "The #1 Secret to...", "I Tried X for 30 Days")
   - Keywords they use
   - Numbers/specifics they include
   
2. For EACH of the 5 successful titles above, create a NEW title for THIS episode by:
   - Using the EXACT SAME structure/pattern
   - Replacing the specifics with content from THIS episode
   - Keeping the niche keywords
   
3. Make each new title SPECIFIC to this episode's content (from the transcript below)

EPISODE INFO:
- Guest: {guest_name or 'the guest'}
- Episode: {episode_number}

EPISODE CONTENT (use this to create specific titles):
{transcript[:5000]}

OUTPUT FORMAT (JSON array - exactly 5 items):
[
  {{
    "title": "Your generated title that mimics the pattern",
    "mimics_youtube_title": "The exact YouTube title you're copying the pattern from",
    "pattern": "The pattern structure (e.g., 'How [someone] Made $[amount] in [time] From [method]')",
    "why": "Brief explanation of how you applied this pattern to this episode"
  }},
  ... (4 more)
]

IMPORTANT: Each generated title MUST directly copy the structure of its mimicked YouTube title.
If you can't create a good match, use a different YouTube title as the example."""

        print("[Optimizer] Calling AI to generate titles FROM YouTube patterns...")
        
        # Call AI
        response, model_used = _call_ai(
            prompt,
            "You are a YouTube title pattern expert. Generate titles that COPY successful patterns.",
            max_tokens=2000,
            prefer_quality=True
        )
        
        if not response:
            print("[Optimizer] AI response empty, returning empty titles")
            return []
        
        print(f"[Optimizer] Pattern generation response: {response[:500]}...")
        
        # Parse and link titles to YouTube videos
        titles = self._parse_titles_with_youtube_links(response, youtube_videos)
        
        # Validate no names
        guest_name = getattr(self, 'guest_name', '')
        host_name = config.get('podcast', {}).get('host', '')
        titles = self._validate_no_names(titles, guest_name, host_name)
        
        return titles
    
    def _parse_titles_with_youtube_links(self, ai_response: str, youtube_videos: List[Dict]) -> List[Dict]:
        """
        Parse AI-generated titles and link each to the YouTube video it mimics.
        
        This ensures we have a REAL connection - not a made-up claim.
        """
        
        # Parse JSON from AI response
        try:
            # Remove markdown code blocks
            clean_response = re.sub(r'```(?:json)?\s*', '', ai_response)
            clean_response = re.sub(r'```\s*$', '', clean_response)
            
            # Try to find JSON array
            json_match = re.search(r'\[[\s\S]*\]', clean_response)
            if json_match:
                titles = json.loads(json_match.group())
            else:
                titles = json.loads(clean_response)
        except json.JSONDecodeError as e:
            print(f"[Optimizer] JSON parse error: {e}")
            print(f"[Optimizer] Response was: {ai_response[:500]}...")
            return []
        
        if not isinstance(titles, list):
            print("[Optimizer] Expected list of titles, got:", type(titles))
            return []
        
        # For each title, find the YouTube video it claims to mimic
        parsed_titles = []
        for title_obj in titles:
            if isinstance(title_obj, str):
                title_obj = {'title': title_obj}
            
            title_text = title_obj.get('title', '').strip()
            if not title_text or len(title_text) < 15:
                continue
            
            mimicked_title = title_obj.get('mimics_youtube_title', '').strip()
            
            # Find the actual YouTube video with that title (or similar)
            matching_video = None
            if mimicked_title:
                for video in youtube_videos:
                    yt_title = video.get('title', '').lower()
                    mimicked_lower = mimicked_title.lower()
                    # Check if titles match or contain each other
                    if yt_title in mimicked_lower or mimicked_lower in yt_title or \
                       self._title_similarity(yt_title, mimicked_lower) > 0.6:
                        matching_video = video
                        break
            
            # Add the YouTube data to this title
            result = {
                'title': title_text,
                'original_title': title_text,  # For consistency with main pipeline
                'mimics_youtube_title': mimicked_title,
                'source_outlier_title': mimicked_title,  # Track the source outlier this was modeled on
                'pattern': title_obj.get('pattern', ''),
                'why': title_obj.get('why', ''),
                'search_query': title_text,  # For validation step
                'youtube_results': [],
                'top_outliers': [],
                'best_outlier': None,
                'score': 0,
                'from_youtube_pattern': True,  # Mark as pattern-generated
            }
            
            if matching_video:
                result['best_outlier'] = matching_video
                result['youtube_results'] = [matching_video]
                result['score'] = matching_video.get('outlier_score', 0) or matching_video.get('normalized_score', 0)
                
                # PHASE 1 SAFETY GATE: Verify similarity between generated title and source outlier
                source_outlier_title = matching_video.get('title', '')
                similarity_check = self._check_similarity_threshold(
                    generated_title=title_text,
                    outlier_title=source_outlier_title
                )
                result['similarity_check'] = similarity_check
                
                if not similarity_check['is_valid']:
                    # Low confidence - don't show as successful match
                    result['best_outlier'] = None
                    result['score'] = 0
                    result['is_low_confidence'] = True
                    result['low_confidence_reason'] = similarity_check['warning']
                    print(f"[Optimizer] SAFETY: Low similarity ({similarity_check['similarity_score']:.3f}) for pattern title: '{title_text[:30]}...'")
                else:
                    result['is_low_confidence'] = False
                    print(f"[Optimizer] Title linked: '{title_text[:40]}...' -> mimics '{mimicked_title[:40]}...' [similarity: {similarity_check['similarity_score']:.2f}]")
            else:
                # Could not verify the mimicry - flag it
                print(f"[Optimizer] WARNING: Could not verify mimicry for '{title_text[:40]}...' (claimed: '{mimicked_title[:40]}...')")
                result['best_outlier'] = None
                result['score'] = 0
                result['is_low_confidence'] = True
                result['low_confidence_reason'] = "Could not verify pattern mimicry - no matching YouTube video found"
            
            parsed_titles.append(result)
        
        print(f"[Optimizer] Parsed {len(parsed_titles)} titles with YouTube links")
        return parsed_titles
    
    def _title_similarity(self, title1: str, title2: str) -> float:
        """
        Calculate similarity between two titles using multiple metrics.
        
        Uses:
        1. Token Jaccard similarity (word overlap)
        2. Normalized sequence similarity (character-level, handles typos)
        
        Returns the higher of the two scores (hybrid approach).
        
        Args:
            title1: First title string
            title2: Second title string
            
        Returns:
            Similarity score between 0.0 and 1.0
        """
        if not title1 or not title2:
            return 0.0
        
        title1_lower = title1.lower().strip()
        title2_lower = title2.lower().strip()
        
        if title1_lower == title2_lower:
            return 1.0
        
        # 1. Token Jaccard similarity
        words1 = set(title1_lower.split())
        words2 = set(title2_lower.split())
        
        if words1 and words2:
            intersection = words1 & words2
            union = words1 | words2
            jaccard = len(intersection) / len(union) if union else 0.0
        else:
            jaccard = 0.0
        
        # 2. Normalized sequence similarity (using difflib)
        # This handles typos, reordering, and partial matches better
        sequence_similarity = difflib.SequenceMatcher(None, title1_lower, title2_lower).ratio()
        
        # Return the higher of the two (hybrid approach)
        # Sequence similarity tends to be more robust for title matching
        return max(jaccard, sequence_similarity)
    
    def _check_similarity_threshold(self, generated_title: str, outlier_title: str, threshold: float = TITLE_SIMILARITY_THRESHOLD) -> Dict[str, Any]:
        """
        PHASE 1 SAFETY GATE: Check if generated title is similar enough to the outlier it claims to match.
        
        Args:
            generated_title: The AI-generated title
            outlier_title: The YouTube outlier title it claims to match
            threshold: Minimum similarity score (default from TITLE_SIMILARITY_THRESHOLD)
            
        Returns:
            Dict with:
            - is_valid: bool - True if similarity >= threshold
            - similarity_score: float - The computed similarity
            - threshold_used: float - The threshold that was applied
            - warning: str - Warning message if below threshold
        """
        similarity = self._title_similarity(generated_title, outlier_title)
        
        result = {
            'is_valid': similarity >= threshold,
            'similarity_score': round(similarity, 3),
            'threshold_used': threshold,
            'warning': None
        }
        
        if not result['is_valid']:
            result['warning'] = f"LOW CONFIDENCE: Title similarity ({similarity:.2f}) below threshold ({threshold}). Match may be spurious."
            print(f"[Optimizer] SAFETY: Low similarity ({similarity:.3f}) for '{generated_title[:40]}...' vs '{outlier_title[:40]}...'")
        
        return result
    
    def _parse_titles_from_response(self, raw: str, config: Dict[str, Any]) -> List[Dict[str, Any]]:
        """Parse titles from AI response with multiple fallback strategies."""
        titles = []
        
        # Check if the response looks like an error message before parsing
        if raw and len(raw) < 200:
            error_indicators = ['error', 'internal server error', 'not found', 'unavailable', 'rate limit', 'failed', 'exception']
            if any(raw.strip().lower().startswith(indicator) for indicator in error_indicators):
                print(f"[Optimizer] AI response appears to be an error message: {raw[:100]}")
                return []
        
        try:
            # Remove markdown code blocks if present
            clean_result = re.sub(r'```(?:json)?\s*', '', raw)
            clean_result = re.sub(r'```\s*$', '', clean_result)
            
            # Try to find JSON array
            json_match = re.search(r'\[[\s\S]*\]', clean_result)
            if json_match:
                parsed = json.loads(json_match.group())
                # Normalize to standard format
                for item in parsed:
                    if isinstance(item, str):
                        titles.append({'topic': 'Key Moment', 'title': item})
                    elif isinstance(item, dict):
                        # Handle both {title: ...} and {topic: ..., title: ...}
                        if 'title' in item:
                            titles.append({
                                'topic': item.get('topic', 'Key Moment'),
                                'title': item['title']
                            })
                        elif 'titles' in item and isinstance(item['titles'], list):
                            # Handle nested titles array
                            for t in item['titles']:
                                titles.append({'topic': item.get('topic', 'Key Moment'), 'title': t})
            else:
                # Try to parse the whole thing
                parsed = json.loads(clean_result)
                if isinstance(parsed, list):
                    for item in parsed:
                        if isinstance(item, str):
                            titles.append({'topic': 'Key Moment', 'title': item})
                        elif isinstance(item, dict) and 'title' in item:
                            titles.append(item)
        except json.JSONDecodeError:
            print("[Optimizer] JSON parse failed, trying regex extraction...")
        
        # Regex fallback: look for numbered lists, bullet points, etc.
        if not titles:
            # Pattern 1: "1. Title" or "1) Title"
            patterns = [
                r'^\s*(?:\d+[\.\)]\s+|[-*]\s+)["\']?([^"\'\n]{15,100})["\']?$',
                r'["\']([^"\'\n]{20,80})["\']',
                r'Title:\s*["\']?([^"\'\n]{15,100})["\']?',
            ]
            for pattern in patterns:
                matches = re.findall(pattern, raw, re.MULTILINE)
                for match in matches:
                    match = match.strip()
                    if len(match) > 15 and len(match) < 100:
                        titles.append({'topic': 'Key Moment', 'title': match})
                if len(titles) >= 5:
                    break
        
        # Deduplicate
        seen = set()
        unique_titles = []
        for t in titles:
            title = t.get('title', '').strip()
            if title and title.lower() not in seen:
                seen.add(title.lower())
                unique_titles.append({
                    'topic': t.get('topic', 'Key Moment'),
                    'title': title
                })
        
        return unique_titles[:10]  # Return max 10 titles
    
    def _validate_no_names(self, titles: List[Dict[str, Any]], guest_name: str, host_name: str) -> List[Dict[str, Any]]:
        """Validate titles follow NO NAMES rule."""
        # Get first names
        banned_names = []
        if guest_name:
            banned_names.append(guest_name.split()[0].lower())
        if host_name:
            banned_names.append(host_name.split()[0].lower())
        
        # Common first names to check
        common_names = ['jeff', 'mike', 'robert', 'andrew', 'ali', 'kevin', 'john', 'chris', 'dave', 'steve', 'matt', 'alex']
        banned_names.extend(common_names)
        
        validated = []
        for title_obj in titles:
            title = title_obj.get('title', '')
            # Check for banned names
            title_lower = title.lower()
            has_name = any(name in title_lower for name in banned_names)
            
            if has_name:
                print(f"[Optimizer] WARNING: Title contains name, humanizing: {title}")
                # Try to fix by removing possessives
                for name in banned_names:
                    title = re.sub(rf"\b{name}'?s?\b", "The Expert", title, flags=re.IGNORECASE)
                title_obj['title'] = title.strip()
            
            # Add scoring fields
            title_obj['search_query'] = title
            title_obj['youtube_results'] = []
            title_obj['top_outliers'] = []
            title_obj['best_outlier'] = None
            title_obj['score'] = 0
            
            validated.append(title_obj)
        
        return validated
    
    # =========================================================================
    # STEP 3: YOUTUBE VALIDATION & MEDAL RANKING
    # =========================================================================
    
    # Track quota status globally
    _quota_exceeded = False
    
    def _validate_single_title(self, title_item: Dict[str, Any], min_duration: int, index: int, total: int, keywords: List[str] = None) -> Dict[str, Any]:
        """
        Validate a single title on YouTube (for parallel execution).
        
        If youtube_results already exist (from generate_title_options), use those.
        Only search if results are missing.
        
        Args:
            title_item: Title dictionary to validate
            min_duration: Minimum video duration
            index: Title index (for logging)
            total: Total number of titles
            keywords: List of YouTube search keywords from config (cycles through them)
        
        Returns:
            Updated title_item with YouTube data
        """
        original_title = title_item.get('title', '')
        
        # If YouTube results already exist from title generation, use them
        existing_results = title_item.get('youtube_results', [])
        existing_best = title_item.get('best_outlier', None)
        
        if existing_results:
            print(f"[Optimizer] [{index+1}/{total}] Using existing YouTube results for: {original_title[:40]}...")
            
            # Use existing top outliers if available
            if existing_best:
                title_item['score'] = existing_best.get('normalized_score', 0)
                title_item['view_count'] = existing_best.get('view_count', 0)
            else:
                # Re-score existing results
                channel_subs = self._get_channel_subscribers(existing_results)
                scored = score_youtube_results(existing_results, channel_subs)
                top_outliers = get_top_outliers(scored, top_n=3)
                title_item['top_outliers'] = top_outliers
                if top_outliers:
                    title_item['best_outlier'] = top_outliers[0]
                    title_item['score'] = top_outliers[0].get('normalized_score', 0)
                    title_item['view_count'] = top_outliers[0].get('view_count', 0)
            
            return title_item
        
        # Cycle through keywords if provided, otherwise use default behavior
        keyword = None
        if keywords:
            keyword = keywords[index % len(keywords)]
        
        # Use the actual title as search query (truncated for YouTube)
        query = original_title[:60] if len(original_title) > 60 else original_title
        title_item['search_query'] = query
        
        print(f"[Optimizer] [{index+1}/{total}] Searching: {query[:50]}...")
        
        try:
            # Try 20+ minute search first
            print(f"[DEBUG] _validate_single_title called for: {original_title}")
            print(f"[DEBUG] Search query: {query}")
            print(f"[DEBUG] Calling _search_youtube...")
            results = self._search_youtube(query, min_duration=min_duration)
            print(f"[DEBUG] _search_youtube returned {len(results)} results")
            
            if not results:
                # ISSUE 4 FIX: Allow all normal videos, not just 3-20 min
                results = self._search_youtube(query, min_duration=1)
            
            # Get channel subscriber counts
            channel_subs = self._get_channel_subscribers(results)
            update_channel_subscribers(channel_subs)
            
            # Score results
            if results:
                scored = score_youtube_results(results, channel_subs)
                top_outliers = get_top_outliers(scored, top_n=3)
                
                title_item['youtube_results'] = scored[:10]
                title_item['top_outliers'] = top_outliers
                
                # Get best outlier with view count
                if top_outliers:
                    best_outlier = top_outliers[0]
                    
                    # PHASE 1 SAFETY GATE: Check similarity
                    similarity_check = self._check_similarity_threshold(
                        generated_title=original_title,
                        outlier_title=best_outlier.get('title', '')
                    )
                    title_item['similarity_check'] = similarity_check
                    
                    if similarity_check['is_valid']:
                        title_item['best_outlier'] = best_outlier
                        title_item['score'] = best_outlier.get('normalized_score', 0)
                        title_item['view_count'] = best_outlier.get('view_count', 0)
                        title_item['is_low_confidence'] = False
                    else:
                        # Low confidence - don't show as successful match
                        title_item['best_outlier'] = None
                        title_item['score'] = 0
                        title_item['view_count'] = 0
                        title_item['is_low_confidence'] = True
                        title_item['low_confidence_reason'] = similarity_check['warning']
                        print(f"[Optimizer] SAFETY: Low similarity ({similarity_check['similarity_score']:.3f}) for '{original_title[:30]}...'")
            else:
                title_item['youtube_results'] = []
                title_item['top_outliers'] = []
                title_item['best_outlier'] = None
                title_item['score'] = 0
                title_item['view_count'] = 0
                
        except QuotaExceededError as e:
            print(f"[Optimizer] YouTube quota exceeded for title {index+1}: {e}")
            self._quota_exceeded = True
            title_item['youtube_results'] = []
            title_item['top_outliers'] = []
            title_item['best_outlier'] = None
            title_item['score'] = 0
            title_item['view_count'] = 0
        except Exception as e:
            print(f"[Optimizer] Error validating title {index+1}: {e}")
            title_item['youtube_results'] = []
            title_item['top_outliers'] = []
            title_item['best_outlier'] = None
            title_item['score'] = 0
            title_item['view_count'] = 0
        
        return title_item

    def validate_and_rank_titles(self, titles: List[Dict[str, Any]], min_duration: int = 1, progress_callback=None, job_id: str = None) -> List[Dict[str, Any]]:
        """
        Step 3: Search YouTube for ALL titles in PARALLEL and rank with medals.
        
        Medal System:
        - GOLD: Highest view count + strong pattern match
        - SILVER: 2nd best
        - BRONZE: 3rd best
        
        Args:
            titles: List of title dictionaries
            min_duration: Minimum video duration filter
            progress_callback: Optional callback(job_id, step, message, percent)
            job_id: Optional job ID for progress updates
        
        Returns:
            Titles with medal rankings and YouTube data
        
        NOTE: If YouTube quota is exceeded, assigns medals based on title quality 
        using AI assessment instead of YouTube data.
        """
        print(f"[Optimizer] Validating {len(titles)} titles on YouTube IN PARALLEL...")
        
        if progress_callback and job_id:
            progress_callback(job_id, 'validating_titles', f'Validating {len(titles)} titles in parallel...', 40)
        
        # Run all YouTube searches in parallel
        completed_count = 0
        lock = threading.Lock()
        
        def progress_tracker():
            """Track progress as titles complete"""
            nonlocal completed_count
            with lock:
                completed_count += 1
                current_progress = 40 + int((completed_count / len(titles)) * 25)  # 40% → 65%
                progress_msg = f"Validated {completed_count}/{len(titles)} titles..."
                
                if progress_callback and job_id:
                    progress_callback(job_id, 'validating_titles', progress_msg, current_progress)
                
                print(f"[Optimizer] Progress: {completed_count}/{len(titles)} titles validated")
        
        # Get YouTube search keywords - prefer custom keywords from questionnaire
        # Check both top-level and nested podcast structure for compatibility
        keywords = self.custom_keywords if self.custom_keywords else []
        
        if not keywords and self.config:
            keywords = self.config.get('youtubeSearchKeywords', [])
            if not keywords:
                podcast_section = self.config.get('podcast', {})
                keywords = podcast_section.get('youtubeSearchKeywords', [])
        
        if keywords:
            source = "custom (questionnaire)" if self.custom_keywords else "config"
            print(f"[Optimizer] Using YouTube search keywords ({source}): {keywords}")
        
        # Use ThreadPoolExecutor to search all titles at once (max 3 concurrent to avoid timeouts)
        with ThreadPoolExecutor(max_workers=3) as executor:
            # Submit all searches
            future_to_title = {}
            for i, title_item in enumerate(titles):
                future = executor.submit(self._validate_single_title, title_item, min_duration, i, len(titles), keywords)
                future_to_title[future] = i
            
            # Collect results as they complete
            for future in as_completed(future_to_title):
                idx = future_to_title[future]
                try:
                    updated_title = future.result()
                    titles[idx] = updated_title
                    progress_tracker()
                except Exception as e:
                    print(f"[Optimizer] Exception in parallel search {idx}: {e}")
                    progress_tracker()
        
        # Check if quota was exceeded for any title
        if self._quota_exceeded:
            print(f"[Optimizer] YouTube quota exceeded during validation - using AI fallback for ranking")
            titles = self._fallback_rank_titles_ai(titles)
        else:
            # Sort by score (highest = Gold)
            titles.sort(key=lambda x: x.get('score', 0), reverse=True)
        
        # Assign medals to top 3
        medals = ['gold', 'silver', 'bronze']
        for i, title in enumerate(titles):
            if i < 3:
                title['medal'] = medals[i]
                # Add pattern info
                if title.get('best_outlier'):
                    title['pattern'] = self._extract_pattern(title['best_outlier'].get('title', ''))
                elif self._quota_exceeded:
                    # Use AI-assessed pattern
                    title['pattern'] = title.get('ai_pattern', 'General')
            else:
                title['medal'] = None
        
        # ISSUE 5 FIX: Add title explanations for top titles
        titles = self._add_title_explanations(titles)
        
        print(f"[Optimizer] Parallel validation complete - medals assigned")
        return titles
    
    def _add_title_explanations(self, titles: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """
        ISSUE 5 FIX: Add explanations for top titles.
        
        Format: "This title mimics [YouTube title] and fits this episode because:
        1. [Reason with quote from episode]
        2. [Reason with quote from episode]
        3. [Reason with quote from episode]"
        """
        print("[Optimizer] Generating title explanations...")
        
        # Get transcript for quotes
        transcript = getattr(self, 'transcript', '')
        if not transcript:
            return titles
        
        # Limit transcript for prompt
        transcript_sample = transcript[:15000] if len(transcript) > 15000 else transcript
        
        # Generate explanations for titles with medals (top 3)
        medal_titles = [t for t in titles if t.get('medal')]
        if not medal_titles:
            return titles
        
        # Build prompt with all titles
        titles_info = ""
        for t in medal_titles:
            title_text = t.get('title', '')
            pattern = t.get('pattern', 'General')
            outlier_title = t.get('best_outlier', {}).get('title', '') if t.get('best_outlier') else ''
            titles_info += f"- Title: \"{title_text}\" | Pattern: {pattern} | Mimics: {outlier_title}\n"
        
        prompt = f"""For each title below, create an explanation of WHY it fits the episode content.

Titles:
{titles_info}

Episode Transcript (excerpt):
{transcript_sample}

For each title, generate an explanation in this EXACT format:
Title: [the title]
Explanation: This title mimics [successful YouTube title from validation] and fits this episode's content because:
1. [Reason with direct quote from episode - what topic/insight this relates to]
2. [Reason with direct quote from episode - what the guest said]
3. [Reason with direct quote from episode - specific details mentioned]

IMPORTANT: 
- Use ACTUAL quotes from the transcript
- Each explanation must have 3 numbered reasons
- Be specific to the episode content, not generic
- If no good YouTube match found, say "tested on YouTube" as the mimicry reference

Generate explanations for ALL {len(medal_titles)} titles above."""
        
        result, model_used = _call_ai(
            prompt,
            "You are an expert at explaining why YouTube titles fit episode content.",
            max_tokens=1500,
            prefer_quality=True
        )
        
        if not result:
            print("[Optimizer] Failed to generate explanations")
            return titles
        
        print(f"[Optimizer] Title explanations generated with: {model_used}")
        
        # Parse explanations and attach to titles
        explanations = self._parse_title_explanations(result, medal_titles)
        
        # Merge explanations into titles
        for title in titles:
            title_text = title.get('title', '')
            for exp in explanations:
                if exp.get('title') == title_text:
                    title['explanation'] = exp.get('explanation', '')
                    break
        
        return titles
    
    def _parse_title_explanations(self, raw: str, titles: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Parse title explanations from AI response."""
        explanations = []
        titles_text = {t.get('title', ''): t for t in titles}
        
        # Split by "Title:" or look for numbered entries
        sections = re.split(r'\n(?=Title:)', raw, flags=re.IGNORECASE)
        
        for section in sections:
            if not section.strip():
                continue
            
            # Extract title
            title_match = re.search(r'Title:\s*(.+?)(?:\n|$)', section, re.IGNORECASE)
            if not title_match:
                continue
            
            title_text = title_match.group(1).strip().strip('"')
            
            # Extract explanation
            explanation_match = re.search(r'Explanation:\s*(.+)$', section, re.DOTALL | re.IGNORECASE)
            if explanation_match:
                explanation = explanation_match.group(1).strip()
                explanations.append({
                    'title': title_text,
                    'explanation': explanation
                })
        
        return explanations
    
    def verify_copied_title(self, copied_title: str, original_video: Dict[str, Any], min_duration: int = 1) -> Dict[str, Any]:
        """
        VERIFICATION STEP: Search the copied title on YouTube to verify the original video appears.
        
        Andrew's workflow (from voice notes):
        "I take that new copied title, put it back into YouTube search, and then see if the 
        exact same videos pop up. If the same videos pop up with many views with the same 
        keywords and the same title format, that's a huge winning title."
        
        Args:
            copied_title: The new title we created by copying a successful one
            original_video: The YouTube video we copied the title from
            min_duration: Minimum video duration filter
        
        Returns:
            {
                'verified': bool,           # True if original video appears in results
                'position': int or None,    # Position of original video (1-indexed)
                'search_results': list,     # All search results
                'verdict': str              # 'WINNER', 'ACCEPTABLE', or 'FAIL'
            }
        
        Thresholds (from Andrew):
        - Top 1-3: "super qualified title" / WINNER
        - Top 5: "good" / ACCEPTABLE  
        - Page 1 (top 10): "fine" / ACCEPTABLE
        - Not found: FAIL
        """
        print(f"[Verification] Searching copied title: '{copied_title[:50]}...'")
        
        # Search YouTube with the copied title
        results = self._search_youtube(copied_title, min_duration=min_duration)
        
        if not results:
            print("[Verification] No search results returned")
            return {
                'verified': False,
                'position': None,
                'search_results': [],
                'verdict': 'FAIL',
                'reason': 'No search results returned'
            }
        
        # Check if original video appears in results
        original_video_id = original_video.get('video_id', '') or original_video.get('id', '')
        original_title_lower = original_video.get('title', '').lower()
        
        position = None
        for i, result in enumerate(results, 1):
            result_id = result.get('video_id', '') or result.get('id', '')
            result_title_lower = result.get('title', '').lower()
            
            # Match by video ID or very similar title
            if result_id == original_video_id:
                position = i
                break
            elif self._title_similarity(result_title_lower, original_title_lower) > 0.7:
                position = i
                break
        
        # Determine verdict based on position
        if position is not None:
            if position <= 3:
                verdict = 'WINNER'
                print(f"[Verification] ✅ WINNER - Original video at position {position}")
            elif position <= 5:
                verdict = 'ACCEPTABLE'
                print(f"[Verification] ✅ ACCEPTABLE - Original video at position {position}")
            elif position <= 10:
                verdict = 'ACCEPTABLE'
                print(f"[Verification] ⚠️ Page 1 - Original video at position {position}")
            else:
                verdict = 'FAIL'
                print(f"[Verification] ❌ FAIL - Original video at position {position} (too low)")
        else:
            verdict = 'FAIL'
            print("[Verification] ❌ FAIL - Original video not found in results")
        
        return {
            'verified': position is not None and position <= 10,
            'position': position,
            'search_results': results[:10],  # Top 10 for reference
            'verdict': verdict,
            'reason': f"Original video at position {position}" if position else "Original video not found"
        }
    
    def _fallback_rank_titles_ai(self, titles: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """
        When YouTube quota is exceeded, use AI to assess title quality and assign scores.
        This ensures we still get medal rankings even without YouTube data.
        """
        print("[Optimizer] Using AI fallback for title ranking...")
        
        if not titles:
            return titles
        
        # Format titles for AI prompt
        titles_text = ""
        for i, title in enumerate(titles, 1):
            titles_text += f"{i}. {title.get('title', '')}\n"
        
        prompt = f"""You are a YouTube title expert. Rate these titles from best to worst based on:
1. Click potential (curiosity gaps, power words)
2. Clarity and specificity  
3. Pattern strength (numbers, secrets, truth, mistakes)

Titles to rank:
{titles_text}

Return as JSON array with the SAME titles but add:
- "score": number from 0-100 (higher = better)
- "ai_pattern": one of: "Number Secrets", "The X Trap", "The Truth About", "What Nobody Tells", "I Learned", "How To", "Why I Quit", "General"

Return ONLY valid JSON array."""
        
        result, model_used = _call_ai(
            prompt, 
            "You are a YouTube title expert. Rate and rank titles.",
            max_tokens=2000,
            prefer_quality=True  # Use GPT-4o for accurate title scoring
        )
        
        if result:
            # Check if the response looks like an error message before parsing
            if result and len(result) < 200:
                error_indicators = ['error', 'internal server error', 'not found', 'unavailable', 'rate limit', 'failed', 'exception']
                if any(result.strip().lower().startswith(indicator) for indicator in error_indicators):
                    print(f"[Optimizer] AI ranking response appears to be an error: {result[:100]}")
                    # Fall back to default scoring
                    for t in titles:
                        t['score'] = 50
                        t['ai_pattern'] = 'General'
                    return titles
            
            try:
                scored_titles = json.loads(result)
                
                # Merge scores back to original titles
                title_map = {t.get('title', ''): t for t in titles}
                
                for scored in scored_titles:
                    scored_title = scored.get('title', '')
                    if scored_title in title_map:
                        original = title_map[scored_title]
                        original['score'] = scored.get('score', 50)
                        original['ai_pattern'] = scored.get('ai_pattern', 'General')
                        original['best_outlier'] = None  # No YouTube data
                        
                print(f"[Optimizer] AI ranking complete with: {model_used}")
                
            except json.JSONDecodeError as e:
                print(f"[Optimizer] Failed to parse AI ranking: {e}")
        
        # Sort by score
        titles.sort(key=lambda x: x.get('score', 0), reverse=True)
        return titles
    
    def _extract_pattern(self, winning_title: str) -> str:
        """Extract the title pattern that made it successful."""
        if not winning_title:
            return "Unknown"
        
        # Common patterns
        patterns = [
            (r'^The\s+\w+\s+Trap', 'The X Trap'),
            (r'^\d+\s+Secrets', 'Number Secrets'),
            (r"I\s+Competed", 'I Competed'),
            (r'^Why\s+I\s+Quit', 'Why I Quit'),
            (r'^The\s+Real\s+Reason', 'The Real Reason'),
            (r"^What\s+Nobody\s+Tells", 'What Nobody Tells'),
            (r'^\d+\s+Ways', 'Number Ways'),
            (r'^How\s+to', 'How To'),
            (r'^I\s+Learned', 'I Learned'),
            (r"The\s+Truth\s+About", 'The Truth About'),
        ]
        
        for pattern, name in patterns:
            if re.search(pattern, winning_title, re.IGNORECASE):
                return name
        
        return "General"
    
    def _search_youtube(self, query: str, min_duration: int = 1, max_duration: Optional[int] = None) -> List[Dict[str, Any]]:
        """Search YouTube with duration filter."""
        try:
            print(f"[DEBUG] _search_youtube: query='{query}', min_duration={min_duration}")
            results = search_titles(query, max_results=15)
            print(f"[DEBUG] search_titles returned {len(results)} raw results")
            # Apply validation filters
            results = self._filter_youtube_results(results)
            print(f"[DEBUG] After filtering: {len(results)} results")
            return results
        except QuotaExceededError:
            print("[Optimizer] YouTube API quota exceeded")
            return []
        except Exception as e:
            print(f"[Optimizer] Search error: {e}")
            return []
    
    def _filter_youtube_results(self, results: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """
        Filter YouTube results based on validation thresholds.
        
        Filters:
        - Minimum views: YOUTUBE_MIN_VIEWS (default 10,000)
        - Maximum age: YOUTUBE_MAX_AGE_DAYS (default 180 days / 6 months)
        - Minimum subscribers: YOUTUBE_MIN_SUBSCRIBERS (default 500)
        - DURATION/IS_SHORT: Exclude YouTube Shorts (< 60 seconds) and very short videos (< 90s)
        
        Logs rejection reasons for transparency.
        """
        if not results:
            return results
        
        filtered = []
        today = datetime.now()
        
        for video in results:
            title = video.get('title', '')[:50]
            views = video.get('view_count', 0)
            subscriber_count = video.get('subscriber_count', 0)
            published_at = video.get('published_at', '')
            
            # FIXED: Get duration info - YouTube search now provides duration_minutes and is_short
            duration_minutes = video.get('duration_minutes', 0)
            is_short = video.get('is_short', False)
            
            # If is_short not provided, compute it (Shorts are < 60 seconds)
            if not is_short and duration_minutes > 0:
                is_short = duration_minutes < 1  # < 60 seconds = Short
            
            # Parse publish date
            try:
                # yt-dlp returns YYYYMMDD format
                if published_at and len(published_at) == 8:
                    pub_date = datetime.strptime(published_at, '%Y%m%d')
                    days_ago = (today - pub_date).days
                else:
                    days_ago = 999  # If can't parse, reject
            except:
                days_ago = 999
            
            # Check all filters
            reject_reasons = []
            
            # FIXED: Exclude shorts AND all videos under 10 minutes (Andrew's rule)
            # Andrew's requirement: "if is_short true or duration < 60-90s, exclude"
            if is_short:
                reject_reasons.append(f"is_short=true (Shorts)")
            elif duration_minutes < 3:  # Exclude actual YouTube Shorts (<3 min) but allow short-form content (3-10 min)
                reject_reasons.append(f"too short ({duration_minutes:.1f} min)")
            
            if views < YOUTUBE_MIN_VIEWS:
                reject_reasons.append(f"{views:,} views")
            
            if days_ago > YOUTUBE_MAX_AGE_DAYS:
                reject_reasons.append(f"published {days_ago} days ago")
            
            if subscriber_count < YOUTUBE_MIN_SUBSCRIBERS:
                reject_reasons.append(f"{subscriber_count} subscribers")
            
            if reject_reasons:
                reason_str = ", ".join(reject_reasons)
                print(f"[YouTube] Rejected: \"{title}...\" ({reason_str})")
            else:
                filtered.append(video)
                print(f"[YouTube] Accepted: \"{title}...\" ({views:,} views, {days_ago} days ago, {duration_minutes:.1f} min)")
        
        print(f"[YouTube] Filtered: {len(filtered)}/{len(results)} videos passed validation")
        return filtered
    
    def _extract_search_keywords(self, title: str, niche: str = None) -> str:
        """Extract SHORT search keywords from title for YouTube search.
        
        Args:
            title: The title to extract keywords from
            niche: Optional niche keyword to include
        
        Returns:
            Short focused search query (max 5 words)
        """
        # Expanded filler words to filter out
        filler_words = {
            'the', 'a', 'an', 'of', 'in', 'on', 'at', 'to', 'for', 'with', 'by',
            'your', 'my', 'his', 'her', 'its', 'our', 'their', 'i', 'you', 'he', 'she',
            'they', 'we', 'this', 'that', 'these', 'those', 'into', 'from', 'over',
            'after', 'before', 'between', 'under', 'above', 'how', 'what', 'why',
            'hidden', 'truth', 'secret', 'real', 'reason', 'behind', 'about',
            'was', 'were', 'is', 'are', 'been', 'being', 'have', 'has', 'had',
            'do', 'does', 'did', 'will', 'would', 'could', 'should', 'may', 'might',
            'all', 'some', 'any', 'no', 'not', 'only', 'just', 'also', 'very',
            'last', 'next', 'then', 'now', 'when', 'where', 'who', 'which', 'can'
        }
        
        # Priority words that are most likely to find relevant YouTube videos
        priority_words = {
            'podcast', 'revenue', 'money', 'million', 'business', 'coaching',
            'marketing', 'growth', 'tips', 'strategy', 'health', 'fitness',
            'weight', 'loss', 'muscle', 'build', 'transform', 'success', 'fail',
            'make', 'generate', 'get', 'grow', 'scale', 'start', 'begin'
        }
        
        words = re.findall(r'\b[\w]+\b', title.lower())
        
        # Filter out filler words and short words
        filtered = [w for w in words if w not in filler_words and len(w) > 2]
        
        # Separate priority words from others
        priority = [w for w in filtered if w in priority_words]
        others = [w for w in filtered if w not in priority_words]
        
        # Take 2-4 most important keywords (prioritize priority words first)
        selected = priority[:4]
        if len(selected) < 4:
            # Fill in with other relevant words
            for w in others:
                if w not in selected:
                    selected.append(w)
                    if len(selected) >= 4:
                        break
        
        # Build result
        result = ' '.join(selected[:4]) if selected else 'podcast'
        
        # Add ONE niche keyword if provided (extract first word from niche phrase)
        if niche:
            niche_word = niche.split()[0] if niche else None
            if niche_word and niche_word not in result:
                result = f"{result} {niche_word}"
        
        # Limit to max 5 words total
        words_list = result.split()
        if len(words_list) > 5:
            result = ' '.join(words_list[:5])
        
        return result
    
    def _get_channel_subscribers(self, results: List[Dict[str, Any]]) -> Dict[str, int]:
        """Extract channel subscriber counts from results."""
        channel_subs = {}
        for r in results:
            channel_id = r.get('channel_id', '')
            subs = r.get('subscriber_count', 0)
            if channel_id and subs:
                channel_subs[channel_id] = subs
        return channel_subs
    
    # =========================================================================
    # STEP 4: GENERATE OUTPUTS (based on user selection)
    # =========================================================================
    
    def generate_outputs(self, winning_title: Dict[str, Any], config: Dict[str, Any], outputs: List[str]) -> Dict[str, Any]:
        """
        Step 4: Generate selected outputs only.
        
        Args:
            winning_title: The gold medal title
            config: Podcast config (MUST have prompts)
            outputs: List of requested outputs ['title', 'timestamps', 'description', 'tags', 'thumbnail']
        
        Returns:
            Dict with only the requested outputs
        
        Raises:
            KeyError: If required prompts are missing from config
        """
        print(f"[Optimizer] Generating outputs: {outputs}")
        
        # Helper to get title string from either 'title' or 'titles' format
        def get_title_str(title_obj):
            if isinstance(title_obj, dict):
                # Check for 'title' key first
                if 'title' in title_obj and title_obj['title']:
                    return title_obj['title']
                # Check for 'titles' array - use first one
                if 'titles' in title_obj and isinstance(title_obj['titles'], list) and title_obj['titles']:
                    return title_obj['titles'][0]
            return ''
        
        # Validate config has prompts (thumbnailText optional)
        prompts = config.get('prompts', {})
        if not prompts.get('chapters') or not prompts.get('description') or not prompts.get('tags'):
            raise KeyError("Config is missing required prompts. Cannot generate outputs.")
        
        result = {}
        best_outlier = winning_title.get('best_outlier', {})
        winning_youtube_title = best_outlier.get('title', '') if best_outlier else ''
        
        # TITLE
        if 'title' in outputs:
            original_title = get_title_str(winning_title)
            if winning_youtube_title:
                result['title'] = self._mimic_title(winning_youtube_title, original_title)
            else:
                result['title'] = original_title
            result['title'] = Humanizer.humanize_title(result['title'])
        
        # TIMESTAMPS
        if 'timestamps' in outputs:
            result['timestamps'] = self._generate_timestamps(config)
        
        # DESCRIPTION
        if 'description' in outputs:
            result['description'] = self._generate_description(winning_title, config)
        
        # TAGS
        if 'tags' in outputs:
            result['tags'] = self._generate_tags(winning_title, config)
        
        # THUMBNAIL TEXT
        if 'thumbnail' in outputs:
            result['thumbnail_text'] = self._generate_thumbnail_text(result.get('title', get_title_str(winning_title)), config)
        
        # Add metadata
        result['mimicked_from'] = winning_youtube_title if winning_youtube_title else None
        result['outlier_score'] = winning_title.get('score', 0)
        result['medal'] = winning_title.get('medal', 'gold')
        
        # Apply NameFilter to ALL tool-generated outputs
        # This ensures no personal names leak into final content
        result = NameFilter.filter_output(result)
        
        # Validate and log any remaining names (for monitoring)
        is_valid, detected = NameFilter.validate_output(result)
        if not is_valid:
            print(f"[Optimizer] WARNING: Names detected after filtering: {detected}")
        
        return result
    
    def _mimic_title(self, winning_youtube_title: str, original_title: str) -> str:
        """Create title that mimics the winning format."""
        prompt = f"""Based on this successful YouTube title:
"{winning_youtube_title}"

Create an optimized title for our podcast episode. The original title we generated was:
"{original_title}"

Requirements:
- Keep the same STRUCTURE/PATTERN that made the YouTube title successful
- Apply it to our content
- Make it sound natural and clickable
- Under 60 characters

Return ONLY the title, nothing else."""
        
        result, model_used = _call_ai(
            prompt, 
            "You are a YouTube title expert. Create optimized titles.",
            max_tokens=100,
            prefer_quality=True  # Force GPT-4o for title mimicry (critical quality)
        )
        
        if not result:
            raise RuntimeError("AI generation failed for title mimicry")
        
        print(f"[Optimizer] Title mimicry with: {model_used}")
        return result.strip()
    
    def _generate_timestamps(self, config: Dict[str, Any]) -> List[Dict[str, str]]:
        """
        Generate timestamps/chapters using FULL prompt from config.
        
        CRITICAL: Uses config prompts ONLY - NO FALLBACKS
        
        Required variables: {title}, {guest_name}, {episode_number}, {transcript}, {duration}
        
        ISSUE 2 FIX: Now includes video duration to ensure chapters cover full video.
        """
        # Get full chapters prompt from config
        prompts = config.get('prompts', {})
        chapters_prompt_template = prompts.get('chapters', '')
        
        if not chapters_prompt_template:
            raise KeyError("Config is missing 'chapters' prompt. Cannot generate timestamps.")
        
        # Get transcript and variables
        transcript = getattr(self, 'transcript', '')
        guest_name = getattr(self, 'guest_name', '')
        episode_number = getattr(self, 'episode_number', '')
        title = getattr(self, 'episode_title', '')
        video_duration = getattr(self, 'video_duration', 0)
        
        # Format duration for prompt (MM:SS or HH:MM:SS)
        if video_duration > 0:
            hours = int(video_duration // 3600)
            mins = int((video_duration % 3600) // 60)
            secs = int(video_duration % 60)
            if hours > 0:
                duration_str = f"{hours}:{mins:02d}:{secs:02d}"
            else:
                duration_str = f"{mins}:{secs:02d}"
        else:
            duration_str = "unknown"
        
        # Substitute variables in prompt
        chapters_prompt = chapters_prompt_template
        chapters_prompt = chapters_prompt.replace('{guest_name}', guest_name or 'the guest')
        chapters_prompt = chapters_prompt.replace('{episode_number}', episode_number or '')
        chapters_prompt = chapters_prompt.replace('{title}', title or '')
        chapters_prompt = chapters_prompt.replace('{duration}', duration_str)
        
        # Handle transcript - some configs use {transcript} placeholder, others need it appended
        if '{transcript}' in chapters_prompt:
            chapters_prompt = chapters_prompt.replace('{transcript}', transcript[:25000] if transcript else 'No transcript available')
        else:
            # Append transcript at end if placeholder not in template
            chapters_prompt = chapters_prompt + "\n\n--- TRANSCRIPT START ---\n" + (transcript[:25000] if transcript else 'No transcript available') + "\n--- TRANSCRIPT END ---"
        
        # ISSUE 2 FIX: Add explicit instruction about full video coverage
        if video_duration > 0:
            chapters_prompt += f"\n\nIMPORTANT: The video is {duration_str} long. Your chapters MUST cover the ENTIRE video from 0:00 to {duration_str}. Do not stop early!"
        
        print(f"[Optimizer] Generating timestamps (video duration: {duration_str})...")
        print(f"[Optimizer] Transcript length: {len(transcript)} chars")
        
        # Call AI - Use Gemini 2.5 Pro for timestamps (prefer_quality=True forces Gemini)
        result, model_used = _call_ai(
            chapters_prompt, 
            "You are an expert at creating YouTube chapter timestamps. Follow the prompt exactly and return ONLY the formatted chapter list.",
            max_tokens=2000,
            prefer_quality=True  # Use Gemini 2.5 Pro for better prompt following (Andrew says GPT-4o is "dog shit" for timestamps)
        )
        
        if not result:
            raise RuntimeError("AI generation failed for timestamps - both Gemini and OpenAI failed")
        
        print(f"[Optimizer] Timestamps generated with: {model_used}")
        print(f"[Optimizer] Raw timestamps response preview: {result[:500]}...")
        
        # Parse chapters from response
        chapters = self._parse_chapters_from_response(result)
        
        print(f"[Optimizer] Parsed {len(chapters)} chapters:")
        for ch in chapters[:5]:  # Log first 5 chapters
            print(f"  - {ch.get('timestamp')}: {ch.get('title')}")
        
        # ISSUE 2 FIX: Validate chapters cover the full video
        if video_duration > 0 and chapters:
            chapters = self._validate_chapters_cover_video(chapters, video_duration)
            print(f"[Optimizer] After validation: {len(chapters)} chapters (last: {chapters[-1].get('timestamp')})")
        
        # Andrew's Decision (2026-02-27): Filter out outro chapters and chapters in last 30 seconds
        if video_duration > 0:
            chapters = self._filter_outro_chapters(chapters, video_duration)
            print(f"[Optimizer] After outro filtering: {len(chapters)} chapters")
        
        return chapters
    
    def _filter_outro_chapters(self, chapters: List[Dict[str, str]], video_duration: float) -> List[Dict[str, str]]:
        """
        Filter out:
        1. Outro chapters (subscribe, follow, etc.)
        2. Chapters within last 30 seconds of video
        """
        # Outro patterns to filter
        outro_patterns = [
            r'subscribe',
            r'follow\s*(me|us|on)?',
            r'like\s*(and\s*)?(subscribe)?',
            r'social\s*(media)?',
            r'instagram',
            r'twitter',
            r'facebook',
            r'youtube\s*channel',
            r'next\s*episode',
            r'see\s*you\s*(next|soon)',
            r'thanks\s*for\s*(watching|listening)',
            r'bye',
            r'outro',
            r'credits',
            r'wrap[\s-]?up',
            r'final',
            r'conclusion',
        ]
        
        def is_outro_chapter(title: str) -> bool:
            """Check if chapter is an outro."""
            title_lower = title.lower()
            return any(re.search(pattern, title_lower) for pattern in outro_patterns)
        
        def ts_to_seconds(ts_str: str) -> float:
            """Convert timestamp string to seconds."""
            if not ts_str:
                return 0
            try:
                parts = ts_str.split(':')
                if len(parts) == 3:
                    return int(parts[0]) * 3600 + int(parts[1]) * 60 + int(parts[2])
                elif len(parts) == 2:
                    return int(parts[0]) * 60 + int(parts[1])
            except:
                pass
            return 0
        
        # Last 30 seconds threshold
        cutoff_time = video_duration - 30
        if cutoff_time < 0:
            cutoff_time = 0
        
        filtered = []
        for chapter in chapters:
            title = chapter.get('title', '')
            ts_str = chapter.get('timestamp', '0:00')
            ts_seconds = ts_to_seconds(ts_str)
            
            # Skip outro chapters
            if is_outro_chapter(title):
                print(f"[Optimizer] Filtering out outro chapter: {ts_str} - {title}")
                continue
            
            # Skip chapters in last 30 seconds
            if ts_seconds >= cutoff_time:
                print(f"[Optimizer] Filtering chapter in last 30s: {ts_str} - {title}")
                continue
            
            filtered.append(chapter)
        
        # Ensure we have at least one chapter (keep last non-outro if all filtered)
        if not filtered and chapters:
            # Keep non-outro chapters even if in last 30s
            for chapter in chapters:
                title = chapter.get('title', '')
                if not is_outro_chapter(title):
                    filtered.append(chapter)
                    break
        
        return filtered
    
    def _validate_chapters_cover_video(self, chapters: List[Dict[str, str]], video_duration: float) -> List[Dict[str, str]]:
        """Validate that chapters cover the full video duration. Add final chapter if needed."""
        if not chapters:
            return chapters
            
        # Parse last chapter timestamp
        last_chapter = chapters[-1]
        last_ts = last_chapter.get('timestamp', '0:00')
        
        # Convert timestamp to seconds
        def ts_to_seconds(ts_str):
            parts = ts_str.split(':')
            if len(parts) == 3:
                return int(parts[0]) * 3600 + int(parts[1]) * 60 + int(parts[2])
            elif len(parts) == 2:
                return int(parts[0]) * 60 + int(parts[1])
            return 0
        
        last_ts_seconds = ts_to_seconds(last_ts)
        
        # If last chapter doesn't cover at least 90% of video, add final chapter
        if last_ts_seconds < video_duration * 0.9:
            print(f"[Optimizer] WARNING: Chapters only cover {last_ts_seconds:.0f}s of {video_duration:.0f}s. Adding final chapter.")
            
            # Add final chapter at video end
            hours = int(video_duration // 3600)
            mins = int((video_duration % 3600) // 60)
            secs = int(video_duration % 60)
            if hours > 0:
                final_ts = f"{hours}:{mins:02d}:{secs:02d}"
            else:
                final_ts = f"{mins}:{secs:02d}"
            
            chapters.append({
                'timestamp': final_ts,
                'title': 'Conclusion'
            })
        
        return chapters
    
    def _parse_chapters_from_response(self, raw: str) -> List[Dict[str, str]]:
        """Parse chapter markers from AI response."""
        chapters = []
        
        # Look for [Time] - Title format
        lines = raw.split('\n')
        for line in lines:
            match = re.match(r'^[\s]*\[?(\d{1,2}:\d{2}(?::\d{2})?)\]?\s*-\s*(.+)$', line.strip())
            if match:
                chapters.append({
                    'timestamp': match.group(1),
                    'title': Humanizer.humanize_title(match.group(2).strip())
                })
        
        if not chapters:
            # Try alternative format
            for line in lines:
                match = re.match(r'^[\s]*(\d{1,2}:\d{2})\s+(.+)$', line.strip())
                if match:
                    chapters.append({
                        'timestamp': match.group(1),
                        'title': Humanizer.humanize_title(match.group(2).strip())
                    })
        
        if not chapters:
            raise RuntimeError(f"Could not parse chapters from AI response: {raw[:200]}")
        
        return chapters
    
    def _generate_cta(self, title_item: Dict[str, Any], config: Dict[str, Any]) -> str:
        """
        Generate episode-relevant CTA as a separate step.
        
        FIXED (2026-03-02): Improved CTA format to always output:
        - Line 1: Hook question relevant to episode
        - Line 2: "Check out [Product] at [URL]"
        
        Andrew's feedback: "The description has not passed once" - bare URLs are BAD.
        """
        def get_title_str(item):
            if isinstance(item, dict):
                if 'title' in item and item['title']:
                    return item['title']
                if 'titles' in item and isinstance(item['titles'], list) and item['titles']:
                    return item['titles'][0]
            return ''
        
        # Get variables
        transcript = getattr(self, 'transcript', '')
        title = get_title_str(title_item)
        
        # Get podcast config
        podcast_config = config.get('podcast', {})
        cta_config = podcast_config.get('cta', {})
        cta_url = cta_config.get('url', 'https://pursuepodcasting.com')
        cta_text = cta_config.get('text', 'The Fitness Authority Academy')  # Use config CTA text
        
        # Get podcast code - check both attribute and config
        podcast_code = getattr(self, 'podcast_code', '').lower() if hasattr(self, 'podcast_code') else ''
        if not podcast_code:
            podcast_code = config.get('podcast_code', '').lower()
        if not podcast_code:
            # Try to infer from podcast name
            podcast_name = podcast_config.get('name', '').lower()
            if 'just pursue it' in podcast_name or 'jpi' in podcast_name:
                podcast_code = 'jpi'
        
        # Get transcript sample for context (first 5000 chars)
        transcript_sample = transcript[:5000] if transcript else 'No transcript available'
        
        # Generate episode-relevant CTA via AI
        # Only generate Line 1 (the hook) - Line 2 is always the same product/URL
        cta_prompt = f"""Write ONE compelling question (hook) for a podcast episode CTA.

EPISODE TITLE: {title}

TRANSCRIPT CONTEXT:
{transcript_sample[:2000]}

Write a 1-line question that:
- Directly relates to this episode's topic
- Makes the viewer want to learn more
- Is under 50 characters
- Sounds conversational, not corporate

EXAMPLES of good hooks:
- "Struggling to grow your podcast?"
- "Want more coaching clients?"
- "Tired of feeling sleazy when selling?"

Return ONLY the question, nothing else."""
        
        print("[Optimizer] Generating episode-relevant CTA hook...")
        
        result, model_used = _call_ai(
            cta_prompt,
            "You are an expert at writing compelling podcast CTA hooks. Output ONLY one question.",
            max_tokens=100,
            prefer_quality=True
        )
        
        if result and len(result.strip()) > 10:
            hook = result.strip()
            # Remove any quotes the AI might have added
            hook = hook.strip('"\'')
            # Use the full CTA line from config (no hardcoded "Check out")
            cta_line = cta_config.get('line', f"{cta_text} at {cta_url}")
            cta = f"{hook}\n{cta_line}"
            print(f"[Optimizer] CTA generated with: {model_used}")
            return cta
        
        # Fallback: Use AI to generate a simple CTA from the episode title (NO hardcoded templates)
        # Per Andrew's rule: NO HARD CODED ANYTHING - even fallback must use AI
        print("[Optimizer] First AI call failed, using fallback AI generation...")
        
        fallback_prompt = f"""Generate a 1-line CTA hook (question format) based ONLY on this episode title:
"{title}"

The hook should ask a relevant question about the episode's topic. Keep it under 50 characters.
Return ONLY the question, nothing else."""
        
        fallback_result, _ = _call_ai(
            fallback_prompt,
            "You are an expert at writing short, relevant CTA hooks.",
            max_tokens=50,
            prefer_quality=True  # Use faster model for simple fallback
        )
        
        if fallback_result and len(fallback_result.strip()) > 5:
            hook = fallback_result.strip()
            # Ensure it ends with a question mark
            if not hook.endswith('?'):
                hook = hook + '?'
            cta_line = cta_config.get('line', f"{cta_text} at {cta_url}")
            return f"{hook}\n{cta_line}"
        
        # Last resort fallback: Generic but still AI-generated, not hardcoded
        # This should rarely/never happen - both AI calls would need to fail
        generic_prompt = f"""Write a short, general fitness business question (under 40 chars) that could apply to any fitness entrepreneur.
Return ONLY the question, nothing else."""
        
        generic_result, _ = _call_ai(
            generic_prompt,
            "You are an expert at writing short fitness business questions.",
            max_tokens=30,
            prefer_quality=True
        )
        
        hook = generic_result.strip() if generic_result else "Want to grow your fitness business?"
        if not hook.endswith('?'):
            hook = hook + '?'
        
        cta_line = cta_config.get('line', f"{cta_text} at {cta_url}")
        return f"{hook}\n{cta_line}"
    
    def _generate_description(self, title_item: Dict[str, Any], config: Dict[str, Any]) -> str:
        """
        Generate YouTube description using Andrew's EXACT prompt.
        
        CRITICAL RULES (from Andrew):
        1. FIRST LINE MUST BE: https://pursuepodcasting.com (exact URL, nothing else)
        2. NO newsletter CTAs - Andrew doesn't have a newsletter
        3. Use Andrew's EXACT prompt from youtube-prompts.txt
        4. Prepend CTA from config after AI generates the description
        
        Required variables: {title}, {guest_name}, {episode_number}, {transcript}
        """
        # Helper to get title string from either 'title' or 'titles' format
        def get_title_str(item):
            if isinstance(item, dict):
                if 'title' in item and item['title']:
                    return item['title']
                if 'titles' in item and isinstance(item['titles'], list) and item['titles']:
                    return item['titles'][0]
            return ''
        
        # Get variables for substitution
        transcript = getattr(self, 'transcript', '')
        title = get_title_str(title_item)
        guest_name = getattr(self, 'guest_name', '')
        episode_number = getattr(self, 'episode_number', '')
        
        # Get podcast config
        podcast_config = config.get('podcast', {})
        podcast_name = podcast_config.get('name', 'Just Pursue It Podcast')
        host_name = podcast_config.get('host', 'Andrew Zaragoza')
        
        # Build guest part for prompt
        guest_part = f"and guest {guest_name}" if guest_name else ""
        
        # Build episode number part (handle missing episode number gracefully)
        if episode_number:
            episode_part = f"episode {episode_number} of"
        else:
            episode_part = "an episode of"
        
        # Get transcript sample (first 15000 chars)
        transcript_sample = transcript[:15000] if transcript else 'No transcript available'
        
        # USE PROMPT FROM CONFIG - not hardcoded
        prompts = config.get('prompts', {})
        description_prompt_template = prompts.get('description', '')
        
        if not description_prompt_template:
            raise KeyError("Config is missing 'prompts.description'. Cannot generate description.")
        
        # Build perspective instruction FIRST - this is CRITICAL
        if guest_name:
            perspective_instruction = f"""**CRITICAL PERSPECTIVE RULE - READ THIS FIRST:**
This episode features guest {guest_name}. When describing what the guest experienced or learned:
- Write in THIRD PERSON about {guest_name}
- Use "{guest_name} shares...", "{guest_name} explains...", "{guest_name} reveals..."
- Do NOT use "I" or "my" for the guest's experiences
- It's OK to use "I" for the host ({host_name}) saying things like "I sit down with {guest_name}..."
- But the guest's personal stories must be third person: "{guest_name} went through..." NOT "I went through..."

"""
        else:
            perspective_instruction = f"""**PERSPECTIVE:**
Write in first person from {host_name}'s perspective. Use 'I' and 'my' naturally.

"""
        
        # Start with perspective instruction, then the config prompt
        description_prompt = perspective_instruction
        
        # Substitute placeholders in the config prompt
        # Handle empty values gracefully
        config_prompt_filled = description_prompt_template.format(
            title=title,
            guest_name=guest_name if guest_name else '',
            episode_number=episode_number if episode_number else '',
            transcript=transcript_sample
        )
        
        # Clean up any empty placeholder remnants (e.g., "the guest  and" or "episode  of")
        config_prompt_filled = re.sub(r'the guest\s+and', 'and', config_prompt_filled)
        config_prompt_filled = re.sub(r'episode\s+of the', 'an episode of the', config_prompt_filled)
        config_prompt_filled = re.sub(r'\s+', ' ', config_prompt_filled)
        
        description_prompt += config_prompt_filled
        
        # Add transcript at the end
        description_prompt += f"\n\nHere is the transcript:\n{transcript_sample}"
        
        print(f"[Optimizer] Generating description using config prompt for {podcast_name}...")
        
        # Call AI (MiniMax first for quality, then OpenAI fallback)
        result, model_used = _call_ai(
            description_prompt,
            "You are an expert YouTube description writer. Follow ALL requirements exactly.",
            max_tokens=2000,
            prefer_quality=True  # Use MiniMax for better descriptions
        )
        
        if not result:
            raise RuntimeError("AI generation failed for description - both Gemini and OpenAI failed")
        
        print(f"[Optimizer] Description generated with: {model_used}")
        
        # Humanize the result
        description = Humanizer.humanize(result)
        
        # NOTE: CTA is handled by the config prompt itself (Andrew's prompts include CTA instructions)
        # We do NOT prepend an additional CTA - that causes duplicates
        
        # Remove any newsletter references completely
        description = re.sub(r'\bnewsletter\b', '', description, flags=re.IGNORECASE)
        description = re.sub(r'\bsubscribe to\b', '', description, flags=re.IGNORECASE)
        description = re.sub(r'\bsign up for\b', '', description, flags=re.IGNORECASE)
        
        # Clean up extra whitespace
        description = re.sub(r'\n\n+', '\n\n', description)
        description = description.strip()
        
        # Enforce max length with proper sentence boundary truncation
        # Target ~600 chars, HARD CAP at 900 chars, NEVER cut mid-word/sentence
        TARGET_DESC_LENGTH = 600
        HARD_CAP_LENGTH = 900
        
        if len(description) > HARD_CAP_LENGTH:
            # Find the last complete sentence before hard cap
            # Look for sentence endings: . ! ?
            description = _truncate_at_sentence_boundary(description, HARD_CAP_LENGTH)
        
        return description
    
    def _generate_tags(self, title_item: Dict[str, Any], config: Dict[str, Any]) -> List[str]:
        """
        Generate YouTube tags using Andrew's EXACT prompt.
        
        CRITICAL RULES (from Andrew):
        1. ALWAYS include: podcast name, host name
        2. Use Andrew's EXACT prompt from youtube-prompts.txt
        3. Add required tags (podcast name, host name) after AI generates
        4. Total must be under 500 characters
        
        Required variables: {title}, {transcript}
        """
        # Helper to get title string from either 'title' or 'titles' format
        def get_title_str(item):
            if isinstance(item, dict):
                if 'title' in item and item['title']:
                    return item['title']
                if 'titles' in item and isinstance(item['titles'], list) and item['titles']:
                    return item['titles'][0]
            return ''
        
        # Get variables
        title = get_title_str(title_item)
        transcript = getattr(self, 'transcript', '')
        
        # Get podcast config
        podcast_config = config.get('podcast', {})
        podcast_name = podcast_config.get('name', 'Just Pursue It Podcast')
        host_name = podcast_config.get('host', 'Andrew Zaragoza')
        
        # Get transcript sample (first 10000 chars)
        transcript_sample = transcript[:10000] if transcript else 'No transcript available'
        
        # USE PROMPT FROM CONFIG - not hardcoded
        prompts = config.get('prompts', {})
        tags_prompt_template = prompts.get('tags', '')
        
        if not tags_prompt_template:
            raise KeyError("Config is missing 'prompts.tags'. Cannot generate tags.")
        
        # Substitute placeholders in the config prompt
        tags_prompt = tags_prompt_template.format(
            title=title,
            transcript=transcript_sample
        )
        
        # Add transcript if not already in prompt
        if '{transcript}' not in tags_prompt_template:
            tags_prompt += f"\n\nHere is the transcript:\n{transcript_sample}"
        
        print(f"[Optimizer] Generating tags using config prompt for {podcast_name}...")
        
        # Call AI (Gemini first, then OpenAI)
        result, model_used = _call_ai(
            tags_prompt,
            "You are an expert at YouTube SEO and tag generation. Follow ALL requirements exactly.",
            max_tokens=800,
            prefer_quality=True
        )
        
        if not result:
            raise RuntimeError("AI generation failed for tags - both Gemini and OpenAI failed")
        
        print(f"[Optimizer] Tags generated with: {model_used}")
        
        # Parse tags (comma-separated)
        tags = [tag.strip() for tag in result.split(',') if tag.strip()]
        
        # Remove character count line if present
        tags = [t for t in tags if not t.lower().startswith('character count') and not t.lower().startswith('total:')]
        
        # Add required tags (podcast name, host name) - add at beginning if not already present
        required_tags = [podcast_name, host_name]
        for req_tag in required_tags:
            req_tag_lower = req_tag.lower()
            found = any(req_tag_lower in tag.lower() for tag in tags)
            if not found:
                tags.insert(0, req_tag)
        
        # Filter out nonsense tags
        cleaned_tags = []
        for tag in tags:
            # Skip tags that are just numbers
            if re.match(r'^\d+$', tag):
                continue
            # Skip tags with 3+ consecutive digits (e.g., "make money online 448")
            if re.search(r'\d{3,}', tag):
                continue
            # Skip very short tags
            if len(tag) < 3:
                continue
            # Skip generic business terms that aren't episode-specific
            generic_terms = ['make money online', 'digital marketing', 'online business', 
                           'entrepreneur', 'side hustle', 'passive income']
            if any(generic in tag.lower() for generic in generic_terms) and len(tags) > 10:
                continue
            cleaned_tags.append(tag)
        
        tags = cleaned_tags
        
        # Enforce max length (500 characters)
        max_chars = 500
        tags_str = ', '.join(tags)
        if len(tags_str) > max_chars:
            # Trim from the end, but never remove required tags
            while tags and len(', '.join(tags)) > max_chars:
                if tags[-1] in required_tags:
                    # Try to remove a non-required tag instead
                    for i in range(len(tags) - 1, -1, -1):
                        if tags[i] not in required_tags:
                            tags.pop(i)
                            break
                    else:
                        break
                else:
                    tags.pop()
        
        print(f"[Optimizer] Final tags ({len(tags)}): {tags[:10]}...")
        return tags
    
    def _generate_thumbnail_text(self, title: str, config: Dict[str, Any]) -> List[str]:
        """
        Generate thumbnail text using Andrew's EXACT prompt.
        
        CRITICAL: Uses Andrew's EXACT prompt from youtube-prompts.txt
        
        Required variables: {title}
        """
        # Get full thumbnail text prompt from config (but we'll use Andrew's exact prompt)
        prompts = config.get('prompts', {})
        podcast_config = config.get('podcast', {})
        target_audience = podcast_config.get('targetAudience', 'listeners')
        
        # Get transcript for context
        transcript = getattr(self, 'transcript', '')
        transcript_sample = transcript[:5000] if transcript else 'No transcript available'
        
        # ANDREW'S EXACT THUMBNAIL PROMPT
        thumbnail_prompt = f"""Using The Ultimate YouTube Thumbnail Creation Framework, please analyze this video and help me come up with text for the thumbnail of this video that is likely to be successful on YouTube, get a high click through rate and garner many views. 

The title is going to be, "{title}". 

The thumbnail text MUST be formatted on three separate lines with 1-2 words per line. Each suggestion should clearly display this formatting. 

Please keep in mind our target audience when coming up with the text ideas. The thumbnail text should be an extension of the title, not a copy or abbreviation of the title. It should invoke an emotion that entices the viewer to click on the video. The text should use short, impactful words that evoke curiosity, urgency, or inspiration, aligning with the fitness and personal development theme. 

Please do not use the phrases, "game changing", "Peak Performance" nor "next level" "Unlock", please do not use "—" and do not use emojis. 

Please come up with 5 ideas.

Here is the transcript:
{transcript_sample}"""
        
        print("[Optimizer] Generating thumbnail text with Andrew's EXACT prompt...")
        
        # Call AI (Gemini first, then OpenAI)
        result, model_used = _call_ai(
            thumbnail_prompt + "\n\nIMPORTANT: Format each option clearly as:\nOption 1:\nLINE1\nLINE2\nLINE3\nOption 2:\nLINE1\nLINE2\nLINE3\netc.\nEach option should have exactly 3 lines of 1-2 words each, in UPPERCASE.",
            "You are an expert at creating YouTube thumbnail text. Follow the prompt exactly and return 5 clear options.",
            max_tokens=1000,
            prefer_quality=True
        )
        
        if not result:
            raise RuntimeError("AI generation failed for thumbnail text - both Gemini and OpenAI failed")
        
        print(f"[Optimizer] Thumbnail text generated with: {model_used}")
        
        # Parse thumbnail options
        return self._parse_thumbnail_options(result)
    
    def _parse_thumbnail_options(self, raw: str) -> List[str]:
        """
        Parse thumbnail text options from AI response.
        
        Returns exactly 5 options, each formatted as 3 lines of uppercase words.
        
        ISSUE 3 FIX: Simplified and more robust parsing.
        """
        print(f"[Optimizer] Raw thumbnail response preview: {raw[:500]}...")
        
        options = []
        
        # Clean up the raw response first
        raw_clean = raw.strip()
        
        # Split into lines and extract short meaningful phrases (potential thumbnail text)
        lines = raw_clean.split('\n')
        short_lines = []
        
        for line in lines:
            line = line.strip()
            # Skip empty lines, headers, numbers alone
            if not line:
                continue
            # Skip lines that are just headers/numbers
            if re.match(r'^(option\s*)?\d+[\.\):]?\s*$', line, re.IGNORECASE):
                continue
            if line.startswith('#'):
                continue
            # Keep lines that are short phrases (1-4 words, likely thumbnail text)
            words = line.split()
            if 1 <= len(words) <= 4 and len(line) <= 30:
                short_lines.append(line.upper())
        
        # Now group short lines into sets of 3
        # We want to find groups that form coherent thumbnail text
        i = 0
        while i < len(short_lines) and len(options) < 5:
            # Take next 3 lines as an option
            group = short_lines[i:i+3]
            if len(group) >= 1:  # At least 1 line is okay
                # Pad to 3 lines if needed
                while len(group) < 3:
                    group.append("")
                option_text = '\n'.join(group[:3])
                if option_text and "OPTION" not in option_text:
                    options.append(option_text)
            i += 3
        
        # If still not enough, try with offset of 1 (to catch different groupings)
        if len(options) < 5:
            i = 1
            while i < len(short_lines) and len(options) < 5:
                group = short_lines[i:i+3]
                if len(group) >= 1:
                    while len(group) < 3:
                        group.append("")
                    option_text = '\n'.join(group[:3])
                    if option_text and "OPTION" not in option_text and option_text not in options:
                        options.append(option_text)
                i += 3
        
        # Deduplicate
        seen = set()
        unique_options = []
        for opt in options:
            if opt and opt not in seen:
                seen.add(opt)
                unique_options.append(opt)
        
        options = unique_options
        
        # Ensure we have exactly 5 options - if not, generate sensible defaults
        if len(options) < 5:
            print(f"[Optimizer] Only parsed {len(options)} thumbnail options, generating defaults...")
            # Generate meaningful defaults based on the title if available
            base_options = [
                "THE TRUTH\nABOUT\nPODCAST",
                "STOP THIS\nMISTAKE\nNOW",
                "SECRET\nMILLION\nMETHOD",
                "WHY\nYOUR\nPODCAST",
                "HOW TO\nGET\nCLIENTS"
            ]
            while len(options) < 5:
                idx = len(options)
                if idx < len(base_options):
                    options.append(base_options[idx])
                else:
                    options.append(f"OPTION\n{idx+1}\nTEXT")
        
        print(f"[Optimizer] Final parsed {len(options)} thumbnail options")
        for i, opt in enumerate(options):
            print(f"  Option {i+1}: {opt[:50]}...")
        
        return options[:5]
    
    # =========================================================================
    # MAIN PIPELINE
    # =========================================================================
    
    def optimize(self,
                 youtube_url: str,
                 podcast_code: str = 'spp',
                 outputs: List[str] = None,
                 guest_name: str = None,
                 episode_number: str = None,
                 episode_title: str = None) -> Dict[str, Any]:
        """
        Full optimization pipeline with output selection.

        Args:
            youtube_url: YouTube video URL
            podcast_code: One of 'spp', 'jpi', 'sbs', 'wow', 'agp'
            outputs: List of outputs to generate ['title', 'timestamps', 'description', 'tags', 'thumbnail']
            guest_name: Guest name for prompt variables
            episode_number: Episode number for prompt variables
            episode_title: Episode title (if already known) for prompt variables

        Returns:
            Complete optimization result with medal rankings
        
        Raises:
            FileNotFoundError: If config file doesn't exist
            KeyError: If required prompts are missing from config
            RuntimeError: If AI generation fails
        """
        # Default outputs
        if outputs is None:
            outputs = ['title', 'timestamps', 'description', 'tags', 'thumbnail']

        print(f"\n{'='*60}")
        print(f"EPISODE OPTIMIZER V3 - Starting")
        print(f"Podcast: {podcast_code}")
        print(f"Outputs: {outputs}")
        print(f"{'='*60}\n")

        # Store for use in generation methods
        self.guest_name = guest_name or ''
        self.episode_number = episode_number or ''
        self.episode_title = episode_title or ''
        
        # Load podcast config (validates prompts exist)
        config = load_podcast_config(podcast_code)
        self.config = config
        self.podcast_code = podcast_code  # Store for use in CTA generation
        
        result = {
            'youtube_url': youtube_url,
            'podcast': podcast_code,
            'podcast_name': config.get('podcast', {}).get('name'),
            'outputs_requested': outputs,
            'transcript': None,
            'transcript_extracted': False,
            'ranked_titles': [],
            'gold_title': None,
            'silver_title': None,
            'bronze_title': None,
            'final_content': {},
            'error': None,
            'timestamp': datetime.now().isoformat()
        }
        
        try:
            # Step 1: Extract transcript
            print("STEP 1: Extracting Transcript")
            print("-" * 40)
            transcript_result = self.extract_transcript(youtube_url)
            
            if transcript_result['success']:
                self.transcript = transcript_result.get('transcript', '')
                result['transcript'] = self.transcript[:500] + "..." if len(self.transcript) > 500 else self.transcript
                result['transcript_extracted'] = True
                result['video_title'] = transcript_result.get('title', '')
                result['video_id'] = transcript_result.get('video_id', '')
                self.video_duration = transcript_result.get('duration', 0)  # Store for chapter validation
                result['video_duration'] = self.video_duration
            else:
                result['error'] = f"Transcript extraction failed: {transcript_result.get('error')}"
                self.transcript = "Sample transcript for testing purposes."
                result['transcript'] = self.transcript
                result['transcript_extracted'] = False
                self.video_duration = 0
            
            # Check if episode title was provided (internal use)
            if self.episode_title:
                print("\nSTEP 2: Using Provided Episode Title (Internal)")
                print("-" * 40)
                # Use provided title as gold, skip generation
                gold = {
                    'title': self.episode_title,
                    'topic': 'Custom',
                    'guest_name': self.guest_name,
                    'episode_number': self.episode_number,
                    'medal': 'gold',
                    'score': 100
                }
                result['gold_title'] = gold
                result['silver_title'] = None
                result['bronze_title'] = None
                result['ranked_titles'] = [gold]
            else:
                # Step 2: Generate title options
                print("\nSTEP 2: Generating Title Options")
                print("-" * 40)
                title_options = self.generate_title_options(result['transcript'], config)
                result['ranked_titles'] = title_options

                # Step 3: Validate and rank with medals
                print("\nSTEP 3: Validating on YouTube & Ranking")
                print("-" * 40)
                ranked_titles = self.validate_and_rank_titles(title_options)
                result['ranked_titles'] = ranked_titles

                # Extract medal winners
                gold = next((t for t in ranked_titles if t.get('medal') == 'gold'), None)
                silver = next((t for t in ranked_titles if t.get('medal') == 'silver'), None)
                bronze = next((t for t in ranked_titles if t.get('medal') == 'bronze'), None)

                result['gold_title'] = gold
                result['silver_title'] = silver
                result['bronze_title'] = bronze

            # Step 4: Generate selected outputs
            print("\nSTEP 4: Generating Outputs")
            print("-" * 40)
            if gold:
                # Add guest info to title item for prompt variables
                gold['guest_name'] = self.guest_name
                gold['episode_number'] = self.episode_number
                final_content = self.generate_outputs(gold, config, outputs)
                result['final_content'] = final_content
            
            print("\n" + "="*60)
            print("OPTIMIZATION COMPLETE")
            print("="*60)
            
            return result
            
        except Exception as e:
            result['error'] = str(e)
            print(f"[Optimizer] Error: {e}")
            return result


# ============================================================================
# SINGLETON
# ============================================================================

optimizer = EpisodeOptimizerV3()


def get_optimizer() -> EpisodeOptimizerV3:
    """Get the optimizer instance."""
    return optimizer

# ============================================================================
# PARTNER WORKFLOW HELPER FUNCTIONS
# ============================================================================

def _generate_timestamps_simple(transcript: str, config: Dict[str, Any]) -> List[Dict[str, str]]:
    """
    Generate timestamps/chapters quickly for topic extraction.
    This is a simplified version for the initial title generation flow.
    """
    prompts = config.get('prompts', {})
    chapters_prompt_template = prompts.get('chapters', '')
    
    if not chapters_prompt_template:
        # Fallback: simple timestamp generation
        print("[TopicTitles] No chapters prompt in config, using simple timestamp generation")
        return _simple_timestamps_fallback(transcript)
    
    podcast_section = config.get('podcast', {})
    podcast_name = podcast_section.get('name', 'The Podcast')
    host_name = podcast_section.get('host', 'Host')
    guest_name = ''
    
    # Estimate duration from transcript length (avg 150 words/min)
    words = len(transcript.split())
    estimated_mins = max(1, words // 150)
    
    # Substitute variables
    prompt = chapters_prompt_template
    prompt = prompt.replace('{guest_name}', guest_name or 'the guest')
    prompt = prompt.replace('{episode_number}', '')
    prompt = prompt.replace('{title}', '')
    prompt = prompt.replace('{duration}', f"{estimated_mins}:00")
    
    if '{transcript}' in prompt:
        prompt = prompt.replace('{transcript}', transcript[:15000])
    else:
        prompt = prompt + "\n\n" + transcript[:15000]
    
    # Add instruction for simpler output
    prompt += "\n\nGenerate simple timestamps only. Format: timestamp - title"
    
    result, model_used = _call_ai(
        prompt,
        "Generate YouTube chapter timestamps.",
        max_tokens=1000,
        prefer_quality=True  # Use Gemini for speed
    )
    
    if not result:
        return _simple_timestamps_fallback(transcript)
    
    # Parse timestamps
    chapters = []
    import re
    for line in result.split('\n'):
        match = re.match(r'^[\s]*\[?(\d{1,2}:\d{2}(?::\d{2})?)\]?\s*-\s*(.+)$', line.strip())
        if match:
            chapters.append({
                'timestamp': match.group(1),
                'title': match.group(2).strip()[:80]
            })
    
    if not chapters:
        return _simple_timestamps_fallback(transcript)
    
    print(f"[TopicTitles] Generated {len(chapters)} chapters")
    return chapters[:10]  # Limit to 10 chapters


def _simple_timestamps_fallback(transcript: str) -> List[Dict[str, str]]:
    """Generate simple timestamps when AI fails."""
    words = len(transcript.split())
    # Create ~8 chapters evenly distributed
    num_chapters = 8
    words_per_chapter = words // num_chapters
    
    chapters = []
    for i in range(num_chapters):
        mins = (i * words_per_chapter * 60) // words if words > 0 else i * 3
        timestamp = f"{mins:02d}:00"
        chapters.append({
            'timestamp': timestamp,
            'title': f"Part {i+1}"
        })
    
    return chapters


def extract_topics_from_chapters(chapters: List[Dict], transcript: str, config: Dict) -> List[Dict]:
    """
    Group chapters into 3-5 main topics.
    
    A TOPIC is a high-level theme that may span multiple chapters.
    A CHAPTER is a specific 3-min segment.
    
    Args:
        chapters: List of timestamp dicts [{'timestamp': '0:00', 'title': '...'}]
        transcript: Full transcript text
        config: Podcast config
        
    Returns:
        [
            {
                'topic': 'YouTube Shorts vs Long Form',
                'chapters': ['Shorts Strategy', 'Why Shorts Kill...'],
                'duration_minutes': 13
            },
            ...
        ]
    """
    # Format chapters for prompt
    chapters_text = "\n".join([f"{c['timestamp']} - {c['title']}" for c in chapters])
    
    podcast_section = config.get('podcast', {})
    target_audience = podcast_section.get('targetAudience', 'general audience')
    
    prompt = f"""Analyze this episode and group the chapters into 3-5 MAIN TOPICS.

A TOPIC is a high-level theme that may span multiple chapters.
A CHAPTER is a specific segment (3-5 min).

Chapters/Timestamps:
{chapters_text}

Task: Identify 3-5 main topics that capture what this episode is ABOUT.
Each topic should group related chapters together.

Target Audience: {target_audience}

Return ONLY valid JSON array:
[
  {{
    "topic": "High-level theme name",
    "chapters": ["Chapter title 1", "Chapter title 2"],
    "duration_minutes": 13
  }}
]

Focus on topics that match the target audience. Topics should be descriptive and specific."""

    result, model_used = _call_ai(
        prompt, 
        "You extract main topics from transcripts.", 
        max_tokens=800,
        prefer_quality=True
    )
    
    # Parse JSON
    import re, json
    try:
        json_match = re.search(r'\[[\s\S]*\]', result)
        if json_match:
            topics = json.loads(json_match.group())
            
            # FIX: Filter out placeholder topics
            filtered_topics = _filter_placeholder_topics(topics)
            print(f"[TopicTitles] Extracted {len(filtered_topics)} topics from chapters")
            return filtered_topics[:5]  # Max 5 topics
    except Exception as e:
        print(f"[TopicTitles] Failed to parse topics: {e}")
    
    # Fallback: treat first 5 NON-placeholder chapters as topics
    print("[TopicTitles] Using fallback: treating first 5 non-placeholder chapters as topics")
    
    # Filter out placeholder chapters first
    valid_chapters = _filter_placeholder_chapters(chapters)
    
    return [
        {'topic': c['title'], 'chapters': [c['title']], 'duration_minutes': 3} 
        for c in valid_chapters[:5]
    ]


def _filter_placeholder_topics(topics: List[Dict]) -> List[Dict]:
    """
    Filter out placeholder topics that don't contain real content.
    
    Placeholder patterns to filter:
    - "Topic X: Deep Dive"
    - "Section X"
    - Generic numbered topics
    """
    placeholder_patterns = [
        r'^topic\s*\d+',
        r'^section\s*\d+',
        r'^chapter\s*\d+',
        r'deep\s*dive',
        r'intro(?:duction)?',
        r'conclusion',
        r'summary',
        r'outro',
        r'q&a',
        r'questions?\s*and\s*answers?',
        r'discussion',
        r'analysis',
        r'part\s*\d+',
    ]
    
    filtered = []
    for topic in topics:
        topic_name = topic.get('topic', '').lower()
        
        # Check if it matches any placeholder pattern
        is_placeholder = any(re.search(pattern, topic_name) for pattern in placeholder_patterns)
        
        if not is_placeholder:
            # Also check if topic name is too short or generic
            if len(topic.get('topic', '')) > 5:
                filtered.append(topic)
            else:
                print(f"[TopicTitles] Filtering out too-short topic: {topic.get('topic')}")
        else:
            print(f"[TopicTitles] Filtering out placeholder topic: {topic.get('topic')}")
    
    return filtered


def _filter_placeholder_chapters(chapters: List[Dict]) -> List[Dict]:
    """
    Filter out placeholder chapter titles.
    
    Andrew's Decision (2026-02-27):
    - 'Topic 5: Deep Dive' never reaches UI
    - Regenerate until 5 real titles or return fewer
    """
    placeholder_patterns = [
        r'^topic\s*\d+',
        r'^section\s*\d+',
        r'^chapter\s*\d+',
        r'^part\s*\d+',
        r'^segment\s*\d+',
        r'deep\s*dive',
        r'intro(?:duction)?',
        r'conclusion',
        r'summary',
        r'outro',
        r'q&a',
        r'questions?\s*and\s*answers?',
        r'next',
        r'continue',
        r'wrap[\s-]?up',
    ]
    
    filtered = []
    for chapter in chapters:
        title = chapter.get('title', '').lower()
        
        # Check if it matches any placeholder pattern
        is_placeholder = any(re.search(pattern, title) for pattern in placeholder_patterns)
        
        if not is_placeholder:
            # Also check for very short or generic titles
            original_title = chapter.get('title', '')
            if len(original_title) > 3:
                filtered.append(chapter)
            else:
                print(f"[TopicTitles] Filtering out short chapter: {original_title}")
        else:
            print(f"[TopicTitles] Filtering out placeholder chapter: {chapter.get('title')}")
    
    return filtered


def generate_initial_titles(transcript: str, config: Dict[str, Any]) -> List[str]:
    """
    NEW: Generate titles based on TOPICS, not random guessing.
    
    Flow:
    1. Generate chapters/timestamps (granular, ~8-10 chapters)
    2. Extract 3-5 main topics by grouping chapters
    3. Generate 1 title per topic using Andrew's framework
    4. Return 5 topic-based titles
    """
    print("[TopicTitles] Starting topic-based title generation...")
    
    # Get podcast context
    podcast_section = config.get('podcast', {})
    podcast_name = podcast_section.get('name', 'The Podcast')
    host_name = podcast_section.get('host', 'Host')
    target_audience = podcast_section.get('targetAudience', 'audience')
    
    # Step 1: Generate chapters/timestamps
    print("[TopicTitles] Step 1: Generating chapters/timestamps...")
    chapters = _generate_timestamps_simple(transcript, config)
    
    if not chapters:
        print("[TopicTitles] Failed to generate chapters, falling back to direct title generation")
        return _generate_titles_direct(transcript, config)
    
    print(f"[TopicTitles] Generated {len(chapters)} chapters:")
    for c in chapters[:5]:
        print(f"  - {c['timestamp']}: {c['title'][:50]}")
    
    # Step 2: Extract topics from chapters
    print("[TopicTitles] Step 2: Extracting 3-5 main topics...")
    topics = extract_topics_from_chapters(chapters, transcript, config)
    
    if not topics:
        print("[TopicTitles] Failed to extract topics, falling back to direct title generation")
        return _generate_titles_direct(transcript, config)
    
    print(f"[TopicTitles] Extracted {len(topics)} topics:")
    for t in topics:
        print(f"  - {t.get('topic', 'Unknown')}: {len(t.get('chapters', []))} chapters")
    
    # Step 3: Generate 1 title per topic
    print("[TopicTitles] Step 3: Generating 1 title per topic...")
    titles = []
    
    for i, topic_obj in enumerate(topics):
        topic = topic_obj.get('topic', '')
        duration = topic_obj.get('duration_minutes', 0)
        
        if not topic:
            continue
        
        # Generate title for this specific topic
        prompt = f"""Using The Ultimate YouTube Title Writing Framework, create 1 compelling title for this podcast episode.

Episode Topic: {topic}
Duration: ~{duration} minutes
Target Audience: {target_audience}

CRITICAL: The title MUST relate specifically to: {topic}
The title should mimic successful YouTube titles about this topic.

Generate exactly 1 title that:
- Relates to: {topic}
- Mimics successful YouTube titles in this niche
- Is enticing and clickable
- Under 60 characters
- NEVER include ANY person's name (no Gary Vee, no Joe Rogan, no experts, no guests, etc.) Focus on topics and concepts, not people.
- Avoid: "Digital Success", "Mastering", "Revenue Operations" (corporate jargon)

Return ONLY the title text, nothing else."""

        result, model_used = _call_ai(
            prompt,
            "You are a YouTube title expert.",
            max_tokens=100,
            prefer_quality=True
        )
        
        if result:
            title = result.strip().strip('"\'')
            # Clean up the title
            title = re.sub(r'^["\']+|["\']+$', '', title)
            if len(title) >= 15 and len(title) <= 70:
                titles.append(title)
                print(f"[TopicTitles]   Topic {i+1}: {title[:50]}")
        
        if len(titles) >= 5:
            break
    
    # If we don't have 5 titles, fill in with direct generation
    if len(titles) < 5:
        print(f"[TopicTitles] Only got {len(titles)} topic-based titles, filling in...")
        remaining = 5 - len(titles)
        additional = _generate_titles_direct(transcript, config)
        for title in additional:
            if title not in titles:
                titles.append(title)
                if len(titles) >= 5:
                    break
    
    # Filter out banned phrases (including "unlock" - case insensitive)
    BANNED_PHRASES = ['unlock', 'digital success', 'revenue operations', 'mastering']
    filtered_titles = []
    for title in titles:
        title_lower = title.lower()
        if not any(phrase in title_lower for phrase in BANNED_PHRASES):
            filtered_titles.append(title)
    
    # ISSUE 3 FIX: Never return placeholder titles
    # If we don't have 5 titles after filtering, try to regenerate more
    max_attempts = 3
    attempt = 0
    
    while len(filtered_titles) < 5 and attempt < max_attempts:
        attempt += 1
        print(f"[TopicTitles] Only have {len(filtered_titles)} titles, regenerating (attempt {attempt})...")
        
        # Generate more titles
        additional = _generate_titles_direct(transcript, config)
        
        for title in additional:
            title_lower = title.lower()
            # Skip banned phrases and placeholders
            if any(phrase in title_lower for phrase in BANNED_PHRASES):
                continue
            if 'topic' in title_lower and 'deep dive' in title_lower:
                continue
            
            if title not in filtered_titles:
                filtered_titles.append(title)
                
            if len(filtered_titles) >= 5:
                break
    
    # If still don't have 5, just return what we have (don't add placeholders)
    if len(filtered_titles) < 5:
        print(f"[TopicTitles] WARNING: Only able to generate {len(filtered_titles)} valid titles (no placeholders)")
    
    print(f"[TopicTitles] Final: {len(filtered_titles)} titles (no placeholders)")
    return filtered_titles[:5]


def _generate_titles_direct(transcript: str, config: Dict[str, Any]) -> List[str]:
    """Fallback: Generate titles directly from transcript (old method)."""
    
    # Get podcast context
    podcast_section = config.get('podcast', {})
    podcast_name = podcast_section.get('name', 'The Podcast')
    host_name = podcast_section.get('host', 'Host')
    target_audience = podcast_section.get('targetAudience', 'audience')
    
    # Get transcript sample
    transcript_sample = transcript[:15000] if len(transcript) > 15000 else transcript
    
    # ANDREW'S EXACT PROMPT from youtube-prompts.txt
    prompt = f"""You are the Lead of my YouTube Department. Your mission is to optimize every podcast episode we publish on YouTube to achieve maximum success, including views, engagement (likes, comments), and watch time.

Podcast & Audience Details:
Podcast Name: {podcast_name}
Host: {host_name}
Target Audience: {target_audience}

Here is today's episode transcript:
{transcript_sample}

Help with Titles:

Using The Ultimate YouTube Title Writing Framework, please analyze this podcast episode and tell me the main themes and topics discussed. With those themes and topics in mind, please list out 5 youtube titles that are likely to be successful on youtube amongst our target audience and mimic successful titles on youtube that have similar topics and themes. 

Please make the title very enticing that makes viewers want to click. Make it interesting and intriguing to where they feel like they need to click on the video. You can make it informative, entertaining, negative and or a little click baity as well. 

Remember, it is a MUST that the titles mimic successful titles you find doing research. The title should still remain true to our episode's content after copying and mimicking other titles on youtube. 

Please do not use phrases such as, "game changing", "Peak Performance" "Unlock" nor "next level"

IMPORTANT: NEVER include ANY person's name in titles (no Gary Vee, no Joe Rogan, no experts, no guests, etc.) Focus on topics and concepts, not people.

Please come up with 5 ideas and return them as a JSON array of strings:
["Title 1", "Title 2", "Title 3", "Title 4", "Title 5"]"""
    
    result, model_used = _call_ai(
        prompt,
        "You are the Lead of my YouTube Department.",
        max_tokens=1000,
        prefer_quality=True
    )
    
    if not result:
        print("[PartnerWorkflow] AI generation failed, returning defaults")
        return ["Title Option 1", "Title Option 2", "Title Option 3", "Title Option 4", "Title Option 5"]
    
    # Parse titles from response
    try:
        import re
        # Try to find JSON array
        json_match = re.search(r'\[[\s\S]*\]', result)
        if json_match:
            titles = json.loads(json_match.group())
            if isinstance(titles, list) and len(titles) >= 5:
                return titles[:5]
    except:
        pass
    
    # Fallback: extract quoted strings
    import re
    titles = re.findall(r'"([^"]+)"', result)
    if len(titles) >= 5:
        titles = titles[:5]
    
    # Filter out banned phrases (including "unlock" - case insensitive)
    BANNED_PHRASES = ['unlock', 'digital success', 'revenue operations', 'mastering']
    filtered_titles = []
    for title in titles:
        title_lower = title.lower()
        if not any(phrase in title_lower for phrase in BANNED_PHRASES):
            filtered_titles.append(title)
    
    # If all titles were filtered out, return defaults
    if not filtered_titles:
        print("[PartnerWorkflow] All titles filtered out (banned phrases), returning defaults")
        return ["Generated Title 1", "Generated Title 2", "Generated Title 3", "Generated Title 4", "Generated Title 5"]
    
    return filtered_titles


def validate_title_on_youtube(title: str, config: Dict[str, Any]) -> Dict[str, Any]:
    """
    Search YouTube for title, return TOP 3 matches with relevance scoring.
    
    Andrew's Decision (2026-02-27):
    - Return TOP 3 candidate matches with views+url+recency
    - View tiers: <1k = warn; 10k=win; 100k+=amazing
    - Recency: prefer <=24 months but allow older; include age in display
    - Add relevance scoring vs generated title (keyword overlap)
    - Soft preference for 5-30 min videos (exclude Shorts)
    - Apply min relevance threshold; if none meet, mark as 'Weak match' and still return best 3
    
    Args:
        title: Title to search for
        config: Podcast config dictionary
    
    Returns:
        Dict with: {
            'matches': [
                {
                    'title': str,
                    'views': int,
                    'url': str,
                    'recency_months': int,
                    'age_label': str,
                    'view_tier': str,
                    'relevance_score': float,
                    'duration_minutes': int,
                    'is_short': bool
                },
                ... (up to 3)
            ],
            'quality_label': str  # 'Strong match', 'Weak match', or 'No match'
        }
    """
    print(f"[PartnerWorkflow] Validating title on YouTube: {title[:50]}...")
    
    try:
        # Search YouTube
        from youtube_search import search_titles
        results = search_titles(title, max_results=15)
        
        if not results:
            return {
                'matches': [],
                'quality_label': 'No match',
                'searched_query': title,
                'filtered_count': 0,
                'excluded_short_count': 0,
                'error': 'No YouTube videos found for this title'
            }
        
        # REMOVED: off_topic_keywords - no hard-coded blacklists
        # Per Andrew's directive: everything must come from the TRANSCRIPT
        # Only allowed presets: duration > 3 min, views > 1k
        filtered_results = results  # No keyword-based filtering
        
        # Calculate relevance score for each result (keyword overlap with generated title)
        import re

        def stem_word(word: str) -> str:
            """Simple stemming - remove common suffixes."""
            if word.endswith('ing'):
                return word[:-3]
            if word.endswith('s') and len(word) > 3:
                return word[:-1]
            if word.endswith('ed') and len(word) > 3:
                return word[:-2]
            return word

        def tokenize_title(title: str) -> set:
            """Tokenize and stem title into keywords, stripping punctuation."""
            # Remove punctuation
            clean = re.sub(r'[^\w\s]', ' ', title.lower())
            # Split into words
            words = clean.split()
            # Common stop words
            stop_words = {'the', 'a', 'an', 'is', 'are', 'was', 'were', 'be', 'been', 'being',
                          'have', 'has', 'had', 'do', 'does', 'did', 'will', 'would', 'could',
                          'should', 'may', 'might', 'must', 'shall', 'can', 'to', 'of', 'in',
                          'for', 'on', 'with', 'at', 'by', 'from', 'as', 'into', 'through',
                          'during', 'before', 'after', 'above', 'below', 'between', 'under',
                          'again', 'further', 'then', 'once', 'here', 'there', 'when', 'where',
                          'why', 'how', 'all', 'each', 'few', 'more', 'most', 'other', 'some',
                          'such', 'no', 'nor', 'not', 'only', 'own', 'same', 'so', 'than',
                          'too', 'very', 'just', 'and', 'but', 'if', 'or', 'because', 'until',
                          'while', 'about', 'against', 'up', 'down', 'out', 'off', 'over',
                          'my', 'your', 'our', 'their', 'this', 'that', 'these', 'those'}
            # Stem each word
            return {stem_word(w) for w in words if w not in stop_words}

        # Use proper tokenization with stemming
        title_keywords = tokenize_title(title)
        original_title_words = set(re.findall(r"[a-z]+", title.lower()))

        # REMOVED: must_terms logic - no hard-coded filters per Andrew's directive
        

        
        # Calculate age in months from published date
        today = datetime.now()
        
        def calculate_recency_months(published_at: str) -> int:
            """Calculate months since publication (approximate)."""
            if not published_at or len(published_at) != 8:
                return 999  # Unknown age
            try:
                pub_date = datetime.strptime(published_at, '%Y%m%d')
                months = (today.year - pub_date.year) * 12 + (today.month - pub_date.month)
                return max(0, months)
            except:
                return 999
        
        # Calculate duration in minutes (if available)
        # FIXED: YouTube search now returns duration_minutes directly and is_short field
        def get_duration_minutes(result: Dict) -> float:
            """Get video duration in minutes from result."""
            # First check if duration_minutes is already computed (from youtube_search)
            if 'duration_minutes' in result:
                return result.get('duration_minutes', 0)
            
            # Fallback: try to parse from duration field (may be seconds or string)
            duration = result.get('duration', 0)
            
            # If it's already a number, it's likely seconds
            if isinstance(duration, (int, float)):
                return round(duration / 60, 1)
            
            # If it's a string in MM:SS or HH:MM:SS format
            if isinstance(duration, str) and duration:
                try:
                    parts = duration.split(':')
                    if len(parts) == 2:
                        return int(parts[0]) + int(parts[1]) / 60
                    elif len(parts) == 3:
                        return int(parts[0]) * 60 + int(parts[1]) + int(parts[2]) / 60
                except:
                    pass
            return 0
        
        # Process each result
        scored_results = []
        
        for result in filtered_results:
            result_title = result.get('title', '').lower()
            result_desc = result.get('description', '').lower()
            combined_text = result_title + ' ' + result_desc

            # REMOVED: must_terms check - no hard-coded filters

            # Use tokenized and stemmed keywords for relevance
            result_keywords = tokenize_title(result_title)
            
            # Relevance: keyword overlap (now with stemming!)
            overlap = title_keywords & result_keywords
            # Better relevance: combine exact keyword overlap + fuzzy title similarity
            exact_overlap = len(overlap) / max(len(title_keywords), 1) if title_keywords else 0
            fuzzy_similarity = difflib.SequenceMatcher(None, title.lower(), result_title).ratio()
            # Weight: 60% keyword overlap, 40% overall title similarity
            relevance_score = exact_overlap * 0.6 + fuzzy_similarity * 0.4
            
            # MINIMUM RELEVANCE FILTER - exclude garbage matches
            MIN_RELEVANCE_THRESHOLD = 0.30  # At least 30% match required
            if relevance_score < MIN_RELEVANCE_THRESHOLD:
                print(f"[YouTube] Excluding low-relevance video: \"{result.get('title', '')[:40]}...\" (relevance: {relevance_score:.0%})")
                continue
            
            
            # REMOVED: is_irrelevant_content call - no hard-coded blacklists
            # Recency calculation
            recency_months = calculate_recency_months(result.get('published_at', ''))
            
            # FIXED: Duration check - use fields from youtube_search or compute
            # is_short: YouTube Shorts are < 60 seconds (official threshold)
            duration_minutes = get_duration_minutes(result)
            is_short = result.get('is_short', duration_minutes < 1)  # Use provided or compute
            
            # FIXED: Andrew's requirements
            # - Exclude shorts reliably (is_short true OR duration < 60-90s = exclude)
            # - Soft preference: >= 5 minutes (NOT 5-30). Do not penalize long videos.
            # - Small bonus for >= 5 min videos
            
            # Exclude shorts and very short videos (known duration < 90 seconds)
            # - Hard exclude Shorts (is_short flag or < 60 seconds)
            # - Hard exclude < 90 seconds (only if duration is KNOWN)
            # - Allow unknown duration (might be valid, just not in search metadata)
            # Exclude actual YouTube Shorts (<3 min) but allow short-form content (3-10 min)
            should_exclude = is_short or (duration_minutes > 0 and duration_minutes < 3)
            
            if should_exclude:
                print(f"[YouTube] Excluding short/unknown video: \"{result.get('title', '')[:40]}...\" ({duration_minutes:.1f} min, is_short={is_short})")
                continue
            
            # CRITICAL: Get views and filter out low-view videos (< 1k views)
            # These don't prove the pattern works - we need meaningful proof
            views = result.get('view_count', 0)
            if views < 1000:
                print(f"[YouTube] Excluding low-view video: \"{result.get('title', '')[:40]}...\" ({views:,} views - below 1k threshold)")
                continue
            
            # Soft bonus for >= 5 minutes (no upper limit)
            duration_bonus = 0
            if duration_minutes >= 5:
                duration_bonus = 0.10  # 10% bonus for preferred duration (>=5 min)
            
            # Recency bonus: prefer <=24 months
            recency_bonus = 0
            if recency_months <= 24:
                recency_bonus = 0.1  # 10% bonus for recent videos
            
            # Calculate combined score - now with views weighted more heavily
            # Weight: views 50%, relevance 30%, recency 10%, duration 10%
            view_score = min(views / 100000, 1)  # Normalize: 100k = max
            
            # Views are now weighted 50% (proof the pattern works), relevance 30%, recency 10%, duration 10%
            combined_score = (
                view_score * 0.50 +
                relevance_score * 0.30 +
                (1 - min(recency_months / 60, 1)) * 0.10 +  # Recent = higher score
                duration_bonus
            )
            
            # Age label for display
            if recency_months <= 6:
                age_label = "Recent"
            elif recency_months <= 12:
                age_label = "6-12 months"
            elif recency_months <= 24:
                age_label = "1-2 years"
            elif recency_months <= 60:
                age_label = "2-5 years"
            else:
                age_label = "5+ years"
            
            # View tier for display
            if views >= 10000:
                view_tier = "great"       # 10k+ = great
            elif views >= 5000:
                view_tier = "good"        # 5k-10k = good
            elif views >= 1000:
                view_tier = "acceptable"  # 1k-5k = acceptable
            elif views >= 500:
                view_tier = "warning"     # 500-999 = yellow warning
            else:
                view_tier = "danger"      # <500 = red warning
            
            scored_results.append({
                'title': result.get('title', ''),
                'views': views,
                'url': f"https://www.youtube.com/watch?v={result.get('video_id', '')}",
                'recency_months': recency_months,
                'age_label': age_label,
                'view_tier': view_tier,
                'relevance_score': round(relevance_score, 2),
                'duration_minutes': round(duration_minutes, 1) if duration_minutes else 0,
                'is_short': is_short,
                'combined_score': combined_score,
                'channel': result.get('channel_title', ''),
                'published_at': result.get('published_at', '')
            })
        
        # At this point, Shorts/unknown durations should already be excluded.
        # If we have no scored results, return 'No match' instead of surfacing Shorts garbage.
        if not scored_results:
            return {
                'matches': [],
                'quality_label': 'No match',
                'best_relevance': 0,
                'searched_query': title,
                'filtered_count': len(filtered_results),
                'excluded_short_count': len(results) - len(filtered_results),
                'error': 'No non-Short, known-duration videos found for this title.'
            }

        # Sort by views (highest first) - the TOP 3 should be the best proof, not random results
        scored_results.sort(key=lambda x: x['combined_score'], reverse=True)
        top_matches = scored_results[:3]
        
        # Determine quality label based on BOTH views AND relevance
        best_views = top_matches[0]['views'] if top_matches else 0
        best_relevance = max(m['relevance_score'] for m in top_matches) if top_matches else 0

        # Strong match requires BOTH good views AND meaningful relevance
        if best_views >= 10000 and best_relevance >= 0.25:
            quality_label = 'Strong match'
        elif best_views >= 1000 and best_relevance >= 0.15:
            quality_label = 'Weak match'
        else:
            quality_label = 'No match'
        
        # If quality_label is 'No match', return empty matches
        if quality_label == 'No match':
            return {
                'matches': [],
                'quality_label': quality_label,
                'best_views': best_views,
                'searched_query': title,
                'filtered_count': len(filtered_results),
                'excluded_short_count': len(results) - len(filtered_results),
                'error': f"No match - best view count ({best_views:,}) below 1k threshold. Try a more specific title."
            }
        
        # Clean up response - remove internal scoring fields
        for match in top_matches:
            del match['combined_score']
        
        return {
            'matches': top_matches,
            'quality_label': quality_label,
            'best_views': best_views,
            'searched_query': title,
            'filtered_count': len(filtered_results),
            'excluded_short_count': len(results) - len(filtered_results)
        }
        
    except Exception as e:
        print(f"[PartnerWorkflow] Error validating title: {e}")
        import traceback
        traceback.print_exc()
        return {
            'matches': [],
            'quality_label': 'Error',
            'searched_query': title,
            'filtered_count': 0,
            'excluded_short_count': 0,
            'error': str(e)
        }


def _analyze_story_subject(transcript: str) -> str:
    """
    Analyze transcript to determine whose story this is.
    
    Returns: 'host', 'client', or 'general'
    """
    if not transcript:
        return 'general'
    
    # Take first 3000 chars for analysis
    sample = transcript[:3000].lower()
    
    # Client/guest indicators
    client_signals = [
        'my client', 'my student', 'my customer', 'worked with', 
        'i helped', 'coached someone', 'this person', 'this guy',
        'this woman', 'case study', 'their story', 'they went from'
    ]
    
    # Host personal story indicators  
    host_signals = [
        'i went from', 'i made', 'i built', 'i grew', 'i started',
        'my journey', 'when i was', 'i struggled', 'i learned',
        'my experience', 'i discovered', 'my story'
    ]
    
    client_count = sum(1 for signal in client_signals if signal in sample)
    host_count = sum(1 for signal in host_signals if signal in sample)
    
    if client_count > host_count and client_count >= 2:
        return 'client'
    elif host_count > client_count and host_count >= 2:
        return 'host'
    else:
        return 'general'


def generate_title_copies(youtube_title: str, our_title: str, config: Dict[str, Any], transcript: str = "") -> List[Dict[str, Any]]:
    """
    Generate 3 copy options and verify each on YouTube.
    
    Args:
        youtube_title: The successful YouTube title to copy
        our_title: Our original AI-generated title
        config: Podcast config dictionary
        transcript: Episode transcript for context analysis
    
    Returns:
        List of dicts: [
            {'copy': '...', 'verification': 'Original ranks #2'},
            {'copy': '...', 'verification': 'Original ranks #8'},
            {'copy': '...', 'verification': 'Original not found'}
        ]
    """
    print(f"[PartnerWorkflow] Generating 3 copy options from: {youtube_title[:40]}...")
    
    # Analyze whose story this is
    story_subject = _analyze_story_subject(transcript)
    print(f"[PartnerWorkflow] Detected story subject: {story_subject}")
    
    podcast_section = config.get('podcast', {})
    podcast_name = podcast_section.get('name', 'The Podcast')
    
    # Pronoun adjustment instruction based on story subject
    pronoun_instruction = ""
    if story_subject == 'client':
        pronoun_instruction = """
PRONOUN ADJUSTMENT (CRITICAL):
This episode is about a CLIENT/STUDENT story, NOT the host's personal story.
- Change "I" → "My Client" or "How [Someone]" 
- Change "My" → "Their" when referring to the client's experience
- Example: "How I Went From BROKE..." → "How My Client Went From BROKE..."
"""
    elif story_subject == 'host':
        pronoun_instruction = """
PRONOUN ADJUSTMENT:
This episode is about the HOST's personal story.
- Keep "I", "My" when it refers to the host's experience
- Example: "How I Built..." can stay "How I Built..." if it's the host's story
"""
    else:
        pronoun_instruction = """
PRONOUN ADJUSTMENT:
This episode is general advice/teaching.
- Change "I" → "You" or make it general
- Example: "How I Made..." → "How to Make..." or "How You Can Make..."
"""
    
    # Get niche keywords from config for relevant examples
    niche_profile = podcast_section.get('nicheProfile', {})
    niche_keywords = niche_profile.get('keywords', [])
    niche_term = niche_keywords[0] if niche_keywords else 'fitness'
    
    # Generate 3 copy options - CONFIG-DRIVEN, not hardcoded
    prompt = f"""LITERALLY COPY this successful YouTube title with MINIMAL changes.

SUCCESSFUL YOUTUBE TITLE (to copy):
"{youtube_title}"

OUR EPISODE TOPIC:
"{our_title}"

PODCAST: {podcast_name}
NICHE: {niche_term}

IMPORTANT: The title "{youtube_title}" is very successful on YouTube. Can we copy this video's title and style, but stay true to our video's content? Keep the STRUCTURE of the successful title but swap minimal words to reflect our episode topic.

{pronoun_instruction}

CRITICAL RULES FOR COPYING:
1. PRESERVE ALL emotional/power words (capitalized words, power words, etc.)
2. PRESERVE the structure and punctuation
3. ONLY change episode-specific terms to match our topic
4. Stay true to the NICHE: {niche_term} (not generic "podcast" or "coaching" unless that IS our niche)

Create exactly 3 copies:

COPY 1 (MINIMAL - barely change anything):
- Change ONLY 1-2 words maximum
- Keep emotional impact identical
- Adjust pronouns correctly

COPY 2 (MEDIUM - small adaptation):
- Change 2-3 words
- Make it more specific to {niche_term}
- Adjust pronouns correctly

COPY 3 (MORE - add specificity):
- Add niche-specific terms related to {niche_term}
- But still keep original emotional words
- NO year references (do NOT use "in 2024", "in 2025", "in 2026")

Return as JSON array:
[
  {{"type": "minimal", "copy": "The copied title"}},
  {{"type": "medium", "copy": "The copied title"}},
  {{"type": "more", "copy": "The copied title"}}
]

IMPORTANT: Each title must be under 60 characters and NEVER include ANY person's name (no experts, no guests, no content creators, etc.) Focus on topics and concepts, not people."""

    result, model_used = _call_ai(
        prompt,
        "You are a YouTube title expert. Create copy variations.",
        max_tokens=600,
        prefer_quality=True
    )
    
    if not result:
        return [
            {'copy': our_title, 'verification': 'Generation failed'}
        ]
    
    # Parse copy options
    import re
    copies = []  # Initialize before try/except
    try:
        json_match = re.search(r'\[[\s\S]*\]', result)
        if json_match:
            copies = json.loads(json_match.group())
    except:
        copies = []
    
    if not copies:
        # Fallback
        copies = [
            {'type': 'minimal', 'copy': our_title},
            {'type': 'medium', 'copy': our_title},
            {'type': 'more', 'copy': our_title}
        ]
    
    # Filter out year references (2024, 2025, 2026) from copy titles
    import re
    filtered_copies = []
    for copy_obj in copies:
        copy_title = copy_obj.get('copy', '')
        if copy_title:
            # Remove year references like "in 2024", "2025", "in 2026"
            copy_title = re.sub(r'\b(in\s+)?(2024|2025|2026)\b', '', copy_title, flags=re.IGNORECASE)
            # Clean up any double spaces left behind
            copy_title = re.sub(r'\s+', ' ', copy_title).strip()
            copy_obj['copy'] = copy_title
            filtered_copies.append(copy_obj)
    
    copies = filtered_copies
    
        # Verify EACH copy by searching YouTube (PARALLEL for speed)
    import concurrent.futures

    def verify_one_copy(copy_obj):
        """Verify a single copy against YouTube."""
        copy_title = copy_obj.get('copy', '')
        if not copy_title:
            return None
        
        # Search YouTube with this copy (use fewer results for speed)
        try:
            from youtube_search import search_titles
            results = search_titles(copy_title, max_results=5)
            
            if results:
                # Check if original YouTube title appears in results
                position = None
                for i, r in enumerate(results, 1):
                    r_title = r.get('title', '').lower()
                    yt_title = youtube_title.lower()
                    # Check similarity
                    if yt_title in r_title or r_title in yt_title:
                        position = i
                        break
                
                if position and position <= 3:
                    verification = f"Original ranks #{position} ✅ WINNER"
                elif position and position <= 5:
                    verification = f"Original ranks #{position} ✅ Good"
                elif position:
                    verification = f"Original ranks #{position} (Page 1)"
                else:
                    verification = "Original not found in top results"
            else:
                verification = "No YouTube results found"
                
        except Exception as e:
            verification = f"Verification error: {str(e)[:30]}"
        
        return {
            'copy': copy_title,
            'type': copy_obj.get('type', 'unknown'),
            'verification': verification
        }

    # Run verifications in parallel (3x faster!)
    verified_copies = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=3) as executor:
        futures = [executor.submit(verify_one_copy, c) for c in copies if c.get('copy')]
        for future in futures:
            result = future.result()
            if result:
                verified_copies.append(result)

    return verified_copies
