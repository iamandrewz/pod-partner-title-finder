"""
YouTube Title Research Scoring System

Implements Andrew's 7-factor hierarchy for finding the best YouTube titles to mimic.
Each factor is scored independently and then weighted to produce a composite score.

Author: Subagent
Date: 2026-02-16
"""

from datetime import datetime, timedelta
from typing import List, Dict, Any, Optional, Tuple
import re
import math


class YouTubeTitleScorer:
    """
    Scores YouTube videos based on 7 factors to find the best titles to mimic.
    
    Weights (default):
    - Channel Outlier: 40%
    - View Count: 20%
    - Recency: 15%
    - Topic Relevance: 10%
    - Channel Similarity: 8%
    - Title Pattern Match: 5%
    - Search Cluster: 2%
    """
    
    DEFAULT_WEIGHTS = {
        'outlier': 0.40,
        'views': 0.20,
        'recency': 0.15,
        'relevance': 0.10,
        'similarity': 0.08,
        'pattern': 0.05,
        'cluster': 0.02
    }
    
    # Title pattern triggers (for pattern matching)
    TITLE_PATTERNS = {
        'numbers': r'\b(\d+)\b',
        'how_to': r'\b(how to|how do|how can)\b',
        'why': r'\b(why|reason|reasons)\b',
        'best': r'\b(best|top|ultimate)\b',
        'mistakes': r'\b(mistake|error|wrong|fail)\b',
        'secret': r'\b(secret|hack|trick|hidden)\b',
        'emotional': r'\b(amazing|incredible|shocking|insane|obsessed|love|hate|fear)\b',
        'tutorial': r'\b(tutorial|guide|step by step|learn)\b',
        'vs': r'\b(vs|versus|compared)\b',
        'list': r'\b(\d+\s+(things|ways|reasons|tips|secrets|tricks|hacks))\b',
    }
    
    # Emotional triggers for scoring
    EMOTIONAL_WORDS = {
        'high': ['amazing', 'incredible', 'shocking', 'insane', 'mind-blowing', 
                 'obsessed', 'life-changing', 'unbelievable', 'impossible'],
        'medium': ['awesome', 'fantastic', 'incredible', 'secret', 'hack', 
                   'trick', 'hidden', 'ultimate', 'powerful'],
        'low': ['good', 'great', 'best', 'helpful', 'useful', 'interesting']
    }
    
    def __init__(self, weights: Optional[Dict[str, float]] = None):
        """
        Initialize the scorer with custom weights.
        
        Args:
            weights: Dictionary of factor weights. If None, uses DEFAULT_WEIGHTS.
        """
        self.weights = weights or self.DEFAULT_WEIGHTS.copy()
        # Normalize weights to sum to 1
        total = sum(self.weights.values())
        if total != 1.0 and total > 0:
            self.weights = {k: v / total for k, v in self.weights.items()}
    
    # =========================================================================
    # FACTOR 1: Channel Outlier (40% weight)
    # =========================================================================
    
    def calculate_outlier_score(self, video_data: Dict[str, Any], 
                                 channel_data: Dict[str, Any]) -> float:
        """
        Calculate how much a video outperforms its channel's subscriber base.
        
        Formula: (views / subscribers) * 1000
        
        A higher ratio means the video is an "outlier" - content that 
        significantly outperformed what the subscriber count would predict.
        
        Args:
            video_data: Dict with 'views' key (int)
            channel_data: Dict with 'subscribers' key (int)
            
        Returns:
            Score between 0 and 1 (normalized)
        """
        views = video_data.get('views', 0)
        subscribers = channel_data.get('subscribers', 0)
        
        if subscribers <= 0 or views <= 0:
            return 0.0
        
        # Calculate raw outlier ratio
        ratio = (views / subscribers) * 1000
        
        # Normalize to 0-1 scale
        # Benchmarks: 
        # - 1.0 = 1000 views per subscriber (viral outlier)
        # - 0.5 = 500 views per subscriber (strong performer)
        # - 0.1 = 100 views per subscriber (average)
        # - 0.01 = 10 views per subscriber (below average)
        
        # Use logarithmic scaling for better distribution
        normalized = min(1.0, math.log10(ratio + 1) / 3)  # 3 = log10(1000)
        
        return normalized
    
    # =========================================================================
    # FACTOR 2: View Count (20% weight)
    # =========================================================================
    
    def calculate_view_score(self, view_count: int) -> float:
        """
        Score based on absolute view count.
        
        Linear scaling from 1K to 100K views.
        Anything above 100K gets max score.
        
        Args:
            view_count: Number of views (int)
            
        Returns:
            Score between 0 and 1
        """
        if view_count <= 0:
            return 0.0
        
        if view_count >= 100000:
            return 1.0
        
        # Linear scale: 1K = 0, 100K = 1.0
        return (view_count - 1000) / (100000 - 1000)
    
    # =========================================================================
    # FACTOR 3: Recency (15% weight)
    # =========================================================================
    
    def calculate_recency_score(self, published_date: str) -> float:
        """
        Score based on how recently the video was uploaded.
        
        Formula: max(0, 30 - days_ago) / 30
        
        Videos within 30 days get full score.
        After 30 days, score drops linearly.
        
        Args:
            published_date: ISO format date string (YYYY-MM-DD or ISO datetime)
            
        Returns:
            Score between 0 and 1
        """
        try:
            # Try parsing ISO format
            if 'T' in published_date:
                pub_date = datetime.fromisoformat(published_date.replace('Z', '+00:00'))
                # Convert to naive datetime for calculation
                pub_date = pub_date.replace(tzinfo=None)
            else:
                pub_date = datetime.strptime(published_date, '%Y-%m-%d')
        except (ValueError, AttributeError):
            return 0.0
        
        now = datetime.now()
        days_ago = (now - pub_date).days
        
        if days_ago < 0:
            # Future date - treat as very recent
            return 1.0
        
        return max(0, (30 - days_ago) / 30)
    
    # =========================================================================
    # FACTOR 4: Topic Relevance (10% weight)
    # =========================================================================
    
    def calculate_relevance_score(self, episode_keywords: List[str], 
                                   video_keywords: List[str]) -> float:
        """
        Calculate keyword overlap between episode context and video.
        
        Uses Jaccard similarity for keyword matching.
        
        Args:
            episode_keywords: List of keywords from the episode
            video_keywords: List of keywords from the YouTube video
            
        Returns:
            Score between 0 and 1
        """
        if not episode_keywords or not video_keywords:
            return 0.0
        
        # Normalize to lowercase for comparison
        ep_set = set(kw.lower().strip() for kw in episode_keywords)
        vid_set = set(kw.lower().strip() for kw in video_keywords)
        
        # Calculate Jaccard similarity
        intersection = len(ep_set & vid_set)
        union = len(ep_set | vid_set)
        
        if union == 0:
            return 0.0
        
        return intersection / union
    
    def calculate_transcript_similarity(self, episode_transcript: str,
                                         video_title: str) -> float:
        """
        Alternative relevance scoring using transcript similarity.
        
        Args:
            episode_transcript: Full transcript text from episode
            video_title: YouTube video title to match against
            
        Returns:
            Score between 0 and 1
        """
        if not episode_transcript or not video_title:
            return 0.0
        
        # Extract words from transcript (common words)
        transcript_words = set(episode_transcript.lower().split())
        title_words = set(video_title.lower().split())
        
        # Remove common stop words
        stop_words = {'the', 'a', 'an', 'and', 'or', 'but', 'in', 'on', 'at', 
                      'to', 'for', 'of', 'with', 'by', 'is', 'are', 'was', 'were',
                      'be', 'been', 'being', 'have', 'has', 'had', 'do', 'does',
                      'did', 'will', 'would', 'could', 'should', 'may', 'might'}
        
        transcript_words -= stop_words
        title_words -= stop_words
        
        intersection = len(transcript_words & title_words)
        union = len(transcript_words | title_words)
        
        if union == 0:
            return 0.0
        
        return intersection / union
    
    # =========================================================================
    # FACTOR 5: Channel Similarity (8% weight)
    # =========================================================================
    
    def calculate_similarity_score(self, user_channel: Dict[str, Any],
                                    target_channel: Dict[str, Any]) -> float:
        """
        Score based on similarity between user's channel and target channel.
        
        Factors:
        - Same niche/category (binary: 0 or 1)
        - Subscriber count similarity (within 10x = 1.0, scales down)
        
        Args:
            user_channel: Dict with 'category', 'subscribers' keys
            target_channel: Dict with 'category', 'subscribers' keys
            
        Returns:
            Score between 0 and 1
        """
        category_score = 0.0
        subscriber_score = 0.0
        
        # Category match
        user_category = user_channel.get('category', '').lower().strip()
        target_category = target_channel.get('category', '').lower().strip()
        
        if user_category and target_category:
            if user_category == target_category:
                category_score = 1.0
            elif user_category in target_category or target_category in user_category:
                category_score = 0.5  # Partial match
        
        # Subscriber count similarity
        user_subs = user_channel.get('subscribers', 0)
        target_subs = target_channel.get('subscribers', 0)
        
        if user_subs > 0 and target_subs > 0:
            ratio = min(user_subs, target_subs) / max(user_subs, target_subs)
            
            # Within 10x = full score, scales down
            # Using exponential falloff for smoother scoring
            if ratio >= 0.1:  # Within 10x
                subscriber_score = ratio  # Linear: 0.1 to 1.0
            else:
                subscriber_score = 0.0
        
        # Weight category more heavily
        return (category_score * 0.6) + (subscriber_score * 0.4)
    
    # =========================================================================
    # FACTOR 6: Title Pattern Match (5% weight)
    # =========================================================================
    
    def calculate_pattern_score(self, title: str) -> float:
        """
        Score based on proven title framework patterns.
        
        Patterns that score well:
        - Numbers (listicles)
        - How-to phrases
        - "Why" questions
        - Superlatives (best, top, ultimate)
        - Mistake/error framing
        - Secret/hack/trick words
        - Emotional triggers
        
        Args:
            title: YouTube video title
            
        Returns:
            Score between 0 and 1
        """
        if not title:
            return 0.0
        
        title_lower = title.lower()
        score = 0.0
        
        # Check each pattern
        pattern_weights = {
            'numbers': 0.15,
            'how_to': 0.15,
            'why': 0.10,
            'best': 0.10,
            'mistakes': 0.15,
            'secret': 0.15,
            'emotional': 0.10,
            'tutorial': 0.10,
            'vs': 0.05,
            'list': 0.20,
        }
        
        for pattern_name, pattern_regex in self.TITLE_PATTERNS.items():
            if re.search(pattern_regex, title_lower):
                score += pattern_weights.get(pattern_name, 0.05)
        
        # Cap at 1.0
        return min(1.0, score)
    
    # =========================================================================
    # FACTOR 7: Search Cluster (2% weight)
    # =========================================================================
    
    def calculate_search_cluster_score(self, search_results: List[Dict[str, Any]]) -> float:
        """
        Score based on search cluster - multiple successful videos in results.
        
        If multiple videos in search results have high view counts,
        it indicates a trending topic cluster.
        
        Args:
            search_results: List of video dicts from search results
            
        Returns:
            Score between 0 and 1
        """
        if not search_results:
            return 0.0
        
        # Count videos with significant views (>10K)
        high_view_count = sum(1 for v in search_results 
                             if v.get('views', 0) > 10000)
        
        # Count total videos
        total = len(search_results)
        
        if total == 0:
            return 0.0
        
        # Ratio of successful videos in cluster
        success_ratio = high_view_count / total
        
        # Also factor in cluster size
        # More results = stronger signal
        size_factor = min(1.0, total / 10)  # Cap at 10 results
        
        return (success_ratio * 0.7) + (size_factor * 0.3)
    
    # =========================================================================
    # COMPOSITE SCORING
    # =========================================================================
    
    def get_composite_score(self, video: Dict[str, Any], 
                            weights: Optional[Dict[str, float]] = None,
                            episode_context: Optional[Dict[str, Any]] = None) -> Tuple[float, Dict[str, float]]:
        """
        Calculate the weighted composite score for a video.
        
        Args:
            video: Video data dict with keys:
                   - views (int)
                   - published_date (str)
                   - title (str)
                   - channel_id (str)
                   - keywords (list, optional)
            weights: Custom weights dict (optional, uses instance weights)
            episode_context: Dict with:
                            - channel (user's channel data)
                            - keywords (episode keywords)
                            - search_results (list, optional)
                            
        Returns:
            Tuple of (composite_score, breakdown_dict)
        """
        w = weights or self.weights
        
        breakdown = {}
        
        # Factor 1: Channel Outlier
        channel_data = video.get('channel_data', {})
        breakdown['outlier'] = self.calculate_outlier_score(video, channel_data) * w.get('outlier', 0)
        
        # Factor 2: View Count
        breakdown['views'] = self.calculate_view_score(video.get('views', 0)) * w.get('views', 0)
        
        # Factor 3: Recency
        breakdown['recency'] = self.calculate_recency_score(video.get('published_date', '')) * w.get('recency', 0)
        
        # Factor 4: Topic Relevance
        if episode_context and episode_context.get('keywords'):
            breakdown['relevance'] = self.calculate_relevance_score(
                episode_context['keywords'],
                video.get('keywords', [])
            ) * w.get('relevance', 0)
        else:
            breakdown['relevance'] = 0.0
        
        # Factor 5: Channel Similarity
        if episode_context and episode_context.get('channel'):
            breakdown['similarity'] = self.calculate_similarity_score(
                episode_context['channel'],
                video.get('channel_data', {})
            ) * w.get('similarity', 0)
        else:
            breakdown['similarity'] = 0.0
        
        # Factor 6: Title Pattern
        breakdown['pattern'] = self.calculate_pattern_score(video.get('title', '')) * w.get('pattern', 0)
        
        # Factor 7: Search Cluster
        if episode_context and episode_context.get('search_results'):
            breakdown['cluster'] = self.calculate_search_cluster_score(
                episode_context['search_results']
            ) * w.get('cluster', 0)
        else:
            breakdown['cluster'] = 0.0
        
        # Calculate composite
        composite = sum(breakdown.values())
        
        return composite, breakdown
    
    def rank_videos(self, videos: List[Dict[str, Any]], 
                   episode_context: Optional[Dict[str, Any]] = None,
                   weights: Optional[Dict[str, float]] = None) -> List[Dict[str, Any]]:
        """
        Rank a list of videos by their composite score.
        
        Args:
            videos: List of video dicts
            episode_context: Context for scoring (keywords, channel, etc.)
            weights: Custom weights
            
        Returns:
            List of videos with added 'score' and 'breakdown' keys, sorted by score descending
        """
        scored_videos = []
        
        for video in videos:
            score, breakdown = self.get_composite_score(
                video, 
                weights=weights,
                episode_context=episode_context
            )
            
            scored_video = {
                **video,
                'score': score,
                'breakdown': breakdown
            }
            scored_videos.append(scored_video)
        
        # Sort by score descending
        scored_videos.sort(key=lambda x: x['score'], reverse=True)
        
        return scored_videos


# =============================================================================
# EXAMPLE USAGE
# =============================================================================

def example_usage():
    """Demonstrate how to use the YouTubeTitleScorer."""
    
    print("=" * 60)
    print("YouTube Title Scorer - Example Usage")
    print("=" * 60)
    
    # Initialize scorer
    scorer = YouTubeTitleScorer()
    
    # Sample user's channel data
    user_channel = {
        'category': 'Technology',
        'subscribers': 50000
    }
    
    # Sample episode context
    episode_context = {
        'channel': user_channel,
        'keywords': ['AI', 'ChatGPT', 'productivity', 'automation', 'tools'],
        'search_results': []  # Would be populated from search
    }
    
    # Sample videos to score
    videos = [
        {
            'id': 'vid001',
            'title': '10 ChatGPT Hacks That Will Change Your Life',
            'views': 250000,
            'published_date': '2026-02-10',
            'channel_data': {
                'subscribers': 100000,
                'category': 'Technology'
            },
            'keywords': ['ChatGPT', 'AI', 'productivity']
        },
        {
            'id': 'vid002',
            'title': 'Why I Stopped Using Other AI Tools',
            'views': 15000,
            'published_date': '2026-02-14',
            'channel_data': {
                'subscribers': 5000,
                'category': 'Tech'
            },
            'keywords': ['AI', 'tools', 'ChatGPT']
        },
        {
            'id': 'vid003',
            'title': 'How to Automate Your Entire Workflow',
            'views': 75000,
            'published_date': '2026-01-15',
            'channel_data': {
                'subscribers': 200000,
                'category': 'Productivity'
            },
            'keywords': ['automation', 'workflow', 'productivity']
        },
        {
            'id': 'vid004',
            'title': 'The Secret AI Feature Nobody Knows About',
            'views': 500000,
            'published_date': '2026-02-15',
            'channel_data': {
                'subscribers': 800000,
                'category': 'Technology'
            },
            'keywords': ['AI', 'secret', 'feature']
        },
    ]
    
    # Score and rank videos
    ranked = scorer.rank_videos(videos, episode_context)
    
    # Print results
    print("\n📊 RANKED VIDEO TITLES (Best to Mimic):\n")
    for i, video in enumerate(ranked, 1):
        print(f"{i}. {video['title']}")
        print(f"   Composite Score: {video['score']:.3f}")
        print(f"   Breakdown:")
        for factor, score in video['breakdown'].items():
            print(f"      - {factor}: {score:.3f}")
        print()
    
    # Show best title to mimic
    best = ranked[0]
    print("=" * 60)
    print(f"🏆 BEST TITLE TO MIMIC:")
    print(f"   \"{best['title']}\"")
    print(f"   Score: {best['score']:.3f}")
    print("=" * 60)
    
    # Demonstrate custom weights
    print("\n⚙️  CUSTOM WEIGHTS EXAMPLE:")
    custom_weights = {
        'outlier': 0.30,
        'views': 0.25,
        'recency': 0.20,
        'relevance': 0.15,
        'similarity': 0.05,
        'pattern': 0.03,
        'cluster': 0.02
    }
    scorer_custom = YouTubeTitleScorer(weights=custom_weights)
    ranked_custom = scorer_custom.rank_videos(videos, episode_context)
    print(f"   With recency boost, top result: \"{ranked_custom[0]['title']}\"")
    
    return ranked


# =============================================================================
# INTEGRATION WITH EPISODE OPTIMIZER
# =============================================================================

class EpisodeOptimizerIntegration:
    """
    Integration layer for connecting YouTubeTitleScorer with Episode Optimizer.
    
    This class provides utilities for:
    - Fetching video data from YouTube API
    - Processing search results
    - Generating title recommendations
    """
    
    def __init__(self, youtube_api_key: str = None):
        self.youtube_api_key = youtube_api_key
        self.scorer = YouTubeTitleScorer()
    
    def process_search_results(self, search_response: Dict[str, Any]) -> List[Dict[str, Any]]:
        """
        Process YouTube search API response into scorer-ready format.
        
        Args:
            search_response: Raw response from YouTube Data API search
            
        Returns:
            List of formatted video dicts
        """
        videos = []
        
        for item in search_response.get('items', []):
            snippet = item.get('snippet', {})
            video = {
                'id': item.get('id', {}).get('videoId'),
                'title': snippet.get('title'),
                'published_date': snippet.get('publishedAt'),
                'channel_id': snippet.get('channelId'),
                'channel_title': snippet.get('channelTitle'),
                'description': snippet.get('description'),
                # These would be populated from additional API calls
                'views': 0,
                'keywords': [],
                'channel_data': {
                    'subscribers': 0,
                    'category': ''
                }
            }
            videos.append(video)
        
        return videos
    
    def enrich_with_statistics(self, videos: List[Dict[str, Any]], 
                               statistics: Dict[str, Dict]) -> List[Dict[str, Any]]:
        """
        Enrich video data with view counts and channel stats.
        
        Args:
            videos: List of basic video dicts
            statistics: Dict mapping video_id to statistics data
            
        Returns:
            Enriched video list
        """
        for video in videos:
            video_id = video.get('id')
            if video_id in statistics:
                stats = statistics[video_id]
                video['views'] = stats.get('views', 0)
                if stats.get('channel_data'):
                    video['channel_data'] = stats['channel_data']
        
        return videos
    
    def generate_title_recommendations(self, videos: List[Dict[str, Any]],
                                        episode_context: Dict[str, Any],
                                        top_n: int = 5) -> List[Dict[str, Any]]:
        """
        Generate title recommendations based on scored videos.
        
        Args:
            videos: List of videos to score
            episode_context: Episode context for relevance scoring
            top_n: Number of top recommendations to return
            
        Returns:
            List of recommended titles with scores and metadata
        """
        ranked = self.scorer.rank_videos(videos, episode_context)
        
        recommendations = []
        for video in ranked[:top_n]:
            recommendations.append({
                'title': video['title'],
                'score': video['score'],
                'factors': video['breakdown'],
                'video_id': video.get('id'),
                'channel': video.get('channel_title'),
                'views': video.get('views'),
                'mimic_reason': self._generate_mimic_reason(video)
            })
        
        return recommendations
    
    def _generate_mimic_reason(self, video: Dict[str, Any]) -> str:
        """Generate human-readable reason for recommendation."""
        reasons = []
        
        if video.get('breakdown', {}).get('outlier', 0) > 0.15:
            reasons.append("viral outlier")
        if video.get('breakdown', {}).get('views', 0) > 0.15:
            reasons.append("high views")
        if video.get('breakdown', {}).get('pattern', 0) > 0.03:
            reasons.append("proven title pattern")
        if video.get('breakdown', {}).get('recency', 0) > 0.10:
            reasons.append("recently trending")
        
        if reasons:
            return f"Matches because: {', '.join(reasons)}"
        return "General good match"


# =============================================================================
# MAIN ENTRY POINT
# =============================================================================

if __name__ == '__main__':
    example_usage()
