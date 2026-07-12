"use client";

import { useRef } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { Loader2, Upload } from "lucide-react";
import { toast } from "sonner";
import { api } from "@/lib/api";
import { errorMessageFromUnknown } from "@/lib/error-message";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import {
  attachmentLabel,
  type ExcelGptInputSource,
  type ExcelGptNodeConfig,
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

  const upload = useMutation({
    mutationFn: (file: File) => api.uploadExcelGptFile(projectId, nodeKey, file),
    onSuccess: (res) => {
      onConfigChange({
        inputSource: "upload",
        uploadedFileName: res.fileName,
        label: res.fileName,
      });
      void qc.invalidateQueries({ queryKey: ["step-attachments", projectId] });
      window.setTimeout(() => {
        window.dispatchEvent(new CustomEvent("canvas-save-workflow"));
      }, 80);
      toast.success(`Файл загружен: ${res.fileName}`);
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

  return (
    <section className="flex flex-col gap-4 rounded-lg border border-white/10 bg-white/[0.02] p-4">
      <div>
        <h3 className="text-sm font-semibold text-foreground">Название ноды</h3>
        <p className="mt-1 text-xs text-muted-foreground">
          Отображается на канвасе. Для загруженного файла подставляется имя файла.
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
      </div>

      <div>
        <h3 className="text-sm font-semibold text-foreground">Файл для отправки в GPT</h3>
        <div className="mt-2 flex flex-wrap gap-2">
          {(
            [
              ["project_xlsx", "project.xlsx"],
              ["upload", "Загрузить свой"],
              ["voiceover", "voiceover.txt"],
            ] as const
          ).map(([value, title]) => (
            <Button
              key={value}
              type="button"
              size="sm"
              variant={source === value ? "secondary" : "outline"}
              onClick={() => setSource(value)}
            >
              {title}
            </Button>
          ))}
        </div>
        <p className="mt-2 font-mono text-[11px] text-muted-foreground">
          Отправляется: {attachmentLabel(source, config.uploadedFileName)}
        </p>
      </div>

      {source === "upload" ? (
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
            Заменить файл
          </Button>
          {config.uploadedFileName ? (
            <span className="font-mono text-xs text-foreground/90">{config.uploadedFileName}</span>
          ) : null}
          <input
            ref={fileRef}
            type="file"
            accept=".xlsx,.xls,.txt"
            className="hidden"
            onChange={(e) => {
              const f = e.target.files?.[0];
              if (f) upload.mutate(f);
              e.target.value = "";
            }}
          />
        </div>
      ) : null}
    </section>
  );
}
