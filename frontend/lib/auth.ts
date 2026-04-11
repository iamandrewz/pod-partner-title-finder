import { NextRequest } from 'next/server';

// Auth configuration
const AUTH_COOKIE_NAME = 'podpartner_session';
const SESSION_SECRET = process.env.SESSION_SECRET || 'pursue-podcasting-secret-2024';

export function createSessionToken(email: string): string {
  const payload = `${email.toLowerCase().trim()}:${SESSION_SECRET}:${Date.now()}`;
  return Buffer.from(payload).toString('base64');
}

export function validateSessionToken(token: string): { valid: boolean; email?: string } {
  try {
    const decoded = Buffer.from(token, 'base64').toString('utf-8');
    const [email, secret, timestamp] = decoded.split(':');
    if (secret !== SESSION_SECRET) return { valid: false };
    if (Date.now() - parseInt(timestamp) > 7 * 24 * 60 * 60 * 1000) return { valid: false };
    return { valid: true, email };
  } catch {
    return { valid: false };
  }
}

export async function validateCredentials(email: string, password: string): Promise<{ success: boolean; error?: string }> {
  try {
    const res = await fetch('/api/users', { method: 'GET' });
    if (!res.ok) return { success: false, error: 'Cannot reach auth server' };
    const data = await res.json();
    const emails: string[] = data.emails || [];
    const validPassword: string = data.password || '';
    if (!emails.map(e => e.toLowerCase()).includes(email.toLowerCase())) {
      return { success: false, error: 'Email not authorized' };
    }
    if (password !== validPassword) {
      return { success: false, error: 'Invalid password' };
    }
    return { success: true };
  } catch {
    return { success: false, error: 'Auth service unavailable' };
  }
}

export const AUTH_COOKIE_CONFIG = {
  name: AUTH_COOKIE_NAME,
  httpOnly: true,
  secure: process.env.NODE_ENV === 'production',
  sameSite: 'lax' as const,
  maxAge: 60 * 60 * 24 * 7,
  path: '/',
};
