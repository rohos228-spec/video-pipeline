"use client";

import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Eye, Loader2, ShieldCheck, X } from "lucide-react";
import { api } from "@/lib/api";
import { errorMessageFromUnknown } from "@/lib/error-message";
import type { OperatorResolve } from "@/lib/gpt-operator";
import { Button } from "@/components/ui/button";
import { ScrollArea } from "@/components/ui/scroll-area";

/**
 * Вкладка «Промты GPT» для ноды с включённой «Проверкой».
 * Свой мастер-промт такой ноде не нужен: критерии — промт источника
 * (upstream) или загруженный файл агента. Показываем фактические критерии,
 * иначе селектор мастер-промта рисует одинаковый дефолт у всех проверок.
 *
 * Просмотр — ИНЛАЙН, без модалки: Node Studio сам портал с оверлеем,
 * вложенный Dialog ловит z-index/focus-конфликты (клики уходят в оверлей).
 */
export function CheckNodePromptPanel({
  projectId,
  nodeKey,
  resolve,
  loading,
}: {
  projectId: number;
  nodeKey: string;
  resolve?: OperatorResolve;
  loading?: boolean;
}) {
  const cps = resolve?.checkPromptSource === "agent" ? "agent" : "upstream";
  const sources = resolve?.sourcePrompts || [];
  const [viewTarget, setViewTarget] = useState<
    { kind: "prompt" } | { kind: "source"; key: string } | null
  >(null);
  const viewSourceKey = viewTarget?.kind === "source" ? viewTarget.key : null;
  const sourcePromptView = useQuery({
    queryKey: ["check-source-prompt", projectId, nodeKey, viewSourceKey],
    queryFn: () => api.getGptOperatorSourcePrompt(projectId, nodeKey, viewSourceKey!),
    enabled: viewSourceKey !== null,
    staleTime: 10_000,
  });
  const checkPromptPreview = useQuery({
    queryKey: ["check-prompt-preview", projectId, nodeKey],
    queryFn: () => api.getCheckPromptPreview(projectId, nodeKey),
    enabled: viewTarget?.kind === "prompt",
    staleTime: 10_000,
  });
  const activeView =
    viewTarget?.kind === "prompt" ? checkPromptPreview : sourcePromptView;
  const viewTitle =
    viewTarget?.kind === "prompt"
      ? "Промт проверки — финальный, как уйдёт в GPT"
      : `${viewSourceKey ?? ""}${sourcePromptView.data?.variant ? ` · ${sourcePromptView.data.variant}` : ""}`;
  return (
    <div className="flex flex-col gap-3 rounded-xl border border-rose-400/25 bg-rose-500/[0.06] p-4 text-sm">
      <h3 className="flex items-center gap-2 text-sm font-semibold text-foreground">
        <ShieldCheck className="h-4 w-4 text-rose-300" />
        Промт проверочной ноды
      </h3>
      <p className="text-xs text-muted-foreground">
        У этой ноды включён режим «Проверка» — свой мастер-промт ей{" "}
        <span className="text-foreground">не используется</span>. Одинаковый
        дефолтный промт в селекторе у всех проверок — не баг: реальные критерии
        проверки перечислены ниже. Доп. указания можно писать в
        «Сопроводительный текст» — они попадут в отчёт как указания ревьюера.
      </p>
      <Button
        type="button"
        size="sm"
        variant={viewTarget?.kind === "prompt" ? "outline" : "default"}
        className="w-fit gap-1.5"
        onClick={() =>
          setViewTarget(viewTarget?.kind === "prompt" ? null : { kind: "prompt" })
        }
      >
        <Eye className="h-3.5 w-3.5" />
        Просмотр промта проверки
      </Button>
      {loading ? (
        <p className="flex items-center gap-2 text-xs text-muted-foreground">
          <Loader2 className="h-3.5 w-3.5 animate-spin" />
          Загружаю критерии…
        </p>
      ) : cps === "agent" ? (
        <div className="rounded-lg border border-white/10 bg-black/20 p-3 text-xs">
          <p className="font-medium text-muted-foreground">
            Критерии — готовый агент (файл: Настройки → Проверка → «Просмотр»):
          </p>
          <p className="mt-1 font-mono text-foreground">
            {resolve?.checkAgentFileName
              ? `${resolve.checkAgentFileName}${
                  resolve.checkAgentChars
                    ? ` · ${resolve.checkAgentChars} симв.`
                    : ""
                }`
              : `builtin: ${resolve?.checkAgentStep || "—"}`}
          </p>
        </div>
      ) : (
        <div className="rounded-lg border border-white/10 bg-black/20 p-3 text-xs">
          <p className="font-medium text-muted-foreground">
            Критерии — мастер-промт ноды выше по стрелке:
          </p>
          <ul className="mt-2 space-y-1">
            {sources.map((s) => (
              <li
                key={String(s.nodeKey)}
                className={
                  s.ok
                    ? "flex items-center gap-1.5 text-emerald-200/90"
                    : "flex items-center gap-1.5 text-destructive"
                }
              >
                <span className="min-w-0 flex-1 truncate">
                  {s.ok ? "✓" : "✗"} {s.nodeKey}
                  {s.variant ? (
                    <span className="font-mono"> · {s.variant}</span>
                  ) : null}
                  {s.chars ? ` · ${s.chars} симв.` : ""}
                  {s.error ? ` · ${s.error}` : ""}
                </span>
                {s.ok ? (
                  <button
                    type="button"
                    title="Просмотр промта источника"
                    className="shrink-0 rounded p-0.5 text-muted-foreground transition hover:bg-white/10 hover:text-foreground"
                    onClick={() =>
                      setViewTarget(
                        viewTarget?.kind === "source" &&
                          viewTarget.key === String(s.nodeKey)
                          ? null
                          : { kind: "source", key: String(s.nodeKey) },
                      )
                    }
                  >
                    <Eye className="h-3.5 w-3.5" />
                  </button>
                ) : null}
              </li>
            ))}
            {!sources.length ? (
              <li className="text-destructive">
                Нет входящих стрелок — проверке нечего брать как критерии.
              </li>
            ) : null}
          </ul>
        </div>
      )}

      {viewTarget !== null ? (
        <div className="overflow-hidden rounded-lg border border-white/10 bg-black/30">
          <div className="flex items-center justify-between gap-2 border-b border-white/10 px-3 py-1.5">
            <p className="min-w-0 truncate font-mono text-[11px] text-foreground">
              {viewTitle}
              {activeView.data?.chars ? (
                <span className="text-muted-foreground">
                  {" "}
                  · {activeView.data.chars} симв.
                </span>
              ) : null}
            </p>
            <button
              type="button"
              title="Скрыть"
              className="shrink-0 rounded p-0.5 text-muted-foreground transition hover:bg-white/10 hover:text-foreground"
              onClick={() => setViewTarget(null)}
            >
              <X className="h-3.5 w-3.5" />
            </button>
          </div>
          <ScrollArea className="max-h-[55vh]">
            {activeView.isLoading ? (
              <div className="flex items-center gap-2 p-4 text-xs text-muted-foreground">
                <Loader2 className="h-3.5 w-3.5 animate-spin" />
                Собираю промт…
              </div>
            ) : activeView.isError ? (
              <p className="p-4 text-xs text-destructive">
                {errorMessageFromUnknown(activeView.error)}
              </p>
            ) : (
              <pre className="whitespace-pre-wrap p-4 font-mono text-[11px] leading-snug text-foreground/90">
                {activeView.data?.text || ""}
              </pre>
            )}
          </ScrollArea>
        </div>
      ) : null}
    </div>
  );
}
