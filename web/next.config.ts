import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  // Локальный SPA-режим: статический экспорт в out/, чтобы FastAPI отдал
  // как статику. Server actions / image optimization не нужны для одного
  // пользователя.
  output: "export",
  trailingSlash: false,
  images: { unoptimized: true },
  // В dev API проксируется к локальному FastAPI на :8765.
  async rewrites() {
    // rewrites не применяются при `output: export`, но Next.js на dev-сервере
    // (next dev) их использует. В проде статику отдаёт сам FastAPI.
    if (process.env.NODE_ENV === "development") {
      return [
        { source: "/api/:path*", destination: "http://127.0.0.1:8765/api/:path*" },
        { source: "/ws/:path*", destination: "http://127.0.0.1:8765/ws/:path*" },
      ];
    }
    return [];
  },
};

export default nextConfig;
