/** Только свой относительный путь. `//`, `/\\`, управляющие символы и абсолютные URL не принимаются. */

const CONTROL = /[\u0000-\u001F\u007F]/;

function decodeNext(raw: string): string | null {
  let value = raw.trim();
  for (let i = 0; i < 3; i += 1) {
    if (!value.includes("%")) break;
    try {
      const decoded = decodeURIComponent(value);
      if (decoded === value) break;
      value = decoded;
    } catch {
      return null;
    }
  }
  return value;
}

export function safeAuthNext(raw: string | null | undefined, fallback = "/profile"): string {
  if (!raw) return fallback;
  const value = decodeNext(raw);
  if (value == null) return fallback;
  if (CONTROL.test(value)) return fallback;
  if (!value.startsWith("/")) return fallback;
  if (value.startsWith("//") || value.startsWith("/\\")) return fallback;
  if (value.includes("\\") || value.includes("://")) return fallback;
  const origin =
    typeof location !== "undefined" && location.origin ? location.origin : "http://127.0.0.1";
  let url: URL;
  try {
    url = new URL(value, origin);
  } catch {
    return fallback;
  }
  if (url.origin !== origin) return fallback;
  return `${url.pathname}${url.search}${url.hash}`;
}
