"use client";

import { useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ChevronLeft, ChevronRight, Loader2 } from "lucide-react";
import { toast } from "sonner";
import { api } from "@/lib/api";
import type { ProjectSummary } from "@/lib/types";
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

type Phase = "topic" | "hero" | "auto" | "wizard";

type WizardAnswers = Record<string, string>;

export function NewProjectWizard({
  trigger,
  onCreated,
}: {
  trigger: React.ReactNode;
  onCreated: (p: ProjectSummary) => void;
}) {
  const [open, setOpen] = useState(false);
  const [phase, setPhase] = useState<Phase>("topic");
  const [wizIndex, setWizIndex] = useState(0);
  const [topic, setTopic] = useState("");
  const [heroMode, setHeroMode] = useState<"hero" | "no_hero" | "auto">("auto");
  const [autoMode, setAutoMode] = useState(false);
  const [answers, setAnswers] = useState<WizardAnswers>({});

  const catalog = useQuery({
    queryKey: ["wizard-catalog"],
    queryFn: api.wizardCatalog,
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

  const reset = () => {
    setPhase("topic");
    setWizIndex(0);
    setTopic("");
    setHeroMode("auto");
    setAutoMode(false);
    setAnswers({});
  };

  const create = useMutation({
    mutationFn: async () => {
      const p = await api.createProject({
        topic: topic.trim(),
        hero_mode: heroMode,
        auto_mode: autoMode,
      });
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
      if (Object.keys(patch).length) {
        return api.patchProject(p.id, patch);
      }
      return p;
    },
    onSuccess: (p) => {
      qc.invalidateQueries({ queryKey: ["projects"] });
      onCreated(p);
      setOpen(false);
      reset();
      toast.success(`Проект «${p.topic}» создан`);
    },
    onError: (e) => toast.error(String(e)),
  });

  const totalSteps = 3 + wizardQuestions.length;
  const currentStepNum =
    phase === "topic" ? 1 : phase === "hero" ? 2 : phase === "auto" ? 3 : 3 + wizIndex + 1;

  const currentWizQ = wizardQuestions[wizIndex];
  const wizAnswered = phase !== "wizard" || !currentWizQ || answers[currentWizQ.field] !== undefined;

  const goNext = () => {
    if (phase === "topic") {
      if (!topic.trim()) {
        toast.error("Введите тему ролика");
        return;
      }
      setPhase("hero");
      return;
    }
    if (phase === "hero") {
      setPhase("auto");
      return;
    }
    if (phase === "auto") {
      if (wizardQuestions.length === 0) {
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
      setPhase("topic");
    }
  };

  const isLast =
    (phase === "auto" && wizardQuestions.length === 0) ||
    (phase === "wizard" && wizIndex >= wizardQuestions.length - 1);

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

        {catalog.isLoading && (
          <div className="flex justify-center py-8">
            <Loader2 className="h-5 w-5 animate-spin text-muted-foreground" />
          </div>
        )}

        {!catalog.isLoading && phase === "topic" && (
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
              disabled={create.isPending || catalog.isLoading || !wizAnswered}
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
