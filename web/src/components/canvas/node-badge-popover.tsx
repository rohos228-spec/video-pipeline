"use client";

import { useMutation, useQueryClient } from "@tanstack/react-query";
import { HelpCircle, Loader2, MessageSquareText, Sparkles } from "lucide-react";
import { toast } from "sonner";
import { api } from "@/lib/api";
import {
  autoReviewKindForNodeType,
  readAutoReviewKinds,
} from "@/lib/control-mode";
import { getNodeSpec } from "@/lib/node-catalog";
import { defaultPromptSlots, type NodePromptSlot } from "@/lib/node-prompts";
import { nodeSupportsGptText } from "@/lib/gpt-text-steps";
import { cn } from "@/lib/utils";

/** Компактное меню ИИ-проверки при клике на верхний кружок ноды. */
export function NodeBadgePopover({
  open,
  nodeKey,
  nodeType,
  projectId,
  projectMeta,
  onClose,
  onOpenHitlReview,
  onOpenPrompt,
  onOpenGptText,
}: {
  open: boolean;
  nodeKey: string;
  nodeType: string;
  projectId: number;
  projectMeta: Record<string, unknown>;
  onClose: () => void;
  onOpenHitlReview: () => void;
  onOpenPrompt: (slot: NodePromptSlot) => void;
  onOpenGptText: () => void;
}) {
  const qc = useQueryClient();
  const kindsOn = new Set(readAutoReviewKinds(projectMeta));
  const targetKind = autoReviewKindForNodeType(nodeType);
  const label = getNodeSpec(nodeType).label;
  const slots = defaultPromptSlots(nodeType).filter((s) => s.kind !== "excel").slice(0, 4);

  const patch = useMutation({
    mutationFn: (meta: Record<string, unknown>) => api.patchProject(projectId, { meta }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["project", projectId] });
    },
    onError: (e) => toast.error(String(e)),
  });

  if (!open) return null;

  const toggleKind = (kind: string) => {
    const next = new Set(kindsOn);
    if (next.has(kind)) next.delete(kind);
    else next.add(kind);
    patch.mutate(
      { ...projectMeta, auto_review_kinds: [...next] },
      {
        onSuccess: () =>
          toast.message(
            next.has(kind) ? `GPT-проверка: ${kind}` : `Авто-апрув: ${kind}`,
          ),
      },
    );
  };

  return (
    <div
      className="node-badge-popover nodrag nopan nowheel absolute -top-[4.5rem] left-1/2 z-[110] w-[min(220px,calc(100vw-2rem))] -translate-x-1/2"
      onMouseDown={(e) => e.stopPropagation()}
    >
      <div className="rounded-xl border border-white/12 bg-[hsl(240_8%_8%/0.98)] p-2 shadow-xl shadow-black/50 backdrop-blur-md">
        <div className="mb-1.5 flex items-center justify-between gap-1 px-0.5">
          <span className="truncate text-[9px] font-semibold uppercase tracking-wider text-violet-300/90">
            ИИ · {label}
          </span>
          <button
            type="button"
            className="text-[9px] text-muted-foreground hover:text-foreground"
            onClick={onClose}
          >
            ✕
          </button>
        </div>

        {slots.length > 0 && (
          <div className="mb-1.5 flex flex-wrap gap-1">
            {slots.map((slot) => (
              <button
                key={slot.id}
                type="button"
                className="h-6 max-w-[100px] truncate rounded-md border border-white/10 bg-white/[0.04] px-1.5 text-[9px] hover:border-amber-400/40 hover:bg-amber-400/10"
                title={slot.title}
                onClick={() => {
                  onOpenPrompt(slot);
                  onClose();
                }}
              >
                {slot.title}
              </button>
            ))}
          </div>
        )}

        {nodeSupportsGptText(nodeType) && (
          <button
            type="button"
            className="mb-1.5 flex h-6 w-full items-center justify-center gap-1 rounded-md border border-violet-400/25 bg-violet-500/10 text-[9px] text-violet-200 hover:bg-violet-500/20"
            onClick={() => {
              onOpenGptText();
              onClose();
            }}
          >
            <MessageSquareText className="h-3 w-3" />
            Текст GPT
          </button>
        )}

        {targetKind && (
          <button
            type="button"
            disabled={patch.isPending}
            onClick={() => toggleKind(targetKind)}
            className={cn(
              "mb-1 flex h-7 w-full items-center justify-between rounded-md border px-2 text-[9px] transition",
              kindsOn.has(targetKind)
                ? "border-red-500/40 bg-red-500/10 text-red-200"
                : "border-white/10 hover:bg-white/5",
            )}
          >
            <span className="flex items-center gap-1">
              <Sparkles className="h-3 w-3" />
              GPT-проверка
            </span>
            <span className="text-[8px] opacity-80">
              {kindsOn.has(targetKind) ? "вкл" : "авто-апрув"}
            </span>
          </button>
        )}

        {targetKind && (
          <button
            type="button"
            className="flex h-6 w-full items-center justify-center gap-1 rounded-md border border-amber-400/30 bg-amber-500/10 text-[9px] text-amber-200 hover:bg-amber-500/20"
            onClick={() => {
              onOpenHitlReview();
              onClose();
            }}
          >
            <HelpCircle className="h-3 w-3" />
            Ручная проверка
          </button>
        )}

        {patch.isPending && (
          <span className="mt-1 flex items-center justify-center gap-1 text-[8px] text-muted-foreground">
            <Loader2 className="h-2.5 w-2.5 animate-spin" />
            …
          </span>
        )}
      </div>
    </div>
  );
}
