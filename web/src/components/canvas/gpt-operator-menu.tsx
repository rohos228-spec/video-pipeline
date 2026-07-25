"use client";

import { useRef } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Loader2, RefreshCw, Upload } from "lucide-react";
import { toast } from "sonner";
import { api } from "@/lib/api";
import { errorMessageFromUnknown } from "@/lib/error-message";
import {
  OUTPUT_OPTIONS,
  ROLE_OPTIONS,
  type OperatorOutputMode,
  type OperatorRole,
} from "@/lib/gpt-operator";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";

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
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ["gpt-operator-resolve", projectId, nodeKey] });
      void qc.invalidateQueries({ queryKey: ["project", projectId] });
      window.setTimeout(() => {
        window.dispatchEvent(new CustomEvent("canvas-save-workflow"));
      }, 60);
    },
    onError: (e) => toast.error(errorMessageFromUnknown(e)),
  });

  const upload = useMutation({
    mutationFn: (file: File) => api.uploadExcelGptFile(projectId, nodeKey, file),
    onSuccess: (res) => {
      toast.success(`Файл: ${res.fileName}`);
      void qc.invalidateQueries({ queryKey: ["gpt-operator-resolve", projectId, nodeKey] });
      void qc.invalidateQueries({ queryKey: ["project", projectId] });
    },
    onError: (e) => toast.error(errorMessageFromUnknown(e)),
  });

  const data = resolve.data;
  const role = (data?.role || "assist") as OperatorRole;
  const outputMode = (data?.outputMode || "text") as OperatorOutputMode;

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
        Роль
      </p>
      <div className="grid grid-cols-2 gap-1">
        {ROLE_OPTIONS.map((opt) => (
          <button
            key={opt.value}
            type="button"
            disabled={patch.isPending}
            onClick={() => patch.mutate({ role: opt.value, transport: "api" })}
            className={cn(
              "rounded-md border px-1.5 py-1 text-left text-[9px] leading-tight transition",
              role === opt.value
                ? "border-violet-400/50 bg-violet-500/15 text-violet-50"
                : "border-white/10 bg-black/20 text-muted-foreground hover:border-white/20",
            )}
            title={opt.hint}
          >
            {opt.title}
          </button>
        ))}
      </div>

      <p className="text-[9px] font-semibold uppercase tracking-wider text-muted-foreground">
        Выход
      </p>
      <div className="flex flex-wrap gap-1">
        {OUTPUT_OPTIONS.map((opt) => (
          <button
            key={opt.value}
            type="button"
            disabled={patch.isPending}
            onClick={() => patch.mutate({ outputMode: opt.value, transport: "api" })}
            className={cn(
              "rounded-md border px-1.5 py-1 text-[9px]",
              outputMode === opt.value
                ? "border-emerald-400/40 bg-emerald-500/15 text-emerald-50"
                : "border-white/10 text-muted-foreground hover:border-white/20",
            )}
          >
            {opt.title}
          </button>
        ))}
      </div>

      <p className="text-[9px] font-semibold uppercase tracking-wider text-muted-foreground">
        Вход
      </p>
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
          Файл(ы)
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
        <Button
          type="button"
          size="sm"
          variant="outline"
          className="h-7 text-[10px]"
          onClick={() => {
            toast.message(
              "Поставьте стрелку «файлы» или «проверка» от нужной ноды — вход подтянется сам",
            );
          }}
        >
          Со стрелки
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
