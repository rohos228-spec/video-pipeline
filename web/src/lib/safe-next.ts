/** Только свой относительный путь. `//`, `/\\` и абсолютные URL не принимаются. */
export function safeAuthNext(raw: string | null | undefined, fallback = "/profile"): string {
  if (!raw) return fallback;
  const value = raw.trim();
  if (!value.startsWith("/")) return fallback;
  if (value.startsWith("//") || value.startsWith("/\\")) return fallback;
  if (value.includes("\\") || value.includes("://")) return fallback;
  return value;
}
