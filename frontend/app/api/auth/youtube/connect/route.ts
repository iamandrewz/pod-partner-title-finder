import { NextRequest, NextResponse } from 'next/server';
import {
  createOAuthState,
  getGoogleOAuthConfig,
  setOAuthStateCookie,
} from '@/lib/youtube-auth';

const YOUTUBE_SCOPES = [
  'https://www.googleapis.com/auth/youtube.readonly',
  'openid',
  'email',
];

const DEFAULT_OWNER = 'pursuepodcasting@gmail.com';

export async function GET(request: NextRequest) {
  try {
    // Try session cookie, fall back to default owner
    let ownerEmail = DEFAULT_OWNER;
    const sessionCookie = request.cookies.get('podpartner_session');
    if (sessionCookie?.value) {
      try {
        const decoded = Buffer.from(decodeURIComponent(sessionCookie.value), 'base64').toString('utf-8');
        const email = decoded.split(':')[0];
        if (email) ownerEmail = email;
      } catch {
        // Use default
      }
    }

    const { clientId, redirectUri } = getGoogleOAuthConfig();
    const encodedState = createOAuthState(ownerEmail);
    const statePayload = JSON.parse(Buffer.from(encodedState, 'base64url').toString('utf-8')) as {
      state: string;
    };

    const url = new URL('https://accounts.google.com/o/oauth2/v2/auth');
    url.searchParams.set('client_id', clientId);
    url.searchParams.set('redirect_uri', redirectUri);
    url.searchParams.set('response_type', 'code');
    url.searchParams.set('scope', YOUTUBE_SCOPES.join(' '));
    url.searchParams.set('access_type', 'offline');
    url.searchParams.set('prompt', 'consent');
    url.searchParams.set('include_granted_scopes', 'true');
    url.searchParams.set('state', statePayload.state);

    const response = NextResponse.redirect(url);
    setOAuthStateCookie(response, encodedState);
    return response;
  } catch (error) {
    const message = error instanceof Error ? error.message : 'Unable to start YouTube OAuth';
    const redirectUrl = new URL('/title-finder?youtube=error&message=' + encodeURIComponent(message), request.url);
    return NextResponse.redirect(redirectUrl);
  }
}

export async function OPTIONS() {
  return new NextResponse(null, { status: 204 });
}
