'use client';

import { useEffect, useState } from 'react';

interface YouTubeStatus {
  connected: boolean;
  email?: string;
  channels?: string[];
  error?: string;
}

function GoogleYouTubeIcon() {
  return (
    <svg viewBox="0 0 24 24" className="h-4 w-4" aria-hidden="true">
      <path fill="#FF0000" d="M23.5 6.2a3 3 0 0 0-2.1-2.1C19.4 3.5 12 3.5 12 3.5s-7.4 0-9.4.6A3 3 0 0 0 .5 6.2 31.3 31.3 0 0 0 0 12a31.3 31.3 0 0 0 .5 5.8 3 3 0 0 0 2.1 2.1c2 .6 9.4.6 9.4.6s7.4 0 9.4-.6a3 3 0 0 0 2.1-2.1A31.3 31.3 0 0 0 24 12a31.3 31.3 0 0 0-.5-5.8Z"/>
      <path fill="#fff" d="m9.75 15.5 6.25-3.5-6.25-3.5v7Z"/>
    </svg>
  );
}

export default function YouTubeConnectButton() {
  const [status, setStatus] = useState<YouTubeStatus | null>(null);
  const [isLoading, setIsLoading] = useState(true);

  useEffect(() => {
    let cancelled = false;

    const loadStatus = async () => {
      try {
        const response = await fetch('/api/auth/youtube/status', {
          credentials: 'include',
          cache: 'no-store',
        });

        if (!cancelled) {
          if (response.ok) {
            const payload = await response.json() as YouTubeStatus;
            setStatus(payload);
          } else {
            setStatus({ connected: false });
          }
        }
      } catch {
        if (!cancelled) {
          setStatus({ connected: false });
        }
      } finally {
        if (!cancelled) {
          setIsLoading(false);
        }
      }
    };

    loadStatus();
    return () => { cancelled = true; };
  }, []);

  if (isLoading) {
    return null;
  }

  if (status?.connected) {
    return (
      <div className="flex justify-end">
        <div className="inline-flex items-center gap-2 rounded-full border border-emerald-400/25 bg-slate-950/85 px-3 py-1.5 text-sm backdrop-blur-xl">
          <span className="h-2 w-2 rounded-full bg-emerald-400 animate-pulse" />
          <span className="font-medium text-emerald-300 text-xs">
            YouTube Connected{status.email ? ` (${status.email})` : ''}
          </span>
        </div>
      </div>
    );
  }

  return (
    <div className="flex justify-end">
      <a
        href="/api/auth/youtube/connect"
        className="inline-flex items-center gap-2 rounded-full border border-red-400/25 bg-slate-950/85 px-3 py-1.5 text-sm backdrop-blur-xl hover:border-red-400/50 transition-colors cursor-pointer"
      >
        <GoogleYouTubeIcon />
        <span className="font-medium text-red-300 text-xs">
          Connect YouTube
        </span>
      </a>
    </div>
  );
}
