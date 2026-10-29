import { BACKEND_URL, backendDown, upstreamHeaders } from "@/lib/proxy";

export const dynamic = "force-dynamic";

export async function POST(req: Request) {
  try {
    const upstream = await fetch(`${BACKEND_URL}/api/confirm`, {
      method: "POST",
      headers: upstreamHeaders(req),
      body: await req.text(),
      cache: "no-store",
    });
    return new Response(await upstream.text(), {
      status: upstream.status,
      headers: { "content-type": "application/json", "cache-control": "no-store" },
    });
  } catch {
    return backendDown();
  }
}
