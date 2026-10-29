/** @type {import('next').NextConfig} */
const nextConfig = {
  output: "standalone",
  poweredByHeader: false,
  // Next's own compression can hold back the SSE stream; the proxy (Traefik/Coolify) does it instead.
  compress: false,
  reactStrictMode: true,
};

export default nextConfig;
