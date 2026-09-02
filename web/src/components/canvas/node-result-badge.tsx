"use client";

import type { MouseEvent } from "react";
import { Circle, Check, Minus, User, X, Loader2 } from "lucide-react";
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
  nodeStatus,
  projectId,
  onClick,
}: {
  snapshot: NodeResultSnapshot;
  nodeType?: string;
  nodeStatus?: string;
  projectId?: number | null;
  onClick: (e: MouseEvent) => void;
}) {
  const isFailed = nodeStatus === "failed";
  const isRunning = nodeStatus === "running";
  const isDone = nodeStatus === "done" || nodeStatus === "waiting_hitl";
  const hasItems = snapshot.itemCount > 0;
  const isDoneEmpty = isDone && !hasItems;
  const ready = snapshot.hasResult && hasItems && !isFailed;
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
            isFailed
              ? "border-red-500/60"
              : isRunning
                ? "border-amber-500/60"
                : ready
                  ? "border-emerald-500/60"
                  : isDoneEmpty
                    ? "border-zinc-500/50"
                    : "border-muted-foreground/40",
          )}
        />
        <button
          type="button"
          onClick={onClick}
          onMouseDown={(e) => e.stopPropagation()}
          className={cn(
            "nodrag nopan absolute -bottom-[4.25rem] left-1/2 z-20 flex h-14 w-[min(280px,calc(100%+2rem))] -translate-x-1/2 overflow-hidden rounded-xl border-2 shadow-lg transition hover:scale-[1.02] hover:brightness-110",
            isFailed
              ? "border-red-500/60 bg-red-950/40"
              : isRunning
                ? "border-amber-500/60 bg-amber-950/40"
                : ready
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
              <User className="h-5 w-5 text-muted-foreground" />
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

  return (
    <>
      <div
        className={cn(
          "pointer-events-none absolute -bottom-5 left-1/2 z-10 h-5 w-px -translate-x-1/2 border-l-2 border-dashed",
          isFailed
            ? "border-red-500/60"
            : isRunning
              ? "border-amber-500/60"
              : ready
                ? "border-emerald-500/60"
                : isDoneEmpty
                  ? "border-zinc-500/50"
                  : "border-muted-foreground/40",
        )}
      />
      <button
        type="button"
        onClick={onClick}
        onMouseDown={(e) => e.stopPropagation()}
        className={cn(
          "nodrag nopan absolute -bottom-12 left-1/2 z-20 flex h-7 w-7 -translate-x-1/2 items-center justify-center rounded-full border-2 shadow-md transition hover:scale-110 hover:brightness-110",
          isFailed
            ? "border-red-500/80 bg-red-500/20 text-red-400 shadow-[0_0_12px_rgba(239,68,68,0.3)]"
            : isRunning
              ? "border-amber-500/80 bg-amber-500/20 text-amber-400 shadow-[0_0_12px_rgba(245,158,11,0.3)]"
              : ready
                ? "border-emerald-500/80 bg-emerald-500/20 text-emerald-400 shadow-[0_0_12px_rgba(16,185,129,0.3)]"
                : isDoneEmpty
                  ? "border-zinc-600/80 bg-zinc-800/80 text-zinc-300 shadow-[0_0_8px_rgba(255,255,255,0.06)] hover:border-zinc-500 hover:text-white"
                  : "border-muted-foreground/40 bg-muted/80 text-muted-foreground",
        )}
        title={
          isFailed
            ? "Ошибка на шаге — нажмите для деталей"
            : isRunning
              ? "Шаг в работе..."
              : ready
                ? `Результат: ${snapshot.summary} — нажмите для просмотра`
                : isDoneEmpty
                  ? `Шаг завершён · ${snapshot.summary || "0 объектов (не требуются)"}`
                  : "Результата пока нет — нажмите для деталей"
        }
      >
        {isFailed ? (
          <X className="h-4 w-4 stroke-[2.5]" />
        ) : isRunning ? (
          <Loader2 className="h-3.5 w-3.5 animate-spin" />
        ) : ready ? (
          <Check className="h-4 w-4 stroke-[2.5]" />
        ) : isDoneEmpty ? (
          <Minus className="h-4 w-4 stroke-[2.5]" />
        ) : (
          <Circle className="h-3.5 w-3.5" />
        )}
      </button>
    </>
  );
}
