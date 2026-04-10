'use client';

import { useState } from 'react';

export default function TitleFinderPage() {
  const [youtubeUrl, setYoutubeUrl] = useState('');
  const [podcast, setPodcast] = useState('spp');
  const [loading, setLoading] = useState(false);
  const [polling, setPolling] = useState(false);
  const [jobId, setJobId] = useState('');
  const [result, setResult] = useState<any>(null);
  const [error, setError] = useState('');
  const [copied, setCopied] = useState('');
  const [startTime, setStartTime] = useState<number | null>(null);

  const copyToClipboard = async (text: string, label: string) => {
    try {
      await navigator.clipboard.writeText(text);
      setCopied(label);
      setTimeout(() => setCopied(''), 2000);
    } catch (err) {
      console.error('Failed to copy:', err);
    }
  };

  const pollForResults = async (jobId: string) => {
    setPolling(true);
    const maxAttempts = 60; // 60 * 2 seconds = 120 seconds max (matches backend timeout)
    let attempts = 0;

    const poll = async () => {
      try {
        const response = await fetch(`/api/title-finder/status/${jobId}`);
        const data = await response.json();

        if (data.status === 'complete') {
          setResult(data.result);
          setPolling(false);
          setLoading(false);
          if (data.result?.fallback) {
            setError(data.result.error || 'Used fallback mode - YouTube search had issues');
          }
        } else if (data.status === 'error') {
          setError(data.error || 'Job failed');
          setPolling(false);
          setLoading(false);
        } else if (attempts < maxAttempts) {
          attempts++;
          setTimeout(poll, 2000);
        } else {
          setError('Timeout waiting for results (120s limit exceeded)');
          setPolling(false);
          setLoading(false);
        }
      } catch (err: any) {
        setError(err.message);
        setPolling(false);
        setLoading(false);
      }
    };

    poll();
  };

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!youtubeUrl.trim()) return;
    
    setLoading(true);
    setError('');
    setResult(null);
    setJobId('');
    setStartTime(Date.now());
    
    try {
      const response = await fetch('/api/title-finder', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ youtube_url: youtubeUrl, podcast }),
      });
      
      const data = await response.json();
      if (!response.ok) throw new Error(data.error || 'Failed');
      
      if (data.job_id) {
        setJobId(data.job_id);
        pollForResults(data.job_id);
      } else {
        // Fallback: if result returned directly
        setResult(data);
        setLoading(false);
      }
    } catch (err: any) {
      setError(err.message);
      setLoading(false);
    }
  };

  const elapsedTime = startTime ? Math.round((Date.now() - startTime) / 1000) : 0;

  return (
    <div className="min-h-screen bg-gray-900 text-white p-8">
      <div className="max-w-4xl mx-auto">
        <h1 className="text-3xl font-bold mb-8 text-center">🎯 Title Finder</h1>
        
        <form onSubmit={handleSubmit} className="space-y-4 mb-8">
          <input
            type="url"
            value={youtubeUrl}
            onChange={(e) => setYoutubeUrl(e.target.value)}
            placeholder="YouTube URL"
            className="w-full p-3 bg-gray-800 rounded text-white"
          />
          <select
            value={podcast}
            onChange={(e) => setPodcast(e.target.value)}
            className="w-full p-3 bg-gray-800 rounded text-white"
          >
            <option value="spp">Savage Perspective Podcast</option>
            <option value="jpi">Just Pursue It</option>
            <option value="sbs">Silverback Summit</option>
            <option value="wow">Wisdom of Wrench</option>
            <option value="agp">Ali Gilbert Podcast</option>
          </select>
          <button
            type="submit"
            disabled={loading}
            className="w-full p-3 bg-yellow-500 text-black font-bold rounded disabled:bg-gray-600"
          >
            {loading ? (polling ? `Processing... (${elapsedTime}s)` : 'Starting...') : 'Find Winning Titles'}
          </button>
        </form>
        
        {error && (
          <div className="mb-4 p-4 bg-red-900/50 border border-red-500 rounded">
            <p className="text-red-300">{error}</p>
          </div>
        )}
        
        {loading && polling && (
          <div className="text-center py-8">
            <div className="inline-block animate-spin rounded-full h-8 w-8 border-b-2 border-yellow-500"></div>
            <p className="mt-4 text-gray-400">Analyzing video for winning titles...</p>
            <p className="text-sm text-gray-500">Time: {elapsedTime}s / 120s max</p>
          </div>
        )}
        
        {result && (
          <div className="space-y-6">
            {/* Status Banner */}
            {result.fallback && (
              <div className="p-4 bg-orange-900/50 border border-orange-500 rounded">
                <p className="text-orange-300 font-semibold">
                  ⚠️ Fallback Mode - YouTube search had issues. Titles are generated but not ranked by real view data.
                </p>
              </div>
            )}
            
            {/* Gold/Silver/Bronze */}
            <div className="grid gap-4">
              {['gold', 'silver', 'bronze'].map((medal) => {
                const title = result[medal]?.title;
                if (!title) return null;
                return (
                  <div key={medal} className={`p-4 rounded border-2 ${
                    medal === 'gold' ? 'bg-yellow-900/30 border-yellow-500' :
                    medal === 'silver' ? 'bg-gray-700/50 border-gray-400' :
                    'bg-orange-900/30 border-orange-700'
                  }`}>
                    <div className="flex justify-between items-start mb-2">
                      <h3 className="font-bold capitalize flex items-center gap-2">
                        {medal === 'gold' && '🥇'}
                        {medal === 'silver' && '🥈'}
                        {medal === 'bronze' && '🥉'}
                        {medal} Title
                      </h3>
                      <button
                        onClick={() => copyToClipboard(title, medal)}
                        className="px-3 py-1 text-sm bg-gray-700 hover:bg-gray-600 rounded transition-colors"
                      >
                        {copied === medal ? '✓ Copied!' : 'Copy'}
                      </button>
                    </div>
                    <p className="text-lg mb-2">{title}</p>
                    <p className="text-sm text-gray-400">
                      Pattern: {result[medal]?.pattern_from || 'N/A'} ({result[medal]?.pattern_views?.toLocaleString() || 0} views)
                    </p>
                  </div>
                );
              })}
            </div>
            
            {/* All 12 Titles List */}
            {result.all_titles && result.all_titles.length > 0 && (
              <div className="mt-8">
                <h2 className="text-xl font-bold mb-4">📋 All Title Options ({result.all_titles.length})</h2>
                <div className="space-y-2">
                  {result.all_titles.map((item: any, idx: number) => (
                    <div 
                      key={idx} 
                      className={`p-3 bg-gray-800 rounded flex justify-between items-center ${
                        item.is_fallback ? 'border-l-2 border-orange-500' : ''
                      }`}
                    >
                      <div>
                        <span className="text-gray-500 mr-3">{idx + 1}.</span>
                        <span>{item.title}</span>
                        {item.topic && <span className="text-gray-500 ml-2 text-sm">({item.topic})</span>}
                      </div>
                      <div className="text-right text-sm text-gray-400">
                        {item.view_count > 0 && <span>{item.view_count.toLocaleString()} views</span>}
                        {item.is_fallback && <span className="text-orange-500 ml-2">fallback</span>}
                      </div>
                    </div>
                  ))}
                </div>
              </div>
            )}
            
            {/* Error Details */}
            {result.error && (
              <div className="mt-4 p-4 bg-gray-800 rounded">
                <p className="text-sm text-gray-400">
                  <strong>Note:</strong> {result.error}
                </p>
              </div>
            )}
          </div>
        )}
      </div>
    </div>
  );
}
