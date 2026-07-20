"use client";

/**
 * Панель настроек генерации в стиле outsee.io/image (gpt-image-2 и др.):
 * модель → соотношение → разрешение → детализация → Безлимит.
 * Сохраняет в Project через PATCH (пайплайн читает эти поля).
 */

import { useEffect, useMemo, useState, type ReactNode } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ImageIcon, Loader2, Video, ExternalLink } from "lucide-react";
import { toast } from "sonner";
import { errorMessageFromUnknown } from "@/lib/error-message";
import { api } from "@/lib/api";
import type { ProjectDetail } from "@/lib/types";
import { cn } from "@/lib/utils";
import { Button } from "@/components/ui/button";

type Choice = { id: string; label: string; description?: string };

const GPT_IDS = new Set(["gpt_image_1_5", "gpt_image_2"]);

export function OutseeGenPanel({ project }: { project: ProjectDetail }) {
  const qc = useQueryClient();
  const catalog = useQuery({
    queryKey: ["wizard-catalog"],
    queryFn: api.wizardCatalog,
  });

  const [imageGenerator, setImageGenerator] = useState(project.image_generator || "gpt_image_2");
  const [aspectRatio, setAspectRatio] = useState(project.aspect_ratio || "9_16");
  const [imageResolution, setImageResolution] = useState(project.image_resolution || "2k");
  const [imageQuality, setImageQuality] = useState(project.image_quality || "medium");
  const [imageRelax, setImageRelax] = useState(Boolean(project.image_relax));
  const [videoGenerator, setVideoGenerator] = useState(project.video_generator || "veo_3_fast");
  const [videoResolution, setVideoResolution] = useState(project.video_resolution || "1080p");
  const [videoRelax, setVideoRelax] = useState(Boolean(project.video_relax));

  useEffect(() => {
    setImageGenerator(project.image_generator || "gpt_image_2");
    setAspectRatio(project.aspect_ratio || "9_16");
    setImageResolution(project.image_resolution || "2k");
    setImageQuality(project.image_quality || "medium");
    setImageRelax(Boolean(project.image_relax));
    setVideoGenerator(project.video_generator || "veo_3_fast");
    setVideoResolution(project.video_resolution || "1080p");
    setVideoRelax(Boolean(project.video_relax));
  }, [
    project.id,
    project.image_generator,
    project.aspect_ratio,
    project.image_resolution,
    project.image_quality,
    project.image_relax,
    project.video_generator,
    project.video_resolution,
    project.video_relax,
  ]);

  const imageGenerators = catalog.data?.questions?.find((q) => q.field === "image_generator")?.choices
    ?? [];
  const aspects = catalog.data?.questions?.find((q) => q.field === "aspect_ratio")?.choices ?? [];
  const allResolutions = catalog.data?.questions?.find((q) => q.field === "image_resolution")?.choices
    ?? [];
  const qualities = catalog.data?.questions?.find((q) => q.field === "image_quality")?.choices ?? [];
  const videoGenerators = catalog.data?.questions?.find((q) => q.field === "video_generator")?.choices
    ?? [];
  const videoResolutions = catalog.data?.questions?.find((q) => q.field === "video_resolution")?.choices
    ?? [];
  const byGen = catalog.data?.image_resolutions_by_generator ?? {};

  const resolutions = useMemo(() => {
    const allowed = byGen[imageGenerator];
    if (!allowed?.length) return allResolutions;
    return allResolutions.filter((c) => allowed.includes(c.id));
  }, [allResolutions, byGen, imageGenerator]);

  useEffect(() => {
    const allowed = byGen[imageGenerator];
    if (!allowed?.length) return;
    if (!allowed.includes(imageResolution)) {
      setImageResolution(allowed.includes("2k") ? "2k" : allowed[0]);
    }
  }, [imageGenerator, byGen, imageResolution]);

  const showQuality = GPT_IDS.has(imageGenerator);
  const showVideoRelax = videoGenerator === "veo_3_1_fast";

  const dirty =
    imageGenerator !== (project.image_generator || "gpt_image_2") ||
    aspectRatio !== (project.aspect_ratio || "9_16") ||
    imageResolution !== (project.image_resolution || "2k") ||
    imageQuality !== (project.image_quality || "medium") ||
    imageRelax !== Boolean(project.image_relax) ||
    videoGenerator !== (project.video_generator || "veo_3_fast") ||
    videoResolution !== (project.video_resolution || "1080p") ||
    videoRelax !== Boolean(project.video_relax);

  const save = useMutation({
    mutationFn: () =>
      api.patchProject(project.id, {
        image_generator: imageGenerator,
        aspect_ratio: aspectRatio,
        image_resolution: imageResolution,
        image_quality: showQuality ? imageQuality : project.image_quality,
        image_relax: imageRelax,
        video_generator: videoGenerator,
        video_resolution: videoResolution,
        video_relax: showVideoRelax ? videoRelax : false,
      }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["project", project.id] });
      toast.success("Настройки outsee сохранены");
    },
    onError: (e) => toast.error(errorMessageFromUnknown(e)),
  });

  const outseeSlug = outseeSlugFromId(imageGenerator);

  return (
    <div className="overflow-hidden rounded-xl border border-white/10 bg-[#121212] shadow-[inset_0_1px_0_rgba(255,255,255,0.04)]">
      <div className="flex items-center justify-between gap-2 border-b border-white/10 bg-[#171717] px-3 py-2.5">
        <div className="flex items-center gap-2">
          <ImageIcon className="h-3.5 w-3.5 text-amber-400/90" />
          <div>
            <div className="text-[11px] font-semibold tracking-wide text-white/90">
              Outsee · Create image
            </div>
            <div className="text-[10px] text-white/40">как на outsee.io/create</div>
          </div>
        </div>
        <a
          href={`https://outsee.io/create?type=image&model=${encodeURIComponent(outseeSlug)}`}
          target="_blank"
          rel="noreferrer"
          className="inline-flex items-center gap-1 text-[10px] text-white/45 hover:text-amber-300"
        >
          открыть сайт
          <ExternalLink className="h-3 w-3" />
        </a>
      </div>

      <div className="flex flex-col gap-3 p-3">
        {catalog.isLoading && (
          <span className="flex items-center gap-1 text-[10px] text-white/40">
            <Loader2 className="h-3 w-3 animate-spin" />
            каталог моделей…
          </span>
        )}

        <Field label="Модель">
          <ChoiceGrid
            cols={1}
            choices={imageGenerators}
            value={imageGenerator}
            onChange={setImageGenerator}
          />
        </Field>

        <Field label="Соотношение">
          <ChoiceGrid cols={4} choices={aspects} value={aspectRatio} onChange={setAspectRatio} mono />
        </Field>

        <Field label="Разрешение">
          <ChoiceGrid
            cols={Math.min(4, Math.max(resolutions.length, 1))}
            choices={resolutions}
            value={imageResolution}
            onChange={setImageResolution}
            mono
          />
        </Field>

        {showQuality && (
          <Field label="Детализация">
            <ChoiceGrid cols={3} choices={qualities} value={imageQuality} onChange={setImageQuality} />
          </Field>
        )}

        <LimitToggle on={imageRelax} onChange={setImageRelax} />

        <div className="my-1 h-px bg-white/10" />

        <div className="flex items-center gap-2 text-[10px] font-semibold uppercase tracking-[0.12em] text-white/40">
          <Video className="h-3 w-3" />
          Видео
        </div>

        <Field label="Модель видео">
          <ChoiceGrid
            cols={1}
            choices={videoGenerators}
            value={videoGenerator}
            onChange={setVideoGenerator}
          />
        </Field>

        <Field label="Разрешение видео">
          <ChoiceGrid
            cols={2}
            choices={videoResolutions}
            value={videoResolution}
            onChange={setVideoResolution}
            mono
          />
        </Field>

        {showVideoRelax && (
          <LimitToggle
            on={videoRelax}
            onChange={setVideoRelax}
            label="Безлимит (Veo Fast)"
          />
        )}

        <Button
          type="button"
          disabled={!dirty || save.isPending}
          onClick={() => save.mutate()}
          className={cn(
            "mt-1 h-10 w-full text-xs font-semibold",
            dirty
              ? "bg-amber-500 text-black hover:bg-amber-400"
              : "bg-white/10 text-white/40",
          )}
        >
          {save.isPending ? (
            <>
              <Loader2 className="mr-1 h-3.5 w-3.5 animate-spin" />
              Сохранение…
            </>
          ) : dirty ? (
            "Применить к проекту"
          ) : (
            "Сохранено"
          )}
        </Button>

        <p className="text-[10px] leading-relaxed text-white/35">
          Пайплайн img/video ходит в outsee с этими настройками · модель{" "}
          <span className="font-mono text-white/55">{outseeSlug}</span>.
        </p>
      </div>
    </div>
  );
}

function outseeSlugFromId(id: string): string {
  const map: Record<string, string> = {
    gpt_image_2: "gpt-image-2",
    gpt_image_1_5: "gpt-image-1.5",
    nano_banana_2: "nano-banana-2",
    nano_banana: "nano-banana",
    nano_banana_pro: "nano-banana-pro",
    seedream_4_5: "seedream-4.5",
    seedream_5_0_lite: "seedream-5-lite",
    seedream_5_pro: "seedream-5-pro",
  };
  return map[id] || id.replace(/_/g, "-");
}

function Field({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div className="flex flex-col gap-1.5">
      <div className="px-0.5 text-[10px] font-medium uppercase tracking-[0.12em] text-white/40">
        {label}
      </div>
      {children}
    </div>
  );
}

function ChoiceGrid({
  choices,
  value,
  onChange,
  cols,
  mono,
}: {
  choices: Choice[];
  value: string;
  onChange: (id: string) => void;
  cols: number;
  mono?: boolean;
}) {
  if (!choices.length) {
    return <div className="text-[10px] text-white/30">нет опций</div>;
  }
  return (
    <div
      className="grid gap-1"
      style={{ gridTemplateColumns: `repeat(${cols}, minmax(0, 1fr))` }}
    >
      {choices.map((ch) => {
        const active = value === ch.id;
        return (
          <button
            key={ch.id}
            type="button"
            title={ch.description || ch.label}
            onClick={() => onChange(ch.id)}
            className={cn(
              "min-h-[34px] rounded-lg border px-1.5 py-1.5 text-center text-[11px] transition",
              mono && "font-mono tabular-nums",
              active
                ? "border-amber-400/40 bg-white/[0.08] text-amber-200"
                : "border-transparent bg-[#222] text-white/50 hover:border-white/15 hover:text-white/80",
            )}
          >
            {ch.label}
          </button>
        );
      })}
    </div>
  );
}

function LimitToggle({
  on,
  onChange,
  label = "Безлимит",
}: {
  on: boolean;
  onChange: (v: boolean) => void;
  label?: string;
}) {
  return (
    <button
      type="button"
      onClick={() => onChange(!on)}
      className={cn(
        "flex w-full items-center justify-between rounded-lg border px-2.5 py-2 transition",
        on ? "border-amber-400/35 bg-amber-500/10" : "border-white/10 bg-[#1a1a1a]",
      )}
    >
      <span className={cn("text-xs font-semibold", on ? "text-gray-200" : "text-gray-400")}>
        {label}
      </span>
      <span
        className={cn(
          "relative h-5 w-9 rounded-full transition-colors",
          on ? "bg-amber-500" : "bg-zinc-600",
        )}
      >
        <span
          className={cn(
            "absolute top-0.5 h-4 w-4 rounded-full bg-white shadow transition-all",
            on ? "left-[18px]" : "left-[2px]",
          )}
        />
      </span>
    </button>
  );
}
