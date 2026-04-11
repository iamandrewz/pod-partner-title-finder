import { NextRequest, NextResponse } from 'next/server';
import { getValidYouTubeTokenForOwner } from '@/lib/youtube-auth';

// Default owner email — used when no login session exists
const DEFAULT_OWNER = 'pursuepodcasting@gmail.com';

export async function GET(request: NextRequest) {
  try {
    // Try to get email from session cookie, fall back to default owner
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

    const token = await getValidYouTubeTokenForOwner(ownerEmail);

    if (!token) {
      return NextResponse.json({ connected: false });
    }

    return NextResponse.json({
      connected: true,
      email: token.email,
      channels: token.channel_name ? [token.channel_name] : [],
    });
  } catch (error) {
    const message = error instanceof Error ? error.message : 'Unable to check YouTube status';
    return NextResponse.json({ connected: false, error: message }, { status: 500 });
  }
}

export async function OPTIONS() {
  return new NextResponse(null, { status: 204 });
}
