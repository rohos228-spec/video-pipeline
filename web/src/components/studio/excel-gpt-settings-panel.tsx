"use client";

import { useRef } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { ImageIcon, Loader2, Upload } from "lucide-react";
import { toast } from "sonner";
import { api } from "@/lib/api";
import { errorMessageFromUnknown } from "@/lib/error-message";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { cn } from "@/lib/utils";
import {
  attachmentLabel,
  INPUT_SOURCE_OPTIONS,
  WORK_MODE_OPTIONS,
  isImageUploadName,
  type ExcelGptInputSource,
  type ExcelGptNodeConfig,
  type ExcelGptWorkMode,
} from "@/lib/excel-gpt-config";

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
  const fileRef = useRef<HTMLInputElement>(null);
  const source: ExcelGptInputSource = config.inputSource ?? "project_xlsx";
  const mode: ExcelGptWorkMode = config.workMode ?? "assist";
  const showUpload =
    source === "upload" || source === "image" || Boolean(config.uploadedFileName);
  const uploadedIsImage =
    source === "image" || isImageUploadName(config.uploadedFileName);
  const previewUrl =
    uploadedIsImage && config.uploadedPreviewUrl ? config.uploadedPreviewUrl : null;

  const upload = useMutation({
    mutationFn: (file: File) => api.uploadExcelGptFile(projectId, nodeKey, file),
    onSuccess: (res) => {
      const nextSource: ExcelGptInputSource = res.isImage ? "image" : "upload";
      onConfigChange({
        inputSource: nextSource,
        uploadedFileName: res.fileName,
        uploadedPreviewUrl: res.isImage ? res.preview_url ?? null : null,
        label: res.fileName,
      });
      void qc.invalidateQueries({ queryKey: ["step-attachments", projectId] });
      void qc.invalidateQueries({ queryKey: ["project", projectId] });
      window.setTimeout(() => {
        window.dispatchEvent(new CustomEvent("canvas-save-workflow"));
      }, 80);
      toast.success(
        res.isImage ? `Изображение загружено: ${res.fileName}` : `Файл загружен: ${res.fileName}`,
      );
    },
    onError: (e) => toast.error(errorMessageFromUnknown(e)),
  });

  const saveConfig = useMutation({
    mutationFn: (patch: Partial<ExcelGptNodeConfig>) =>
      api.patchExcelGptConfig(projectId, nodeKey, patch),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ["step-attachments", projectId] });
      window.setTimeout(() => {
        window.dispatchEvent(new CustomEvent("canvas-save-workflow"));
      }, 80);
    },
    onError: (e) => toast.error(errorMessageFromUnknown(e)),
  });

  const setSource = (next: ExcelGptInputSource) => {
    onConfigChange({ inputSource: next });
    void saveConfig.mutateAsync({ inputSource: next });
  };

  const setMode = (next: ExcelGptWorkMode) => {
    onConfigChange({ workMode: next });
    void saveConfig.mutateAsync({ workMode: next });
  };

  const dataSources = INPUT_SOURCE_OPTIONS.filter((o) => o.group === "data");
  const mediaSources = INPUT_SOURCE_OPTIONS.filter((o) => o.group === "media");

  return (
    <div className="flex flex-col gap-4">
      {/* ── Роль ноды ─────────────────────────────────────────── */}
      <section className="rounded-xl border border-violet-400/20 bg-violet-500/[0.06] p-4">
        <h3 className="text-sm font-semibold text-foreground">Роль в пайплайне</h3>
        <p className="mt-1 text-xs text-muted-foreground">
          Как эта нода связана с другими: участвует в генерации, проверяет готовое или
          преобразует вход (в т.ч. изображение).
        </p>
        <div className="mt-3 grid gap-2 sm:grid-cols-3">
          {WORK_MODE_OPTIONS.map((opt) => {
            const active = mode === opt.value;
            return (
              <button
                key={opt.value}
                type="button"
                onClick={() => setMode(opt.value)}
                className={cn(
                  "rounded-lg border px-3 py-2.5 text-left transition",
                  active
                    ? "border-violet-400/50 bg-violet-500/15 shadow-[inset_0_0_0_1px_rgba(167,139,250,0.25)]"
                    : "border-white/10 bg-black/20 hover:border-white/20 hover:bg-white/[0.03]",
                )}
              >
                <span
                  className={cn(
                    "block text-[12px] font-medium",
                    active ? "text-violet-100" : "text-foreground/90",
                  )}
                >
                  {opt.title}
                </span>
                <span className="mt-1 block text-[10px] leading-snug text-muted-foreground">
                  {opt.hint}
                </span>
              </button>
            );
          })}
        </div>
      </section>

      {/* ── Название ─────────────────────────────────────────── */}
      <section className="rounded-xl border border-white/10 bg-white/[0.02] p-4">
        <h3 className="text-sm font-semibold text-foreground">Название ноды</h3>
        <p className="mt-1 text-xs text-muted-foreground">
          Отображается на канвасе. Для загруженного файла можно подставить имя файла.
        </p>
        <Input
          className="mt-2"
          value={config.label ?? ""}
          placeholder="Работа с GPT"
          onChange={(e) => onConfigChange({ label: e.target.value })}
          onBlur={() => {
            const label = (config.label ?? "").trim();
            if (label) void saveConfig.mutateAsync({ label });
          }}
        />
      </section>

      {/* ── Входные данные ───────────────────────────────────── */}
      <section className="rounded-xl border border-emerald-400/20 bg-emerald-500/[0.05] p-4">
        <h3 className="text-sm font-semibold text-foreground">Что отправить в GPT</h3>
        <p className="mt-1 text-xs text-muted-foreground">
          Данные проекта, свой файл или изображение для проверки / преобразования.
        </p>

        <p className="mt-3 text-[10px] font-semibold uppercase tracking-wider text-emerald-200/80">
          Данные
        </p>
        <div className="mt-1.5 flex flex-wrap gap-2">
          {dataSources.map((opt) => (
            <Button
              key={opt.value}
              type="button"
              size="sm"
              variant={source === opt.value ? "secondary" : "outline"}
              onClick={() => setSource(opt.value)}
            >
              {opt.title}
            </Button>
          ))}
        </div>

        <p className="mt-3 text-[10px] font-semibold uppercase tracking-wider text-sky-200/80">
          Изображения
        </p>
        <div className="mt-1.5 flex flex-wrap gap-2">
          {mediaSources.map((opt) => (
            <Button
              key={opt.value}
              type="button"
              size="sm"
              variant={source === opt.value ? "secondary" : "outline"}
              className={
                source === opt.value
                  ? "border-sky-400/40 bg-sky-500/15 text-sky-50"
                  : undefined
              }
              onClick={() => setSource(opt.value)}
            >
              <ImageIcon className="mr-1 h-3.5 w-3.5 opacity-80" />
              {opt.title}
            </Button>
          ))}
        </div>

        <p className="mt-3 font-mono text-[11px] text-muted-foreground">
          Отправляется: {attachmentLabel(source, config.uploadedFileName)}
        </p>

        {(source === "upload" || source === "image" || showUpload) && (
          <div className="mt-3 rounded-lg border border-dashed border-white/15 bg-black/25 p-3">
            <div className="flex flex-wrap items-center gap-2">
              <Button
                type="button"
                size="sm"
                variant="outline"
                disabled={upload.isPending}
                onClick={() => fileRef.current?.click()}
              >
                {upload.isPending ? (
                  <Loader2 className="h-3.5 w-3.5 animate-spin" />
                ) : (
                  <Upload className="h-3.5 w-3.5" />
                )}
                {uploadedIsImage ? "Загрузить изображение" : "Загрузить файл"}
              </Button>
              {config.uploadedFileName ? (
                <span className="font-mono text-xs text-foreground/90">
                  {config.uploadedFileName}
                </span>
              ) : (
                <span className="text-[11px] text-muted-foreground">
                  .xlsx · .txt · .png · .jpg · .webp
                </span>
              )}
              <input
                ref={fileRef}
                type="file"
                accept=".xlsx,.xls,.txt,.png,.jpg,.jpeg,.webp,.gif,image/*"
                className="hidden"
                onChange={(e) => {
                  const f = e.target.files?.[0];
                  if (f) upload.mutate(f);
                  e.target.value = "";
                }}
              />
            </div>
            {uploadedIsImage && config.uploadedFileName ? (
              <div className="mt-3 flex gap-3">
                {previewUrl ? (
                  <div className="relative h-20 w-20 shrink-0 overflow-hidden rounded-lg border border-sky-400/30 bg-black/40">
                    {/* eslint-disable-next-line @next/next/no-img-element */}
                    <img
                      src={previewUrl}
                      alt={config.uploadedFileName}
                      className="h-full w-full object-cover"
                    />
                  </div>
                ) : (
                  <div className="flex h-20 w-20 shrink-0 items-center justify-center rounded-lg border border-sky-400/20 bg-sky-500/10">
                    <ImageIcon className="h-7 w-7 text-sky-200/70" />
                  </div>
                )}
                <p className="min-w-0 flex-1 text-[10px] leading-snug text-sky-200/80">
                  Изображение уйдёт в GPT как вложение вместе с мастер-промтом. В режиме
                  «Проверяет» / «Преобразует» ответ сохранится текстом (без подмены Excel).
                </p>
              </div>
            ) : null}
          </div>
        )}

        {(source === "hero_refs" || source === "scene_images") && (
          <p className="mt-2 text-[10px] leading-snug text-muted-foreground">
            В GPT уйдут файлы из папки проекта (
            {source === "hero_refs" ? "characters/" : "scenes/"}
            ). Удобно для проверки или доработки уже сгенерированных картинок.
          </p>
        )}
      </section>

      {config.lastReplyPath ? (
        <section className="rounded-xl border border-white/10 bg-white/[0.02] px-4 py-3 text-[11px] text-muted-foreground">
          Последний текстовый ответ GPT:{" "}
          <span className="font-mono text-foreground/85">{config.lastReplyPath}</span>
          {config.lastReplyAt ? (
            <span className="ml-2 opacity-70">· {config.lastReplyAt}</span>
          ) : null}
        </section>
      ) : null}
    </div>
  );
}
