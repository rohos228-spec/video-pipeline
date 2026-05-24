import type { NextConfig } from "next";
import path from "path";
import { fileURLToPath } from "url";

const configDir = path.dirname(fileURLToPath(import.meta.url));
const repoRoot = path.join(configDir, "..");

const isDev = process.env.NODE_ENV === "development";

const nextConfig: NextConfig = isDev
  ? {
      outputFileTracingRoot: repoRoot,
      // dev: проксируем /api и /ws на локальный FastAPI :8765.
      async rewrites() {
        return [
          { source: "/api/:path*", destination: "http://127.0.0.1:8765/api/:path*" },
          { source: "/ws/:path*", destination: "http://127.0.0.1:8765/ws/:path*" },
        ];
      },
    }
  : {
      outputFileTracingRoot: repoRoot,
      // prod: статический экспорт в out/, FastAPI сам отдаёт фронт + API
      // из одного origin.
      output: "export",
      trailingSlash: false,
      images: { unoptimized: true },
    };

export default nextConfig;
