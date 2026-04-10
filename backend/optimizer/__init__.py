"""
Episode Optimizer Module
Split from episode_optimizer_v3.py for maintainability.
"""

# Re-export main functions for backwards compatibility
# Note: These are imported lazily to avoid circular imports

__all__ = [
    'load_podcast_config',
    'generate_initial_titles', 
    'validate_title_on_youtube',
]

def __getattr__(name):
    """Lazy import to avoid circular imports."""
    if name == 'load_podcast_config':
        from episode_optimizer_v3 import load_podcast_config
        return load_podcast_config
    elif name == 'generate_initial_titles':
        from episode_optimizer_v3 import generate_initial_titles
        return generate_initial_titles
    elif name == 'validate_title_on_youtube':
        from episode_optimizer_v3 import validate_title_on_youtube
        return validate_title_on_youtube
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
