"use client";

import type { MouseEvent } from "react";
import { Sparkles } from "lucide-react";
import { cn } from "@/lib/utils";

/** ИИ-проверка GPT — кружок справа от ноды (не сверху). */
export function NodeAiReviewBadge({
  onClick,
  active,
}: {
  onClick: (e: MouseEvent) => void;
  active?: boolean;
}) {
  return (
    <>
      <div
        className={cn(
          "pointer-events-none absolute top-1/2 z-10 h-px w-4 -translate-y-1/2 border-t-2 border-dashed",
          active ? "border-violet-400/60" : "border-violet-400/40",
        )}
        style={{ left: "100%", marginLeft: 2 }}
      />
      <button
        type="button"
        onClick={onClick}
        onMouseDown={(e) => e.stopPropagation()}
        className={cn(
          "nodrag nopan absolute top-1/2 z-30 flex h-8 w-8 -translate-y-1/2 items-center justify-center rounded-full border-2 shadow-lg transition",
          "hover:scale-110 hover:brightness-110",
          active
            ? "border-violet-300/80 bg-gradient-to-br from-violet-500/90 to-red-500/70 text-white shadow-violet-500/30"
            : "border-violet-400/50 bg-gradient-to-br from-violet-600/80 to-red-600/60 text-white shadow-violet-500/20",
        )}
        style={{ left: "calc(100% + 1.25rem)" }}
        title="ИИ-проверка GPT — шаблоны, промт, запуск"
      >
        <Sparkles className="h-4 w-4" />
      </button>
    </>
  );
}
