"use client";

import { useRef } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Loader2, RefreshCw, Upload } from "lucide-react";
import { toast } from "sonner";
import { api } from "@/lib/api";
import { errorMessageFromUnknown } from "@/lib/error-message";
import {
  EMIT_OPTIONS,
  OUTPUT_OPTIONS,
  ROLE_OPTIONS,
  defaultLabelForRole,
  isBranchingRole,
  type OperatorEmitKind,
  type OperatorOutputMode,
  type OperatorRole,
} from "@/lib/gpt-operator";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";

function syncNodeLabelOnCanvas(nodeKey: string, label: string, role: string) {
  const workMode =
    role === "gate" || role === "compare" || role === "extract"
      ? "review"
      : role === "assist" || role === "review" || role === "transform"
        ? role
        : "assist";
  window.dispatchEvent(
    new CustomEvent("canvas-patch-node-data", {
      detail: { nodeKey, patch: { label, role, workMode } },
    }),
  );
}

/** Временный пульт: 15 функций оператора + фактические файлы. */
export function GptOperatorMenuPanel({
  projectId,
  nodeKey,
  onOpenPrompts,
}: {
  projectId: number;
  nodeKey: string;
  onOpenPrompts?: () => void;
}) {
  const qc = useQueryClient();
  const fileRef = useRef<HTMLInputElement>(null);

  const resolve = useQuery({
    queryKey: ["gpt-operator-resolve", projectId, nodeKey],
    queryFn: () => api.resolveGptOperator(projectId, nodeKey),
    staleTime: 2000,
  });

  const patch = useMutation({
    mutationFn: (body: Record<string, unknown>) =>
      api.patchGptOperator(projectId, nodeKey, body),
    onSuccess: (res, vars) => {
      if (res.resolve) {
        qc.setQueryData(["gpt-operator-resolve", projectId, nodeKey], res.resolve);
      }
      const resolved = res.resolve;
      const role = (resolved?.role || (vars.role as string) || "assist") as OperatorRole;
      const label =
        String(resolved?.label || resolved?.config?.label || "").trim() ||
        defaultLabelForRole(role);
      syncNodeLabelOnCanvas(nodeKey, label, role);
      void qc.invalidateQueries({ queryKey: ["gpt-operator-resolve", projectId, nodeKey] });
      void qc.invalidateQueries({ queryKey: ["project", projectId] });
      window.setTimeout(() => {
        window.dispatchEvent(new CustomEvent("canvas-save-workflow"));
      }, 60);
      if (vars.role && isBranchingRole(String(vars.role))) {
        toast.message(
          "Роль с ветками: проведите две стрелки наружу и на метках выберите «Ок» и «Не ок»",
        );
      }
    },
    onError: (e) => toast.error(errorMessageFromUnknown(e)),
  });

  const upload = useMutation({
    mutationFn: (file: File) => api.uploadExcelGptFile(projectId, nodeKey, file),
    onSuccess: (res) => {
      if (res.usedAsCheckAgent) {
        toast.success(
          `Агент проверки: ${res.fileName}${res.chars ? ` · ${res.chars} симв.` : ""} — перезапустите ноду`,
        );
        if (res.resolve) {
          qc.setQueryData(["gpt-operator-resolve", projectId, nodeKey], res.resolve);
        }
      } else {
        toast.success(
          res.replacedXlsx
            ? `Excel подменён: ${res.fileName}`
            : `Файл: ${res.fileName}`,
        );
      }
      void qc.invalidateQueries({ queryKey: ["gpt-operator-resolve", projectId, nodeKey] });
      void qc.invalidateQueries({ queryKey: ["project", projectId] });
      void qc.invalidateQueries({ queryKey: ["xlsx-preview", projectId] });
      void qc.invalidateQueries({ queryKey: ["xlsx-sheets", projectId] });
      void qc.invalidateQueries({ queryKey: ["v-menu-xlsx-preview", projectId] });
      window.dispatchEvent(
        new CustomEvent("canvas-patch-node-data", {
          detail: {
            nodeKey,
            patch: {
              inputSource: "upload",
              uploadedFileName: res.fileName,
            },
          },
        }),
      );
    },
    onError: (e) => toast.error(errorMessageFromUnknown(e)),
  });

  const uploadAgent = useMutation({
    mutationFn: (file: File) => api.uploadCheckAgentFile(projectId, nodeKey, file),
    onSuccess: (res) => {
      toast.success(`Агент: ${res.fileName} · ${res.chars} симв.`);
      void qc.invalidateQueries({ queryKey: ["gpt-operator-resolve", projectId, nodeKey] });
      void qc.invalidateQueries({ queryKey: ["project", projectId] });
    },
    onError: (e) => toast.error(errorMessageFromUnknown(e)),
  });

  const clearAgent = useMutation({
    mutationFn: () => api.clearCheckAgentFile(projectId, nodeKey),
    onSuccess: () => {
      toast.success("Свой агент сброшен → builtin");
      void qc.invalidateQueries({ queryKey: ["gpt-operator-resolve", projectId, nodeKey] });
    },
    onError: (e) => toast.error(errorMessageFromUnknown(e)),
  });

  const agentFileRef = useRef<HTMLInputElement>(null);

  const data = resolve.data;
  const role = (data?.role || "assist") as OperatorRole;
  const checkMode = data?.checkMode === true;
  const checkFix = data?.checkFix !== false;
  const checkPromptSource =
    data?.checkPromptSource === "agent" ? "agent" : "upstream";
  const checkAgentStep = data?.checkAgentStep || null;
  const checkAgentFileName = data?.checkAgentFileName || null;
  const checkAgentChars = data?.checkAgentChars || 0;
  const outputMode = (data?.outputMode || "text") as OperatorOutputMode;
  const emitKinds = (data?.emitKinds?.length
    ? data.emitKinds
    : checkMode || role === "review" || role === "gate" || role === "compare"
      ? (["inputs", "reply_txt"] as OperatorEmitKind[])
      : (["result", "reply_txt"] as OperatorEmitKind[]));
  const branching = data?.branching;
  const showBranches = isBranchingRole(role) || checkMode;
  const takeFromEdges = data?.takeFromEdges !== false;
  const incomingCount = data?.incomingEdges?.length ?? 0;
  const sourcePrompts = data?.sourcePrompts || [];

  const toggleEmit = (kind: OperatorEmitKind) => {
    const next = emitKinds.includes(kind)
      ? emitKinds.filter((k) => k !== kind)
      : [...emitKinds, kind];
    // хотя бы один вид выхода
    const safe = next.length ? next : (["result"] as OperatorEmitKind[]);
    patch.mutate({ emitKinds: safe, transport: "api" });
  };

  return (
    <div className="mt-2 space-y-2 border-t border-white/10 pt-2">
      <div className="flex items-center justify-between gap-2">
        <span className="text-[9px] font-semibold uppercase tracking-widest text-sky-300/90">
          Оператор GPT
        </span>
        <button
          type="button"
          className="flex h-5 w-5 items-center justify-center rounded text-muted-foreground hover:text-foreground"
          title="Пересверить файлы"
          onClick={() => void resolve.refetch()}
        >
          {resolve.isFetching ? (
            <Loader2 className="h-3 w-3 animate-spin" />
          ) : (
            <RefreshCw className="h-3 w-3" />
          )}
        </button>
      </div>

      {data && !data.consistent ? (
        <p className="rounded-md border border-destructive/40 bg-destructive/10 px-2 py-1 text-[10px] text-destructive">
          {(data.errors || []).join("; ") || "Рассинхрон входов"}
        </p>
      ) : null}

      <p className="text-[9px] font-semibold uppercase tracking-wider text-muted-foreground">
        Проверка
      </p>
      <div className="rounded-lg border border-rose-400/25 bg-rose-500/10 px-2 py-1.5">
        <div className="flex flex-wrap gap-1">
          <button
            type="button"
            disabled={patch.isPending}
            title="Проверить файл. Критерии: промт со стрелки ИЛИ .txt агента. Формат отчёта — в настройках ноды. check_report.txt после ▶"
            onClick={() =>
              patch.mutate({
                checkMode: !checkMode,
                transport: "api",
                ...( !checkMode
                  ? { emitKinds: ["inputs", "reply_txt"], outputMode: "text" }
                  : {}),
              })
            }
            className={cn(
              "rounded-md border px-1.5 py-1 text-[9px] font-medium transition",
              checkMode
                ? "border-rose-400/70 bg-rose-500/30 text-rose-50 ring-1 ring-rose-400/40"
                : "border-white/10 text-muted-foreground hover:border-white/20",
            )}
          >
            {checkMode ? "✓ " : ""}
            Проверка
          </button>
          {checkMode ? (
            <>
              <button
                type="button"
                disabled={patch.isPending}
                title="Можно исправить файл в этой ноде"
                onClick={() => patch.mutate({ checkFix: true, transport: "api" })}
                className={cn(
                  "rounded-md border px-1.5 py-1 text-[9px] font-medium transition",
                  checkFix
                    ? "border-emerald-400/70 bg-emerald-500/25 text-emerald-50 ring-1 ring-emerald-400/40"
                    : "border-white/10 text-muted-foreground hover:border-white/20",
                )}
              >
                {checkFix ? "✓ " : ""}
                Чинить
              </button>
              <button
                type="button"
                disabled={patch.isPending}
                title="Только отчёт, файл на диске не менять"
                onClick={() => patch.mutate({ checkFix: false, transport: "api" })}
                className={cn(
                  "rounded-md border px-1.5 py-1 text-[9px] font-medium transition",
                  !checkFix
                    ? "border-amber-400/70 bg-amber-500/25 text-amber-50 ring-1 ring-amber-400/40"
                    : "border-white/10 text-muted-foreground hover:border-white/20",
                )}
              >
                {!checkFix ? "✓ " : ""}
                Только отчёт
              </button>
            </>
          ) : null}
        </div>
        {checkMode ? (
          <div className="mt-1.5 space-y-1">
            <p className="text-[9px] font-semibold uppercase tracking-wider text-rose-100/70">
              Откуда критерии отчёта
            </p>
            <div className="flex flex-wrap gap-1">
              <button
                type="button"
                disabled={patch.isPending}
                title="Берёт активный мастер-промт ноды ВЫШЕ по стрелке (Studio → Промты GPT). Не из «Загрузить файл»."
                onClick={() =>
                  patch.mutate({ checkPromptSource: "upstream", transport: "api" })
                }
                className={cn(
                  "rounded-md border px-1.5 py-1 text-[9px] font-medium transition",
                  checkPromptSource === "upstream"
                    ? "border-sky-400/70 bg-sky-500/25 text-sky-50 ring-1 ring-sky-400/40"
                    : "border-white/10 text-muted-foreground hover:border-white/20",
                )}
              >
                {checkPromptSource === "upstream" ? "✓ " : ""}
                Промт источника
              </button>
              <button
                type="button"
                disabled={patch.isPending}
                title="Свой .txt/.md (кнопка ниже или Загрузить файл) либо builtin prompts/check_operator"
                onClick={() =>
                  patch.mutate({ checkPromptSource: "agent", transport: "api" })
                }
                className={cn(
                  "rounded-md border px-1.5 py-1 text-[9px] font-medium transition",
                  checkPromptSource === "agent"
                    ? "border-violet-400/70 bg-violet-500/25 text-violet-50 ring-1 ring-violet-400/40"
                    : "border-white/10 text-muted-foreground hover:border-white/20",
                )}
              >
                {checkPromptSource === "agent" ? "✓ " : ""}
                Готовый агент
              </button>
            </div>
            {checkPromptSource === "agent" ? (
              <div className="space-y-1">
                <p className="text-[9px] leading-snug text-rose-50/80">
                  {checkAgentFileName ? (
                    <>
                      Сейчас: свой{" "}
                      <span className="font-mono text-violet-100">
                        {checkAgentFileName}
                      </span>
                      {checkAgentChars ? ` · ${checkAgentChars} симв.` : ""}. Старый
                      check_report.txt не меняется сам — нажмите ▶ на ноде.
                    </>
                  ) : (
                    <>
                      Сейчас: builtin{" "}
                      <span className="font-mono text-violet-100">
                        {checkAgentStep || "—"}
                      </span>
                      . Загрузите .txt — станет критерием отчёта.
                    </>
                  )}
                </p>
                <div className="flex flex-wrap gap-1">
                  <input
                    ref={agentFileRef}
                    type="file"
                    accept=".txt,.md,text/plain,text/markdown"
                    className="hidden"
                    onChange={(e) => {
                      const f = e.target.files?.[0];
                      e.target.value = "";
                      if (f) uploadAgent.mutate(f);
                    }}
                  />
                  <button
                    type="button"
                    disabled={uploadAgent.isPending}
                    title="Загрузить свой .txt / .md агента проверки"
                    onClick={() => agentFileRef.current?.click()}
                    className="inline-flex items-center gap-1 rounded-md border border-violet-400/40 bg-violet-500/15 px-1.5 py-1 text-[9px] font-medium text-violet-50 hover:border-violet-400/70"
                  >
                    {uploadAgent.isPending ? (
                      <Loader2 className="h-3 w-3 animate-spin" />
                    ) : (
                      <Upload className="h-3 w-3" />
                    )}
                    Загрузить .txt
                  </button>
                  {checkAgentFileName ? (
                    <button
                      type="button"
                      disabled={clearAgent.isPending}
                      onClick={() => clearAgent.mutate()}
                      className="rounded-md border border-white/15 px-1.5 py-1 text-[9px] text-muted-foreground hover:border-white/30"
                    >
                      Сбросить
                    </button>
                  ) : null}
                </div>
              </div>
            ) : (
              <>
                <p className="text-[9px] leading-snug text-rose-50/80">
                  Критерии = активный мастер-промт ноды со стрелки (Studio →
                  Промты GPT → сделать активным). «Загрузить файл» сюда — это
                  данные, не критерии. После смены промта — ▶ снова.
                </p>
                {sourcePrompts.length ? (
                  <ul className="space-y-0.5">
                    {sourcePrompts.map((s) => (
                      <li
                        key={String(s.nodeKey)}
                        className={cn(
                          "font-mono text-[9px]",
                          s.ok ? "text-emerald-100/85" : "text-rose-100/90",
                        )}
                      >
                        {s.ok ? "✓" : "✗"} {s.nodeKey}
                        {s.variant ? ` · ${s.variant}` : ""}
                        {s.source ? ` · ${s.source}` : ""}
                        {s.chars ? ` · ${s.chars} симв` : ""}
                        {s.error ? ` · ${s.error}` : ""}
                      </li>
                    ))}
                  </ul>
                ) : (
                  <p className="text-[9px] text-rose-100/80">
                    Нет входящих стрелок — подключи источник
                  </p>
                )}
              </>
            )}
          </div>
        ) : null}
      </div>

      <p className="text-[9px] font-semibold uppercase tracking-wider text-muted-foreground">
        Роль
      </p>
      <div className="grid grid-cols-2 gap-1">
        {ROLE_OPTIONS.map((opt) => (
          <button
            key={opt.value}
            type="button"
            disabled={patch.isPending}
            onClick={() =>
              patch.mutate({
                role: opt.value,
                transport: "api",
                label: defaultLabelForRole(opt.value),
              })
            }
            className={cn(
              "rounded-md border px-1.5 py-1 text-left text-[9px] font-medium leading-tight transition",
              role === opt.value
                ? "border-violet-400/70 bg-violet-500/25 text-violet-50 ring-1 ring-violet-400/40"
                : "border-white/10 bg-black/20 text-muted-foreground hover:border-white/20",
            )}
            title={opt.hint}
          >
            {role === opt.value ? "✓ " : ""}
            {opt.title}
          </button>
        ))}
      </div>

      {showBranches ? (
        <div className="rounded-lg border border-amber-400/25 bg-amber-500/10 px-2 py-1.5">
          <p className="text-[10px] font-semibold text-amber-100">
            Ветки: Ок и Не ок
          </p>
          <p className="mt-0.5 text-[9px] leading-snug text-amber-100/80">
            От этой ноды — две стрелки. На метке связи выберите «Ок» (если
            прошло) и «Не ок» (если чинить / другой путь). Подпись ноды: «Ок /
            не ок».
          </p>
          <div className="mt-1.5 flex flex-wrap gap-1">
            <span
              className={cn(
                "rounded-full border px-1.5 py-0.5 text-[9px] font-medium",
                branching?.hasPass
                  ? "border-emerald-400/40 bg-emerald-500/15 text-emerald-100"
                  : "border-white/15 bg-black/20 text-muted-foreground",
              )}
            >
              Ок →{" "}
              {branching?.hasPass
                ? branching.passEdges.map((e) => e.target).join(", ")
                : "нет стрелки"}
            </span>
            <span
              className={cn(
                "rounded-full border px-1.5 py-0.5 text-[9px] font-medium",
                branching?.hasFail
                  ? "border-rose-400/40 bg-rose-500/15 text-rose-100"
                  : "border-white/15 bg-black/20 text-muted-foreground",
              )}
            >
              Не ок →{" "}
              {branching?.hasFail
                ? branching.failEdges.map((e) => e.target).join(", ")
                : "нет стрелки"}
            </span>
            {branching?.verdict ? (
              <span className="rounded-full border border-sky-400/30 bg-sky-500/10 px-1.5 py-0.5 text-[9px] text-sky-100">
                вердикт: {branching.verdict === "pass" ? "ок" : "не ок"}
              </span>
            ) : null}
          </div>
          {(data?.warnings || []).length ? (
            <ul className="mt-1 space-y-0.5">
              {data!.warnings.map((w) => (
                <li key={w} className="text-[9px] text-amber-100/70">
                  · {w}
                </li>
              ))}
            </ul>
          ) : null}
          {data?.analysis ? (
            <div className="mt-1.5 space-y-1 rounded-md border border-white/10 bg-black/25 px-1.5 py-1.5">
              <p className="text-[9px] font-semibold uppercase tracking-wider text-muted-foreground">
                Анализ проверки
              </p>
              {data.analysis.summary ? (
                <p className="text-[10px] leading-snug text-foreground/90">
                  {data.analysis.summary}
                </p>
              ) : null}
              {(data.analysis.checks || []).length ? (
                <ul className="space-y-0.5">
                  {data.analysis.checks!.slice(0, 8).map((c) => (
                    <li
                      key={`${c.id}-${c.note || ""}`}
                      className={cn(
                        "text-[9px] leading-snug",
                        c.ok ? "text-emerald-100/85" : "text-rose-100/85",
                      )}
                    >
                      {c.ok ? "✓" : "✗"} {c.id}
                      {c.note ? ` — ${c.note}` : ""}
                    </li>
                  ))}
                </ul>
              ) : null}
              {data.analysis.fix?.instructions ? (
                <p className="text-[9px] leading-snug text-amber-100/80">
                  правки: {data.analysis.fix.instructions}
                </p>
              ) : null}
              {data.analysis.forward?.mode === "explicit" &&
              (data.analysis.forward.paths || []).length ? (
                <p className="text-[9px] text-muted-foreground">
                  дальше: {(data.analysis.forward.paths || []).join(", ")}
                </p>
              ) : null}
              {data.analysis.raw_error ? (
                <p className="text-[9px] text-rose-200/90">
                  ошибка разбора: {data.analysis.raw_error}
                </p>
              ) : null}
            </div>
          ) : showBranches ? (
            <p className="mt-1 text-[9px] text-muted-foreground">
              После Run здесь будет summary и checks из analysis.json
            </p>
          ) : null}
        </div>
      ) : null}

      <p className="text-[9px] font-semibold uppercase tracking-wider text-muted-foreground">
        Формат ответа GPT
      </p>
      <div className="flex flex-wrap gap-1">
        {OUTPUT_OPTIONS.map((opt) => (
          <button
            key={opt.value}
            type="button"
            disabled={patch.isPending}
            title={opt.hint}
            onClick={() => patch.mutate({ outputMode: opt.value, transport: "api" })}
            className={cn(
              "rounded-md border px-1.5 py-1 text-[9px] font-medium transition",
              outputMode === opt.value
                ? "border-emerald-400/70 bg-emerald-500/25 text-emerald-50 ring-1 ring-emerald-400/40"
                : "border-white/10 text-muted-foreground hover:border-white/20",
            )}
          >
            {opt.title}
          </button>
        ))}
      </div>

      <p className="text-[9px] font-semibold uppercase tracking-wider text-muted-foreground">
        Что отдаёт дальше
      </p>
      <div className="rounded-lg border border-white/10 bg-black/25 px-2 py-1.5">
        <p className="text-[9px] leading-snug text-muted-foreground">
          Какие файлы получит следующая нода по стрелке (можно несколько).
        </p>
        <div className="mt-1.5 flex flex-wrap gap-1">
          {EMIT_OPTIONS.map((opt) => {
            const on = emitKinds.includes(opt.value);
            return (
              <button
                key={opt.value}
                type="button"
                disabled={patch.isPending}
                title={opt.hint}
                onClick={() => toggleEmit(opt.value)}
                className={cn(
                  "rounded-md border px-1.5 py-1 text-[9px] font-medium transition",
                  on
                    ? "border-sky-400/70 bg-sky-500/25 text-sky-50 ring-1 ring-sky-400/40"
                    : "border-white/10 text-muted-foreground hover:border-white/20",
                )}
              >
                {on ? "✓ " : ""}
                {opt.title}
              </button>
            );
          })}
        </div>
      </div>

      <p className="text-[9px] font-semibold uppercase tracking-wider text-muted-foreground">
        Что принимает
      </p>
      <div className="rounded-lg border border-white/10 bg-black/25 px-2 py-1.5">
        <p className="text-[9px] leading-snug text-muted-foreground">
          Подвели стрелку — нода претендует на результат прошлой. Здесь решаете:
          брать или нет.
        </p>
        <div className="mt-1.5 flex flex-wrap gap-1">
          <button
            type="button"
            disabled={patch.isPending || incomingCount === 0}
            title={
              incomingCount === 0
                ? "Сначала соедините ноду входящей стрелкой"
                : "Брать файлы/результат с входящих связей"
            }
            onClick={() =>
              patch.mutate({ takeFromEdges: true, transport: "api" })
            }
            className={cn(
              "rounded-md border px-1.5 py-1 text-[9px] font-medium transition",
              takeFromEdges && incomingCount > 0
                ? "border-emerald-400/70 bg-emerald-500/25 text-emerald-50 ring-1 ring-emerald-400/40"
                : "border-white/10 text-muted-foreground hover:border-white/20",
              incomingCount === 0 && "opacity-50",
            )}
          >
            {takeFromEdges && incomingCount > 0 ? "✓ " : ""}
            От прошлых ({incomingCount})
          </button>
          <button
            type="button"
            disabled={patch.isPending}
            title="Не брать ничего со стрелок — только загрузка/свой вход"
            onClick={() =>
              patch.mutate({ takeFromEdges: false, transport: "api" })
            }
            className={cn(
              "rounded-md border px-1.5 py-1 text-[9px] font-medium transition",
              !takeFromEdges
                ? "border-amber-400/70 bg-amber-500/25 text-amber-50 ring-1 ring-amber-400/40"
                : "border-white/10 text-muted-foreground hover:border-white/20",
            )}
          >
            {!takeFromEdges ? "✓ " : ""}
            Ничего со стрелок
          </button>
        </div>
      </div>
      <div className="flex flex-wrap gap-1">
        <Button
          type="button"
          size="sm"
          variant="outline"
          className="h-7 text-[10px]"
          disabled={upload.isPending}
          onClick={() => fileRef.current?.click()}
        >
          {upload.isPending ? (
            <Loader2 className="h-3 w-3 animate-spin" />
          ) : (
            <Upload className="h-3 w-3" />
          )}
          Загрузить
        </Button>
        <Button
          type="button"
          size="sm"
          variant={data?.useSnapshot ? "secondary" : "outline"}
          className="h-7 text-[10px]"
          onClick={() =>
            patch.mutate({ useSnapshot: !data?.useSnapshot, transport: "api" })
          }
        >
          Снимок
        </Button>
        <input
          ref={fileRef}
          type="file"
          multiple
          accept=".xlsx,.xls,.txt,.md,.json,.csv,.pdf,.png,.jpg,.jpeg,.webp,.gif,.mp4,.webm,.mov"
          className="hidden"
          onChange={(e) => {
            const list = e.target.files;
            if (!list?.length) return;
            Array.from(list).forEach((f) => upload.mutate(f));
            e.target.value = "";
          }}
        />
      </div>

      <div className="rounded-lg border border-white/10 bg-black/30 px-2 py-1.5">
        <p className="mb-1 text-[9px] font-semibold uppercase tracking-wider text-muted-foreground">
          Файлы сейчас ({data?.okFileCount ?? 0})
        </p>
        {resolve.isLoading ? (
          <Loader2 className="h-3.5 w-3.5 animate-spin text-muted-foreground" />
        ) : (data?.files?.length ?? 0) === 0 ? (
          <p className="text-[10px] text-muted-foreground">Пусто — загрузите или соедините стрелкой</p>
        ) : (
          <ul className="max-h-28 space-y-1 overflow-y-auto">
            {data!.files.map((f) => (
              <li
                key={f.path}
                className={cn(
                  "flex items-center gap-2 text-[10px]",
                  f.ok ? "text-foreground/90" : "text-destructive",
                )}
              >
                {f.preview_url && f.kind === "image" ? (
                  // eslint-disable-next-line @next/next/no-img-element
                  <img
                    src={f.preview_url}
                    alt=""
                    className="h-7 w-7 rounded object-cover"
                  />
                ) : (
                  <span className="flex h-7 w-7 items-center justify-center rounded bg-white/5 font-mono text-[8px] uppercase">
                    {f.kind.slice(0, 3)}
                  </span>
                )}
                <span className="min-w-0 flex-1 truncate font-mono" title={f.path}>
                  {f.name}
                  {f.fromNode ? ` ← ${f.fromNode}` : ""}
                  {!f.ok && f.error ? ` · ${f.error}` : ""}
                </span>
              </li>
            ))}
          </ul>
        )}
      </div>

      <div className="flex flex-wrap gap-1">
        <Button
          type="button"
          size="sm"
          variant="outline"
          className="h-7 text-[10px]"
          onClick={() => onOpenPrompts?.()}
        >
          Промт + текст
        </Button>
        <Button
          type="button"
          size="sm"
          variant="outline"
          className="h-7 text-[10px]"
          onClick={() => {
            void resolve.refetch();
            toast.success(
              data?.consistent
                ? `Сверка ок · ${data.okFileCount} файл(ов)`
                : "Сверка: есть ошибки",
            );
          }}
        >
          Пересверить
        </Button>
      </div>

      {data?.lastResult && typeof data.lastResult.replyPreview === "string" ? (
        <p className="line-clamp-3 rounded-md border border-white/8 bg-white/[0.02] px-2 py-1 font-mono text-[9px] text-muted-foreground">
          {String(data.lastResult.replyPreview)}
        </p>
      ) : null}
    </div>
  );
}
