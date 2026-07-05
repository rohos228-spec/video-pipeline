"use client";

import { AlertTriangle, CheckCircle2, Info } from "lucide-react";
import { cn } from "@/lib/utils";
import type { ComposeResult } from "@/lib/prompt-builder/types";
import { BlockKindBadge } from "./block-kind-badge";

export function PromptPreviewPanel({
  result,
  className,
}: {
  result: ComposeResult;
  className?: string;
}) {
  return (
    <div className={cn("flex h-full flex-col", className)}>
      <div className="flex items-center justify-between border-b border-border px-3 py-2">
        <span className="text-xs font-medium text-foreground">Собранный промт</span>
        <span className="font-mono text-[10px] text-muted-foreground">{result.charCount} симв.</span>
      </div>

      {result.warnings.length > 0 && (
        <div className="space-y-1 border-b border-border px-3 py-2">
          {result.warnings.map((w, i) => (
            <div
              key={i}
              className={cn(
                "flex items-start gap-1.5 text-[10px]",
                w.level === "error" && "text-destructive",
                w.level === "warn" && "text-[hsl(var(--warning))]",
                w.level === "info" && "text-muted-foreground",
              )}
            >
              {w.level === "warn" ? (
                <AlertTriangle className="mt-0.5 h-3 w-3 shrink-0" />
              ) : w.level === "error" ? (
                <AlertTriangle className="mt-0.5 h-3 w-3 shrink-0" />
              ) : (
                <Info className="mt-0.5 h-3 w-3 shrink-0" />
              )}
              {w.message}
            </div>
          ))}
        </div>
      )}

      <div className="min-h-0 flex-1 overflow-y-auto p-3">
        <div className="space-y-3">
          {result.sections.map((sec, i) => (
            <section
              key={i}
              className="rounded-lg border border-border/80 bg-card/50 p-2.5"
            >
              <div className="mb-1.5 flex items-center gap-2">
                <BlockKindBadge kind={sec.kind} compact />
                <span className="text-[11px] font-medium">{sec.label}</span>
              </div>
              <p className="whitespace-pre-wrap text-[11px] leading-relaxed text-muted-foreground">
                {sec.body}
              </p>
            </section>
          ))}
        </div>
      </div>

      {result.warnings.length === 0 && (
        <div className="flex items-center gap-1.5 border-t border-border px-3 py-2 text-[10px] text-[hsl(var(--success))]">
          <CheckCircle2 className="h-3 w-3" />
          Все блоки совместимы с шагом
        </div>
      )}
    </div>
  );
}
