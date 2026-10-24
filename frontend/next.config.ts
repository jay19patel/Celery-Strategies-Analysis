import type { NextConfig } from "next";

// The dashboard talks to the FastAPI backend through this proxy, so the session cookie is
// first-party and no CORS is involved. The live WebSocket connects to the backend directly.
const BACKEND_URL = process.env.BACKEND_URL ?? "http://127.0.0.1:8000";

const nextConfig: NextConfig = {
  // The dev server trusts only `localhost` by default; opening the dashboard at 127.0.0.1 would
  // otherwise block its hot-reload socket.
  allowedDevOrigins: ["127.0.0.1"],
  async rewrites() {
    return [{ source: "/api/:path*", destination: `${BACKEND_URL}/api/:path*` }];
  },
};

export default nextConfig;
