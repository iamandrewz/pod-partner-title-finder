"""
AI Client Module for Episode Optimizer
Extracted from episode_optimizer_v3.py for maintainability.
"""

import os
import requests
from typing import Optional, Tuple
from dotenv import load_dotenv

# Load MiniMax credentials
load_dotenv('/Users/pursuebot/.openclaw/workspace/secrets/minimax.env')

# Import availability flags from episode_optimizer_v3
try:
    from openai import OpenAI
    OPENAI_AVAILABLE = True
except ImportError:
    OPENAI_AVAILABLE = False

# Global state
_openai_client = None
_openai_model = None
_openai_configured = False
_gemini_available = None
_minimax_api_key = None
_minimax_configured = False


def _ensure_minimax_configured():
    """Lazy-load MiniMax API key."""
    global _minimax_api_key, _minimax_configured
    if _minimax_configured:
        return
    
    _minimax_api_key = os.getenv('MINIMAX_API_KEY')
    if _minimax_api_key:
        print("[Optimizer] MiniMax API configured")
        _minimax_configured = True
    else:
        print("[Optimizer] MiniMax API key not found")


def _call_minimax(prompt: str, system_prompt: str = None, max_tokens: int = 2000) -> Optional[str]:
    """Call MiniMax API (OpenAI-compatible endpoint)."""
    _ensure_minimax_configured()
    
    if not _minimax_api_key:
        return None
    
    try:
        headers = {
            "Authorization": f"Bearer {_minimax_api_key}",
            "Content-Type": "application/json"
        }
        
        messages = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        messages.append({"role": "user", "content": prompt})
        
        payload = {
            "model": "MiniMax-M2.5",
            "messages": messages,
            "temperature": 0.7,
            "max_tokens": max_tokens
        }
        
        # Use OpenAI-compatible endpoint
        response = requests.post(
            "https://api.minimax.io/v1/chat/completions",
            headers=headers,
            json=payload,
            timeout=60
        )
        
        if response.status_code == 200:
            result = response.json()
            text = result.get('choices', [{}])[0].get('message', {}).get('content', '')
            if text:
                # Strip MiniMax thinking tags
                import re
                text = re.sub(r'<think>.*?</think>', '', text, flags=re.DOTALL).strip()
                print("[Optimizer] Successfully generated with MiniMax")
                return text
            else:
                print(f"[Optimizer] MiniMax returned empty content: {result}")
        else:
            print(f"[Optimizer] MiniMax error: {response.status_code} - {response.text[:200]}")
        
        return None
        
    except Exception as e:
        print(f"[Optimizer] MiniMax error: {e}")
        return None


def _check_gemini_available() -> bool:
    """Check if Gemini is available."""
    global _gemini_available
    if _gemini_available is not None:
        return _gemini_available
    
    try:
        import google.generativeai as genai
        api_key = os.getenv('GEMINI_API_KEY')
        if api_key:
            genai.configure(api_key=api_key)
            _gemini_available = True
            print("[EpisodeOptimizerV3] Gemini available")
            return True
        else:
            _gemini_available = False
            print("[EpisodeOptimizerV3] Gemini API key not found")
            return False
    except ImportError:
        _gemini_available = False
        print("[EpisodeOptimizerV3] Gemini not installed")
        return False


def _ensure_openai_configured():
    """Lazy-load and configure OpenAI."""
    global _openai_client, _openai_model, _openai_configured
    if _openai_configured:
        return
    
    OPENAI_API_KEY = os.getenv('OPENAI_API_KEY') or os.environ.get('OPENAI_API_KEY')
    
    if OPENAI_AVAILABLE and OPENAI_API_KEY:
        try:
            _openai_client = OpenAI(api_key=OPENAI_API_KEY)
            _openai_model = "gpt-4o"
            print("[EpisodeOptimizerV3] OpenAI GPT-4o configured")
            _openai_configured = True
        except Exception as e:
            print(f"[EpisodeOptimizerV3] OpenAI config error: {e}")


def _call_gemini(prompt: str, system_prompt: str = None) -> Optional[str]:
    """Call Gemini API with working model names."""
    if not _check_gemini_available():
        return None
    
    # Import exceptions outside try block so they're available in except clauses
    try:
        import google.generativeai as genai
        from google.generativeai.types import HarmCategory, HarmBlockThreshold
    except ImportError:
        print("[Optimizer] Failed to import google.generativeai")
        return None
    
    try:
        from google.api_core.exceptions import APIError, RpcError
    except ImportError:
        # Fallback: use broad Exception for error handling
        APIError = Exception
        RpcError = Exception
    
    try:
        
        # Use verified working models (from list_models)
        # PRIORITY: Gemini 2.5 Pro first for QUALITY
        model_names = [
            'gemini-2.5-pro',         # Latest pro - HIGH QUALITY (Andrew's request)
            'gemini-2.5-flash',      # Fallback: Latest flash
            'gemini-2.0-flash',      # Fallback: Stable 2.0
        ]
        
        gemini_model = None
        for model_name in model_names:
            try:
                gemini_model = genai.GenerativeModel(model_name)
                print(f"[Optimizer] Using Gemini model: {model_name}")
                break
            except Exception as e:
                print(f"[Optimizer] Failed to load {model_name}: {e}")
                continue
        
        if gemini_model is None:
            print("[Optimizer] Could not load any Gemini model")
            return None
        
        safety_settings = {
            HarmCategory.HARM_CATEGORY_HARASSMENT: HarmBlockThreshold.BLOCK_NONE,
            HarmCategory.HARM_CATEGORY_HATE_SPEECH: HarmBlockThreshold.BLOCK_NONE,
            HarmCategory.HARM_CATEGORY_SEXUALLY_EXPLICIT: HarmBlockThreshold.BLOCK_NONE,
            HarmCategory.HARM_CATEGORY_DANGEROUS_CONTENT: HarmBlockThreshold.BLOCK_NONE,
        }
        
        # Build messages
        messages = []
        if system_prompt:
            messages.append({"role": "user", "parts": [f"System: {system_prompt}\n\n{prompt}"]})
        else:
            messages.append({"role": "user", "parts": [prompt]})
        
        response = gemini_model.generate_content(
            messages,
            safety_settings=safety_settings
        )
        
        # Validate response before returning
        if not hasattr(response, 'text') or not response.text:
            print("[Optimizer] Gemini returned empty response")
            return None
        
        # Check if response indicates an error (starts with common error indicators)
        text = response.text.strip()
        error_indicators = ['error', 'internal server error', 'not found', 'unavailable', 'rate limit']
        if any(text.lower().startswith(indicator) for indicator in error_indicators):
            print(f"[Optimizer] Gemini returned error message: {text[:100]}")
            return None
        
        print("[Optimizer] Successfully generated with Gemini")
        return text
        
    except RpcError as e:
        print(f"[Optimizer] Gemini RPC error: {e}")
        return None
    except APIError as e:
        print(f"[Optimizer] Gemini API error: {e}")
        return None
    except Exception as e:
        print(f"[Optimizer] Gemini error: {e}")
        return None


def _call_openai(prompt: str, system_prompt: str = None, max_tokens: int = 2000) -> Optional[str]:
    """Call OpenAI API."""
    _ensure_openai_configured()
    
    if not _openai_client:
        return None
    
    try:
        messages = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        messages.append({"role": "user", "content": prompt})
        
        response = _openai_client.chat.completions.create(
            model=_openai_model,
            messages=messages,
            temperature=0.7,
            max_tokens=max_tokens
        )
        
        # Validate response before returning
        if not response.choices or not response.choices[0].message.content:
            print("[Optimizer] OpenAI returned empty response")
            return None
        
        text = response.choices[0].message.content.strip()
        
        # Check if response indicates an error (starts with common error indicators)
        error_indicators = ['error', 'internal server error', 'not found', 'unavailable', 'rate limit']
        if any(text.lower().startswith(indicator) for indicator in error_indicators):
            print(f"[Optimizer] OpenAI returned error message: {text[:100]}")
            return None
        
        print("[Optimizer] Successfully generated with OpenAI")
        return text
        
    except Exception as e:
        print(f"[Optimizer] OpenAI error: {e}")
        return None


def _call_ai(prompt: str, system_prompt: str = None, max_tokens: int = 2000, prefer_quality: bool = False) -> Tuple[Optional[str], str]:
    """
    Call AI with smart model selection.
    
    Args:
        prompt: The prompt text
        system_prompt: Optional system instruction
        max_tokens: Max response tokens
        prefer_quality: If True, use MiniMax first (for titles). If False, use Gemini first (for descriptions/tags)
    
    Returns:
        (result, model_name)
    """
    if prefer_quality:
        # QUALITY MODE: MiniMax first (for titles and critical content)
        result = _call_minimax(prompt, system_prompt, max_tokens)
        if result:
            return result, "minimax"
        
        # Fallback to OpenAI
        result = _call_openai(prompt, system_prompt, max_tokens)
        if result:
            return result, "gpt-4o-fallback"
        
        # Final fallback to Gemini
        result = _call_gemini(prompt, system_prompt)
        if result:
            return result, "gemini-fallback"
    else:
        # SPEED MODE: Gemini first (for descriptions, tags, timestamps)
        result = _call_gemini(prompt, system_prompt)
        if result:
            return result, "gemini"
        
        # Fallback to OpenAI
        result = _call_openai(prompt, system_prompt, max_tokens)
        if result:
            return result, "gpt-4o-fallback"
        
        # Final fallback to MiniMax
        result = _call_minimax(prompt, system_prompt, max_tokens)
        if result:
            return result, "minimax-fallback"
    
    return None, "none"
