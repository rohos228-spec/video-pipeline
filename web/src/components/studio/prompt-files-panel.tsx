"use client";

/**
 * Файловый браузер для папки `prompts/<step>/` ноды.
 *
 * Что показывает:
 * - список .md-файлов из соответствующей шагу папки (real-time,
 *   refetchInterval 2 сек);
 * - выбранный файл — превью + редактирование контента;
 * - кнопки: ⇩ скачать (для всех файлов), ⤴ загрузить новый .md,
 *   💾 сохранить, 🗑 удалить (default удалять нельзя).
 *
 * Привязка папки выводится в шапке как `prompts/<folder>`.
 */

import { useEffect, useMemo, useRef, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Download, FileText, Loader2, RefreshCw, Save, Trash2, Upload } from "lucide-react";
import { toast } from "sonner";
import { errorMessageFromUnknown } from "@/lib/error-message";
import { api, type PromptFileInfo } from "@/lib/api";
import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/textarea";
import { cn } from "@/lib/utils";

const POLL_INTERVAL_MS = 2000;

function formatBytes(n: number): string {
  if (n < 1024) return `${n} Б`;
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} КБ`;
  return `${(n / (1024 * 1024)).toFixed(1)} МБ`;
}

function formatModified(mtime: number): string {
  const ms = mtime > 1e12 ? mtime : mtime * 1000;
  const d = new Date(ms);
  if (Number.isNaN(d.getTime())) return "—";
  return d.toLocaleString("ru-RU", {
    hour: "2-digit",
    minute: "2-digit",
    day: "2-digit",
    month: "2-digit",
    year: "numeric",
  });
}

export function PromptFilesPanel({
  stepCode,
  folderHint,
  slotId,
  preferredFile,
  activeVariant,
  onActivateVariant,
  activating = false,
}: {
  stepCode: string;
  folderHint?: string;
  /** Уникальный id слота — изолирует кэш редактора между промтами одного шага. */
  slotId?: string;
  /** Предпочитаемый .md (имя без расширения) для этого слота. */
  preferredFile?: string;
  activeVariant?: string;
  onActivateVariant?: (variant: string) => void;
  activating?: boolean;
}) {
  const qc = useQueryClient();
  const fileInputRef = useRef<HTMLInputElement>(null);
  const [selectedName, setSelectedName] = useState<string | null>(null);
  const [draft, setDraft] = useState<string>("");
  const [dirty, setDirty] = useState(false);

  const cacheKey = slotId ? `${stepCode}::${slotId}` : stepCode;

  const files = useQuery({
    queryKey: ["prompt-files", cacheKey],
    queryFn: () => api.listPromptFiles(stepCode),
    enabled: Boolean(stepCode),
    refetchInterval: POLL_INTERVAL_MS,
  });

  // Автовыбор первого файла, если ничего не выбрано / выбранный удалён.
  useEffect(() => {
    const list = files.data ?? [];
    if (list.length === 0) {
      if (selectedName !== null) setSelectedName(null);
      return;
    }
    if (preferredFile && list.some((f) => f.name === preferredFile)) {
      if (selectedName !== preferredFile) setSelectedName(preferredFile);
      return;
    }
    if (!selectedName || !list.some((f) => f.name === selectedName)) {
      setSelectedName(list[0].name);
    }
  }, [files.data, selectedName, preferredFile]);

  const content = useQuery({
    queryKey: ["prompt-file", cacheKey, selectedName],
    queryFn: () => api.getPromptFile(stepCode, selectedName!),
    enabled: Boolean(stepCode) && Boolean(selectedName),
    // Файл может меняться извне (юзер правит .md в редакторе) —
    // тоже опрашиваем, но только когда нет несохранённых правок.
    refetchInterval: () => (dirty ? false : POLL_INTERVAL_MS),
  });

  useEffect(() => {
    if (!content.data) return;
    // Не затираем то, что юзер уже редактирует.
    if (dirty) return;
    setDraft(content.data.content);
  }, [content.data, dirty]);

  // При смене выбранного файла сбрасываем грязный флаг.
  useEffect(() => {
    setDirty(false);
  }, [selectedName, slotId, stepCode]);

  const save = useMutation({
    mutationFn: () => api.savePromptFile(stepCode, selectedName!, draft),
    onSuccess: () => {
      toast.success(`Сохранено: ${selectedName}.md`);
      setDirty(false);
      qc.invalidateQueries({ queryKey: ["prompt-files", cacheKey] });
      qc.invalidateQueries({ queryKey: ["prompt-file", cacheKey, selectedName] });
      qc.invalidateQueries({ queryKey: ["prompt-variants", stepCode] });
    },
    onError: (e) => toast.error(errorMessageFromUnknown(e)),
  });

  const remove = useMutation({
    mutationFn: (name: string) => api.deletePromptFile(stepCode, name),
    onSuccess: (_, name) => {
      toast.success(`Удалён: ${name}.md`);
      if (selectedName === name) setSelectedName(null);
      qc.invalidateQueries({ queryKey: ["prompt-files", cacheKey] });
      qc.invalidateQueries({ queryKey: ["prompt-variants", stepCode] });
    },
    onError: (e) => toast.error(errorMessageFromUnknown(e)),
  });

  const upload = useMutation({
    mutationFn: (file: File) => api.uploadPromptFile(stepCode, file),
    onSuccess: (info) => {
      toast.success(`Загружен: ${info.filename}`);
      setSelectedName(info.name);
      qc.invalidateQueries({ queryKey: ["prompt-files", cacheKey] });
      qc.invalidateQueries({ queryKey: ["prompt-variants", stepCode] });
    },
    onError: (e) => toast.error(errorMessageFromUnknown(e)),
  });

  const fileList = files.data ?? [];

  const folderLabel = useMemo(
    () => (folderHint ? `prompts/${folderHint}` : `prompts/${stepCode}`),
    [folderHint, stepCode],
  );

  return (
    <section className="rounded-xl border border-white/10 bg-white/[0.02] p-3">
      <header className="mb-2 flex items-center justify-between gap-2">
        <div className="min-w-0">
          <h3 className="flex items-center gap-1.5 text-xs font-medium uppercase tracking-wider text-muted-foreground">
            <FileText className="h-3.5 w-3.5" />
            Файлы промтов
          </h3>
          <p className="mt-0.5 truncate font-mono text-[10px] text-muted-foreground/70">
            {folderLabel} • {fileList.length} файл(ов)
            {activeVariant ? (
              <span className="ml-1 text-emerald-400/80">• активен: {activeVariant}</span>
            ) : null}
            {files.isFetching && (
              <span className="ml-1 inline-flex items-center gap-1 text-primary/70">
                <Loader2 className="h-2.5 w-2.5 animate-spin" />
              </span>
            )}
          </p>
        </div>
        <div className="flex shrink-0 items-center gap-1">
          <Button
            size="sm"
            variant="outline"
            className="h-7 px-2 text-[10px]"
            onClick={() => fileInputRef.current?.click()}
            disabled={upload.isPending}
            title="Загрузить .md файл в эту папку"
          >
            {upload.isPending ? (
              <Loader2 className="h-3 w-3 animate-spin" />
            ) : (
              <Upload className="h-3 w-3" />
            )}
            Загрузить
          </Button>
          <Button
            size="sm"
            variant="ghost"
            className="h-7 w-7 p-0"
            onClick={() => files.refetch()}
            title="Обновить список"
          >
            <RefreshCw className="h-3 w-3" />
          </Button>
          <input
            ref={fileInputRef}
            type="file"
            accept=".md,text/markdown,text/plain"
            className="hidden"
            onChange={(e) => {
              const f = e.target.files?.[0];
              if (f) upload.mutate(f);
              // сбрасываем — иначе повторная загрузка того же файла не сработает.
              e.target.value = "";
            }}
          />
        </div>
      </header>

      <div className="grid gap-3 md:grid-cols-[160px,1fr]">
        <ul className="flex max-h-[260px] flex-col gap-0.5 overflow-y-auto rounded-lg border border-white/5 bg-black/20 p-1">
          {fileList.length === 0 && (
            <li className="px-2 py-2 text-[10px] text-muted-foreground">
              Папка пуста.
            </li>
          )}
          {fileList.map((f) => (
            <li key={f.name}>
              <button
                type="button"
                onClick={() => setSelectedName(f.name)}
                className={cn(
                  "flex w-full items-center justify-between gap-1 rounded-md px-2 py-1 text-left text-[10px] transition-colors",
                  selectedName === f.name
                    ? "bg-primary/20 text-foreground"
                    : activeVariant === f.name
                      ? "bg-emerald-500/15 text-emerald-100"
                      : "text-muted-foreground hover:bg-white/5 hover:text-foreground",
                )}
                title={`${f.filename} • ${formatBytes(f.size)} • ${formatModified(f.modified)}`}
              >
                <span className="flex min-w-0 flex-col">
                  <span className="truncate">
                    {f.name}
                    {f.is_default && (
                      <span className="ml-1 rounded bg-amber-500/20 px-1 py-px text-[8px] uppercase text-amber-300">
                        def
                      </span>
                    )}
                    {activeVariant === f.name && (
                      <span className="ml-1 rounded bg-emerald-500/20 px-1 py-px text-[8px] uppercase text-emerald-300">
                        ✓
                      </span>
                    )}
                  </span>
                  <span className="text-[8px] text-muted-foreground/60">
                    {formatBytes(f.size)} • {formatModified(f.modified)}
                  </span>
                </span>
              </button>
            </li>
          ))}
        </ul>

        <div className="flex flex-col gap-2">
          <div className="flex flex-wrap items-center gap-1">
            <PromptActionButton
              label="Скачать"
              icon={Download}
              disabled={!selectedName}
              asLink={selectedName ? api.downloadPromptFileUrl(stepCode, selectedName) : undefined}
              download={selectedName ? `${selectedName}.md` : undefined}
            />
            <Button
              size="sm"
              variant={dirty ? "default" : "outline"}
              className="h-7 gap-1 px-2 text-[10px]"
              onClick={() => save.mutate()}
              disabled={!selectedName || !dirty || save.isPending}
              title={dirty ? "Сохранить изменения" : "Нет несохранённых изменений"}
            >
              {save.isPending ? (
                <Loader2 className="h-3 w-3 animate-spin" />
              ) : (
                <Save className="h-3 w-3" />
              )}
              Сохранить
            </Button>
            {onActivateVariant && selectedName ? (
              <Button
                size="sm"
                variant="outline"
                className="h-7 gap-1 border-emerald-400/30 px-2 text-[10px] text-emerald-100"
                onClick={() => onActivateVariant(selectedName)}
                disabled={activating || activeVariant === selectedName}
                title={
                  activeVariant === selectedName
                    ? "Этот файл уже активен для шага"
                    : "Использовать этот .md как мастер-промт"
                }
              >
                {activating ? (
                  <Loader2 className="h-3 w-3 animate-spin" />
                ) : null}
                {activeVariant === selectedName ? "Активен" : "Сделать активным"}
              </Button>
            ) : null}
            <Button
              size="sm"
              variant="ghost"
              className="h-7 gap-1 px-2 text-[10px] text-destructive hover:text-destructive"
              onClick={() => {
                if (!selectedName) return;
                if (selectedName === "default") {
                  toast.error("default удалять нельзя");
                  return;
                }
                if (!confirm(`Удалить ${selectedName}.md?`)) return;
                remove.mutate(selectedName);
              }}
              disabled={
                !selectedName || selectedName === "default" || remove.isPending
              }
              title={selectedName === "default" ? "default удалять нельзя" : "Удалить файл"}
            >
              <Trash2 className="h-3 w-3" />
              Удалить
            </Button>
          </div>

          <Textarea
            value={draft}
            onChange={(e) => {
              setDraft(e.target.value);
              setDirty(true);
            }}
            rows={12}
            className="font-mono text-[10px] leading-relaxed"
            placeholder={
              selectedName
                ? "Содержимое файла…"
                : "Выберите файл слева или загрузите новый."
            }
            disabled={!selectedName || content.isLoading}
          />
        </div>
      </div>
    </section>
  );
}

function PromptActionButton({
  label,
  icon: Icon,
  disabled,
  asLink,
  download,
}: {
  label: string;
  icon: typeof Download;
  disabled?: boolean;
  asLink?: string;
  download?: string;
}) {
  const className =
    "inline-flex h-7 items-center gap-1 rounded-md border border-input bg-background px-2 text-[10px] font-medium transition-colors hover:bg-accent hover:text-accent-foreground disabled:pointer-events-none disabled:opacity-50";
  if (asLink && !disabled) {
    return (
      <a href={asLink} download={download} className={className}>
        <Icon className="h-3 w-3" />
        {label}
      </a>
    );
  }
  return (
    <button type="button" disabled className={className}>
      <Icon className="h-3 w-3" />
      {label}
    </button>
  );
}

export type { PromptFileInfo };
