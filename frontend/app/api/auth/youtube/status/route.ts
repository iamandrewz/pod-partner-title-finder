import { NextResponse } from 'next/server';

export async function GET() {
  // Stub: YouTube OAuth not available in standalone mode
  return NextResponse.json({ connected: false, error: 'Not configured' });
}

export async function OPTIONS() {
  return new NextResponse(null, { status: 204 });
}
