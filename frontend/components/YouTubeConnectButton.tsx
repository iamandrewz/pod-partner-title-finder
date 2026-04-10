'use client';

import { useEffect, useState } from 'react';

interface YouTubeStatus {
  connected: boolean;
  email?: string;
  channels?: string[];
  error?: string;
}

export default function YouTubeConnectButton() {
  const [status, setStatus] = useState<YouTubeStatus | null>(null);

  useEffect(() => {
    fetch('/api/auth/youtube/status', { credentials: 'include' })
      .then(r => r.json())
      .then(data => setStatus(data))
      .catch(() => setStatus({ connected: false }));
  }, []);

  if (!status) return null;

  return (
    <div className="flex justify-end">
      <div className={`inline-flex items-center gap-2 rounded-full border px-3 py-1.5 text-xs font-medium ${
        status.connected
          ? 'border-emerald-400/25 bg-slate-950/85 text-emerald-300'
          : 'border-red-400/25 bg-slate-950/85 text-red-300'
      }`}>
        <span className={`h-2 w-2 rounded-full ${status.connected ? 'bg-emerald-400 animate-pulse' : 'bg-red-400'}`} />
        {status.connected ? 'YouTube Connected' : 'YouTube Disconnected'}
      </div>
    </div>
  );
}
