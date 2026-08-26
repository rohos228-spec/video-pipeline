"use client";

import { useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ChevronLeft, ChevronRight, Loader2, Trash2 } from "lucide-react";
import { toast } from "sonner";
import { errorMessageFromUnknown } from "@/lib/error-message";
import { api } from "@/lib/api";
import { projectDisplayName } from "@/lib/project-display";
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

type Phase = "title" | "config" | "hero" | "auto" | "wizard";

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
  const [phase, setPhase] = useState<Phase>("title");
  const [wizIndex, setWizIndex] = useState(0);
  const [projectTitle, setProjectTitle] = useState("");
  const [heroMode, setHeroMode] = useState<"hero" | "no_hero" | "auto">("auto");
  const [autoMode, setAutoMode] = useState(false);
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
    const byGen = catalog.data?.image_resolutions_by_generator ?? {};
    return qs
      .filter((q) => {
        if (q.field === "image_quality") {
          const g = answers.image_generator;
          if (!g || !["gpt_image_1_5", "gpt_image_2", "gpt_image_2_vip"].includes(g)) return false;
        }
        if (q.field === "video_relax" && answers.video_generator !== "veo_3_1_fast") {
          return false;
        }
        return true;
      })
      .map((q) => {
        if (q.field !== "image_resolution") return q;
        const g = answers.image_generator;
        const allowed = g ? byGen[g] : undefined;
        if (!allowed?.length) return q;
        return {
          ...q,
          choices: q.choices.filter((c) => allowed.includes(c.id)),
          cols: Math.min(q.cols, Math.max(allowed.length, 1)),
        };
      });
  }, [
    catalog.data?.questions,
    catalog.data?.image_resolutions_by_generator,
    answers.video_generator,
    answers.image_generator,
  ]);

  const deletePreset = useMutation({
    mutationFn: (id: string) => api.deleteGenerationConfigPreset(id),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["generation-config-presets"] });
      toast.success("Конфигурация удалена");
    },
    onError: (e) => toast.error(errorMessageFromUnknown(e)),
  });

  const reset = () => {
    setPhase("title");
    setWizIndex(0);
    setProjectTitle("");
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
        title: projectTitle.trim(),
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
      toast.success(`Проект «${projectDisplayName(p)}» создан`);
    },
    onError: (e) => toast.error(errorMessageFromUnknown(e)),
  });

  const totalSteps = 4 + wizardQuestions.length;
  const effectiveTotalSteps = skipWizard ? 4 : totalSteps;
  const currentStepNum =
    phase === "title"
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
    if (phase === "title") {
      if (!projectTitle.trim()) {
        toast.error("Введите название проекта");
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
    if (phase === "wizard") {
      if (wizIndex < wizardQuestions.length - 1) {
        setWizIndex((i) => i + 1);
        return;
      }
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
      setPhase("title");
    }
  };

  const presets = presetsQ.data?.presets ?? [];
  const isLast =
    (phase === "auto" && (skipWizard || wizardQuestions.length === 0)) ||
    (phase === "wizard" && wizIndex === wizardQuestions.length - 1);

  return (
    <Dialog
      open={open}
      onOpenChange={(o) => {
        setOpen(o);
        if (!o) reset();
      }}
    >
      <DialogTrigger asChild>{trigger}</DialogTrigger>
      <DialogContent
        className="max-h-[90vh] max-w-lg overflow-y-auto rounded-2xl border border-zinc-800 bg-zinc-950/95 shadow-2xl backdrop-blur-xl"
        onPointerDownOutside={(e) => e.preventDefault()}
        onInteractOutside={(e) => e.preventDefault()}
      >
        <DialogHeader>
          <DialogTitle className="text-base font-bold text-zinc-100">Новый проект</DialogTitle>
          <DialogDescription className="text-xs font-medium text-sky-400">
            Шаг {currentStepNum} из {effectiveTotalSteps}
          </DialogDescription>
        </DialogHeader>

        {(catalog.isLoading || presetsQ.isLoading) && (
          <div className="flex justify-center py-8">
            <Loader2 className="h-5 w-5 animate-spin text-sky-400" />
          </div>
        )}

        {!catalog.isLoading && !presetsQ.isLoading && phase === "title" && (
          <div className="flex flex-col gap-2">
            <label className="text-xs font-semibold text-zinc-300">Название проекта</label>
            <Input
              value={projectTitle}
              onChange={(e) => setProjectTitle(e.target.value)}
              placeholder="Например: Полет в космос"
              autoFocus
              className="h-10 rounded-xl border-zinc-700 bg-zinc-900/80 px-3.5 text-sm font-medium text-zinc-100 placeholder:text-zinc-500 focus-visible:border-sky-500 focus-visible:ring-1 focus-visible:ring-sky-500/70"
            />
            <p className="text-[11.5px] text-zinc-400">
              Тема ролика заполняется отдельно в первой ноде графа.
            </p>
          </div>
        )}

        {!catalog.isLoading && !presetsQ.isLoading && phase === "config" && (
          <div className="flex flex-col gap-3">
            <p className="text-sm font-semibold text-zinc-100">Конфигурация генерации</p>
            {selectedPresetName && (
              <div className="rounded-xl border border-sky-400/40 bg-sky-500/15 px-3.5 py-2.5 text-xs text-sky-200">
                Применена: <b className="text-white">{selectedPresetName}</b> — детальные настройки генерации будут взяты из этой конфигурации
              </div>
            )}
            {presets.length === 0 ? (
              <p className="text-xs text-muted-foreground">Сохранённых конфигураций пока нет.</p>
            ) : (
              <div className="flex flex-col gap-2">
                <p className="text-xs font-medium text-zinc-400">Сохранённые конфигурации</p>
                {presets.map((p) => (
                  <div key={p.id} className="flex items-center gap-1.5">
                    <button
                      type="button"
                      onClick={() => applyPreset(p)}
                      className={cn(
                        "flex-1 rounded-xl border px-3.5 py-2.5 text-left text-xs transition-all",
                        selectedPresetName === p.name
                          ? "border-sky-500/60 bg-sky-500/20 text-sky-100 ring-1 ring-sky-400/50 shadow-sm"
                          : "border-zinc-800 bg-zinc-900/60 text-zinc-300 hover:bg-zinc-800 hover:border-zinc-700",
                      )}
                    >
                      <div className="font-bold text-zinc-100">{p.name}</div>
                    </button>
                    <Button
                      type="button"
                      variant="ghost"
                      size="icon"
                      className="h-9 w-9 shrink-0 text-zinc-400 hover:text-red-400 hover:bg-zinc-800/80 rounded-xl"
                      onClick={() => deletePreset.mutate(p.id)}
                      disabled={deletePreset.isPending}
                      title="Удалить конфигурацию"
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
                toast.info("Выбран режим создания новой конфигурации");
              }}
              className={cn(
                "rounded-xl border px-3.5 py-2.5 text-left text-xs transition-all",
                savePresetAfterCreate && !skipWizard
                  ? "border-sky-500/60 bg-sky-500/20 text-sky-100 ring-1 ring-sky-400/50 shadow-sm"
                  : "border-zinc-800 bg-zinc-900/60 text-zinc-300 hover:bg-zinc-800 hover:border-zinc-700",
              )}
            >
              <div className="font-bold text-zinc-100">➕ Создание новой конфигурации</div>
              <div className="text-zinc-400 mt-0.5">Пройти мастер и сохранить настройки в шаблон для будущих проектов</div>
            </button>
            <button
              type="button"
              onClick={() => {
                setSkipWizard(false);
                setSelectedPresetName(null);
                setSavePresetAfterCreate(false);
                toast.info("Выбран режим ручной настройки");
              }}
              className={cn(
                "rounded-xl border px-3.5 py-2.5 text-left text-xs transition-all",
                !skipWizard && !savePresetAfterCreate
                  ? "border-sky-500/60 bg-sky-500/20 text-sky-100 ring-1 ring-sky-400/50 shadow-sm"
                  : "border-zinc-800 bg-zinc-900/60 text-zinc-300 hover:bg-zinc-800 hover:border-zinc-700",
              )}
            >
              <div className="font-bold text-zinc-100">⚙️ Настроить вручную</div>
              <div className="text-zinc-400 mt-0.5">Пройти все шаги без сохранения шаблона — только для этого проекта</div>
            </button>
          </div>
        )}

        {!catalog.isLoading && phase === "hero" && (
          <div className="flex flex-col gap-2">
            <label className="text-xs font-semibold text-zinc-300">Главный герой</label>
            <div className="flex gap-2">
              {(["auto", "hero", "no_hero"] as const).map((mode) => (
                <Button
                  key={mode}
                  type="button"
                  variant="outline"
                  size="sm"
                  className={cn(
                    "flex-1 text-xs font-semibold rounded-xl transition-all h-9",
                    heroMode === mode
                      ? "border-sky-500/60 bg-sky-500/20 text-sky-200 ring-1 ring-sky-400/50 shadow-sm"
                      : "border-zinc-800 bg-zinc-900/60 text-zinc-300 hover:bg-zinc-800 hover:text-white",
                  )}
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
            <label className="text-xs font-semibold text-zinc-300">Режим проверки</label>
            <div className="flex flex-col gap-2">
              <button
                type="button"
                onClick={() => setAutoMode(false)}
                className={cn(
                  "rounded-xl border px-3.5 py-2.5 text-left text-xs transition-all",
                  !autoMode
                    ? "border-sky-500/60 bg-sky-500/20 text-sky-100 ring-1 ring-sky-400/50 shadow-sm"
                    : "border-zinc-800 bg-zinc-900/60 text-zinc-300 hover:bg-zinc-800",
                )}
              >
                <div className="font-bold text-zinc-100">Ручная проверка</div>
                <div className="text-zinc-400 mt-0.5">Жёлтый кружок на HITL-нодах, кнопки одобрения</div>
              </button>
              <button
                type="button"
                onClick={() => setAutoMode(true)}
                className={cn(
                  "rounded-xl border px-3.5 py-2.5 text-left text-xs transition-all",
                  autoMode
                    ? "border-sky-500/60 bg-sky-500/20 text-sky-100 ring-1 ring-sky-400/50 shadow-sm"
                    : "border-zinc-800 bg-zinc-900/60 text-zinc-300 hover:bg-zinc-800",
                )}
              >
                <div className="font-bold text-zinc-100">Автопроверка GPT</div>
                <div className="text-zinc-400 mt-0.5">Как массовая генерация — иконка GPT на нодах</div>
              </button>
            </div>
          </div>
        )}

        {!catalog.isLoading && phase === "wizard" && currentWizQ && (
          <div className="flex flex-col gap-3">
            <p className="text-sm font-semibold text-zinc-100">{currentWizQ.title}</p>
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
                  variant="outline"
                  size="sm"
                  className={cn(
                    "h-auto min-h-10 whitespace-normal py-2 text-xs font-semibold rounded-xl transition-all",
                    answers[currentWizQ.field] === ch.id
                      ? "border-sky-500/60 bg-sky-500/20 text-sky-200 ring-1 ring-sky-400/50 shadow-sm"
                      : "border-zinc-800 bg-zinc-900/60 text-zinc-300 hover:bg-zinc-800 hover:text-white",
                  )}
                  onClick={() =>
                    setAnswers((a) => {
                      const next: WizardAnswers = {
                        ...a,
                        [currentWizQ.field]: ch.id,
                      };
                      if (currentWizQ.field === "image_generator") {
                        const allowed =
                          catalog.data?.image_resolutions_by_generator?.[ch.id] ?? [];
                        if (
                          allowed.length &&
                          next.image_resolution &&
                          !allowed.includes(next.image_resolution)
                        ) {
                          next.image_resolution = allowed.includes("2k")
                            ? "2k"
                            : allowed[0];
                        }
                      }
                      return next;
                    })
                  }
                >
                  {ch.label}
                </Button>
              ))}
            </div>
            {savePresetAfterCreate && isLast && (
              <div className="flex flex-col gap-1.5 pt-2">
                <label className="text-xs font-semibold text-zinc-300">
                  Имя конфигурации для сохранения
                </label>
                <Input
                  value={savePresetName}
                  onChange={(e) => setSavePresetName(e.target.value)}
                  placeholder="Например: GPT Image 2 — 16:9 — Безлимит"
                  className="h-10 rounded-xl border-zinc-700 bg-zinc-900/80 px-3.5 text-sm font-medium text-zinc-100 placeholder:text-zinc-500 focus-visible:border-sky-500 focus-visible:ring-1 focus-visible:ring-sky-500/70"
                />
              </div>
            )}
          </div>
        )}

        <DialogFooter className="gap-2 sm:justify-between pt-3 border-t border-zinc-800/80">
          <Button
            type="button"
            variant="ghost"
            className="h-9 px-4 rounded-xl text-xs font-semibold text-zinc-400 hover:text-zinc-100 hover:bg-zinc-800/80 transition-all"
            onClick={goBack}
            disabled={phase === "title" || create.isPending}
          >
            <ChevronLeft className="h-3.5 w-3.5 mr-1" />
            Назад
          </Button>
          <div className="flex items-center gap-2">
            <Button
              type="button"
              variant="ghost"
              className="h-9 px-5 min-w-[88px] rounded-xl text-xs font-semibold text-zinc-300 hover:text-zinc-100 hover:bg-zinc-800/80 border border-zinc-800 transition-all"
              onClick={() => setOpen(false)}
            >
              Отмена
            </Button>
            <Button
              type="button"
              className="h-9 px-5 min-w-[88px] gap-1.5 text-xs font-semibold text-white bg-sky-600 hover:bg-sky-500 border border-sky-400/50 shadow-md shadow-sky-500/20 rounded-xl transition-all disabled:opacity-50"
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
