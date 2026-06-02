"use client";

import { useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ChevronLeft, ChevronRight, Loader2, Trash2 } from "lucide-react";
import { toast } from "sonner";
import { errorMessageFromUnknown } from "@/lib/error-message";
import { api } from "@/lib/api";
import type { GenerationConfigPresetSettings, ProjectSummary } from "@/lib/types";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from "@/components/ui/dialog";
import { cn } from "@/lib/utils";

type Phase = "topic" | "config" | "hero" | "auto" | "wizard";

type WizardAnswers = Record<string, string>;

type WizardQuestion = {
  field: string;
  title: string;
  choices: { id: string; label: string }[];
  cols: number;
};

function answersToSettings(
  answers: WizardAnswers,
  questions: WizardQuestion[],
): GenerationConfigPresetSettings {
  const out: GenerationConfigPresetSettings = {};
  for (const q of questions) {
    const v = answers[q.field];
    if (v === undefined) continue;
    if (q.field === "image_relax" || q.field === "video_relax") {
      (out as Record<string, boolean>)[q.field] = v === "yes";
    } else {
      (out as Record<string, string>)[q.field] = v;
    }
  }
  return out;
}

function presetToAnswers(settings: GenerationConfigPresetSettings): WizardAnswers {
  const out: WizardAnswers = {};
  for (const [k, v] of Object.entries(settings)) {
    if (v === null || v === undefined) continue;
    if (k === "image_relax" || k === "video_relax") {
      out[k] = v ? "yes" : "no";
    } else {
      out[k] = String(v);
    }
  }
  return out;
}

export function NewProjectWizard({
  trigger,
  onCreated,
  folderId = null,
}: {
  trigger: React.ReactNode;
  onCreated: (p: ProjectSummary) => void;
  folderId?: string | null;
}) {
  const [open, setOpen] = useState(false);
  const [phase, setPhase] = useState<Phase>("topic");
  const [wizIndex, setWizIndex] = useState(0);
  const [topic, setTopic] = useState("");
  const [heroMode, setHeroMode] = useState<"hero" | "no_hero" | "auto">("auto");
  const [autoMode, setAutoMode] = useState(true);
  const [answers, setAnswers] = useState<WizardAnswers>({});
  const [skipWizard, setSkipWizard] = useState(false);
  const [savePresetAfterCreate, setSavePresetAfterCreate] = useState(false);
  const [savePresetName, setSavePresetName] = useState("");
  const [selectedPresetName, setSelectedPresetName] = useState<string | null>(null);

  const catalog = useQuery({
    queryKey: ["wizard-catalog"],
    queryFn: api.wizardCatalog,
    enabled: open,
  });

  const presetsQ = useQuery({
    queryKey: ["generation-config-presets"],
    queryFn: api.listGenerationConfigPresets,
    enabled: open,
  });

  const qc = useQueryClient();

  const wizardQuestions = useMemo(() => {
    const qs = catalog.data?.questions ?? [];
    return qs.filter((q) => {
      if (q.field === "image_quality") {
        const g = answers.image_generator;
        if (!g || !["gpt_image_1_5", "gpt_image_2"].includes(g)) return false;
      }
      if (q.field === "video_relax" && answers.video_generator !== "veo_3_1_fast") {
        return false;
      }
      return true;
    });
  }, [catalog.data?.questions, answers.video_generator, answers.image_generator]);

  const deletePreset = useMutation({
    mutationFn: (id: string) => api.deleteGenerationConfigPreset(id),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["generation-config-presets"] });
      toast.success("Конфигурация удалена");
    },
    onError: (e) => toast.error(errorMessageFromUnknown(e)),
  });

  const reset = () => {
    setPhase("topic");
    setWizIndex(0);
    setTopic("");
    setHeroMode("auto");
    setAutoMode(true);
    setAnswers({});
    setSkipWizard(false);
    setSavePresetAfterCreate(false);
    setSavePresetName("");
    setSelectedPresetName(null);
  };

  const buildPatch = () => {
    const patch: Record<string, unknown> = {};
    for (const q of wizardQuestions) {
      const v = answers[q.field];
      if (v === undefined) continue;
      if (q.field === "image_relax" || q.field === "video_relax") {
        patch[q.field] = v === "yes";
      } else {
        patch[q.field] = v;
      }
    }
    if (wizardQuestions.some((q) => q.field === "video_relax") && answers.video_generator !== "veo_3_1_fast") {
      patch.video_relax = false;
    }
    return patch;
  };

  const create = useMutation({
    mutationFn: async () => {
      const p = await api.createProject({
        topic: topic.trim(),
        hero_mode: heroMode,
        auto_mode: autoMode,
        sidebar_folder_id: folderId,
      });
      const patch = buildPatch();
      let result = p;
      if (Object.keys(patch).length) {
        result = await api.patchProject(p.id, patch);
      }
      const name = savePresetName.trim();
      if (savePresetAfterCreate && name) {
        await api.createGenerationConfigPreset({
          name,
          settings: answersToSettings(answers, wizardQuestions),
        });
      }
      return result;
    },
    onSuccess: (p) => {
      qc.invalidateQueries({ queryKey: ["projects"] });
      qc.invalidateQueries({ queryKey: ["generation-config-presets"] });
      onCreated(p);
      setOpen(false);
      reset();
      toast.success(`Проект «${p.topic}» создан`);
    },
    onError: (e) => toast.error(errorMessageFromUnknown(e)),
  });

  const totalSteps = 4 + wizardQuestions.length;
  const currentStepNum =
    phase === "topic"
      ? 1
      : phase === "config"
        ? 2
        : phase === "hero"
          ? 3
          : phase === "auto"
            ? 4
            : 4 + wizIndex + 1;

  const currentWizQ = wizardQuestions[wizIndex];
  const wizAnswered = phase !== "wizard" || !currentWizQ || answers[currentWizQ.field] !== undefined;

  const applyPreset = (preset: { id: string; name: string; settings: GenerationConfigPresetSettings }) => {
    setAnswers(presetToAnswers(preset.settings));
    setSkipWizard(true);
    setSelectedPresetName(preset.name);
    setSavePresetAfterCreate(false);
    setSavePresetName("");
    toast.success(`Конфигурация «${preset.name}» применена`);
  };

  const goNext = () => {
    if (phase === "topic") {
      if (!topic.trim()) {
        toast.error("Введите тему ролика");
        return;
      }
      setPhase("config");
      return;
    }
    if (phase === "config") {
      setPhase("hero");
      return;
    }
    if (phase === "hero") {
      setPhase("auto");
      return;
    }
    if (phase === "auto") {
      if (skipWizard || wizardQuestions.length === 0) {
        create.mutate();
        return;
      }
      setPhase("wizard");
      setWizIndex(0);
      return;
    }
    if (wizIndex < wizardQuestions.length - 1) {
      setWizIndex((i) => i + 1);
    } else {
      create.mutate();
    }
  };

  const goBack = () => {
    if (phase === "wizard") {
      if (wizIndex > 0) {
        setWizIndex((i) => i - 1);
        return;
      }
      setPhase("auto");
      return;
    }
    if (phase === "auto") {
      setPhase("hero");
      return;
    }
    if (phase === "hero") {
      setPhase("config");
      return;
    }
    if (phase === "config") {
      setPhase("topic");
    }
  };

  const isLast =
    (phase === "auto" && (skipWizard || wizardQuestions.length === 0)) ||
    (phase === "wizard" && wizIndex >= wizardQuestions.length - 1);

  const presets = presetsQ.data?.presets ?? [];

  return (
    <Dialog
      open={open}
      onOpenChange={(o) => {
        setOpen(o);
        if (!o) reset();
      }}
    >
      <DialogTrigger asChild>{trigger}</DialogTrigger>
      <DialogContent className="max-h-[90vh] max-w-lg overflow-y-auto">
        <DialogHeader>
          <DialogTitle>Новый проект</DialogTitle>
          <DialogDescription>
            Шаг {currentStepNum} из {totalSteps} — мастер как в Telegram-боте
          </DialogDescription>
        </DialogHeader>

        {(catalog.isLoading || presetsQ.isLoading) && (
          <div className="flex justify-center py-8">
            <Loader2 className="h-5 w-5 animate-spin text-muted-foreground" />
          </div>
        )}

        {!catalog.isLoading && !presetsQ.isLoading && phase === "topic" && (
          <div className="flex flex-col gap-2">
            <label className="text-xs font-medium text-muted-foreground">Тема ролика</label>
            <Input
              value={topic}
              onChange={(e) => setTopic(e.target.value)}
              placeholder="Например: 5 фактов о рачках в стиле киберпанк"
              autoFocus
            />
          </div>
        )}

        {!catalog.isLoading && !presetsQ.isLoading && phase === "config" && (
          <div className="flex flex-col gap-3">
            <p className="text-sm font-medium">Конфигурация генерации</p>
            {selectedPresetName && (
              <p className="rounded-lg border border-emerald-400/40 bg-emerald-400/10 px-3 py-2 text-xs">
                Применена: <b>{selectedPresetName}</b> — мастер настроек будет пропущен
              </p>
            )}
            {presets.length === 0 ? (
              <p className="text-xs text-muted-foreground">Сохранённых конфигураций пока нет.</p>
            ) : (
              <div className="flex flex-col gap-2">
                <p className="text-xs text-muted-foreground">Выбор конфигурации</p>
                {presets.map((p) => (
                  <div key={p.id} className="flex gap-1">
                    <button
                      type="button"
                      onClick={() => applyPreset(p)}
                      className={cn(
                        "flex-1 rounded-xl border px-3 py-2 text-left text-xs",
                        selectedPresetName === p.name
                          ? "border-emerald-400/50 bg-emerald-400/10"
                          : "border-white/10 hover:border-white/20",
                      )}
                    >
                      <div className="font-medium">{p.name}</div>
                    </button>
                    <Button
                      type="button"
                      variant="ghost"
                      size="icon"
                      className="shrink-0"
                      onClick={() => deletePreset.mutate(p.id)}
                      disabled={deletePreset.isPending}
                    >
                      <Trash2 className="h-3.5 w-3.5" />
                    </Button>
                  </div>
                ))}
              </div>
            )}
            <button
              type="button"
              onClick={() => {
                setSkipWizard(false);
                setSelectedPresetName(null);
                setAnswers({});
                setSavePresetAfterCreate(true);
                setSavePresetName("");
              }}
              className={cn(
                "rounded-xl border px-3 py-2 text-left text-xs",
                savePresetAfterCreate && !skipWizard
                  ? "border-violet-400/50 bg-violet-500/10"
                  : "border-white/10",
              )}
            >
              <div className="font-medium">➕ Создание конфигурации</div>
              <div className="text-muted-foreground">Пройти мастер и сохранить настройки для следующих проектов</div>
            </button>
            <button
              type="button"
              onClick={() => {
                setSkipWizard(false);
                setSelectedPresetName(null);
                setSavePresetAfterCreate(false);
              }}
              className={cn(
                "rounded-xl border px-3 py-2 text-left text-xs",
                !skipWizard && !savePresetAfterCreate
                  ? "border-amber-400/50 bg-amber-400/10"
                  : "border-white/10",
              )}
            >
              <div className="font-medium">⚙️ Настроить вручную</div>
              <div className="text-muted-foreground">Без сохранения пресета — только для этого проекта</div>
            </button>
          </div>
        )}

        {!catalog.isLoading && phase === "hero" && (
          <div className="flex flex-col gap-2">
            <label className="text-xs font-medium text-muted-foreground">Главный герой</label>
            <div className="flex gap-1">
              {(["auto", "hero", "no_hero"] as const).map((mode) => (
                <Button
                  key={mode}
                  type="button"
                  variant={heroMode === mode ? "default" : "outline"}
                  size="sm"
                  className="flex-1 text-xs"
                  onClick={() => setHeroMode(mode)}
                >
                  {mode === "auto" ? "Авто" : mode === "hero" ? "Есть герой" : "Без героя"}
                </Button>
              ))}
            </div>
          </div>
        )}

        {!catalog.isLoading && phase === "auto" && (
          <div className="flex flex-col gap-3">
            <label className="text-xs font-medium text-muted-foreground">Режим проверки</label>
            <div className="flex flex-col gap-2">
              <button
                type="button"
                onClick={() => setAutoMode(false)}
                className={cn(
                  "rounded-xl border px-3 py-2 text-left text-xs",
                  !autoMode ? "border-amber-400/50 bg-amber-400/10" : "border-white/10",
                )}
              >
                <div className="font-medium">Ручная проверка</div>
                <div className="text-muted-foreground">Жёлтый кружок на HITL-нодах, кнопки одобрения</div>
              </button>
              <button
                type="button"
                onClick={() => setAutoMode(true)}
                className={cn(
                  "rounded-xl border px-3 py-2 text-left text-xs",
                  autoMode ? "border-violet-400/50 bg-violet-500/10" : "border-white/10",
                )}
              >
                <div className="font-medium">Автопроверка GPT</div>
                <div className="text-muted-foreground">Как массовая генерация — иконка GPT на нодах</div>
              </button>
            </div>
          </div>
        )}

        {!catalog.isLoading && phase === "wizard" && currentWizQ && (
          <div className="flex flex-col gap-3">
            <p className="text-sm font-medium">{currentWizQ.title}</p>
            <div
              className="grid gap-2"
              style={{
                gridTemplateColumns: `repeat(${Math.min(currentWizQ.cols, 4)}, minmax(0, 1fr))`,
              }}
            >
              {currentWizQ.choices.map((ch) => (
                <Button
                  key={ch.id}
                  type="button"
                  variant={answers[currentWizQ.field] === ch.id ? "default" : "outline"}
                  size="sm"
                  className="h-auto min-h-9 whitespace-normal py-2 text-xs"
                  onClick={() =>
                    setAnswers((a) => ({
                      ...a,
                      [currentWizQ.field]: ch.id,
                    }))
                  }
                >
                  {ch.label}
                </Button>
              ))}
            </div>
            {savePresetAfterCreate && isLast && (
              <div className="flex flex-col gap-1 pt-2">
                <label className="text-xs font-medium text-muted-foreground">
                  Имя конфигурации для сохранения
                </label>
                <Input
                  value={savePresetName}
                  onChange={(e) => setSavePresetName(e.target.value)}
                  placeholder="Например: GPT Image 2 — 16:9 — Relax"
                />
              </div>
            )}
          </div>
        )}

        <DialogFooter className="gap-2 sm:justify-between">
          <Button
            type="button"
            variant="ghost"
            size="sm"
            onClick={goBack}
            disabled={phase === "topic" || create.isPending}
          >
            <ChevronLeft className="h-3.5 w-3.5" />
            Назад
          </Button>
          <div className="flex gap-2">
            <Button type="button" variant="ghost" onClick={() => setOpen(false)}>
              Отмена
            </Button>
            <Button
              type="button"
              onClick={goNext}
              disabled={
                create.isPending ||
                catalog.isLoading ||
                presetsQ.isLoading ||
                !wizAnswered ||
                (savePresetAfterCreate && isLast && phase === "wizard" && !savePresetName.trim())
              }
            >
              {create.isPending ? (
                <Loader2 className="h-3.5 w-3.5 animate-spin" />
              ) : (
                <ChevronRight className="h-3.5 w-3.5" />
              )}
              {isLast ? "Создать" : "Далее"}
            </Button>
          </div>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
