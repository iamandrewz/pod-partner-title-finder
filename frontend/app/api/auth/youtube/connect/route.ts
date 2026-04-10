import { NextResponse } from 'next/server';

export async function GET() {
  return NextResponse.json({ error: 'YouTube OAuth not configured' }, { status: 501 });
}

export async function OPTIONS() {
  return new NextResponse(null, { status: 204 });
}
