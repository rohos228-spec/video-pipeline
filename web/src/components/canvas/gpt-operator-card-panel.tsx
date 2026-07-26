"use client";

import { useState } from "react";
import { ChevronDown, ChevronUp, Sparkles } from "lucide-react";
import { useQuery } from "@tanstack/react-query";
import { api } from "@/lib/api";
import { isBranchingRole, roleChip } from "@/lib/gpt-operator";
import { GptOperatorMenuPanel } from "./gpt-operator-menu";
import { cn } from "@/lib/utils";

/** Пульт оператора прямо на карточке ноды «Работа с GPT». */
export function GptOperatorCardPanel({
  projectId,
  nodeKey,
  onOpenStudio,
}: {
  projectId: number;
  nodeKey: string;
  onOpenStudio?: () => void;
}) {
  /** По умолчанию свёрнут — карточка компактная, пульт по клику. */
  const [open, setOpen] = useState(false);
  const resolve = useQuery({
    queryKey: ["gpt-operator-resolve", projectId, nodeKey],
    queryFn: () => api.resolveGptOperator(projectId, nodeKey),
    staleTime: 4000,
  });
  const data = resolve.data;

  return (
    <div className="border-t border-white/[0.06] px-3 pb-3 pt-2">
      <button
        type="button"
        className="nodrag nopan flex w-full items-center gap-2 rounded-lg border border-violet-400/25 bg-violet-500/10 px-2.5 py-2 text-left transition hover:border-violet-400/45 hover:bg-violet-500/15"
        onMouseDown={(e) => e.stopPropagation()}
        onClick={(e) => {
          e.stopPropagation();
          setOpen((v) => !v);
        }}
      >
        <Sparkles className="h-3.5 w-3.5 shrink-0 text-violet-300" />
        <span className="min-w-0 flex-1">
          <span className="block text-[11px] font-semibold text-foreground">
            Пульт оператора GPT
          </span>
          <span className="mt-0.5 block text-[9px] text-muted-foreground">
            {roleChip(data?.role)}
            {isBranchingRole(data?.role) ? " · ок/не ок" : ""}
            {" · "}файлов {data?.okFileCount ?? "…"}
            {data && !data.consistent ? " · рассинхрон" : ""}
            {data?.branching && isBranchingRole(data.role)
              ? ` · ветки ${data.branching.hasPass ? "ок" : "—"}/${data.branching.hasFail ? "неок" : "—"}`
              : ""}
            {" · "}
            {open ? "свернуть" : "открыть роли / файлы / выход"}
          </span>
        </span>
        {open ? (
          <ChevronUp className="h-3.5 w-3.5 text-muted-foreground" />
        ) : (
          <ChevronDown className="h-3.5 w-3.5 text-muted-foreground" />
        )}
      </button>

      <div
        className={cn(
          "overflow-hidden transition-[max-height,opacity]",
          open ? "mt-2 max-h-[640px] opacity-100 overflow-y-auto" : "max-h-0 opacity-0",
        )}
        onMouseDown={(e) => e.stopPropagation()}
        onClick={(e) => e.stopPropagation()}
      >
        {open ? (
          <div className="rounded-xl border border-white/10 bg-black/30 p-2">
            <GptOperatorMenuPanel
              projectId={projectId}
              nodeKey={nodeKey}
              onOpenPrompts={onOpenStudio}
            />
          </div>
        ) : null}
      </div>
    </div>
  );
}
