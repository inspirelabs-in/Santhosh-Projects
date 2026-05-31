import { NextRequest } from "next/server";

const BACKEND = process.env.BACKEND_URL ?? process.env.NEXT_PUBLIC_API_BASE ?? "http://localhost:8000";
const API_KEY = process.env.GRABON_API_KEY ?? process.env.NEXT_PUBLIC_API_KEY ?? "";

export async function GET(req: NextRequest) {
  if (!API_KEY) return new Response("API key not configured", { status: 500 });

  const url = new URL(req.url);
  const brandId = url.searchParams.get("brand_id");
  const target = new URL(`${BACKEND}/stream/traces`);
  if (brandId) target.searchParams.set("brand_id", brandId);

  const upstream = await fetch(target.toString(), {
    headers: { "X-API-Key": API_KEY, Accept: "text/event-stream" },
  });
  if (!upstream.ok || !upstream.body) {
    return new Response(`upstream ${upstream.status}`, { status: 502 });
  }
  return new Response(upstream.body, {
    headers: {
      "Content-Type": "text/event-stream",
      "Cache-Control": "no-cache, no-transform",
      Connection: "keep-alive",
    },
  });
}
