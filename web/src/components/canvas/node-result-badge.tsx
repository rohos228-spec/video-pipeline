"use client";

import type { MouseEvent } from "react";
import { Circle, Package } from "lucide-react";
import { cn } from "@/lib/utils";
import type { NodeResultSnapshot } from "@/lib/node-result-resolver";

function parseHeroId(content: string | undefined, projectId: number | undefined, index: number): string {
  const m = content?.match(/\[ID:\s*(P\d+-HERO\d+-V\d+-[^\]]+)\]/i);
  if (m?.[1]) return m[1];
  if (projectId != null) return `P${projectId}-HERO${index + 1}`;
  return `HERO${index + 1}`;
}

export function NodeResultBadge({
  snapshot,
  nodeType,
  projectId,
  onClick,
}: {
  snapshot: NodeResultSnapshot;
  nodeType?: string;
  projectId?: number | null;
  onClick: (e: MouseEvent) => void;
}) {
  const ready = snapshot.hasResult;
  const isHero = nodeType === "hero" || nodeType === "hitl_hero";
  const heroItem = isHero ? snapshot.items.find((i) => i.previewUrl || i.content) : null;

  if (isHero && heroItem) {
    const heroIndex = snapshot.items.indexOf(heroItem);
    const heroId = parseHeroId(heroItem.content ?? undefined, projectId ?? undefined, heroIndex);
    const desc =
      heroItem.content?.replace(/\[ID:[^\]]+\]/gi, "").trim() ||
      heroItem.label ||
      "Описание персонажа";

    return (
      <>
        <div
          className={cn(
            "pointer-events-none absolute -bottom-5 left-1/2 z-10 h-5 w-px -translate-x-1/2 border-l-2 border-dashed",
            ready ? "border-emerald-500/60" : "border-muted-foreground/40",
          )}
        />
        <button
          type="button"
          onClick={onClick}
          onMouseDown={(e) => e.stopPropagation()}
          className={cn(
            "nodrag nopan absolute -bottom-[4.25rem] left-1/2 z-20 flex h-14 w-[min(280px,calc(100%+2rem))] -translate-x-1/2 overflow-hidden rounded-xl border-2 shadow-lg transition hover:scale-[1.02] hover:brightness-110",
            ready
              ? "border-emerald-500/60 bg-card/95"
              : "border-muted-foreground/40 bg-muted/90",
          )}
          title={`Персонаж · ${heroId} — нажмите для просмотра`}
        >
          <div className="flex h-full w-1/2 shrink-0 items-center justify-center border-r border-white/10 bg-black/30">
            {heroItem.previewUrl ? (
              <img
                src={heroItem.previewUrl}
                alt=""
                className="h-full w-full object-cover object-top"
              />
            ) : (
              <Package className="h-5 w-5 text-muted-foreground" />
            )}
          </div>
          <div className="flex min-w-0 flex-1 flex-col justify-center px-2 py-1 text-left">
            <span className="truncate font-mono text-[8px] font-semibold text-primary">{heroId}</span>
            <span className="line-clamp-2 text-[9px] leading-snug text-foreground/85">{desc}</span>
          </div>
        </button>
      </>
    );
  }

  const Icon = ready ? Package : Circle;

  return (
    <>
      <div
        className={cn(
          "pointer-events-none absolute -bottom-5 left-1/2 z-10 h-5 w-px -translate-x-1/2 border-l-2 border-dashed",
          ready ? "border-emerald-500/60" : "border-muted-foreground/40",
        )}
      />
      <button
        type="button"
        onClick={onClick}
        onMouseDown={(e) => e.stopPropagation()}
        className={cn(
          "nodrag nopan absolute -bottom-12 left-1/2 z-20 flex h-7 w-7 -translate-x-1/2 items-center justify-center rounded-full border-2 shadow-md transition hover:scale-110 hover:brightness-110",
          ready
            ? "border-emerald-500/70 bg-emerald-500/25 text-emerald-400"
            : "border-muted-foreground/40 bg-muted/80 text-muted-foreground",
        )}
        title={
          ready
            ? `Результат: ${snapshot.summary} — нажмите для просмотра`
            : "Результата пока нет — нажмите для деталей"
        }
      >
        <Icon className="h-3.5 w-3.5" />
      </button>
    </>
  );
}
