'use client';

import { useState } from 'react';
import Navbar from '@/components/Navbar';
import YouTubeConnectButton from '@/components/YouTubeConnectButton';

interface YouTubeMatch {
  title: string;
  views: number;
  channel: string;
  url: string;
}

interface TitleResult {
  title: string;
  topic: string;
  youtube_matches: YouTubeMatch[];
  mimicked_title: string | null;
}

interface TopicSearchResult {
  title: string;
  views: number;
  channel: string;
  url: string;
}

interface OptimizeResult {
  success: boolean;
  video_title: string;
  transcript?: string;
  episode_summary: string;
  topics: string[];
  topic_searches: Record<string, TopicSearchResult[]>;
  titles: TitleResult[];
  error: string | null;
}

export default function V3OptimizerPage() {
  const [youtubeUrl, setYoutubeUrl] = useState('');
  const [manualTranscript, setManualTranscript] = useState('');
  const [showTranscriptBox, setShowTranscriptBox] = useState(false);
  const [niche, setNiche] = useState('');
  const [audience, setAudience] = useState('');
  const [focus, setFocus] = useState('');
  const [loading, setLoading] = useState(false);
  const [result, setResult] = useState<OptimizeResult | null>(null);
  const [error, setError] = useState('');
  const [errorHint, setErrorHint] = useState('');
  const [copiedIndex, setCopiedIndex] = useState<number | null>(null);
  const [editedTitles, setEditedTitles] = useState<Record<number, string>>({});
  const [mimicInput, setMimicInput] = useState('');
  const [mimicLoading, setMimicLoading] = useState(false);
  const [mimicResults, setMimicResults] = useState<string[]>([]);
  const [mimicCopiedIndex, setMimicCopiedIndex] = useState<number | null>(null);
  const [editedMimicTitles, setEditedMimicTitles] = useState<Record<number, string>>({});

  const copyToClipboard = async (text: string, index: number) => {
    try {
      await navigator.clipboard.writeText(text);
      setCopiedIndex(index);
      setTimeout(() => setCopiedIndex(null), 2000);
    } catch (err) {
      console.error('Failed to copy:', err);
    }
  };

  const copyMimicToClipboard = async (index: number) => {
    try {
      const text = editedMimicTitles[index] ?? mimicResults[index];
      await navigator.clipboard.writeText(text);
      setMimicCopiedIndex(index);
      setTimeout(() => setMimicCopiedIndex(null), 2000);
    } catch (err) {
      console.error('Failed to copy:', err);
    }
  };

  const handleMimicTitleChange = (index: number, newTitle: string) => {
    setEditedMimicTitles(prev => ({ ...prev, [index]: newTitle }));
  };

  const handleTitleChange = (index: number, newTitle: string) => {
    setEditedTitles(prev => ({ ...prev, [index]: newTitle }));
  };

  const getTitle = (item: TitleResult, index: number): string => {
    return editedTitles[index] ?? item.title;
  };

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!youtubeUrl.trim() && !manualTranscript.trim()) return;
    
    setLoading(true);
    setError('');
    setErrorHint('');
    setResult(null);
    setEditedTitles({});
    setMimicInput('');
    setMimicResults([]);
    
    try {
      const response = await fetch('/api/v3/optimize', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ 
          youtube_url: youtubeUrl || undefined,
          manual_transcript: manualTranscript || undefined,
          niche: niche || undefined,
          audience: audience || undefined,
          focus: focus || undefined,
        }),
      });
      
      const data = await response.json();
      
      if (!response.ok || !data.success) {
        const errMsg = data.error || 'Something went wrong';
        // If transcript extraction failed, show the paste box automatically
        if (errMsg.toLowerCase().includes('transcript') || errMsg.toLowerCase().includes('captions') || errMsg.toLowerCase().includes('bot')) {
          setShowTranscriptBox(true);
          setError('');
          setErrorHint('');
          return;
        }
        setError(errMsg);
        setErrorHint(data.error_hint || '');
        return;
      }
      
      setResult(data);
    } catch (err: any) {
      setError(err.message || 'Something went wrong');
      setErrorHint('');
    } finally {
      setLoading(false);
    }
  };

  const handleMimicSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!mimicInput.trim() || !result) return;
    
    setMimicLoading(true);
    setMimicResults([]);
    
    try {
      const response = await fetch('/api/v3/mimic', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          title_to_mimic: mimicInput,
          topics: result.topics,
          transcript_summary: result.transcript?.substring(0, 500) || '',
          niche: niche || undefined,
          audience: audience || undefined,
          focus: focus || undefined,
        }),
      });
      
      const data = await response.json();
      
      if (!response.ok) {
        throw new Error(data.error || 'Mimic generation failed');
      }
      
      setMimicResults(data.mimicked_titles || []);
      setEditedMimicTitles({});  // Reset edits for new results
    } catch (err: any) {
      console.error('Mimic error:', err);
    } finally {
      setMimicLoading(false);
    }
  };

  const formatViews = (views: number | null | undefined): string => {
    if (views == null) return 'N/A';
    if (views >= 1000000) return `${(views / 1000000).toFixed(1)}M`;
    if (views >= 1000) return `${(views / 1000).toFixed(0)}K`;
    return views.toString();
  };

  return (
    <div className="min-h-screen bg-gray-900 text-white">
      <Navbar />
      <div className="pt-20 p-8">
      <div className="max-w-4xl mx-auto">
        <div className="mb-4">
          <YouTubeConnectButton />
        </div>
        <h1 className="text-3xl font-bold mb-2 text-center">Podcast Title Lab</h1>
        <p className="text-gray-400 text-center mb-8">
          Powered by the Pursue Podcasting Method
        </p>
        
        {/* Input Form */}
        <form onSubmit={handleSubmit} className="mb-8">
          <div className="space-y-3 mb-4">
            <input
              type="text"
              value={niche}
              onChange={(e) => setNiche(e.target.value)}
              placeholder="Podcast niche (optional)"
              className="w-full p-3 bg-gray-800 rounded-lg text-white text-sm focus:ring-2 focus:ring-blue-500 focus:outline-none"
            />
            <input
              type="text"
              value={audience}
              onChange={(e) => setAudience(e.target.value)}
              placeholder="Target audience (optional)"
              className="w-full p-3 bg-gray-800 rounded-lg text-white text-sm focus:ring-2 focus:ring-blue-500 focus:outline-none"
            />
            <input
              type="text"
              value={focus}
              onChange={(e) => setFocus(e.target.value)}
              placeholder="What's the most important part of this episode? (optional)"
              className="w-full p-3 bg-gray-800 rounded-lg text-white text-sm focus:ring-2 focus:ring-blue-500 focus:outline-none"
            />
          </div>
          <div className="flex gap-4">
            <input
              type="url"
              value={youtubeUrl}
              onChange={(e) => setYoutubeUrl(e.target.value)}
              placeholder="Paste YouTube URL..."
              className="flex-1 p-4 bg-gray-800 rounded-lg text-white focus:ring-2 focus:ring-blue-500 focus:outline-none"
            />
            <button
              type="submit"
              disabled={loading}
              className="px-8 py-4 bg-blue-500 text-black font-bold rounded-lg hover:bg-blue-400 disabled:bg-gray-600 disabled:cursor-not-allowed transition-colors"
            >
              {loading ? 'Finding Titles...' : 'Find Titles'}
            </button>
          </div>
          {/* Transcript paste - shown on error or manual toggle */}
          {showTranscriptBox && (
            <div className="p-4 bg-yellow-900/30 border border-yellow-600/50 rounded-lg space-y-3">
              <div className="flex items-start gap-2">
                <span className="text-yellow-400 text-lg">⚠️</span>
                <div>
                  <p className="text-yellow-300 font-medium text-sm">Couldn&apos;t pull the transcript automatically</p>
                  <p className="text-yellow-400/70 text-xs mt-1">Paste it below and we&apos;ll generate your titles. You can grab it from YouTube → &quot;Show Transcript&quot; under the video.</p>
                </div>
              </div>
              <textarea
                value={manualTranscript}
                onChange={(e) => setManualTranscript(e.target.value)}
                placeholder="Paste transcript here..."
                rows={6}
                autoFocus
                className="w-full p-3 bg-gray-800 rounded-lg text-white text-sm focus:ring-2 focus:ring-yellow-500 focus:outline-none resize-y"
              />
              {manualTranscript.trim() && (
                <p className="text-green-400 text-xs">✓ Transcript ready — hit &quot;Find Titles&quot; to generate</p>
              )}
            </div>
          )}
          {!showTranscriptBox && (
            <button
              type="button"
              onClick={() => setShowTranscriptBox(true)}
              className="text-xs text-gray-500 hover:text-gray-300 transition-colors"
            >
              Or paste transcript manually →
            </button>
          )}
        </form>
        
        {/* Error */}
        {error && (
          <div className="mb-6 p-4 bg-red-900/50 border border-red-500 rounded-lg">
            <p className="text-red-300 font-medium">{error}</p>
            {errorHint && (
              <p className="text-red-400/80 text-sm mt-2">{errorHint}</p>
            )}
          </div>
        )}
        
        {/* Loading */}
        {loading && (
          <div className="text-center py-12">
            <div className="inline-block animate-spin rounded-full h-12 w-12 border-4 border-gray-600 border-t-blue-500"></div>
            <p className="mt-4 text-gray-400">{manualTranscript.trim() ? 'Generating titles...' : 'Extracting transcript & generating titles...'}</p>
            <p className="text-sm text-gray-500 mt-2">This may take 30-60 seconds</p>
          </div>
        )}
        
        {/* Results */}
        {result && result.success && (
          <div className="space-y-6">
            {/* Video Info */}
            <div className="p-4 bg-gray-800 rounded-lg">
              <h2 className="text-lg font-semibold text-gray-300">Episode Summary</h2>
              <p className="text-white">{result.episode_summary}</p>
            </div>
            
            {/* Topics */}
            <div className="p-4 bg-gray-800 rounded-lg">
              <h2 className="text-lg font-semibold text-gray-300 mb-3">Key Topics</h2>
              <div className="flex flex-wrap gap-2">
                {result.topics.map((topic, i) => (
                  <span key={i} className="px-3 py-1 bg-gray-700 rounded-full text-sm">
                    {topic}
                  </span>
                ))}
              </div>
            </div>
            
            {/* Title Options */}
            <div>
              <div className="flex justify-between items-center mb-4">
                <h2 className="text-xl font-bold">
                  📋 Title Options ({result.titles.length})
                </h2>
                <button
                  onClick={handleSubmit}
                  disabled={loading}
                  className="px-4 py-2 bg-gray-700 text-white text-sm font-medium rounded-lg hover:bg-gray-600 disabled:bg-gray-800 disabled:cursor-not-allowed transition-colors"
                >
                  {loading ? '⏳ Regenerating...' : '🔄 Regenerate'}
                </button>
              </div>
              <p className="text-gray-400 text-sm mb-4">
                Edit title inline, then click Copy
              </p>
              
              <div className="space-y-3">
                {[...result.titles]
                  .sort((a, b) => {
                    const aViews = (a.youtube_matches || [])[0]?.views || 0;
                    const bViews = (b.youtube_matches || [])[0]?.views || 0;
                    return bViews - aViews; // Highest views first
                  })
                  .map((item, idx) => {
                  // Sort matches by views (highest first) within each card
                  const matches = [...(item.youtube_matches || [])].sort((a, b) => (b.views || 0) - (a.views || 0));
                  const bestMatch = matches.length > 0 ? matches[0] : null;
                  const viewCount = bestMatch?.views || 0;
                  const currentTitle = getTitle(item, idx);
                  
                  // Color coding based on views
                  let viewColor = 'text-gray-500';
                  if (viewCount >= 1000000) viewColor = 'text-green-400';
                  else if (viewCount >= 100000) viewColor = 'text-blue-400';
                  else if (viewCount >= 10000) viewColor = 'text-orange-400';
                  
                  return (
                    <div
                      key={idx}
                      className={`p-4 bg-gray-800 rounded-lg transition-all hover:bg-gray-700 ${
                        idx === 0 ? 'ring-2 ring-blue-500' : ''
                      }`}
                    >
                      {/* Header with rank and views */}
                      <div className="flex justify-between items-center mb-3">
                        <div className="flex items-center gap-2">
                          <span className="text-gray-500 text-sm font-mono">#{idx + 1}</span>
                          {idx === 0 && <span className="text-blue-500 text-sm">🏆 Best Match</span>}
                        </div>
                        {bestMatch && (
                          <span className={`font-bold ${viewColor}`}>
                            {formatViews(bestMatch.views)} views
                          </span>
                        )}
                      </div>
                      
                      {/* YouTube Matches Section - List of similar videos */}
                      {matches.length > 0 ? (
                        <div className="mb-3 p-3 bg-gray-900/50 rounded-lg border border-gray-700">
                          <p className="text-xs text-gray-400 uppercase tracking-wide mb-2">Similar videos on YouTube:</p>
                          <div className="space-y-2">
                            {matches.slice(0, 3).map((m, mIdx) => (
                              <div key={mIdx} className="text-sm">
                                <p className="text-white line-clamp-1">• {m.title}</p>
                                <p className="text-xs text-gray-500">{m.channel} • {formatViews(m.views)} views</p>
                              </div>
                            ))}
                          </div>
                        </div>
                      ) : null}
                      
                      {/* Mimicked Title Section */}
                      {item.mimicked_title ? (
                        <div className="mb-3 p-3 bg-green-900/30 rounded-lg border border-green-700">
                          <p className="text-xs text-green-400 uppercase tracking-wide mb-1">✨ Mimicked (Based on YouTube Pattern)</p>
                          <p className="text-lg text-white font-semibold">{item.mimicked_title}</p>
                        </div>
                      ) : null}
                      
                      {/* Our Title Section - Editable */}
                      <div className="mb-3">
                        <p className="text-xs text-blue-400 uppercase tracking-wide mb-1">Your Title</p>
                        <input
                          type="text"
                          value={currentTitle}
                          onChange={(e) => handleTitleChange(idx, e.target.value)}
                          className="w-full text-lg text-white font-semibold bg-transparent border-b border-gray-600 focus:border-blue-500 focus:outline-none py-1"
                        />
                      </div>
                      
                      {/* Topic and Copy Button */}
                      <div className="flex justify-between items-center">
                        <span className="text-sm text-gray-500">Topic: {item.topic}</span>
                        <button
                          onClick={() => copyToClipboard(currentTitle, idx)}
                          className={`px-4 py-2 rounded-lg font-semibold text-sm transition-colors ${
                            copiedIndex === idx
                              ? 'bg-green-600 text-white'
                              : 'bg-blue-500 hover:bg-blue-400 text-black'
                          }`}
                        >
                          {copiedIndex === idx ? '✓ Copied!' : 'Copy'}
                        </button>
                      </div>
                    </div>
                  );
                })}
              </div>
            </div>
            
            {/* Mimic Section */}
            <div className="mt-8 pt-8 border-t border-gray-700">
              <div className="p-6 bg-gray-800 rounded-lg">
                <h2 className="text-xl font-bold mb-2">
                  📋 Found a better title? Mimic it!
                </h2>
                <p className="text-gray-400 text-sm mb-4">
                  Paste a successful YouTube title and we&apos;ll generate versions that follow its pattern.
                </p>
                
                <form onSubmit={handleMimicSubmit} className="flex gap-4 mb-4">
                  <input
                    type="text"
                    value={mimicInput}
                    onChange={(e) => setMimicInput(e.target.value)}
                    placeholder="Paste a successful YouTube title here..."
                    className="flex-1 p-4 bg-gray-900 rounded-lg text-white focus:ring-2 focus:ring-green-500 focus:outline-none"
                  />
                  <button
                    type="submit"
                    disabled={mimicLoading || !mimicInput.trim()}
                    className="px-6 py-4 bg-green-500 text-black font-bold rounded-lg hover:bg-green-400 disabled:bg-gray-600 disabled:cursor-not-allowed transition-colors"
                  >
                    {mimicLoading ? 'Generating...' : 'Generate Mimicked Versions'}
                  </button>
                </form>
                
                {/* Mimic Loading Indicator */}
                {mimicLoading && (
                  <div className="flex items-center gap-2 mt-4 text-green-400">
                    <span className="flex gap-1">
                      <span className="w-1.5 h-1.5 bg-green-400 rounded-full animate-bounce" style={{animationDelay: '0ms'}}></span>
                      <span className="w-1.5 h-1.5 bg-green-400 rounded-full animate-bounce" style={{animationDelay: '150ms'}}></span>
                      <span className="w-1.5 h-1.5 bg-green-400 rounded-full animate-bounce" style={{animationDelay: '300ms'}}></span>
                    </span>
                  </div>
                )}

                {/* Mimic Results */}
                {mimicResults.length > 0 && (
                  <div id="mimic-results" className="space-y-3 mt-6">
                    {mimicResults.map((title, idx) => (
                      <div
                        key={idx}
                        className="flex items-center justify-between p-4 bg-gray-900 rounded-lg border border-gray-700"
                      >
                        <span className="text-gray-400 mr-2">{idx + 1}.</span>
                        <input
                          type="text"
                          value={editedMimicTitles[idx] ?? title}
                          onChange={(e) => handleMimicTitleChange(idx, e.target.value)}
                          className="flex-1 bg-transparent text-white font-medium border-none outline-none focus:ring-1 focus:ring-green-500 rounded px-2 py-1"
                        />
                        <button
                          onClick={() => copyMimicToClipboard(idx)}
                          className={`px-4 py-2 rounded-lg font-semibold text-sm transition-colors ml-4 ${
                            mimicCopiedIndex === idx
                              ? 'bg-green-600 text-white'
                              : 'bg-green-500 hover:bg-green-400 text-black'
                          }`}
                        >
                          {mimicCopiedIndex === idx ? '✓ Copied!' : 'Copy'}
                        </button>
                      </div>
                    ))}
                  </div>
                )}
              </div>
            </div>
          </div>
        )}
        
        {/* Error Result */}
        {result && !result.success && (
          <div className="p-6 bg-red-900/30 border border-red-500 rounded-lg">
            <h3 className="text-red-400 font-semibold mb-2">Optimization Failed</h3>
            <p className="text-gray-300">{result.error}</p>
          </div>
        )}
      </div>
      </div>
    </div>
  );
}
