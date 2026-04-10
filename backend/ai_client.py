"""
MiniMax AI Client for Title Finder
==================================
Simple, fast, reliable. MiniMax M2.5 only.
No OAuth. No Kimi. No Gemini fallback. Just works.
"""

import os
import re
import json
from typing import Dict, List, Any, Optional, Tuple

# MiniMax Configuration
MINIMAX_API_KEY = os.getenv('MINIMAX_API_KEY', 'sk-cp-Q4U1CLcZeChyuy11a6QWCdIY0bnh-ij-TZU1m4fXpr-5RIggtHFPPkp3JCdzJjdRTDTHDvj5wJrp7iOkJ5U0vDV1mJ8V4sIzh1e2qOPfjTJGa8eGjW_4V30')
MINIMAX_BASE_URL = "https://api.minimax.io/v1"
MINIMAX_MODEL = "MiniMax-M2.5"

# Banned phrases that scream "AI generated"
DEFAULT_BANNED_PHRASES = [
    'unlock',
    'peak performance', 
    'next level',
    'game changing',
    'game-changing',
]

# Common first names to avoid in titles
COMMON_FIRST_NAMES = [
    'jeff', 'mike', 'robert', 'john', 'david', 'chris', 'mark', 'steve', 'james', 'matt',
    'andrew', 'brian', 'tom', 'dan', 'ryan', 'kevin', 'jason', 'will', 'sam', 'tony',
    'nick', 'anthony', 'ben', 'adam', 'paul', 'eric', 'greg', 'joe', 'alex', 'josh'
]

# Stop words for keyword extraction
STOP_WORDS = {
    'the', 'a', 'an', 'and', 'or', 'but', 'to', 'of', 'in', 'for', 'on', 'with', 'at', 'by',
    'from', 'as', 'is', 'are', 'was', 'were', 'be', 'been', 'being', 'this', 'that', 'these',
    'those', 'your', 'you', 'how', 'why', 'what', 'when', 'where', 'who', 'it', 'its'
}


def _extract_json_from_response(response: str) -> Optional[str]:
    """Extract JSON from response that may contain <think> tags or extra text."""
    if not response:
        return None
    
    # Find first { or [
    first_brace = response.find('{')
    first_bracket = response.find('[')
    
    start = -1
    if first_brace == -1:
        start = first_bracket
    elif first_bracket == -1:
        start = first_brace
    else:
        start = min(first_brace, first_bracket)
    
    if start == -1:
        return None
    
    # Find last } or ]
    last_brace = response.rfind('}')
    last_bracket = response.rfind(']')
    
    end = -1
    if start == first_brace:
        end = last_brace
    else:
        end = last_bracket
    
    if end == -1 or end < start:
        return None
    
    return response[start:end+1]


def call_ai(prompt: str, system_prompt: str = None, max_tokens: int = 2000, require_json: bool = False) -> Tuple[Optional[str], str]:
    """Call MiniMax M2.5. That's it. No fallbacks. No OAuth. Just works."""
    
    from openai import OpenAI
    
    # Build system prompt
    effective_system = system_prompt or "You are a helpful assistant."
    if require_json:
        effective_system += "\n\nIMPORTANT: Return ONLY valid JSON. No explanations, no markdown, no thinking tags. Start with { or [ and end with } or ]."
    
    try:
        client = OpenAI(api_key=MINIMAX_API_KEY, base_url=MINIMAX_BASE_URL)
        
        messages = [
            {"role": "system", "content": effective_system},
            {"role": "user", "content": prompt}
        ]
        
        response = client.chat.completions.create(
            model=MINIMAX_MODEL,
            messages=messages,
            temperature=0.7,
            max_tokens=max_tokens
        )
        
        raw_response = response.choices[0].message.content.strip()
        
        # Extract JSON if required
        if require_json:
            json_text = _extract_json_from_response(raw_response)
            if json_text:
                return json_text, f"minimax/{MINIMAX_MODEL}"
            # Return raw anyway - caller handles
            return raw_response, f"minimax/{MINIMAX_MODEL}"
        
        return raw_response, f"minimax/{MINIMAX_MODEL}"
        
    except Exception as e:
        print(f"[AI-Client] MiniMax failed: {e}")
        return None, "none"


def validate_title(
    title: str,
    banned_phrases: List[str] = None,
    allow_first_names: bool = False
) -> Tuple[bool, str]:
    """Validate title - banned phrases and first names only."""
    
    if not title or not title.strip():
        return False, "Empty title"
    
    title_lower = title.lower()
    
    # 1. Check banned phrases
    phrases_to_check = banned_phrases or DEFAULT_BANNED_PHRASES
    for phrase in phrases_to_check:
        if phrase.lower() in title_lower:
            return False, f"Banned phrase: '{phrase}'"
    
    # 2. Check first names
    if not allow_first_names:
        for name in COMMON_FIRST_NAMES:
            if re.search(r'\b' + re.escape(name) + r'\b', title_lower):
                return False, f"First name: '{name}'"
    
    return True, "Valid"


def extract_keywords_from_topics(topics: List[str]) -> List[str]:
    """Extract keywords from topic strings."""
    keywords = set()
    
    for topic in topics:
        words = re.split(r"[^a-zA-Z0-9]+", topic)
        for w in words:
            wl = w.lower().strip()
            if len(wl) >= 4 and wl not in STOP_WORDS:
                keywords.add(wl)
    
    return list(keywords)


def generate_niche_keywords(podcast_name: str, niche_data: str) -> Optional[List[str]]:
    """Generate precise YouTube search keywords from niche questionnaire answers.
    
    Args:
        podcast_name: Name of the podcast
        niche_data: Q6 section answers (similar channels, keywords, search terms)
        
    Returns:
        List of 5-7 precise YouTube search keywords
    """
    
    system_prompt = """You are a YouTube keyword research expert. Your task is to generate precise, high-performing YouTube search keywords based on podcast niche information.

Return ONLY a JSON array of 5-7 keywords/phrase strings. Each keyword should be:
- What someone would actually search on YouTube to find content like this podcast
- Specific enough to target the exact audience
- A mix of short keywords (1-2 words) and longer search phrases (3-5 words)

Examples of good keywords:
- For a keto bodybuilding podcast: ["ketogenic bodybuilding", "carnivore diet fitness", "natural bodybuilding science", "evidence based keto", "protein sparing fasting"]
- For a business podcast: ["startup growth secrets", "SaaS marketing strategy", "founder mindset"]

Do NOT return:
- Generic keywords like "podcast" or "interview"
- Keywords unrelated to the niche
- More than 7 keywords
- Anything except a JSON array"""

    prompt = f"""Generate 5-7 YouTube search keywords for the podcast "{podcast_name}".

Niche Discovery Questionnaire Answers:
{niche_data}

Analyze these answers and generate keywords that:
1. Reflect the specific topics/keywords mentioned
2. Consider the similar channels for context
3. Incorporate the search terms people would use
4. Capture the unique angle/differentiation

Return ONLY a JSON array of strings, like:
["keyword one", "keyword two", "keyword three"]

Do not include any other text or explanation."""

    try:
        keyword_json, model_used = call_ai(
            prompt=prompt,
            system_prompt=system_prompt,
            max_tokens=500,
            require_json=True
        )
        
        if not keyword_json:
            print("[AI-Client] AI returned empty keywords")
            return None
        
        # Parse JSON
        import json
        keywords = json.loads(keyword_json)
        
        # Ensure it's a list
        if isinstance(keywords, list):
            # Filter out non-string items and limit to 7
            keywords = [k for k in keywords if isinstance(k, str)][:7]
            print(f"[AI-Client] Generated {len(keywords)} niche keywords using {model_used}")
            return keywords
        else:
            print(f"[AI-Client] Unexpected keyword format: {type(keywords)}")
            return None
            
    except json.JSONDecodeError as e:
        print(f"[AI-Client] JSON parse error for keywords: {e}")
        return None
    except Exception as e:
        print(f"[AI-Client] Error generating niche keywords: {e}")
        return None


# Default model constant
DEFAULT_MODEL = "minimax/MiniMax-M2.5"


# ============================================================================
# TARGET AUDIENCE PROFILE GENERATOR
# ============================================================================

TARGET_AUDIENCE_SYSTEM_PROMPT = """You are an expert podcast strategist who creates clear, actionable target audience profiles.

Your job is to transform questionnaire answers into a detailed profile that helps podcasters understand exactly who their ideal listener is.

The profile must be:
1. WRITTEN FOR THE PODCASTER - This is a reference guide about their audience, not content for the audience
2. SPECIFIC - No generic statements. Use concrete details from the questionnaire.
3. CLEAN - Use headers with dashes, not asterisks or markdown
4. ACCURATE - Only include places/platforms/channels that the user explicitly mentions in their answers

Format your response EXACTLY like this (use these exact headers):

---

TARGET AUDIENCE PROFILE FOR [PODCAST NAME]

Who is your target listener?
[Write 4-6 paragraphs describing the target listener. Use "Your target listener is..." Write about them like you're explaining them to someone else. Include: demographics (age range, gender breakdown as given), profession/career, daily life, personality/values, biggest frustrations, goals/aspirations]

How do they consume content?
[Describe when and where they listen to podcasts - only include what the user explicitly mentioned]

What podcasts, channels, or creators do they follow?
[List only the specific podcasts, YouTube channels, or creators the user mentioned - do NOT add any that weren't listed]

What social platforms and communities are they in?
[List only the specific platforms/communities the user mentioned - do NOT add any that weren't listed]

What are their biggest frustrations?
[List 3-5 specific pain points - only include what can be inferred from the user's answers]

What does success look like for them?
[Describe what they're trying to achieve - only include what's in their answers]

---

IMPORTANT: 
- Total length: 400-600 words
- Use headers with dashes (like "Who is your target listener?")
- NEVER use asterisks for formatting
- Only include platforms/channels/communities the user explicitly listed in their answers
- If the user didn't mention something, don't include it
- Write in clean paragraphs, not bullet points"""


def generate_target_audience_profile(podcast_name: str, host_names: str, answers: Dict[str, str]) -> Tuple[str, str]:
    """Generate a detailed, actionable target audience profile from questionnaire answers.
    
    Args:
        podcast_name: Name of the podcast
        host_names: Name(s) of the host(s)
        answers: Dictionary with formatted sections (section1, section2, etc.)
        
    Returns:
        Tuple of (profile_text, model_used)
    """
    
    # Map new section structure
    section_names = {
        'section1': 'Ideal Listener',
        'section2': 'Demographics', 
        'section3': 'Values & Mindset',
        'section4': 'Media & Content Habits',
        'section5': 'Podcast Value Proposition',
        'section6': 'Niche Discovery'
    }
    
    # Build the prompt from answers
    prompt = f"""Create a target audience profile for the podcast "{podcast_name}".
"""
    if host_names:
        prompt += f"\nHost(s): {host_names}\n"
    
    prompt += "\n## QUESTIONNAIRE RESPONSES:\n"
    
    for section_key, section_name in section_names.items():
        if section_key in answers and answers[section_key] and answers[section_key] != "No answers provided.":
            prompt += f"\n### {section_name}:\n{answers[section_key]}\n"
    
    prompt += """\n
Based on these questionnaire answers, create a comprehensive target audience profile following the EXACT format specified in your instructions.

Remember:
- Be SPECIFIC using the actual details provided
- Make every statement ACTIONABLE
- Describe them like a REAL PERSON
- 500-700 words total"""

    # Call MiniMax
    profile_text, model_used = call_ai(
        prompt=prompt,
        system_prompt=TARGET_AUDIENCE_SYSTEM_PROMPT,
        max_tokens=2500,
        require_json=False
    )
    
    if not profile_text:
        raise Exception("AI returned empty response when generating profile")
    
    # Strip <think> tags if present (MiniMax sometimes includes reasoning)
    profile_text = re.sub(r'<think>.*?</think>', '', profile_text, flags=re.DOTALL)
    profile_text = profile_text.strip()
    
    return profile_text, model_used
