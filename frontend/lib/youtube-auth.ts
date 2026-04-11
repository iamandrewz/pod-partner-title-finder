import { promises as fs } from 'fs';
import path from 'path';
import crypto from 'crypto';
import { cookies } from 'next/headers';
import { NextRequest, NextResponse } from 'next/server';
import { AUTH_COOKIE_CONFIG, validateSessionToken } from '@/lib/auth';

export interface StoredYouTubeToken {
  access_token: string;
  refresh_token: string;
  expiry: string;
  email: string;
  channel_name: string;
  owner_email?: string;
}

interface TokenStore {
  tokens: Record<string, StoredYouTubeToken>;
}

interface OAuthCookiePayload {
  state: string;
  ownerEmail: string;
}

const DATA_DIR = path.join(process.cwd(), 'data');
const TOKENS_FILE = path.join(DATA_DIR, 'youtube_tokens.json');
const OAUTH_STATE_COOKIE = 'youtube_oauth_state';
const GOOGLE_TOKEN_URL = 'https://oauth2.googleapis.com/token';
const GOOGLE_USERINFO_URL = 'https://www.googleapis.com/oauth2/v2/userinfo';
const YOUTUBE_CHANNELS_URL = 'https://www.googleapis.com/youtube/v3/channels';

function getRequiredEnv(name: 'GOOGLE_CLIENT_ID' | 'GOOGLE_CLIENT_SECRET' | 'GOOGLE_REDIRECT_URI'): string {
  const value = process.env[name];
  if (!value) {
    throw new Error(`Missing required environment variable: ${name}`);
  }
  return value;
}

export function getGoogleOAuthConfig() {
  return {
    clientId: getRequiredEnv('GOOGLE_CLIENT_ID'),
    clientSecret: getRequiredEnv('GOOGLE_CLIENT_SECRET'),
    redirectUri: getRequiredEnv('GOOGLE_REDIRECT_URI'),
  };
}

async function ensureTokenStore(): Promise<void> {
  await fs.mkdir(DATA_DIR, { recursive: true });
  try {
    await fs.access(TOKENS_FILE);
  } catch {
    await fs.writeFile(TOKENS_FILE, JSON.stringify({ tokens: {} }, null, 2), 'utf-8');
  }
}

export async function readTokenStore(): Promise<TokenStore> {
  await ensureTokenStore();
  const raw = await fs.readFile(TOKENS_FILE, 'utf-8');
  try {
    const parsed = JSON.parse(raw) as Partial<TokenStore>;
    return { tokens: parsed.tokens ?? {} };
  } catch {
    return { tokens: {} };
  }
}

export async function writeTokenStore(store: TokenStore): Promise<void> {
  await ensureTokenStore();
  await fs.writeFile(TOKENS_FILE, JSON.stringify(store, null, 2), 'utf-8');
}

export async function getSessionEmailFromRequest(request: NextRequest): Promise<string | null> {
  const sessionCookie = request.cookies.get(AUTH_COOKIE_CONFIG.name)?.value;
  if (!sessionCookie) return null;
  const session = validateSessionToken(sessionCookie);
  return session.valid && session.email ? session.email.toLowerCase() : null;
}

export async function getStoredTokenForOwner(ownerEmail: string): Promise<StoredYouTubeToken | null> {
  const store = await readTokenStore();
  for (const token of Object.values(store.tokens)) {
    if (token.owner_email?.toLowerCase() === ownerEmail.toLowerCase()) {
      return token;
    }
  }
  return null;
}

export async function upsertStoredToken(token: StoredYouTubeToken): Promise<void> {
  const store = await readTokenStore();
  store.tokens[token.email.toLowerCase()] = token;
  await writeTokenStore(store);
}

export async function removeStoredTokenForOwner(ownerEmail: string): Promise<boolean> {
  const store = await readTokenStore();
  const existingEntry = Object.entries(store.tokens).find(([, token]) => {
    return token.owner_email?.toLowerCase() === ownerEmail.toLowerCase();
  });
  if (!existingEntry) return false;
  delete store.tokens[existingEntry[0]];
  await writeTokenStore(store);
  return true;
}

export function createOAuthState(ownerEmail: string): string {
  const payload: OAuthCookiePayload = {
    state: crypto.randomBytes(24).toString('hex'),
    ownerEmail,
  };
  return Buffer.from(JSON.stringify(payload)).toString('base64url');
}

export function readOAuthStateCookie(): OAuthCookiePayload | null {
  const raw = cookies().get(OAUTH_STATE_COOKIE)?.value;
  if (!raw) return null;
  try {
    return JSON.parse(Buffer.from(raw, 'base64url').toString('utf-8')) as OAuthCookiePayload;
  } catch {
    return null;
  }
}

export function setOAuthStateCookie(response: NextResponse, encodedPayload: string): void {
  response.cookies.set(OAUTH_STATE_COOKIE, encodedPayload, {
    httpOnly: true,
    secure: process.env.NODE_ENV === 'production',
    sameSite: 'lax',
    maxAge: 60 * 10,
    path: '/',
  });
}

export function clearOAuthStateCookie(response: NextResponse): void {
  response.cookies.set(OAUTH_STATE_COOKIE, '', {
    httpOnly: true,
    secure: process.env.NODE_ENV === 'production',
    sameSite: 'lax',
    maxAge: 0,
    path: '/',
  });
}

export async function exchangeCodeForTokens(code: string) {
  const { clientId, clientSecret, redirectUri } = getGoogleOAuthConfig();
  const body = new URLSearchParams({
    code,
    client_id: clientId,
    client_secret: clientSecret,
    redirect_uri: redirectUri,
    grant_type: 'authorization_code',
  });

  const response = await fetch(GOOGLE_TOKEN_URL, {
    method: 'POST',
    headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
    body,
    cache: 'no-store',
  });

  if (!response.ok) {
    const errorText = await response.text();
    throw new Error(`Failed to exchange OAuth code: ${errorText}`);
  }

  return response.json() as Promise<{
    access_token: string;
    expires_in: number;
    refresh_token?: string;
    scope: string;
    token_type: string;
  }>;
}

export async function refreshYouTubeAccessToken(token: StoredYouTubeToken): Promise<StoredYouTubeToken> {
  const { clientId, clientSecret } = getGoogleOAuthConfig();
  if (!token.refresh_token) throw new Error('Missing refresh token');

  const body = new URLSearchParams({
    client_id: clientId,
    client_secret: clientSecret,
    refresh_token: token.refresh_token,
    grant_type: 'refresh_token',
  });

  const response = await fetch(GOOGLE_TOKEN_URL, {
    method: 'POST',
    headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
    body,
    cache: 'no-store',
  });

  if (!response.ok) {
    const errorText = await response.text();
    throw new Error(`Failed to refresh YouTube token: ${errorText}`);
  }

  const refreshed = await response.json() as { access_token: string; expires_in: number };

  const updatedToken: StoredYouTubeToken = {
    ...token,
    access_token: refreshed.access_token,
    expiry: new Date(Date.now() + refreshed.expires_in * 1000).toISOString(),
  };

  await upsertStoredToken(updatedToken);
  return updatedToken;
}

export async function getValidYouTubeTokenForOwner(ownerEmail: string): Promise<StoredYouTubeToken | null> {
  const token = await getStoredTokenForOwner(ownerEmail);
  if (!token) return null;

  const expiresAt = new Date(token.expiry).getTime();
  const isExpired = Number.isNaN(expiresAt) || expiresAt <= Date.now() + 60_000;
  if (!isExpired) return token;

  return refreshYouTubeAccessToken(token);
}

export async function fetchGoogleProfile(accessToken: string) {
  const [userInfoResponse, channelsResponse] = await Promise.all([
    fetch(GOOGLE_USERINFO_URL, {
      headers: { Authorization: `Bearer ${accessToken}` },
      cache: 'no-store',
    }),
    fetch(`${YOUTUBE_CHANNELS_URL}?part=snippet&mine=true`, {
      headers: { Authorization: `Bearer ${accessToken}` },
      cache: 'no-store',
    }),
  ]);

  if (!userInfoResponse.ok) throw new Error('Failed to fetch Google user profile');
  if (!channelsResponse.ok) throw new Error('Failed to fetch YouTube channel profile');

  const userInfo = await userInfoResponse.json() as { email?: string };
  const channels = await channelsResponse.json() as {
    items?: Array<{ snippet?: { title?: string } }>;
  };

  return {
    email: userInfo.email?.toLowerCase() ?? '',
    channelName: channels.items?.[0]?.snippet?.title ?? 'YouTube Channel',
  };
}
