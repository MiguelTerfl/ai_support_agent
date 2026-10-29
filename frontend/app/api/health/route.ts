import { BACKEND_URL, upstreamHeaders } from "@/lib/proxy";

export const dynamic = "force-dynamic";

/** Always answers 200 with the status; "offline" is a normal state for the demo (the PC may be switched off). */
export async function GET(req: Request) {
  try {
    const upstream = await fetch(`${BACKEND_URL}/api/health`, {
      headers: upstreamHeaders(req),
      cache: "no-store",
      signal: AbortSignal.timeout(5000),
    });
    if (upstream.ok) return Response.json(await upstream.json(), { headers: { "cache-control": "no-store" } });
  } catch {
    /* backend down: reported below */
  }
  return Response.json({ ok: false, llm: false, db: false, model: "" }, { headers: { "cache-control": "no-store" } });
}
