import { NextRequest, NextResponse } from 'next/server';
import { removeStoredTokenForOwner } from '@/lib/youtube-auth';

const DEFAULT_OWNER = 'pursuepodcasting@gmail.com';

function getOwnerEmail(request: NextRequest): string {
  const sessionCookie = request.cookies.get('podpartner_session');
  if (sessionCookie?.value) {
    try {
      const decoded = Buffer.from(decodeURIComponent(sessionCookie.value), 'base64').toString('utf-8');
      const email = decoded.split(':')[0];
      if (email) return email;
    } catch {
      // Use default
    }
  }
  return DEFAULT_OWNER;
}

export async function POST(request: NextRequest) {
  try {
    const ownerEmail = getOwnerEmail(request);
    await removeStoredTokenForOwner(ownerEmail);
    return NextResponse.json({ success: true });
  } catch (error) {
    const message = error instanceof Error ? error.message : 'Unable to disconnect YouTube';
    return NextResponse.json({ success: false, error: message }, { status: 500 });
  }
}

export async function GET(request: NextRequest) {
  const baseUrl = process.env.GOOGLE_REDIRECT_URI
    ? new URL(process.env.GOOGLE_REDIRECT_URI).origin
    : new URL(request.url).origin;
  const redirectUrl = new URL('/title-finder?youtube=disconnected', baseUrl);

  try {
    const ownerEmail = getOwnerEmail(request);
    await removeStoredTokenForOwner(ownerEmail);
    return NextResponse.redirect(redirectUrl);
  } catch (error) {
    const message = error instanceof Error ? error.message : 'Unable to disconnect YouTube';
    redirectUrl.searchParams.set('youtube', 'error');
    redirectUrl.searchParams.set('message', message);
    return NextResponse.redirect(redirectUrl);
  }
}

export async function OPTIONS() {
  return new NextResponse(null, { status: 204 });
}
