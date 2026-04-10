'use client';

import Link from 'next/link';
import { FileText, Scissors, Headphones } from 'lucide-react';

export default function Navbar() {
  return (
    <nav className="fixed top-0 left-0 right-0 z-50 bg-slate-950/90 backdrop-blur-xl border-b border-white/5">
      <div className="max-w-6xl mx-auto px-4 sm:px-6">
        <div className="flex items-center justify-between h-16">
          <Link href="/v3" className="flex items-center space-x-2">
            <div className="w-8 h-8 rounded-lg bg-gradient-to-br from-blue-500 to-blue-600 flex items-center justify-center shadow-lg shadow-blue-500/20">
              <span className="text-white font-bold text-sm">PP</span>
            </div>
            <span className="text-white font-semibold text-lg tracking-tight">Pod Partner</span>
          </Link>
          <div className="flex items-center gap-1">
            <Link
              href="/v3"
              className="flex items-center gap-2 px-3 py-2 text-sm font-medium rounded-lg transition-all text-white bg-blue-600 hover:bg-blue-500"
            >
              <FileText className="w-4 h-4" />
              <span className="hidden sm:inline">Title Lab</span>
            </Link>
            <Link
              href="/title-finder"
              className="flex items-center gap-2 px-3 py-2 text-sm font-medium rounded-lg transition-all text-white bg-gray-700 hover:bg-gray-600"
            >
              <Scissors className="w-4 h-4" />
              <span className="hidden sm:inline">Title Finder</span>
            </Link>
          </div>
        </div>
      </div>
    </nav>
  );
}
