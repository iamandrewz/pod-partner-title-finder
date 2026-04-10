import { NextRequest, NextResponse } from 'next/server';

const AUTH_COOKIE_NAME = 'podpartner_session';

export async function POST(request: NextRequest) {
  try {
    const body = await request.json();
    const { email, password } = body;

    if (!email || !password) {
      return NextResponse.json({ error: 'Email and password are required' }, { status: 400 });
    }

    // Call backend auth
    const BASE_URL = process.env.NEXT_PUBLIC_API_URL || 'http://localhost:5003';
    const response = await fetch(`${BASE_URL}/api/auth/login`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ email, password }),
    });

    const data = await response.json();

    if (!response.ok) {
      return NextResponse.json({ error: data.error || 'Invalid credentials' }, { status: 401 });
    }

    // Forward the session cookie from backend to the browser
    const responseHeaders = new Headers();
    const backendSetCookie = response.headers.get('set-cookie');
    if (backendSetCookie) {
      // Adapt cookie for the frontend domain
      responseHeaders.set('Set-Cookie', backendSetCookie.replace('Path=/', 'Path=/'));
    } else {
      // Fallback: set cookie ourselves
      // Fetch a new token from the backend
      const tokenResponse = await fetch(`${BASE_URL}/api/auth/login`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ email, password }),
      });
      const setCookieHeader = tokenResponse.headers.get('set-cookie');
      if (setCookieHeader) {
        responseHeaders.set('Set-Cookie', setCookieHeader);
      }
    }

    return new NextResponse(JSON.stringify({ success: true }), {
      status: 200,
      headers: {
        'Content-Type': 'application/json',
        ...Object.fromEntries(responseHeaders),
      },
    });
  } catch (error) {
    console.error('Login error:', error);
    return NextResponse.json({ error: 'An error occurred during login' }, { status: 500 });
  }
}
