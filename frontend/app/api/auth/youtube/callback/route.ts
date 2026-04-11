import { NextRequest, NextResponse } from 'next/server';
import {
  clearOAuthStateCookie,
  exchangeCodeForTokens,
  fetchGoogleProfile,
  readOAuthStateCookie,
  upsertStoredToken,
} from '@/lib/youtube-auth';

export async function GET(request: NextRequest) {
  const callbackUrl = new URL(request.url);
  // Use the GOOGLE_REDIRECT_URI's origin as the base, falling back to request origin
  const baseUrl = process.env.GOOGLE_REDIRECT_URI
    ? new URL(process.env.GOOGLE_REDIRECT_URI).origin
    : new URL(request.url).origin;
  const redirectUrl = new URL('/title-finder', baseUrl);

  try {
    const error = callbackUrl.searchParams.get('error');
    if (error) {
      redirectUrl.searchParams.set('youtube', 'error');
      redirectUrl.searchParams.set('message', error);
      const response = NextResponse.redirect(redirectUrl);
      clearOAuthStateCookie(response);
      return response;
    }

    const code = callbackUrl.searchParams.get('code');
    const state = callbackUrl.searchParams.get('state');
    const cookiePayload = readOAuthStateCookie();

    if (!code || !state || !cookiePayload || cookiePayload.state !== state) {
      throw new Error('Invalid OAuth state');
    }

    const tokens = await exchangeCodeForTokens(code);
    const profile = await fetchGoogleProfile(tokens.access_token);

    if (!profile.email) {
      throw new Error('Unable to determine Google account email');
    }

    await upsertStoredToken({
      access_token: tokens.access_token,
      refresh_token: tokens.refresh_token ?? '',
      expiry: new Date(Date.now() + tokens.expires_in * 1000).toISOString(),
      email: profile.email,
      channel_name: profile.channelName,
      owner_email: cookiePayload.ownerEmail,
    });

    redirectUrl.searchParams.set('youtube', 'connected');
    redirectUrl.searchParams.set('email', profile.email);

    const response = NextResponse.redirect(redirectUrl);
    clearOAuthStateCookie(response);
    return response;
  } catch (error) {
    const message = error instanceof Error ? error.message : 'YouTube connection failed';
    redirectUrl.searchParams.set('youtube', 'error');
    redirectUrl.searchParams.set('message', message);
    const response = NextResponse.redirect(redirectUrl);
    clearOAuthStateCookie(response);
    return response;
  }
}

export async function OPTIONS() {
  return new NextResponse(null, { status: 204 });
}
