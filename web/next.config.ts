import type { NextConfig } from "next";
import fs from "fs";
import path from "path";
import { fileURLToPath } from "url";

const configDir = path.dirname(fileURLToPath(import.meta.url));
const repoRoot = path.join(configDir, "..");

function readStudioVersionLabel(): string {
  const file = path.join(configDir, "STUDIO_VERSION");
  try {
    const lines = fs.readFileSync(file, "utf8").trim().split(/\r?\n/);
    const build = lines[0]?.trim() || "0";
    const sha = (lines[1]?.trim() || "dev").slice(0, 7);
    return sha && sha !== "dev" ? `v${build} · ${sha}` : `v${build}`;
  } catch {
    return "dev";
  }
}

const studioVersion = readStudioVersionLabel();
const isDev = process.env.NODE_ENV === "development";

const sharedEnv = {
  NEXT_PUBLIC_STUDIO_VERSION: studioVersion,
};

const nextConfig: NextConfig = isDev
  ? {
      outputFileTracingRoot: repoRoot,
      env: sharedEnv,
      async rewrites() {
        return [
          { source: "/api/:path*", destination: "http://127.0.0.1:8765/api/:path*" },
          { source: "/ws/:path*", destination: "http://127.0.0.1:8765/ws/:path*" },
        ];
      },
    }
  : {
      outputFileTracingRoot: repoRoot,
      env: sharedEnv,
      output: "export",
      trailingSlash: false,
      images: { unoptimized: true },
    };

export default nextConfig;
