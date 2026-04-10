"""
Centralized configuration for Pod Partner backend.
All paths, API keys, and environment-specific settings go here.
"""
import shutil
import os

def get_ytdlp_path():
    """Find yt-dlp binary. Raises RuntimeError if not found."""
    # Try PATH first
    ytdlp = shutil.which('yt-dlp')
    if ytdlp:
        return ytdlp
    
    # Fallback paths
    fallbacks = [
        '/Users/ottis/Library/Python/3.9/bin/yt-dlp',
        '/usr/local/bin/yt-dlp',
        '/usr/bin/yt-dlp',
        '/opt/homebrew/bin/yt-dlp',
    ]
    for path in fallbacks:
        if os.path.exists(path):
            return path
    
    raise RuntimeError("yt-dlp not found! Install with: pip install yt-dlp")

# Export the path
YTDLP_PATH = get_ytdlp_path()

print(f"[Config] yt-dlp path: {YTDLP_PATH}")
