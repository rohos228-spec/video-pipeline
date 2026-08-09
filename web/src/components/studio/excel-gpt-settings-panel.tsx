"use client";

import { useEffect, useRef, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Eye, Loader2, RefreshCw, Upload } from "lucide-react";
import { toast } from "sonner";
import { api } from "@/lib/api";
import { errorMessageFromUnknown } from "@/lib/error-message";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { ScrollArea } from "@/components/ui/scroll-area";
import { cn } from "@/lib/utils";
import type { ExcelGptNodeConfig } from "@/lib/excel-gpt-config";
import {
  EMIT_OPTIONS,
  OUTPUT_OPTIONS,
  ROLE_OPTIONS,
  type OperatorEmitKind,
  type OperatorOutputMode,
  type OperatorRole,
} from "@/lib/gpt-operator";

export function ExcelGptSettingsPanel({
  projectId,
  nodeKey,
  config,
  onConfigChange,
}: {
  projectId: number;
  nodeKey: string;
  config: ExcelGptNodeConfig;
  onConfigChange: (patch: Partial<ExcelGptNodeConfig>) => void;
}) {
  const qc = useQueryClient();
  const resolve = useQuery({
    queryKey: ["gpt-operator-resolve", projectId, nodeKey],
    queryFn: () => api.resolveGptOperator(projectId, nodeKey),
  });

  const patch = useMutation({
    mutationFn: (body: Record<string, unknown>) =>
      api.patchGptOperator(projectId, nodeKey, body),
    onSuccess: (res) => {
      if (res.resolve) {
        qc.setQueryData(["gpt-operator-resolve", projectId, nodeKey], res.resolve);
      }
      const cfg = res.resolve?.config || {};
      onConfigChange({
        workMode: (cfg.workMode as ExcelGptNodeConfig["workMode"]) || undefined,
        inputSource: (cfg.inputSource as ExcelGptNodeConfig["inputSource"]) || undefined,
        uploadedFileName: Array.isArray(cfg.uploadedFileNames)
          ? String(cfg.uploadedFileNames[0] || "")
          : undefined,
      });
      void qc.invalidateQueries({ queryKey: ["gpt-operator-resolve", projectId, nodeKey] });
      void qc.invalidateQueries({ queryKey: ["project", projectId] });
      window.setTimeout(() => {
        window.dispatchEvent(new CustomEvent("canvas-save-workflow"));
      }, 60);
    },
    onError: (e) => toast.error(errorMessageFromUnknown(e)),
  });

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
  const sourcePrompts = data?.sourcePrompts || [];

  const [formatDraft, setFormatDraft] = useState("");
  const [formatDirty, setFormatDirty] = useState(false);
  useEffect(() => {
    if (!data || formatDirty) return;
    setFormatDraft(
      String(data.checkReportFormat || data.checkReportFormatDefault || ""),
    );
  }, [
    data,
    formatDirty,
    data?.checkReportFormat,
    data?.checkReportFormatDefault,
  ]);

  const agentFileRef = useRef<HTMLInputElement>(null);
  // Что смотрим в диалоге: финальный промт проверки / файл агента / промт источника.
  const [viewTarget, setViewTarget] = useState<
    { kind: "prompt" } | { kind: "agent" } | { kind: "source"; key: string } | null
  >(null);
  const agentFileView = useQuery({
    queryKey: ["check-agent-file", projectId, nodeKey],
    queryFn: () => api.getCheckAgentFile(projectId, nodeKey),
    enabled: viewTarget?.kind === "agent",
    staleTime: 10_000,
  });
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
    viewTarget?.kind === "prompt"
      ? checkPromptPreview
      : viewTarget?.kind === "agent"
        ? agentFileView
        : sourcePromptView;
  const viewOpen = viewTarget !== null;
  const viewLoading = activeView.isLoading;
  const viewError = activeView.isError ? activeView.error : null;
  const viewText = activeView.data?.text;
  const viewChars = activeView.data?.chars;
  const viewTitle =
    viewTarget?.kind === "prompt"
      ? "Промт проверки — финальный, как уйдёт в GPT"
      : viewTarget?.kind === "source"
        ? `${viewTarget.key}${sourcePromptView.data?.variant ? ` · ${sourcePromptView.data.variant}` : ""}`
        : agentFileView.data?.fileName || checkAgentFileName || "Агент проверки";
  const uploadAgent = useMutation({
    mutationFn: (file: File) => api.uploadCheckAgentFile(projectId, nodeKey, file),
    onSuccess: (res) => {
      toast.success(`Агент: ${res.fileName}`);
      if (res.resolve) {
        qc.setQueryData(["gpt-operator-resolve", projectId, nodeKey], res.resolve);
      }
    },
    onError: (e) => toast.error(errorMessageFromUnknown(e)),
  });
  const clearAgent = useMutation({
    mutationFn: () => api.clearCheckAgentFile(projectId, nodeKey),
    onSuccess: (res) => {
      toast.success("Свой агент сброшен");
      if (res.resolve) {
        qc.setQueryData(["gpt-operator-resolve", projectId, nodeKey], res.resolve);
      }
    },
    onError: (e) => toast.error(errorMessageFromUnknown(e)),
  });

  const toggleEmit = (kind: OperatorEmitKind) => {
    const next = emitKinds.includes(kind)
      ? emitKinds.filter((k) => k !== kind)
      : [...emitKinds, kind];
    const safe = next.length ? next : (["result"] as OperatorEmitKind[]);
    patch.mutate({ emitKinds: safe, transport: "api" });
  };

  return (
    <div className="flex flex-col gap-4">
      <section className="rounded-xl border border-white/10 bg-white/[0.02] p-4">
        <h3 className="text-sm font-semibold text-foreground">Название ноды</h3>
        <Input
          className="mt-2"
          value={config.label ?? ""}
          placeholder="Работа с GPT"
          onChange={(e) => onConfigChange({ label: e.target.value })}
          onBlur={() => {
            const label = (config.label ?? "").trim();
            if (label) void patch.mutateAsync({ label });
          }}
        />
      </section>

      <section className="rounded-xl border border-rose-400/25 bg-rose-500/[0.07] p-4">
        <h3 className="text-sm font-semibold text-foreground">Проверка</h3>
        <p className="mt-1 text-xs text-muted-foreground">
          Отчёт check_report.txt пишется после ▶. Критерии — в режиме ниже.
          Формат ответа модели редактируется в блоке «Формат ответа модели».
        </p>
        <div className="mt-3 flex flex-wrap gap-2">
          <Button
            type="button"
            size="sm"
            variant={checkMode ? "default" : "outline"}
            className={cn(
              checkMode && "ring-2 ring-rose-400/50 ring-offset-1 ring-offset-background",
            )}
            onClick={() =>
              patch.mutate({
                checkMode: !checkMode,
                transport: "api",
                ...(!checkMode
                  ? { emitKinds: ["inputs", "reply_txt"], outputMode: "text" }
                  : {}),
              })
            }
          >
            {checkMode ? "✓ " : ""}
            Проверка
          </Button>
          {checkMode ? (
            <>
              <Button
                type="button"
                size="sm"
                variant={checkFix ? "default" : "outline"}
                onClick={() => patch.mutate({ checkFix: true, transport: "api" })}
              >
                Чинить
              </Button>
              <Button
                type="button"
                size="sm"
                variant={!checkFix ? "default" : "outline"}
                onClick={() => patch.mutate({ checkFix: false, transport: "api" })}
              >
                Только отчёт
              </Button>
            </>
          ) : null}
        </div>
        {checkMode ? (
          <Button
            type="button"
            size="sm"
            variant="default"
            className="mt-3 w-fit gap-1.5"
            onClick={() => setViewTarget({ kind: "prompt" })}
          >
            <Eye className="h-3.5 w-3.5" />
            Просмотр промта проверки
          </Button>
        ) : null}
        {checkMode ? (
          <div className="mt-3 space-y-2">
            <p className="text-[11px] font-medium text-muted-foreground">
              Откуда критерии отчёта
            </p>
            <div className="flex flex-wrap gap-2">
              <Button
                type="button"
                size="sm"
                variant={checkPromptSource === "upstream" ? "default" : "outline"}
                title="Активный мастер-промт ноды выше по стрелке (Studio → Промты GPT)"
                onClick={() =>
                  patch.mutate({ checkPromptSource: "upstream", transport: "api" })
                }
              >
                {checkPromptSource === "upstream" ? "✓ " : ""}
                Промт источника
              </Button>
              <Button
                type="button"
                size="sm"
                variant={checkPromptSource === "agent" ? "default" : "outline"}
                title="Свой .txt/.md или builtin check_operator"
                onClick={() =>
                  patch.mutate({ checkPromptSource: "agent", transport: "api" })
                }
              >
                {checkPromptSource === "agent" ? "✓ " : ""}
                Готовый агент
              </Button>
            </div>
            {checkPromptSource === "agent" ? (
              <div className="space-y-2">
                <p className="text-[11px] text-muted-foreground">
                  {checkAgentFileName ? (
                    <>
                      Свой файл:{" "}
                      <span className="font-mono text-foreground">
                        {checkAgentFileName}
                      </span>
                      {checkAgentChars ? ` · ${checkAgentChars} симв.` : ""}. После
                      загрузки перезапустите ноду ▶ — иначе виден старый отчёт.
                    </>
                  ) : (
                    <>
                      Builtin{" "}
                      <span className="font-mono text-foreground">
                        {checkAgentStep || "—"}
                      </span>
                      . Загрузите .txt/.md — или через «Загрузить файл» при
                      включённой Проверке.
                    </>
                  )}
                </p>
                <div className="flex flex-wrap gap-2">
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
                  <Button
                    type="button"
                    size="sm"
                    variant="outline"
                    disabled={uploadAgent.isPending}
                    onClick={() => agentFileRef.current?.click()}
                    className="gap-1.5"
                  >
                    {uploadAgent.isPending ? (
                      <Loader2 className="h-3.5 w-3.5 animate-spin" />
                    ) : (
                      <Upload className="h-3.5 w-3.5" />
                    )}
                    Загрузить .txt / .md
                  </Button>
                  <Button
                    type="button"
                    size="sm"
                    variant="outline"
                    className="gap-1.5"
                    title="Просмотр текста агента (файл или builtin)"
                    onClick={() => setViewTarget({ kind: "agent" })}
                  >
                    <Eye className="h-3.5 w-3.5" />
                    Просмотр
                  </Button>
                  {checkAgentFileName ? (
                    <Button
                      type="button"
                      size="sm"
                      variant="ghost"
                      disabled={clearAgent.isPending}
                      onClick={() => clearAgent.mutate()}
                    >
                      Сбросить
                    </Button>
                  ) : null}
                </div>
              </div>
            ) : (
              <ul className="space-y-1 text-[11px]">
                <li className="text-muted-foreground">
                  Мастер-промт ноды со стрелки (Studio → сделать активным). Не
                  «Загрузить файл».
                </li>
                {sourcePrompts.length ? (
                  sourcePrompts.map((s) => (
                    <li
                      key={String(s.nodeKey)}
                      className={cn(
                        "flex items-center gap-1.5",
                        s.ok ? "text-emerald-200/90" : "text-destructive",
                      )}
                    >
                      <span className="min-w-0 flex-1 truncate">
                        {s.ok ? "✓" : "✗"} {s.nodeKey}
                        {s.variant ? ` · ${s.variant}` : ""}
                        {s.chars ? ` · ${s.chars} симв` : ""}
                        {s.error ? ` · ${s.error}` : ""}
                      </span>
                      {s.ok ? (
                        <button
                          type="button"
                          title="Просмотр промта источника"
                          className="shrink-0 rounded p-0.5 text-muted-foreground transition hover:bg-white/10 hover:text-foreground"
                          onClick={() =>
                            setViewTarget({ kind: "source", key: String(s.nodeKey) })
                          }
                        >
                          <Eye className="h-3.5 w-3.5" />
                        </button>
                      ) : null}
                    </li>
                  ))
                ) : (
                  <li className="text-destructive">Нет входящих стрелок</li>
                )}
              </ul>
            )}
          </div>
        ) : null}

        {checkMode ? (
          <div className="mt-3 space-y-2 rounded-lg border border-rose-400/25 bg-rose-500/[0.06] p-3">
            <div className="flex flex-wrap items-center justify-between gap-2">
              <p className="text-[11px] font-medium text-muted-foreground">
                Формат ответа модели{" "}
                {data?.checkReportFormatCustom ? (
                  <span className="text-amber-200/90">(свой)</span>
                ) : (
                  <span className="text-muted-foreground/80">(дефолт)</span>
                )}
              </p>
              <div className="flex flex-wrap gap-1.5">
                <Button
                  type="button"
                  size="sm"
                  variant="outline"
                  disabled={patch.isPending || !formatDirty}
                  onClick={() => {
                    patch.mutate(
                      { checkReportFormat: formatDraft, transport: "api" },
                      {
                        onSuccess: () => {
                          setFormatDirty(false);
                          toast.success("Формат отчёта сохранён");
                        },
                      },
                    );
                  }}
                >
                  Сохранить
                </Button>
                <Button
                  type="button"
                  size="sm"
                  variant="ghost"
                  disabled={patch.isPending}
                  onClick={() => {
                    const def = String(data?.checkReportFormatDefault || "");
                    setFormatDraft(def);
                    setFormatDirty(false);
                    patch.mutate(
                      { checkReportFormat: null, transport: "api" },
                      {
                        onSuccess: () => toast.success("Формат сброшен к дефолту"),
                      },
                    );
                  }}
                >
                  Дефолт
                </Button>
              </div>
            </div>
            <p className="text-[10px] text-muted-foreground">
              Редактируйте шаблон ответа GPT. Для стрелок ок/не ок оставьте строку{" "}
              <span className="font-mono text-foreground/90">verdict: pass|fail</span>.
              Критерии — в агенте/.txt отдельно.
            </p>
            <Textarea
              value={formatDraft}
              onChange={(e) => {
                setFormatDraft(e.target.value);
                setFormatDirty(true);
              }}
              rows={14}
              className="min-h-[220px] font-mono text-[11px] leading-snug"
              spellCheck={false}
            />
          </div>
        ) : null}
      </section>

      <section className="rounded-xl border border-violet-400/20 bg-violet-500/[0.06] p-4">
        <h3 className="text-sm font-semibold text-foreground">Роль</h3>
        <p className="mt-1 text-xs text-muted-foreground">
          Полный пульт также во всплывающем меню V. Транспорт — API (без браузера).
        </p>
        <div className="mt-3 grid gap-2 sm:grid-cols-3">
          {ROLE_OPTIONS.map((opt) => (
            <button
              key={opt.value}
              type="button"
              onClick={() => patch.mutate({ role: opt.value, transport: "api" })}
              className={cn(
                "rounded-lg border px-3 py-2.5 text-left transition",
                role === opt.value
                  ? "border-violet-400/60 bg-violet-500/25 ring-2 ring-violet-400/40"
                  : "border-white/10 bg-black/20 hover:border-white/20",
              )}
            >
              <span className="block text-[12px] font-medium">{opt.title}</span>
              <span className="mt-1 block text-[10px] text-muted-foreground">{opt.hint}</span>
            </button>
          ))}
        </div>
      </section>

      <section className="rounded-xl border border-emerald-400/20 bg-emerald-500/[0.05] p-4">
        <h3 className="text-sm font-semibold text-foreground">Формат ответа GPT</h3>
        <div className="mt-2 flex flex-wrap gap-2">
          {OUTPUT_OPTIONS.map((opt) => (
            <Button
              key={opt.value}
              type="button"
              size="sm"
              title={opt.hint}
              variant={outputMode === opt.value ? "default" : "outline"}
              className={cn(
                outputMode === opt.value &&
                  "ring-2 ring-emerald-400/50 ring-offset-1 ring-offset-background",
              )}
              onClick={() => patch.mutate({ outputMode: opt.value, transport: "api" })}
            >
              {opt.title}
            </Button>
          ))}
        </div>
      </section>

      <section className="rounded-xl border border-sky-400/20 bg-sky-500/[0.05] p-4">
        <h3 className="text-sm font-semibold text-foreground">Что отдаёт дальше</h3>
        <p className="mt-1 text-xs text-muted-foreground">
          Файлы для следующей ноды по стрелке. Можно выбрать несколько.
        </p>
        <div className="mt-2 flex flex-wrap gap-2">
          {EMIT_OPTIONS.map((opt) => {
            const on = emitKinds.includes(opt.value);
            return (
              <Button
                key={opt.value}
                type="button"
                size="sm"
                variant={on ? "default" : "outline"}
                title={opt.hint}
                className={cn(
                  on && "ring-2 ring-sky-400/50 ring-offset-1 ring-offset-background",
                )}
                onClick={() => toggleEmit(opt.value)}
              >
                {opt.title}
              </Button>
            );
          })}
        </div>
      </section>

      <section className="rounded-xl border border-sky-400/20 bg-sky-500/[0.05] p-4">
        <div className="flex items-center justify-between gap-2">
          <h3 className="text-sm font-semibold text-foreground">Фактические файлы на входе</h3>
          <Button
            type="button"
            size="sm"
            variant="outline"
            onClick={() => void resolve.refetch()}
          >
            {resolve.isFetching ? (
              <Loader2 className="h-3.5 w-3.5 animate-spin" />
            ) : (
              <RefreshCw className="h-3.5 w-3.5" />
            )}
            Сверить
          </Button>
        </div>
        <p className="mt-1 text-xs text-muted-foreground">
          Список с диска и со стрелок feed/review. Загрузка — в меню V на канвасе.
        </p>
        {!data?.consistent ? (
          <p className="mt-2 text-[11px] text-destructive">
            {(data?.errors || []).join("; ") || "Рассинхрон"}
          </p>
        ) : null}
        <ul className="mt-3 max-h-48 space-y-1.5 overflow-y-auto">
          {(data?.files || []).map((f) => (
            <li
              key={f.path}
              className={cn(
                "flex items-center gap-2 rounded-md border px-2 py-1.5 text-[11px]",
                f.ok ? "border-white/10" : "border-destructive/40 text-destructive",
              )}
            >
              {f.preview_url && f.kind === "image" ? (
                // eslint-disable-next-line @next/next/no-img-element
                <img src={f.preview_url} alt="" className="h-8 w-8 rounded object-cover" />
              ) : (
                <span className="flex h-8 w-8 items-center justify-center rounded bg-white/5 font-mono text-[9px] uppercase">
                  {f.kind.slice(0, 3)}
                </span>
              )}
              <span className="min-w-0 flex-1 truncate font-mono">
                {f.name}
                {f.fromNode ? ` ← ${f.fromNode}` : ` · ${f.origin}`}
              </span>
            </li>
          ))}
          {!resolve.isLoading && !(data?.files || []).length ? (
            <li className="text-[11px] text-muted-foreground">Файлов нет</li>
          ) : null}
        </ul>
      </section>

      <Dialog
        open={viewOpen}
        onOpenChange={(o) => {
          if (!o) setViewTarget(null);
        }}
      >
        <DialogContent className="max-w-3xl">
          <DialogHeader>
            <DialogTitle className="font-mono text-sm">
              {viewTitle}
              {viewChars ? (
                <span className="ml-2 text-xs font-normal text-muted-foreground">
                  {viewChars} симв.
                </span>
              ) : null}
            </DialogTitle>
          </DialogHeader>
          <ScrollArea className="max-h-[70vh] rounded-lg border border-white/10 bg-black/30">
            {viewLoading ? (
              <div className="flex items-center gap-2 p-4 text-xs text-muted-foreground">
                <Loader2 className="h-3.5 w-3.5 animate-spin" />
                Читаю файл…
              </div>
            ) : viewError ? (
              <p className="p-4 text-xs text-destructive">
                {errorMessageFromUnknown(viewError)}
              </p>
            ) : (
              <pre className="whitespace-pre-wrap p-4 font-mono text-[11px] leading-snug text-foreground/90">
                {viewText || ""}
              </pre>
            )}
          </ScrollArea>
        </DialogContent>
      </Dialog>
    </div>
  );
}
