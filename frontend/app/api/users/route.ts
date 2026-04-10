import { NextResponse } from 'next/server';

export async function GET() {
  const BASE_URL = process.env.NEXT_PUBLIC_API_URL || 'http://localhost:5003';
  try {
    const response = await fetch(`${BASE_URL}/api/users`, {
      method: 'GET',
      headers: { 'Content-Type': 'application/json' },
    });
    const data = await response.json();
    return NextResponse.json(data, { status: response.status });
  } catch (error) {
    // Fallback to hardcoded values if backend unreachable
    return NextResponse.json({
      emails: [
        'pursuepodcasting@gmail.com',
        'calebsettlage@gmail.com',
        'settlagesac@gmail.com',
      ],
      password: 'PursuePodcasting!Team1',
    });
  }
}
