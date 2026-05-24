/** Baked at `npm run build` from web/STUDIO_VERSION via next.config env. */
export const CLIENT_STUDIO_VERSION =
  process.env.NEXT_PUBLIC_STUDIO_VERSION ?? "dev";

export function formatStudioVersionLabel(build: number, sha: string): string {
  const short = (sha || "dev").slice(0, 7);
  return short && short !== "dev" ? `v${build} · ${short}` : `v${build}`;
}
