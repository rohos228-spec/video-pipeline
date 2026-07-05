"use client";

import { cn } from "@/lib/utils";
import type { ActiveContext } from "@/lib/prompt-builder/compatibility";
import { CRITERIA_DIMENSIONS } from "@/lib/prompt-builder/compatibility";

const DIM_LABELS: Record<string, string> = Object.fromEntries(
  CRITERIA_DIMENSIONS.map((d) => [d.id, d.label]),
);

export function ContextStrip({
  context,
  className,
}: {
  context: ActiveContext;
  className?: string;
}) {
  const entries = Object.entries(context.values).filter(([, set]) => set.size > 0);

  if (entries.length === 0) {
    return (
      <p className={cn("text-[10px] text-muted-foreground", className)}>
        Контекст пуст — выберите блоки, критерии сочетаемости появятся здесь
      </p>
    );
  }

  return (
    <div className={cn("flex flex-wrap items-center gap-2", className)}>
      <span className="text-[10px] font-medium uppercase tracking-wide text-muted-foreground">
        Сейчас в промте:
      </span>
      {entries.map(([dim, vals]) => (
        <div
          key={dim}
          className="flex items-center gap-1 rounded-md border border-border/80 bg-muted/30 px-2 py-0.5"
        >
          <span className="text-[9px] text-muted-foreground">{DIM_LABELS[dim] ?? dim}:</span>
          {[...vals].map((v) => (
            <span key={v} className="rounded bg-primary/15 px-1.5 py-0.5 text-[10px] font-medium text-primary">
              {v}
            </span>
          ))}
        </div>
      ))}
    </div>
  );
}

export function CriteriaToggles({
  enabled,
  onChange,
}: {
  enabled: Set<string>;
  onChange: (next: Set<string>) => void;
}) {
  return (
    <div className="flex flex-wrap items-center gap-2">
      <span className="text-[10px] font-medium uppercase tracking-wide text-muted-foreground">
        Критерии сочетаемости:
      </span>
      {CRITERIA_DIMENSIONS.filter((d) => d.toggleable).map((d) => {
        const on = enabled.has(d.id);
        return (
          <button
            key={d.id}
            type="button"
            title={d.description}
            onClick={() => {
              const next = new Set(enabled);
              if (on) next.delete(d.id);
              else next.add(d.id);
              onChange(next);
            }}
            className={cn(
              "rounded-md border px-2 py-0.5 text-[10px] transition-colors",
              on
                ? "border-primary/50 bg-primary/15 text-primary"
                : "border-border text-muted-foreground hover:border-border/80",
            )}
          >
            {d.label}
          </button>
        );
      })}
    </div>
  );
}
