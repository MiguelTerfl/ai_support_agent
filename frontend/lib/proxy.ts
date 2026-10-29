/** Utilities shared by the route handlers that proxy to the backend (internal network). */

export const BACKEND_URL = process.env.BACKEND_URL ?? "http://localhost:8000";

/**
 * Client IP for the backend's rate limiting.
 * Behind Traefik (Coolify) the proxy APPENDS the real IP at the end of X-Forwarded-For; earlier
 * entries can be forged by the client. That's why the entry is taken counting from the end
 * (TRUSTED_PROXY_HOPS = how many trusted proxies sit in front of this app).
 */
export function clientIp(req: Request): string {
  const hops = Math.max(1, Number(process.env.TRUSTED_PROXY_HOPS ?? "1"));
  const parts = (req.headers.get("x-forwarded-for") ?? "").split(",").map((p) => p.trim()).filter(Boolean);
  return parts[parts.length - hops] ?? req.headers.get("x-real-ip") ?? "unknown";
}

export function upstreamHeaders(req: Request): Record<string, string> {
  const headers: Record<string, string> = { "content-type": "application/json", "x-client-ip": clientIp(req) };
  if (process.env.INTERNAL_API_KEY) headers["x-internal-key"] = process.env.INTERNAL_API_KEY;
  return headers;
}

export function backendDown(): Response {
  return Response.json({ detail: "Could not reach the service. Please try again in a few minutes." }, { status: 502 });
}
