"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Loader2, RefreshCw } from "lucide-react";
import { toast } from "sonner";
import { api } from "@/lib/api";
import { errorMessageFromUnknown } from "@/lib/error-message";
import { Input } from "@/components/ui/input";
import { Button } from "@/components/ui/button";
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
  const outputMode = (data?.outputMode || "text") as OperatorOutputMode;
  const emitKinds = (data?.emitKinds?.length
    ? data.emitKinds
    : role === "review" || role === "gate" || role === "compare"
      ? (["inputs"] as OperatorEmitKind[])
      : (["result", "reply_txt"] as OperatorEmitKind[]));

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
    </div>
  );
}
