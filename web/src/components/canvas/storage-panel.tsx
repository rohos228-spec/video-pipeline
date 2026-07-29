"use client";

import { useRef, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Archive, Loader2, RefreshCw, Trash2, Upload } from "lucide-react";
import { toast } from "sonner";
import { api } from "@/lib/api";
import { errorMessageFromUnknown } from "@/lib/error-message";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";

const FORMAT_OPTIONS: { value: string; title: string }[] = [
  { value: "any", title: "Любые" },
  { value: "image", title: "Картинки" },
  { value: "video", title: "Видео" },
  { value: "xlsx", title: "Excel" },
  { value: "text", title: "Текст" },
];

/** Панель ноды «Хранилище»: принимает файлы со стрелок / загрузкой, свои папки. */
export function StoragePanel({
  projectId,
  nodeKey,
}: {
  projectId: number;
  nodeKey: string;
}) {
  const qc = useQueryClient();
  const fileRef = useRef<HTMLInputElement>(null);
  const [open, setOpen] = useState(true);

  const resolve = useQuery({
    queryKey: ["storage-resolve", projectId, nodeKey],
    queryFn: () => api.resolveStorage(projectId, nodeKey),
    staleTime: 2000,
    refetchInterval: 5000,
  });

  const patch = useMutation({
    mutationFn: (body: { formats?: string[]; label?: string }) =>
      api.patchStorage(projectId, nodeKey, body),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ["storage-resolve", projectId, nodeKey] });
      window.setTimeout(() => {
        window.dispatchEvent(new CustomEvent("canvas-save-workflow"));
      }, 40);
    },
    onError: (e) => toast.error(errorMessageFromUnknown(e)),
  });

  const sync = useMutation({
    mutationFn: () => api.syncStorage(projectId, nodeKey),
    onSuccess: (r) => {
      void qc.invalidateQueries({ queryKey: ["storage-resolve", projectId, nodeKey] });
      const n = r.copied?.length ?? 0;
      toast.success(n ? `Забрано файлов: ${n}` : "Новых файлов нет");
    },
    onError: (e) => toast.error(errorMessageFromUnknown(e)),
  });

  const upload = useMutation({
    mutationFn: (file: File) => api.uploadStorageFile(projectId, nodeKey, file),
    onSuccess: (r) => {
      toast.success(`Файл: ${r.fileName}`);
      void qc.invalidateQueries({ queryKey: ["storage-resolve", projectId, nodeKey] });
    },
    onError: (e) => toast.error(errorMessageFromUnknown(e)),
  });

  const clear = useMutation({
    mutationFn: () => api.clearStorageFiles(projectId, nodeKey),
    onSuccess: (r) => {
      toast.message(`Удалено: ${r.removed}`);
      void qc.invalidateQueries({ queryKey: ["storage-resolve", projectId, nodeKey] });
    },
    onError: (e) => toast.error(errorMessageFromUnknown(e)),
  });

  const data = resolve.data;
  const formats = new Set(data?.formats?.length ? data.formats : ["any"]);
  const files = data?.files ?? [];

  const toggleFormat = (value: string) => {
    let next: string[];
    if (value === "any") {
      next = ["any"];
    } else {
      const cur = new Set([...formats].filter((f) => f !== "any"));
      if (cur.has(value)) cur.delete(value);
      else cur.add(value);
      next = cur.size ? [...cur] : ["any"];
    }
    patch.mutate({ formats: next });
  };

  return (
    <div
      className="nodrag nopan border-t border-sky-500/20 bg-sky-500/5 px-3 py-2"
      onMouseDown={(e) => e.stopPropagation()}
      onClick={(e) => e.stopPropagation()}
    >
      <button
        type="button"
        className="flex w-full items-center gap-2 text-left"
        onClick={() => setOpen((v) => !v)}
      >
        <Archive className="h-3.5 w-3.5 text-sky-300" />
        <span className="min-w-0 flex-1">
          <span className="block text-[11px] font-semibold text-foreground">
            Хранилище
          </span>
          <span className="mt-0.5 block text-[9px] text-muted-foreground">
            файлов {data?.okFileCount ?? "…"}
            {data?.incomingSources?.length
              ? ` · входов ${data.incomingSources.length}`
              : " · нет стрелок"}
            {" · "}
            {open ? "свернуть" : "открыть"}
          </span>
        </span>
      </button>

      {open ? (
        <div className="mt-2 space-y-2">
          <p className="text-[9px] leading-snug text-muted-foreground">
            Автоматически забирает все файлы со входящих стрелок (формат «Любые»).
            Можно сузить фильтр или загрузить вручную.
          </p>
          <div className="flex flex-wrap gap-1">
            {FORMAT_OPTIONS.map((opt) => (
              <button
                key={opt.value}
                type="button"
                disabled={patch.isPending}
                onClick={() => toggleFormat(opt.value)}
                className={cn(
                  "rounded-md border px-1.5 py-0.5 text-[9px] font-medium",
                  formats.has(opt.value)
                    ? "border-sky-400/70 bg-sky-500/25 text-sky-50 ring-1 ring-sky-400/40"
                    : "border-white/10 text-muted-foreground hover:border-white/20",
                )}
              >
                {formats.has(opt.value) ? "✓ " : ""}
                {opt.title}
              </button>
            ))}
          </div>

          <div className="flex flex-wrap gap-1">
            <Button
              type="button"
              size="sm"
              variant="outline"
              className="h-7 text-[10px]"
              disabled={sync.isPending}
              onClick={() => sync.mutate()}
              title="Принудительно обновить со стрелок"
            >
              {sync.isPending ? (
                <Loader2 className="h-3 w-3 animate-spin" />
              ) : (
                <RefreshCw className="h-3 w-3" />
              )}
              Обновить
            </Button>
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
              variant="ghost"
              className="h-7 text-[10px] text-destructive"
              disabled={clear.isPending || files.length === 0}
              onClick={() => clear.mutate()}
            >
              <Trash2 className="h-3 w-3" />
              Очистить
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

          <div className="max-h-28 overflow-y-auto rounded-md border border-white/8 bg-black/25 px-2 py-1.5">
            {resolve.isLoading ? (
              <Loader2 className="h-3.5 w-3.5 animate-spin text-muted-foreground" />
            ) : files.length === 0 ? (
              <p className="text-[10px] text-muted-foreground">
                Пусто — подведите стрелку от ноды с файлами (подтянется само)
              </p>
            ) : (
              <ul className="space-y-1">
                {files.map((f) => (
                  <li
                    key={f.path}
                    className="flex items-center gap-2 text-[10px] text-foreground/90"
                  >
                    <span className="flex h-6 w-6 items-center justify-center rounded bg-white/5 font-mono text-[8px] uppercase">
                      {f.kind.slice(0, 3)}
                    </span>
                    <span className="min-w-0 flex-1 truncate font-mono" title={f.path}>
                      {f.name}
                    </span>
                  </li>
                ))}
              </ul>
            )}
          </div>
        </div>
      ) : null}
    </div>
  );
}
