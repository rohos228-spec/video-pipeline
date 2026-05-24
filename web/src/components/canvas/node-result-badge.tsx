"use client";

import type { MouseEvent } from "react";
import { Circle, Package } from "lucide-react";
import { cn } from "@/lib/utils";
import type { NodeResultSnapshot } from "@/lib/node-result-resolver";

export function NodeResultBadge({
  snapshot,
  onClick,
}: {
  snapshot: NodeResultSnapshot;
  onClick: (e: MouseEvent) => void;
}) {
  const ready = snapshot.hasResult;
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
