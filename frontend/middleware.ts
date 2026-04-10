import { NextResponse } from 'next/server';
import type { NextRequest } from 'next/server';

const AUTH_COOKIE_NAME = 'podpartner_session';
const SESSION_SECRET = process.env.SESSION_SECRET || 'pursue-podcasting-secret-2024';

// Routes that don't require authentication
const PUBLIC_ROUTES = [
  '/login',
  '/api/auth/login',
  '/api/auth/logout',
  '/api/users',
  '/api/health',
  '/api/v3',
  '/api/title-finder',
];

function validateSessionToken(token: string): boolean {
  try {
    const decoded = Buffer.from(token, 'base64').toString('utf-8');
    const [email, secret, timestamp] = decoded.split(':');
    if (secret !== SESSION_SECRET) return false;
    if (Date.now() - parseInt(timestamp) > 7 * 24 * 60 * 60 * 1000) return false;
    return true;
  } catch {
    return false;
  }
}

export function middleware(request: NextRequest) {
  const { pathname } = request.nextUrl;

  // Skip static files
  if (/^\/_next|^\/brand|^\/favicon/.test(pathname)) {
    return NextResponse.next();
  }

  // Public routes
  const isPublic = PUBLIC_ROUTES.some(r => pathname === r || pathname.startsWith(r + '/'));
  if (isPublic) return NextResponse.next();

  // Redirect / to /v3
  if (pathname === '/') {
    return NextResponse.redirect(new URL('/v3', request.url));
  }

  // Check session for all other routes
  const sessionCookie = request.cookies.get(AUTH_COOKIE_NAME);
  if (!sessionCookie?.value || !validateSessionToken(sessionCookie.value)) {
    // API routes get JSON 401, pages redirect to login
    if (pathname.startsWith('/api/')) {
      return NextResponse.json({ error: 'Unauthorized' }, { status: 401 });
    }
    const loginUrl = new URL('/login', request.url);
    loginUrl.searchParams.set('redirect', pathname);
    return NextResponse.redirect(loginUrl);
  }

  return NextResponse.next();
}

export const config = {
  matcher: ['/((?!_next/static|_next/image|brand|favicon.ico).*)'],
};
