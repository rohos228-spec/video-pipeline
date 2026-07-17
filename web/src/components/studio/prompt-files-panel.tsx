"use client";

/**
 * Файловый браузер для папки `prompts/<step>/` ноды.
 *
 * Слева — список .md; у каждого промта справа кнопка «История» с выпадашкой версий.
 * Справа — редактор, сохранение (старая версия → .history), переименование файла.
 */

import { useEffect, useMemo, useRef, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  Download,
  FileText,
  History,
  Loader2,
  Pencil,
  RefreshCw,
  RotateCcw,
  Save,
  Trash2,
  Upload,
} from "lucide-react";
import { toast } from "sonner";
import { errorMessageFromUnknown } from "@/lib/error-message";
import { api, type PromptFileInfo, type PromptVersionInfo } from "@/lib/api";
import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/textarea";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { cn } from "@/lib/utils";

const POLL_INTERVAL_MS = 2000;

function toastError(e: unknown) {
  toast.error(errorMessageFromUnknown(e));
}

function formatBytes(n: number): string {
  if (n < 1024) return `${n} Б`;
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} КБ`;
  return `${(n / (1024 * 1024)).toFixed(1)} МБ`;
}

function formatModified(mtime: number | null | undefined): string {
  if (mtime == null || mtime <= 0) return "—";
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
  activeVariantSourceLabel,
  onActivateVariant,
  activating = false,
  onPromptRenamed,
}: {
  stepCode: string;
  folderHint?: string;
  slotId?: string;
  preferredFile?: string;
  activeVariant?: string;
  activeVariantSourceLabel?: string;
  onActivateVariant?: (variant: string) => void;
  activating?: boolean;
  onPromptRenamed?: (oldName: string, newName: string) => void;
}) {
  const qc = useQueryClient();
  const fileInputRef = useRef<HTMLInputElement>(null);
  const [selectedName, setSelectedName] = useState<string | null>(null);
  const [draft, setDraft] = useState<string>("");
  const [dirty, setDirty] = useState(false);
  const [previewVersion, setPreviewVersion] = useState<PromptVersionInfo | null>(null);

  const cacheKey = slotId ? `${stepCode}::${slotId}` : stepCode;

  const files = useQuery({
    queryKey: ["prompt-files", cacheKey],
    queryFn: () => api.listPromptFiles(stepCode),
    enabled: Boolean(stepCode),
    refetchInterval: POLL_INTERVAL_MS,
  });

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
    refetchInterval: () => (dirty ? false : POLL_INTERVAL_MS),
  });

  useEffect(() => {
    if (!content.data) return;
    if (dirty) return;
    setDraft(content.data.content);
  }, [content.data, dirty]);

  useEffect(() => {
    setDirty(false);
    setPreviewVersion(null);
  }, [selectedName, slotId, stepCode]);

  const save = useMutation({
    mutationFn: () => api.savePromptFile(stepCode, selectedName!, draft),
    onSuccess: () => {
      toast.success(`Сохранено: ${selectedName}.md (старая версия в истории)`);
      setDirty(false);
      setPreviewVersion(null);
      qc.invalidateQueries({ queryKey: ["prompt-files", cacheKey] });
      qc.invalidateQueries({ queryKey: ["prompt-file", cacheKey, selectedName] });
      qc.invalidateQueries({ queryKey: ["prompt-file-history", cacheKey] });
      qc.invalidateQueries({ queryKey: ["prompt-global-active"] });
      qc.invalidateQueries({ queryKey: ["prompt-variants", stepCode] });
    },
    onError: toastError,
  });

  const renamePrompt = useMutation({
    mutationFn: (newName: string) =>
      api.renamePromptFile(stepCode, selectedName!, newName),
    onSuccess: (info) => {
      const old = selectedName!;
      toast.success(`Промт переименован: ${old} → ${info.name}`);
      setSelectedName(info.name);
      onPromptRenamed?.(old, info.name);
      qc.invalidateQueries({ queryKey: ["prompt-files", cacheKey] });
      qc.invalidateQueries({ queryKey: ["prompt-file-history", cacheKey] });
      qc.invalidateQueries({ queryKey: ["prompt-global-active"] });
      qc.invalidateQueries({ queryKey: ["prompt-variants", stepCode] });
    },
    onError: toastError,
  });

  const renameVersionLabel = useMutation({
    mutationFn: ({
      fileName,
      versionId,
      label,
    }: {
      fileName: string;
      versionId: string;
      label: string;
    }) => api.renamePromptVersionLabel(stepCode, fileName, versionId, label),
    onSuccess: (_, { fileName }) => {
      toast.success("Название версии обновлено");
      qc.invalidateQueries({
        queryKey: ["prompt-file-history", cacheKey, fileName],
      });
    },
    onError: toastError,
  });

  const restoreVersion = useMutation({
    mutationFn: ({
      fileName,
      versionId,
    }: {
      fileName: string;
      versionId: string;
    }) => api.restorePromptFileVersion(stepCode, fileName, versionId),
    onSuccess: (data, { fileName }) => {
      toast.success("Версия восстановлена");
      if (fileName === selectedName) {
        setDraft(data.content);
        setDirty(false);
        setPreviewVersion(null);
        qc.invalidateQueries({ queryKey: ["prompt-file", cacheKey, selectedName] });
      }
      qc.invalidateQueries({
        queryKey: ["prompt-file-history", cacheKey, fileName],
      });
      qc.invalidateQueries({ queryKey: ["prompt-files", cacheKey] });
    },
    onError: toastError,
  });

  const loadVersionPreview = useMutation({
    mutationFn: ({
      fileName,
      versionId,
    }: {
      fileName: string;
      versionId: string;
    }) => api.getPromptFileHistory(stepCode, fileName, versionId),
    onSuccess: (data, { fileName }) => {
      if (fileName !== selectedName) setSelectedName(fileName);
      setDraft(data.content);
      setDirty(false);
      setPreviewVersion({
        id: data.id,
        label: data.label,
        saved_at: data.saved_at,
        size: data.size,
      });
    },
    onError: toastError,
  });

  const remove = useMutation({
    mutationFn: (name: string) => api.deletePromptFile(stepCode, name),
    onSuccess: (_, name) => {
      toast.success(`Удалён: ${name}.md`);
      if (selectedName === name) setSelectedName(null);
      qc.invalidateQueries({ queryKey: ["prompt-files", cacheKey] });
      qc.invalidateQueries({ queryKey: ["prompt-file-history", cacheKey] });
      qc.invalidateQueries({ queryKey: ["prompt-global-active"] });
      qc.invalidateQueries({ queryKey: ["prompt-variants", stepCode] });
    },
    onError: toastError,
  });

  const upload = useMutation({
    mutationFn: (file: File) => api.uploadPromptFile(stepCode, file),
    onSuccess: (info) => {
      toast.success(`Загружен: ${info.filename}`);
      setSelectedName(info.name);
      qc.invalidateQueries({ queryKey: ["prompt-files", cacheKey] });
      qc.invalidateQueries({ queryKey: ["prompt-file-history", cacheKey] });
      qc.invalidateQueries({ queryKey: ["prompt-global-active"] });
      qc.invalidateQueries({ queryKey: ["prompt-variants", stepCode] });
    },
    onError: toastError,
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
              <span className="ml-1 text-emerald-400/80">
                • {activeVariant}
                {activeVariantSourceLabel ? (
                  <span className="text-muted-foreground/70"> ({activeVariantSourceLabel})</span>
                ) : null}
              </span>
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
              e.target.value = "";
            }}
          />
        </div>
      </header>

      <div className="grid gap-3 md:grid-cols-[180px,1fr]">
        <ul className="flex max-h-[260px] flex-col gap-0.5 overflow-y-auto rounded-lg border border-white/5 bg-black/20 p-1">
          {files.isError && (
            <li className="px-2 py-2 text-[10px] text-destructive">
              Ошибка загрузки списка промтов. Нужен бэкенд v237+ — STUDIO → [4].
            </li>
          )}
          {!files.isError && fileList.length === 0 && (
            <li className="px-2 py-2 text-[10px] text-muted-foreground">
              Папка пуста. Если так на всех нодах — обновите студию (бейдж v237+)
              и перезапустите бэкенд.
            </li>
          )}
          {fileList.map((f) => (
            <li key={f.name} className="flex items-stretch gap-0.5">
              <button
                type="button"
                onClick={() => setSelectedName(f.name)}
                className={cn(
                  "flex min-w-0 flex-1 items-center justify-between gap-1 rounded-md px-2 py-1 text-left text-[10px] transition-colors",
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
              <PromptFileRowHistory
                stepCode={stepCode}
                cacheKey={cacheKey}
                fileName={f.name}
                isActive={selectedName === f.name}
                onSelectFile={() => setSelectedName(f.name)}
                onPreview={(v) =>
                  loadVersionPreview.mutate({ fileName: f.name, versionId: v.id })
                }
                onRestore={(v) => {
                  if (
                    !confirm(
                      `Восстановить версию «${v.label}» для ${f.name}.md? Текущий текст уйдёт в историю.`,
                    )
                  ) {
                    return;
                  }
                  restoreVersion.mutate({ fileName: f.name, versionId: v.id });
                }}
                onRenameLabel={(v) => {
                  const next = window.prompt("Название версии:", v.label);
                  if (next == null) return;
                  const label = next.trim();
                  if (!label || label === v.label) return;
                  renameVersionLabel.mutate({
                    fileName: f.name,
                    versionId: v.id,
                    label,
                  });
                }}
              />
            </li>
          ))}
        </ul>

        <div className="flex flex-col gap-2">
          <div className="flex flex-wrap items-center gap-1">
            <Button
              size="sm"
              variant="outline"
              className="h-7 gap-1 px-2 text-[10px]"
              disabled={!selectedName || selectedName === "default" || renamePrompt.isPending}
              title={
                selectedName === "default"
                  ? "default переименовывать нельзя"
                  : "Переименовать файл промта"
              }
              onClick={() => {
                if (!selectedName || selectedName === "default") return;
                const next = window.prompt("Новое имя промта (без .md):", selectedName);
                if (next == null) return;
                const name = next.trim();
                if (!name || name === selectedName) return;
                renamePrompt.mutate(name);
              }}
            >
              {renamePrompt.isPending ? (
                <Loader2 className="h-3 w-3 animate-spin" />
              ) : (
                <Pencil className="h-3 w-3" />
              )}
              Имя
            </Button>
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

          {previewVersion ? (
            <div className="flex flex-wrap items-center justify-between gap-2 rounded-md border border-amber-500/30 bg-amber-500/10 px-2 py-1 text-[10px] text-amber-100">
              <span>
                Просмотр версии: <strong>{previewVersion.label}</strong>
              </span>
              <div className="flex gap-1">
                <Button
                  size="sm"
                  variant="ghost"
                  className="h-6 px-2 text-[10px]"
                  onClick={() => {
                    setPreviewVersion(null);
                    if (content.data) setDraft(content.data.content);
                  }}
                >
                  К текущему
                </Button>
                <Button
                  size="sm"
                  variant="outline"
                  className="h-6 gap-1 px-2 text-[10px]"
                  disabled={restoreVersion.isPending || !selectedName}
                  onClick={() =>
                    restoreVersion.mutate({
                      fileName: selectedName!,
                      versionId: previewVersion.id,
                    })
                  }
                >
                  <RotateCcw className="h-3 w-3" />
                  Восстановить
                </Button>
              </div>
            </div>
          ) : null}

          <Textarea
            value={draft}
            onChange={(e) => {
              setDraft(e.target.value);
              setDirty(true);
              setPreviewVersion(null);
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

function PromptFileRowHistory({
  stepCode,
  cacheKey,
  fileName,
  isActive,
  onSelectFile,
  onPreview,
  onRestore,
  onRenameLabel,
}: {
  stepCode: string;
  cacheKey: string;
  fileName: string;
  isActive: boolean;
  onSelectFile: () => void;
  onPreview: (v: PromptVersionInfo) => void;
  onRestore: (v: PromptVersionInfo) => void;
  onRenameLabel: (v: PromptVersionInfo) => void;
}) {
  const [open, setOpen] = useState(false);

  const history = useQuery({
    queryKey: ["prompt-file-history", cacheKey, fileName],
    queryFn: () => api.listPromptFileHistory(stepCode, fileName),
    enabled: true,
    staleTime: 30_000,
  });

  const versionCount = history.data?.length ?? 0;
  const versions = history.data ?? [];

  return (
    <DropdownMenu
      open={open}
      onOpenChange={(next) => {
        setOpen(next);
        if (next) onSelectFile();
      }}
      modal={false}
    >
      <DropdownMenuTrigger asChild>
        <button
          type="button"
          className={cn(
            "flex shrink-0 items-center gap-0.5 rounded-md border border-white/10 px-1.5 py-1 text-[9px] text-muted-foreground transition-colors hover:bg-white/10 hover:text-foreground",
            open && "bg-white/10 text-foreground",
          )}
          title={`История сохранений: ${fileName}.md`}
          onClick={(e) => e.stopPropagation()}
        >
          {history.isLoading && versionCount === 0 ? (
            <Loader2 className="h-3 w-3 animate-spin" />
          ) : (
            <History className="h-3 w-3" />
          )}
          <span className="min-w-[1ch] tabular-nums">{versionCount}</span>
        </button>
      </DropdownMenuTrigger>
      <DropdownMenuContent
        align="end"
        side="right"
        className="max-h-72 w-72 overflow-y-auto"
      >
        <DropdownMenuLabel className="text-[10px] font-normal text-muted-foreground">
          {fileName}.md — старые версии
        </DropdownMenuLabel>
        <DropdownMenuSeparator />
        {versionCount === 0 ? (
          <DropdownMenuItem disabled className="text-[10px]">
            {history.isLoading
              ? "Загрузка…"
              : "Пока пусто — сохраните промт один раз"}
          </DropdownMenuItem>
        ) : (
          versions.map((v) => (
            <DropdownMenuItem
              key={v.id}
              className="flex flex-col items-start gap-1 py-2 text-[10px]"
              onSelect={(e) => {
                e.preventDefault();
                onPreview(v);
                setOpen(false);
              }}
            >
              <span className="font-medium leading-tight">{v.label}</span>
              <span className="text-muted-foreground">
                {formatBytes(v.size)} · {formatModified(v.saved_at)}
              </span>
              <span className="flex gap-1 pt-0.5">
                <button
                  type="button"
                  className="rounded border border-white/10 px-1.5 py-0.5 hover:bg-white/10"
                  onClick={(e) => {
                    e.stopPropagation();
                    onRenameLabel(v);
                  }}
                >
                  ✎ имя
                </button>
                <button
                  type="button"
                  className="rounded border border-emerald-500/30 px-1.5 py-0.5 text-emerald-200 hover:bg-emerald-500/10"
                  onClick={(e) => {
                    e.stopPropagation();
                    onRestore(v);
                    setOpen(false);
                  }}
                >
                  ↩ восстановить
                </button>
              </span>
            </DropdownMenuItem>
          ))
        )}
      </DropdownMenuContent>
    </DropdownMenu>
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
