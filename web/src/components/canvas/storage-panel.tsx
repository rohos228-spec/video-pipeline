"use client";

import { useMemo, useRef, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Archive, Download, Loader2, RefreshCw, Trash2, Upload } from "lucide-react";
import { toast } from "sonner";
import { api } from "@/lib/api";
import { errorMessageFromUnknown } from "@/lib/error-message";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";

/** Фильтр витрины (что показать), не отбор при sync со стрелок. */
const VIEW_OPTIONS: { value: string; title: string }[] = [
  { value: "any", title: "Любые" },
  { value: "image", title: "Картинки" },
  { value: "video", title: "Видео" },
  { value: "xlsx", title: "Excel" },
  { value: "text", title: "Текст" },
];

type StoredFile = {
  name: string;
  path: string;
  size: number;
  kind: string;
  fromNode?: string | null;
  fromLabel?: string | null;
  originalName?: string | null;
  savedAt?: string | null;
  download_url?: string | null;
  preview_url?: string | null;
};

function formatBytes(n: number): string {
  if (n < 1024) return `${n} Б`;
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} КБ`;
  return `${(n / (1024 * 1024)).toFixed(1)} МБ`;
}

function formatWhen(iso?: string | null): string {
  if (!iso) return "";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  return d.toLocaleString("ru-RU", {
    day: "2-digit",
    month: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
  });
}

function fileMatchesView(kind: string, view: Set<string>): boolean {
  if (view.has("any") || view.size === 0) return true;
  const k = (kind || "other").toLowerCase();
  if (view.has(k)) return true;
  // json/md/pdf и пр. показываем во вкладке «Текст»
  if (view.has("text") && (k === "text" || k === "other")) return true;
  return false;
}

/** Панель ноды «Хранилище»: витрина всех файлов + авто-забор со стрелок. */
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
  /** Локальный фильтр списка — без PATCH/API (иначе гонка с poll и canvas-save). */
  const [viewKinds, setViewKinds] = useState<Set<string>>(() => new Set(["any"]));

  const resolve = useQuery({
    queryKey: ["storage-resolve", projectId, nodeKey],
    queryFn: () => api.resolveStorage(projectId, nodeKey),
    staleTime: 1500,
    refetchInterval: 4000,
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
  const allFiles = (data?.files ?? []) as StoredFile[];
  const files = useMemo(
    () => allFiles.filter((f) => fileMatchesView(f.kind, viewKinds)),
    [allFiles, viewKinds],
  );

  const toggleView = (value: string) => {
    setViewKinds((prev) => {
      if (value === "any") return new Set(["any"]);
      const next = new Set([...prev].filter((f) => f !== "any"));
      if (next.has(value)) next.delete(value);
      else next.add(value);
      return next.size ? next : new Set(["any"]);
    });
  };

  return (
    <div
      className="nodrag nopan border-t border-sky-500/25 bg-gradient-to-b from-sky-500/10 to-black/20 px-3 py-2.5"
      onMouseDown={(e) => e.stopPropagation()}
      onClick={(e) => e.stopPropagation()}
    >
      <button
        type="button"
        className="flex w-full items-center gap-2.5 text-left"
        onClick={() => setOpen((v) => !v)}
      >
        <span className="flex h-9 w-9 shrink-0 items-center justify-center rounded-xl border border-sky-400/35 bg-sky-500/20">
          <Archive className="h-4 w-4 text-sky-200" />
        </span>
        <span className="min-w-0 flex-1">
          <span className="block text-[12px] font-semibold tracking-tight text-foreground">
            Хранилище
          </span>
          <span className="mt-0.5 block text-[9px] text-muted-foreground">
            {allFiles.length} файл
            {allFiles.length === 1 ? "" : allFiles.length >= 2 && allFiles.length <= 4 ? "а" : "ов"}
            {files.length !== allFiles.length ? ` · показано ${files.length}` : ""}
            {data?.incomingSources?.length
              ? ` · входов ${data.incomingSources.length}`
              : " · нет стрелок"}
            {data?.storageDir ? ` · папка storage/` : ""}
            {" · "}
            {open ? "свернуть" : "показать все"}
          </span>
        </span>
      </button>

      {open ? (
        <div className="mt-2.5 space-y-2">
          <div className="flex flex-wrap gap-1">
            {VIEW_OPTIONS.map((opt) => (
              <button
                key={opt.value}
                type="button"
                title="Фильтр списка: что показать (не влияет на забор со стрелок)"
                onClick={() => toggleView(opt.value)}
                className={cn(
                  "rounded-md border px-1.5 py-0.5 text-[9px] font-medium",
                  viewKinds.has(opt.value)
                    ? "border-sky-400/70 bg-sky-500/25 text-sky-50 ring-1 ring-sky-400/40"
                    : "border-white/10 text-muted-foreground hover:border-white/20",
                )}
              >
                {viewKinds.has(opt.value) ? "✓ " : ""}
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
            {allFiles.length > 0 ? (
              <Button type="button" size="sm" variant="secondary" className="h-7 text-[10px]" asChild>
                <a
                  href={api.storageDownloadZipUrl(projectId, nodeKey)}
                  download
                  title="Скачать все файлы одним zip"
                >
                  <Download className="h-3 w-3" />
                  Скачать всё
                </a>
              </Button>
            ) : (
              <Button
                type="button"
                size="sm"
                variant="secondary"
                className="h-7 text-[10px]"
                disabled
                title="Нет файлов"
              >
                <Download className="h-3 w-3" />
                Скачать всё
              </Button>
            )}
            <Button
              type="button"
              size="sm"
              variant="ghost"
              className="h-7 text-[10px] text-destructive"
              disabled={clear.isPending || allFiles.length === 0}
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

          <div className="rounded-xl border border-sky-400/20 bg-black/40">
            <div className="flex items-center justify-between border-b border-white/8 px-2.5 py-1.5">
              <span className="text-[9px] font-semibold uppercase tracking-wider text-sky-200/90">
                Содержимое
              </span>
              <span className="font-mono text-[9px] text-muted-foreground">
                {files.length}
                {files.length !== allFiles.length ? ` / ${allFiles.length}` : ""}
              </span>
            </div>
            <div className="max-h-56 overflow-y-auto px-1.5 py-1.5">
              {resolve.isLoading ? (
                <div className="flex items-center gap-2 px-2 py-3 text-[10px] text-muted-foreground">
                  <Loader2 className="h-3.5 w-3.5 animate-spin" />
                  Читаю папку…
                </div>
              ) : allFiles.length === 0 ? (
                <p className="px-2 py-3 text-[10px] text-muted-foreground">
                  Пусто — подведите стрелку от ноды с файлами (подтянется само).
                  Имя: номерНоды_время_файл
                </p>
              ) : files.length === 0 ? (
                <p className="px-2 py-3 text-[10px] text-muted-foreground">
                  Нет файлов выбранного типа — нажмите «Любые» или другой фильтр.
                </p>
              ) : (
                <ul className="space-y-1">
                  {files.map((f) => {
                    const dl =
                      f.download_url ||
                      `/api/files?path=${encodeURIComponent(f.path)}&download=1`;
                    return (
                      <li
                        key={f.path}
                        className="flex cursor-pointer items-start gap-2 rounded-lg border border-white/6 bg-white/[0.03] px-2 py-1.5 transition hover:border-sky-400/35 hover:bg-sky-500/10"
                        title="Двойной клик — открыть файл"
                        onDoubleClick={(e) => {
                          e.stopPropagation();
                          const openUrl =
                            f.preview_url ||
                            `/api/files?path=${encodeURIComponent(f.path)}`;
                          window.open(openUrl, "_blank", "noopener,noreferrer");
                        }}
                      >
                        {f.preview_url && f.kind === "image" ? (
                          // eslint-disable-next-line @next/next/no-img-element
                          <img
                            src={f.preview_url}
                            alt=""
                            className="mt-0.5 h-8 w-8 shrink-0 rounded object-cover"
                          />
                        ) : (
                          <span className="mt-0.5 flex h-8 w-8 shrink-0 items-center justify-center rounded bg-white/5 font-mono text-[8px] uppercase text-muted-foreground">
                            {f.kind.slice(0, 3)}
                          </span>
                        )}
                        <span className="min-w-0 flex-1">
                          <span
                            className="block break-all font-mono text-[10px] leading-snug text-foreground"
                            title={f.path}
                          >
                            {f.name}
                          </span>
                          <span className="mt-0.5 block text-[9px] text-muted-foreground">
                            {f.fromLabel || f.fromNode || "—"}
                            {f.originalName && f.originalName !== f.name
                              ? ` · было: ${f.originalName}`
                              : ""}
                            {" · "}
                            {formatBytes(f.size)}
                            {f.savedAt ? ` · ${formatWhen(f.savedAt)}` : ""}
                          </span>
                        </span>
                        <a
                          href={dl}
                          download={f.name}
                          title={`Скачать ${f.name}`}
                          className="mt-0.5 flex h-7 w-7 shrink-0 items-center justify-center rounded-md border border-white/12 text-muted-foreground transition hover:border-sky-400/40 hover:bg-sky-500/15 hover:text-sky-100"
                          onClick={(e) => e.stopPropagation()}
                        >
                          <Download className="h-3.5 w-3.5" />
                        </a>
                      </li>
                    );
                  })}
                </ul>
              )}
            </div>
          </div>
        </div>
      ) : null}
    </div>
  );
}
