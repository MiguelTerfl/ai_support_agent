import { BACKEND_URL, backendDown, upstreamHeaders } from "@/lib/proxy";

export const dynamic = "force-dynamic";

/** SSE stream proxy: the browser only talks to this server; the backend is not exposed to the internet. */
export async function POST(req: Request) {
  let upstream: Response;
  try {
    upstream = await fetch(`${BACKEND_URL}/api/chat`, {
      method: "POST",
      headers: upstreamHeaders(req),
      body: await req.text(),
      signal: req.signal, // if the user cancels, the request to the backend is cancelled too
      cache: "no-store",
    });
  } catch {
    return backendDown();
  }
  const headers = new Headers({
    "content-type": upstream.headers.get("content-type") ?? "application/json",
    "cache-control": "no-cache, no-transform",
    "x-accel-buffering": "no",
  });
  const retry = upstream.headers.get("retry-after");
  if (retry) headers.set("retry-after", retry);
  return new Response(upstream.body, { status: upstream.status, headers });
}
