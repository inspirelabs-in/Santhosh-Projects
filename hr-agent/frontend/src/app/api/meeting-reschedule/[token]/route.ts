import { NextRequest, NextResponse } from "next/server";

// Server-side base URL for the backend. Candidate reschedule pages are opened
// over a public HTTPS origin (e.g. ngrok), so the browser must NOT call the
// backend directly (mixed-content + unreachable localhost). Instead the page
// hits this same-origin route, and the Next server proxies to the backend.
//
// This runs INSIDE the frontend container, so it reaches the backend over the
// Docker network by service name (`backend:8000`), NOT via host ports or
// NEXT_PUBLIC_API_BASE_URL (which points at the public frontend origin).
// Override with BACKEND_INTERNAL_URL if your backend service/port differs.
const BACKEND = process.env.BACKEND_INTERNAL_URL || "http://backend:8000";

export const dynamic = "force-dynamic";

async function relay(res: Response): Promise<NextResponse> {
  const body = await res.text();
  return new NextResponse(body, {
    status: res.status,
    headers: {
      "Content-Type": res.headers.get("content-type") || "application/json",
    },
  });
}

export async function GET(
  _req: NextRequest,
  { params }: { params: Promise<{ token: string }> },
): Promise<NextResponse> {
  const { token } = await params;
  try {
    const res = await fetch(`${BACKEND}/meeting/reschedule/${token}`, {
      headers: { Accept: "application/json" },
      cache: "no-store",
    });
    return relay(res);
  } catch {
    return NextResponse.json(
      { detail: "Could not reach the scheduling service." },
      { status: 502 },
    );
  }
}

export async function POST(
  req: NextRequest,
  { params }: { params: Promise<{ token: string }> },
): Promise<NextResponse> {
  const { token } = await params;
  const payload = await req.text();
  try {
    const res = await fetch(`${BACKEND}/meeting/reschedule/${token}`, {
      method: "POST",
      headers: { "Content-Type": "application/json", Accept: "application/json" },
      body: payload,
      cache: "no-store",
    });
    return relay(res);
  } catch {
    return NextResponse.json(
      { detail: "Could not reach the scheduling service." },
      { status: 502 },
    );
  }
}
