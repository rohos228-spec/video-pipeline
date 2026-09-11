"use client";

/**
 * Мини-превью стиля/категории для панели «Помощник промпта».
 * Чистый SVG-эскиз в палитре окна генерации (dark + цвет стиля).
 * Растягивается на весь контейнер: родителю нужен relative + высота.
 */

import { GEN_STYLE_COLORS } from "@/lib/gen-assistant-styles";
import type { GenStyleArt, GenStyleDef } from "@/lib/gen-assistant-styles";

const INK = "rgba(255,255,255,0.85)";
const BG = "#15151a";
const PANEL = "#1d1d24";
const SOFT = "rgba(255,255,255,0.14)";
const FAINT = "rgba(255,255,255,0.07)";

export function GenStyleArt({
  art,
  color,
}: {
  art: GenStyleArt;
  color: GenStyleDef["color"];
}) {
  const c = GEN_STYLE_COLORS[color];
  const common = {
    preserveAspectRatio: "xMidYMid slice",
    viewBox: "0 0 120 96",
    style: { position: "absolute", inset: 0, width: "100%", height: "100%", display: "block" } as const,
  };
  if (art === "polka")
    return (
      <svg {...common}>
        <rect width="120" height="96" fill={BG} />
        <circle cx="34" cy="44" r="22" fill={c} opacity={0.85} />
        <path d="M8 84 L74 10" stroke={INK} strokeWidth={6} />
        <path d="M62 90 L114 20" stroke={INK} strokeWidth={3} />
        <circle cx="92" cy="58" r="9" fill={INK} />
        <rect x="96" y="66" width="20" height="16" fill={c} opacity={0.6} />
        <circle cx="18" cy="20" r="4" fill={INK} opacity={0.5} />
      </svg>
    );
  if (art === "pixel")
    return (
      <svg {...common}>
        <rect width="120" height="96" fill={BG} />
        {[
          [16, 16], [24, 16], [32, 16], [40, 16], [24, 24], [24, 32], [32, 32],
          [16, 40], [24, 40], [24, 48], [72, 24], [80, 24], [88, 24], [80, 32],
          [80, 40], [88, 40], [96, 32], [80, 48], [88, 56], [96, 56],
        ].map(([x, y], i) => (
          <rect key={i} x={x} y={y} width={8} height={8} fill={i % 3 === 0 ? INK : c} />
        ))}
      </svg>
    );
  if (art === "noir")
    return (
      <svg {...common}>
        <rect width="120" height="96" fill={PANEL} />
        <path d="M0 96 L54 0 L78 0 L24 96 Z" fill={SOFT} />
        <circle cx="88" cy="36" r="13" fill={INK} opacity={0.8} />
        <rect x="79" y="49" width="18" height="47" fill={INK} opacity={0.8} />
        <path d="M100 0 L120 0 L120 40 Z" fill={FAINT} />
      </svg>
    );
  if (art === "clay")
    return (
      <svg {...common}>
        <rect width="120" height="96" fill={BG} />
        <ellipse cx="36" cy="54" rx="26" ry="20" fill={c} opacity={0.9} />
        <ellipse cx="82" cy="42" rx="17" ry="24" fill={INK} opacity={0.35} />
        <circle cx="102" cy="66" r="11" fill={c} opacity={0.6} />
        <ellipse cx="14" cy="80" rx="14" ry={8} fill={INK} opacity={0.2} />
      </svg>
    );
  if (art === "knit")
    return (
      <svg {...common}>
        <rect width="120" height="96" fill={BG} />
        {[18, 38, 58, 78].map((y, i) => (
          <path
            key={i}
            d={`M4 ${y} Q 20 ${y - 10} 36 ${y} T 68 ${y} T 100 ${y} T 132 ${y}`}
            stroke={i % 2 === 0 ? c : INK}
            strokeWidth={4}
            fill="none"
            opacity={i % 2 === 0 ? 0.9 : 0.4}
          />
        ))}
      </svg>
    );
  if (art === "infographic")
    return (
      <svg {...common}>
        <rect width="120" height="96" fill={BG} />
        <rect x="14" y="52" width="14" height="30" fill={c} opacity={0.9} />
        <rect x="34" y="38" width="14" height="44" fill={c} opacity={0.65} />
        <rect x="54" y="24" width="14" height="58" fill={c} opacity={0.4} />
        <path d="M12 34 L48 18 L66 24 L100 8" stroke={INK} strokeWidth={3} fill="none" />
        <path d="M100 8 l-10 2 M100 8 l-3 9" stroke={INK} strokeWidth={3} fill="none" />
        <circle cx="96" cy="62" r="14" fill="none" stroke={INK} strokeWidth={4} />
        <path d="M96 62 L96 48 A14 14 0 0 1 108 68 Z" fill={c} />
      </svg>
    );
  if (art === "photo")
    return (
      <svg {...common}>
        <rect width="120" height="96" fill={BG} />
        <circle cx="88" cy="26" r="12" fill={c} opacity={0.8} />
        <path d="M0 96 L34 44 L56 72 L76 40 L120 96 Z" fill={INK} opacity={0.55} />
        <path d="M0 96 L26 60 L44 80 L60 58 L84 96 Z" fill={c} opacity={0.45} />
        <rect x="6" y="6" width="108" height="84" fill="none" stroke={INK} strokeWidth={2} opacity={0.4} />
      </svg>
    );
  if (art === "tutor")
    return (
      <svg {...common}>
        <rect width="120" height="96" fill={BG} />
        <rect x="52" y="10" width="60" height="42" rx={3} fill={PANEL} stroke={INK} strokeWidth={2} opacity={0.9} />
        <path d="M58 22 h34 M58 30 h26 M58 38 h30" stroke={c} strokeWidth={2.5} opacity={0.9} />
        <circle cx="30" cy="40" r="14" fill={c} opacity={0.9} />
        <path d="M22 30 l-4 -8 M38 30 l4 -8" stroke={c} strokeWidth={4} opacity={0.9} />
        <circle cx="25" cy="38" r="2.4" fill={INK} />
        <circle cx="35" cy="38" r="2.4" fill={INK} />
        <ellipse cx="30" cy="72" rx="18" ry="16" fill={c} opacity={0.75} />
        <path d="M42 62 L76 34" stroke={INK} strokeWidth={3} />
        <circle cx="42" cy="62" r="4" fill={c} />
      </svg>
    );
  // retro — плёнка с перфорацией
  return (
    <svg {...common}>
      <rect width="120" height="96" fill={PANEL} />
      {[8, 104].map((x) => (
        <g key={x}>
          {[10, 30, 50, 70].map((y) => (
            <rect key={y} x={x} y={y} width={8} height={12} rx={2} fill={INK} opacity={0.5} />
          ))}
        </g>
      ))}
      <rect x="24" y="14" width="72" height="52" fill={BG} />
      <circle cx="48" cy="36" r="10" fill={c} opacity={0.8} />
      <path d="M24 66 L44 44 L58 58 L70 46 L96 66 Z" fill={INK} opacity={0.5} />
      <rect x="24" y="72" width="72" height={6} fill={c} opacity={0.5} />
    </svg>
  );
}
