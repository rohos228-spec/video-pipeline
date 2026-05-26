"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  Download,
  FileSpreadsheet,
  FolderOpen,
  Loader2,
  Save,
  Upload,
} from "lucide-react";
import { toast } from "sonner";
import { api } from "@/lib/api";
import { getNodeSpec } from "@/lib/node-catalog";
import {
  buildPresetSnapshot,
  clampPerceptionScore,
  collectPresetFileRefs,
  readNodePerception,
  readNodePresets,
  applyPresetToMeta,
  upsertNodePresetInMeta,
  setNodePerceptionInMeta,
  type NodePresetSnapshot,
} from "@/lib/node-presets";
import {
  promptFolderGroupsForNode,
  slotsForFolderGroup,
  translateFolderName,
} from "@/lib/prompt-folder-groups";
import { promptPathsForNode } from "@/lib/prompt-catalog";
import {
  nodeTypeRequiresExcel,
  resolvePromptSlots,
  type NodePromptSlot,
} from "@/lib/node-prompts";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { ScrollArea } from "@/components/ui/scroll-area";
import { Sheet, SheetContent } from "@/components/ui/sheet";
import { cn } from "@/lib/utils";

export function NodeTitleSidePanel({
  open,
  onOpenChange,
  projectId,
  nodeKey,
  nodeType,
  projectMeta,
  promptOverrides,
  slots,
  disabled,
  onOpenExcel,
  onSelectPrompt,
}: {
  open: boolean;
  onOpenChange: (o: boolean) => void;
  projectId: number;
  nodeKey: string;
  nodeType: string;
  projectMeta: Record<string, unknown>;
  promptOverrides: Record<string, unknown>;
  slots: NodePromptSlot[];
  disabled: boolean;
  onOpenExcel: () => void;
  onSelectPrompt: (slot: NodePromptSlot) => void;
}) {
  const qc = useQueryClient();
  const fileRef = useRef<HTMLInputElement>(null);
  const [presetName, setPresetName] = useState("");
  const [perception, setPerception] = useState(() =>
    readNodePerception(projectMeta, nodeKey),
  );
  const [uploadStep, setUploadStep] = useState<string | null>(null);

  const menuSlots = useMemo(
    () => resolvePromptSlots(nodeType, slots),
    [nodeType, slots],
  );
  const folderGroups = useMemo(() => promptFolderGroupsForNode(nodeType), [nodeType]);
  const paths = promptPathsForNode(nodeType);
  const presets = readNodePresets(projectMeta, nodeKey);
  const spec = getNodeSpec(nodeType);
  const showExcel = nodeTypeRequiresExcel(nodeType);

  useEffect(() => {
    if (open) setPerception(readNodePerception(projectMeta, nodeKey));
  }, [open, projectMeta, nodeKey]);

  const persistMeta = useMutation({
    mutationFn: (meta: Record<string, unknown>) =>
      api.patchProject(projectId, { meta }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["project", projectId] });
    },
    onError: (e) => toast.error(String(e)),
  });

  const savePerception = () => {
    const next = setNodePerceptionInMeta(projectMeta, nodeKey, perception);
    persistMeta.mutate(next, {
      onSuccess: () => toast.success("Способ восприятия сохранён"),
    });
  };

  const savePreset = async () => {
    const name = presetName.trim();
    if (!name) {
      toast.error("Введите название пресета");
      return;
    }
    const folderHints: Record<string, string | undefined> = {};
    for (const g of folderGroups) {
      folderHints[g.stepCode] = g.folderPath;
    }
    const fileRefs = collectPresetFileRefs(
      projectMeta,
      nodeKey,
      menuSlots,
      promptOverrides,
      folderHints,
    );
    const snapshot = buildPresetSnapshot({
      name,
      nodeKey,
      meta: projectMeta,
      promptOverrides,
      slots: menuSlots,
      fileRefs,
      perceptionScore: perception,
      disabled,
    });
    let meta = upsertNodePresetInMeta(projectMeta, nodeKey, snapshot);
    meta = setNodePerceptionInMeta(meta, nodeKey, perception);
    await persistMeta.mutateAsync(meta);
    setPresetName("");
    toast.success(`Пресет «${name}» сохранён`);
  };

  const loadPreset = (preset: NodePresetSnapshot) => {
    let meta = applyPresetToMeta(projectMeta, nodeKey, preset);
    const overrides = { ...promptOverrides, ...preset.promptOverrides };
    persistMeta.mutate(meta, {
      onSuccess: async () => {
        if (Object.keys(preset.promptOverrides).length) {
          await api.patchProjectPromptConfig(projectId, {
            legacy: preset.promptOverrides,
          });
        }
        setPerception(preset.perceptionScore);
        toast.success(`Пресет «${preset.name}» применён`);
        qc.invalidateQueries({ queryKey: ["project", projectId] });
      },
    });
  };

  const uploadMutation = useMutation({
    mutationFn: ({ step, file }: { step: string; file: File }) =>
      api.uploadPromptFile(step, file),
    onSuccess: () => {
      toast.success("Файл загружен");
      qc.invalidateQueries({ queryKey: ["prompt-files"] });
    },
    onError: (e) => toast.error(String(e)),
  });

  return (
    <Sheet open={open} onOpenChange={onOpenChange}>
      <SheetContent side="right" className="flex w-[min(380px,92vw)] flex-col gap-0 p-0">
        <header className="shrink-0 border-b border-white/10 px-4 py-3">
          <h2 className="pr-8 text-sm font-semibold">{spec.label}</h2>
          <p className="text-[10px] text-muted-foreground">Промты и пресеты ноды</p>
        </header>

        <ScrollArea className="flex-1 px-4 py-3">
          {showExcel && (
            <section className="mb-4">
              <h3 className="mb-1.5 text-[10px] font-semibold uppercase tracking-wider text-emerald-400/90">
                Excel
              </h3>
              <Button
                type="button"
                size="sm"
                variant="outline"
                className="h-8 w-full justify-start gap-2 text-xs"
                onClick={onOpenExcel}
              >
                <FileSpreadsheet className="h-3.5 w-3.5 text-emerald-400" />
                Открыть таблицу проекта
              </Button>
            </section>
          )}

          <section className="mb-4">
            <h3 className="mb-2 text-[10px] font-semibold uppercase tracking-wider text-amber-400/90">
              Промты по папкам
            </h3>
            {folderGroups.length === 0 && paths.stepCode && (
              <FolderPromptList
                stepCode={paths.stepCode}
                folderLabel={paths.legacyDir ? translateFolderName(paths.legacyDir) : "Промты"}
                slots={menuSlots.filter((s) => s.kind === "gpt")}
                onSelectPrompt={onSelectPrompt}
              />
            )}
            {folderGroups.map((group) => (
              <div key={group.id} className="mb-3 rounded-lg border border-white/8 bg-white/[0.02] p-2">
                <div className="mb-1.5 flex items-center gap-1.5 text-[10px] font-medium text-foreground">
                  <FolderOpen className="h-3 w-3 text-muted-foreground" />
                  {group.label}
                  <span className="font-mono text-[9px] text-muted-foreground">
                    prompts/{group.folderPath}
                  </span>
                </div>
                <FolderPromptList
                  stepCode={group.stepCode}
                  folderLabel={group.label}
                  slots={slotsForFolderGroup(menuSlots, group)}
                  onSelectPrompt={onSelectPrompt}
                />
              </div>
            ))}
          </section>

          <section className="mb-4 rounded-lg border border-white/8 p-2.5">
            <h3 className="mb-2 text-[10px] font-semibold uppercase tracking-wider text-muted-foreground">
              Загрузка промта
            </h3>
            <div className="flex flex-wrap gap-1">
              {folderGroups.length > 0
                ? folderGroups.map((g) => (
                    <button
                      key={g.id}
                      type="button"
                      className={cn(
                        "rounded-md border px-2 py-1 text-[9px]",
                        uploadStep === g.stepCode
                          ? "border-primary/50 bg-primary/10"
                          : "border-white/10 hover:bg-white/5",
                      )}
                      onClick={() => setUploadStep(g.stepCode)}
                    >
                      {g.label}
                    </button>
                  ))
                : paths.stepCode && (
                    <button
                      type="button"
                      className="rounded-md border border-primary/50 bg-primary/10 px-2 py-1 text-[9px]"
                      onClick={() => setUploadStep(paths.stepCode!)}
                    >
                      {paths.legacyDir ? translateFolderName(paths.legacyDir) : "Промты"}
                    </button>
                  )}
            </div>
            <input
              ref={fileRef}
              type="file"
              accept=".md,.txt"
              className="hidden"
              onChange={(e) => {
                const file = e.target.files?.[0];
                const step = uploadStep ?? paths.stepCode;
                if (!file || !step) {
                  toast.error("Выберите папку и файл");
                  return;
                }
                uploadMutation.mutate({ step, file });
                e.target.value = "";
              }}
            />
            <Button
              type="button"
              size="sm"
              variant="secondary"
              className="mt-2 h-8 w-full gap-1.5 text-xs"
              disabled={!uploadStep && !paths.stepCode}
              onClick={() => fileRef.current?.click()}
            >
              {uploadMutation.isPending ? (
                <Loader2 className="h-3.5 w-3.5 animate-spin" />
              ) : (
                <Upload className="h-3.5 w-3.5" />
              )}
              Загрузить .md в выбранную папку
            </Button>
          </section>

          <section className="mb-4 rounded-lg border border-white/8 p-2.5">
            <h3 className="mb-2 text-[10px] font-semibold uppercase tracking-wider text-muted-foreground">
              Способ восприятия данных
            </h3>
            <p className="mb-2 text-[9px] text-muted-foreground">
              Шкала 0–10: насколько «цифрово» интерпретировать таблицу и промты при GPT-проверке.
            </p>
            <div className="flex items-center gap-3">
              <input
                type="range"
                min={0}
                max={10}
                step={1}
                value={perception}
                onChange={(e) => setPerception(clampPerceptionScore(Number(e.target.value)))}
                className="flex-1 accent-amber-500"
              />
              <span className="w-6 text-center font-mono text-sm tabular-nums">{perception}</span>
            </div>
            <Button
              type="button"
              size="sm"
              variant="ghost"
              className="mt-2 h-7 text-[10px]"
              onClick={savePerception}
              disabled={persistMeta.isPending}
            >
              Применить значение
            </Button>
          </section>

          <section className="rounded-lg border border-amber-400/20 bg-amber-400/5 p-2.5">
            <h3 className="mb-2 text-[10px] font-semibold uppercase tracking-wider text-amber-400/90">
              Пресеты
            </h3>
            <p className="mb-2 text-[9px] text-muted-foreground">
              Сохраняются настройки, пути и имена файлов промтов, восприятие {perception}/10.
            </p>
            <div className="flex gap-2">
              <Input
                value={presetName}
                onChange={(e) => setPresetName(e.target.value)}
                placeholder="Название пресета"
                className="h-8 text-xs"
              />
              <Button
                type="button"
                size="sm"
                className="h-8 shrink-0 gap-1 text-xs"
                onClick={() => savePreset().catch((e) => toast.error(String(e)))}
                disabled={persistMeta.isPending}
              >
                <Save className="h-3.5 w-3.5" />
                Сохранить
              </Button>
            </div>
            {presets.length > 0 && (
              <ul className="mt-2 flex flex-col gap-1">
                {presets.map((p) => (
                  <li key={p.id}>
                    <button
                      type="button"
                      className="flex w-full items-center justify-between rounded-md border border-white/10 px-2 py-1.5 text-left text-[10px] hover:bg-white/5"
                      onClick={() => loadPreset(p)}
                    >
                      <span className="font-medium">{p.name}</span>
                      <span className="text-[9px] text-muted-foreground">
                        {p.perceptionScore}/10 · {p.files.length} файлов
                      </span>
                    </button>
                  </li>
                ))}
              </ul>
            )}
          </section>
        </ScrollArea>
      </SheetContent>
    </Sheet>
  );
}

function FolderPromptList({
  stepCode,
  folderLabel,
  slots,
  onSelectPrompt,
}: {
  stepCode: string;
  folderLabel: string;
  slots: NodePromptSlot[];
  onSelectPrompt: (slot: NodePromptSlot) => void;
}) {
  const files = useQuery({
    queryKey: ["prompt-files", stepCode, "title-panel"],
    queryFn: () => api.listPromptFiles(stepCode),
    enabled: Boolean(stepCode),
  });

  const fileNames = files.data?.map((f) => f.name) ?? [];

  return (
    <div className="flex flex-col gap-1">
      {slots.map((slot) => (
        <button
          key={slot.id}
          type="button"
          className="flex items-center justify-between rounded-md px-2 py-1 text-left text-[10px] hover:bg-white/5"
          onClick={() => onSelectPrompt(slot)}
        >
          <span>{slot.title}</span>
          <span className="text-[9px] text-muted-foreground">{slot.kind}</span>
        </button>
      ))}
      {fileNames.map((name) => (
        <div
          key={name}
          className="flex items-center justify-between rounded-md bg-black/20 px-2 py-0.5 font-mono text-[9px] text-muted-foreground"
        >
          <span>{name}.md</span>
          <a
            href={api.downloadPromptFileUrl(stepCode, name)}
            className="text-primary hover:underline"
            onClick={(e) => e.stopPropagation()}
          >
            <Download className="inline h-3 w-3" />
          </a>
        </div>
      ))}
      {!slots.length && !fileNames.length && (
        <span className="text-[9px] text-muted-foreground">Нет файлов в {folderLabel}</span>
      )}
      {files.isLoading && (
        <Loader2 className="mx-auto h-3 w-3 animate-spin text-muted-foreground" />
      )}
    </div>
  );
}
