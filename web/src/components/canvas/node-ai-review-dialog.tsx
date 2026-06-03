"use client";

import { useMutation, useQueryClient } from "@tanstack/react-query";
import { Loader2, MessageSquareText, Sparkles } from "lucide-react";
import { toast } from "sonner";
import { errorMessageFromUnknown } from "@/lib/error-message";
import { api } from "@/lib/api";
import {
  AUTO_REVIEW_KINDS,
  autoReviewKindForNodeType,
  readAiNewWindowPerCheck,
  readAutoReviewKinds,
} from "@/lib/control-mode";
import { getNodeSpec } from "@/lib/node-catalog";
import { nodeSupportsGptText } from "@/lib/gpt-text-steps";
import { nodeSupportsGptVerdict, verdictStepForNode } from "@/lib/gpt-verdict-steps";
import { GptVerdictPanel } from "@/components/studio/gpt-verdict-panel";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { cn } from "@/lib/utils";

export function NodeAiReviewDialog({
  open,
  onOpenChange,
  projectId,
  nodeKey,
  nodeType,
  projectMeta,
  onOpenPrompt,
  onOpenGptText,
}: {
  open: boolean;
  onOpenChange: (o: boolean) => void;
  projectId: number;
  nodeKey: string;
  nodeType: string;
  projectMeta: Record<string, unknown>;
  onOpenPrompt: (nodeKey: string, nodeType: string) => void;
  onOpenGptText: (nodeKey: string, nodeType: string) => void;
}) {
  const qc = useQueryClient();
  const label = getNodeSpec(nodeType).label;
  const stepCode = verdictStepForNode(nodeType);
  const showVerdict = stepCode && nodeSupportsGptVerdict(nodeType);
  const kindsOn = new Set(readAutoReviewKinds(projectMeta));
  const newWindow = readAiNewWindowPerCheck(projectMeta);
  const targetKind = autoReviewKindForNodeType(nodeType);

  const patch = useMutation({
    mutationFn: (meta: Record<string, unknown>) => api.patchProject(projectId, { meta }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["project", projectId] });
    },
    onError: (e) => toast.error(errorMessageFromUnknown(e)),
  });

  const persist = (patchMeta: Record<string, unknown>) => {
    patch.mutate({ ...projectMeta, ...patchMeta });
  };

  const toggleKind = (kind: string) => {
    const next = new Set(kindsOn);
    if (next.has(kind)) next.delete(kind);
    else next.add(kind);
    persist({ auto_review_kinds: [...next] });
    toast.message(next.has(kind) ? `GPT-проверка: ${kind}` : `Авто-апрув: ${kind}`);
  };

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-h-[92vh] max-w-2xl overflow-y-auto">
        <DialogHeader>
          <DialogTitle className="flex items-center gap-2">
            <Sparkles className="h-4 w-4 text-violet-400" />
            ИИ-проверка — {label}
          </DialogTitle>
          <DialogDescription>
            Шаблоны проверки GPT, текст промта и настройки авто-одобрения для этой ноды.
          </DialogDescription>
        </DialogHeader>

        {showVerdict ? (
          <GptVerdictPanel
            projectId={projectId}
            stepCode={stepCode}
            projectMeta={projectMeta}
            onPersistMeta={(meta) => patch.mutate(meta)}
          />
        ) : (
          <p className="text-sm text-muted-foreground">
            Для этой ноды нет GPT-проверки «Вердикт».
          </p>
        )}

        <Section title="Промты ноды">
          <div className="flex flex-wrap gap-2">
            <Button
              type="button"
              size="sm"
              variant="outline"
              className="text-xs"
              onClick={() => onOpenPrompt(nodeKey, nodeType)}
            >
              Открыть промты…
            </Button>
            {nodeSupportsGptText(nodeType) && (
              <Button
                type="button"
                size="sm"
                variant="outline"
                className="gap-1.5 text-xs"
                onClick={() => onOpenGptText(nodeKey, nodeType)}
              >
                <MessageSquareText className="h-3.5 w-3.5" />
                Текст для GPT
              </Button>
            )}
          </div>
        </Section>

        <Section title="GPT-проверка шага">
          <p className="mb-2 text-[10px] text-muted-foreground">
            Включено — GPT проверяет перед авто-одобрением. Выключено — шаг одобряется без проверки.
          </p>
          <div className="flex flex-col gap-1">
            {(targetKind
              ? AUTO_REVIEW_KINDS.filter((k) => k.kind === targetKind)
              : AUTO_REVIEW_KINDS
            ).map(({ kind, label: kindLabel }) => {
              const on = kindsOn.has(kind);
              return (
                <button
                  key={kind}
                  type="button"
                  disabled={patch.isPending}
                  onClick={() => toggleKind(kind)}
                  className={cn(
                    "flex items-center justify-between rounded-lg border px-2.5 py-1.5 text-left text-xs transition",
                    on
                      ? "border-red-500/40 bg-red-500/10 text-red-200"
                      : "border-border/60 hover:bg-accent/30",
                  )}
                >
                  <span>{kindLabel}</span>
                  <span className="text-[10px] text-muted-foreground">
                    {on ? "GPT проверяет" : "авто-апрув"}
                  </span>
                </button>
              );
            })}
          </div>
        </Section>

        <Section title="Окно браузера">
          <button
            type="button"
            disabled={patch.isPending}
            onClick={() => {
              persist({ ai_new_window_per_check: !newWindow });
              toast.success(!newWindow ? "Новое окно на проверку" : "Одно окно на все проверки");
            }}
            className={cn(
              "flex w-full items-start justify-between gap-2 rounded-lg border px-2.5 py-2 text-left",
              newWindow ? "border-red-500/40 bg-red-500/10" : "border-border/60",
            )}
          >
            <span className="flex flex-col">
              <span className="text-xs font-medium">Каждая проверка — новое окно</span>
              <span className="text-[10px] text-muted-foreground">Отдельная CDP-сессия для HITL</span>
            </span>
            <span
              className={cn(
                "mt-0.5 h-5 w-9 shrink-0 rounded-full p-0.5",
                newWindow ? "bg-red-600" : "bg-muted",
              )}
            >
              <span
                className={cn(
                  "block h-4 w-4 rounded-full bg-white shadow transition-transform",
                  newWindow && "translate-x-4",
                )}
              />
            </span>
          </button>
        </Section>

        <DialogFooter>
          <Button variant="ghost" size="sm" onClick={() => onOpenChange(false)}>
            Закрыть
          </Button>
          {patch.isPending && (
            <span className="flex items-center gap-1 text-[10px] text-muted-foreground">
              <Loader2 className="h-3 w-3 animate-spin" />
              Сохранение…
            </span>
          )}
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <div className="flex flex-col gap-1.5 rounded-lg border border-white/8 bg-white/[0.02] p-2.5">
      <span className="text-[10px] font-semibold uppercase tracking-wider text-muted-foreground">
        {title}
      </span>
      {children}
    </div>
  );
}
