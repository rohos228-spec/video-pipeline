"use client";

import { Loader2, ShieldCheck } from "lucide-react";
import type { OperatorResolve } from "@/lib/gpt-operator";

/**
 * Вкладка «Промты GPT» для ноды с включённой «Проверкой».
 * Свой мастер-промт такой ноде не нужен: критерии — промт источника
 * (upstream) или загруженный файл агента. Показываем фактические критерии,
 * иначе селектор мастер-промта рисует одинаковый дефолт у всех проверок.
 */
export function CheckNodePromptPanel({
  resolve,
  loading,
}: {
  resolve?: OperatorResolve;
  loading?: boolean;
}) {
  const cps = resolve?.checkPromptSource === "agent" ? "agent" : "upstream";
  const sources = resolve?.sourcePrompts || [];
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
      {loading ? (
        <p className="flex items-center gap-2 text-xs text-muted-foreground">
          <Loader2 className="h-3.5 w-3.5 animate-spin" />
          Загружаю критерии…
        </p>
      ) : cps === "agent" ? (
        <div className="rounded-lg border border-white/10 bg-black/20 p-3 text-xs">
          <p className="font-medium text-muted-foreground">
            Критерии — готовый агент:
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
                className={s.ok ? "text-emerald-200/90" : "text-destructive"}
              >
                {s.ok ? "✓" : "✗"} {s.nodeKey}
                {s.variant ? (
                  <span className="font-mono"> · {s.variant}</span>
                ) : null}
                {s.chars ? ` · ${s.chars} симв.` : ""}
                {s.error ? ` · ${s.error}` : ""}
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
    </div>
  );
}
