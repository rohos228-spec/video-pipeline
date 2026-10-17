"use client";

/** Локальные упрощённые логотипы провайдеров для фона строк в меню модели (без внешних ссылок). */

type Brand = { id: string; name: string };

export function brandForModel(id: string, label: string): Brand {
  const s = `${id} ${label}`.toLowerCase();
  if (s.includes("claude")) return { id: "anthropic", name: "Anthropic" };
  if (s.includes("gpt")) return { id: "openai", name: "OpenAI" };
  if (s.includes("veo")) return { id: "google", name: "Google Veo" };
  if (s.includes("gemini") || s.includes("nano-banana")) return { id: "google", name: "Google" };
  if (s.includes("deepseek")) return { id: "deepseek", name: "DeepSeek" };
  if (s.includes("grok")) return { id: "xai", name: "xAI" };
  if (s.includes("flux")) return { id: "bfl", name: "Black Forest" };
  if (s.includes("seed")) return { id: "bytedance", name: "ByteDance" };
  if (s.includes("qwen") || s.includes("wan") || s.includes("z-image")) return { id: "alibaba", name: "Alibaba" };
  if (s.includes("kling")) return { id: "kling", name: "Kling" };
  if (s.includes("hailuo") || s.includes("minimax")) return { id: "minimax", name: "MiniMax" };
  if (s.includes("pixverse")) return { id: "pixverse", name: "PixVerse" };
  if (s.includes("topaz")) return { id: "topaz", name: "Topaz" };
  if (s.includes("eleven")) return { id: "elevenlabs", name: "ElevenLabs" };
  return { id: "generic", name: "" };
}

export function versionForModel(id: string, label: string): string {
  const fromLabel = label.match(/\b[vV]?(\d+(?:\.\d+)?)\b/);
  if (fromLabel) return fromLabel[1];
  const fromId = id.replace(/(\d)-(\d)/g, "$1.$2").match(/(\d+(?:\.\d+)?)/);
  return fromId ? fromId[1] : "";
}

export function BrandLogo({ brand, className }: { brand: string; className?: string }) {
  const p = { fill: "none", stroke: "currentColor", strokeWidth: 3.2, strokeLinecap: "round" as const, strokeLinejoin: "round" as const };
  switch (brand) {
    case "openai":
      return (
        <svg viewBox="0 0 64 64" className={className} aria-hidden>
          {[0, 60, 120, 180, 240, 300].map((r) => (
            <path key={r} {...p} transform={`rotate(${r} 32 32)`} d="M32 10c7 0 12 5 12 12v14l-12 7" />
          ))}
        </svg>
      );
    case "anthropic":
      return (
        <svg viewBox="0 0 64 64" className={className} aria-hidden>
          <path fill="currentColor" d="M23 12h8l15 40h-8.4l-3.3-9H19.7l-3.3 9H8zm-.8 24h10.6L27.5 21z" />
          <path fill="currentColor" d="M37 12h8l15 40h-8z" />
        </svg>
      );
    case "google":
      return (
        <svg viewBox="0 0 64 64" className={className} aria-hidden>
          <path fill="currentColor" d="M32 4c2 15 13 26 28 28-15 2-26 13-28 28-2-15-13-26-28-28C19 30 30 19 32 4z" />
        </svg>
      );
    case "xai":
      return (
        <svg viewBox="0 0 64 64" className={className} aria-hidden>
          <path {...p} strokeWidth={5} d="M12 10l40 44M52 10L12 54" />
        </svg>
      );
    case "deepseek":
      return (
        <svg viewBox="0 0 64 64" className={className} aria-hidden>
          <path {...p} d="M6 34c6 12 22 18 36 12 8-3 13-10 16-18-6 3-10 2-14-2-5-6-14-8-22-3C14 27 10 31 6 34z" />
          <circle cx="40" cy="30" r="2.6" fill="currentColor" />
        </svg>
      );
    case "elevenlabs":
      return (
        <svg viewBox="0 0 64 64" className={className} aria-hidden>
          <rect x="20" y="10" width="7" height="44" rx="2" fill="currentColor" />
          <rect x="37" y="10" width="7" height="44" rx="2" fill="currentColor" />
        </svg>
      );
    case "bytedance":
      return (
        <svg viewBox="0 0 64 64" className={className} aria-hidden>
          {[10, 24, 38, 50].map((x, i) => (
            <rect key={x} x={x} y={[22, 12, 26, 16][i]} width="7" height={[30, 40, 26, 36][i]} rx="2" fill="currentColor" />
          ))}
        </svg>
      );
    default: {
      const ch = brand === "generic" ? "AI" : brand.slice(0, 1).toUpperCase();
      return (
        <svg viewBox="0 0 64 64" className={className} aria-hidden>
          <path {...p} d="M32 5 55.4 18.5v27L32 59 8.6 45.5v-27z" />
          <text x="32" y="41" textAnchor="middle" fontSize="24" fontWeight="700" fill="currentColor" fontFamily="Rubik, sans-serif">
            {ch}
          </text>
        </svg>
      );
    }
  }
}

/** Фоновая декорация справа: логотип + крупная версия + название. */
export function ModelBrandBackdrop({ id, label }: { id: string; label: string }) {
  const brand = brandForModel(id, label);
  const ver = versionForModel(id, label);
  const name = label.replace(/\s*\(.*\)\s*$/, "").replace(ver, "").replace(/\s+/g, " ").trim();
  return (
    <div
      aria-hidden
      data-vp-model-brand={brand.id}
      className="pointer-events-none absolute inset-y-0 right-0 flex select-none items-center gap-2 pr-3 text-[#2fd5b5] [mask-image:linear-gradient(to_right,transparent,black_35%)]"
    >
      <div className="flex flex-col items-end leading-none">
        {ver ? <span className="text-[44px] font-bold tabular-nums tracking-tight opacity-[0.16]">{ver}</span> : null}
        <span className="mt-0.5 max-w-[170px] truncate text-[11px] font-semibold uppercase tracking-[0.18em] opacity-[0.28]">
          {brand.name ? `${brand.name} · ${name}` : name}
        </span>
      </div>
      <BrandLogo brand={brand.id} className="h-[62px] w-[62px] opacity-[0.2]" />
    </div>
  );
}
