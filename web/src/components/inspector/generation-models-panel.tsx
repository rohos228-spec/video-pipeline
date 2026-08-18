"use client";

import type { ReactNode } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Image as ImageIcon, Loader2, Video } from "lucide-react";
import { toast } from "sonner";
import { errorMessageFromUnknown } from "@/lib/error-message";
import { api } from "@/lib/api";
import type { ProjectDetail } from "@/lib/types";

type GenChoice = { id: string; label: string; description?: string };

const DEFAULT_IMAGE = "gpt_image_2";
const DEFAULT_VIDEO = "veo_3_fast";

function choiceById(list: GenChoice[], id: string): GenChoice | undefined {
  return list.find((c) => c.id === id);
}

function ModelSelect({
  label,
  icon,
  currentId,
  choices,
  disabled,
  onPick,
}: {
  label: string;
  icon: ReactNode;
  currentId: string;
  choices: GenChoice[];
  disabled?: boolean;
  onPick: (id: string) => void;
}) {
  const current = choiceById(choices, currentId);
  const options = current || !currentId
    ? choices
    : [{ id: currentId, label: currentId }, ...choices];

  return (
    <div className="rounded-lg border border-border/50 bg-card/40 px-2.5 py-2">
      <div className="mb-1.5 flex items-center gap-1.5 text-xs font-medium">
        {icon}
        {label}
      </div>
      <p className="mb-2 text-[10px] leading-snug text-muted-foreground">
        Сейчас:{" "}
        <span className="font-medium text-foreground/80">
          {current?.label?.replace(/^\+\s*/, "") || currentId}
        </span>
        <span className="font-mono text-muted-foreground"> · {currentId}</span>
      </p>
      <select
        className="h-8 w-full rounded-md border border-border/60 bg-background px-2 text-xs"
        value={currentId}
        disabled={disabled || options.length === 0}
        onChange={(e) => onPick(e.target.value)}
      >
        {options.map((ch) => (
          <option key={ch.id} value={ch.id}>
            {ch.label.replace(/^\+\s*/, "")}
          </option>
        ))}
      </select>
    </div>
  );
}

/** Выбор моделей картинок/видео для текущего проекта — сразу под «Потоки». */
export function GenerationModelsPanel({ project }: { project: ProjectDetail }) {
  const qc = useQueryClient();
  const catalog = useQuery({
    queryKey: ["wizard-catalog"],
    queryFn: api.wizardCatalog,
    staleTime: 60_000,
  });

  const imageChoices = catalog.data?.image_generators ?? [];
  const videoChoices = catalog.data?.video_generators ?? [];
  const imageId = project.image_generator || catalog.data?.defaults?.image_generator || DEFAULT_IMAGE;
  const videoId = project.video_generator || catalog.data?.defaults?.video_generator || DEFAULT_VIDEO;

  const patch = useMutation({
    mutationFn: (body: { image_generator?: string; video_generator?: string }) =>
      api.patchProject(project.id, body),
    onMutate: async (body) => {
      await qc.cancelQueries({ queryKey: ["project", project.id] });
      const prev = qc.getQueryData<ProjectDetail>(["project", project.id]);
      if (prev) {
        qc.setQueryData<ProjectDetail>(["project", project.id], { ...prev, ...body });
      }
      return { prev };
    },
    onSuccess: (data, vars) => {
      qc.setQueryData(["project", project.id], data);
      if (vars.image_generator) {
        const lab = choiceById(imageChoices, vars.image_generator)?.label?.replace(/^\+\s*/, "")
          || vars.image_generator;
        toast.success(`Картинки: ${lab}`);
      }
      if (vars.video_generator) {
        const lab = choiceById(videoChoices, vars.video_generator)?.label?.replace(/^\+\s*/, "")
          || vars.video_generator;
        toast.success(`Видео: ${lab}`);
      }
    },
    onError: (e, _vars, ctx) => {
      if (ctx?.prev) qc.setQueryData(["project", project.id], ctx.prev);
      toast.error(errorMessageFromUnknown(e));
    },
    onSettled: () => {
      void qc.invalidateQueries({ queryKey: ["project", project.id] });
    },
  });

  return (
    <div className="flex flex-col gap-2 rounded-xl border border-white/10 bg-white/[0.03] p-3">
      <div className="flex items-center gap-2 text-[10px] font-semibold uppercase tracking-wider text-muted-foreground">
        Модели генерации
        {patch.isPending || catalog.isLoading ? (
          <Loader2 className="h-3 w-3 animate-spin opacity-70" />
        ) : null}
      </div>
      <p className="text-[10px] leading-snug text-muted-foreground">
        Шаги картинок и видео этого проекта берут модель отсюда. Смена сразу
        пишется в проект.
      </p>
      <ModelSelect
        label="Картинки"
        icon={<ImageIcon className="h-3.5 w-3.5 text-muted-foreground" />}
        currentId={imageId}
        choices={imageChoices}
        disabled={patch.isPending || catalog.isLoading}
        onPick={(id) => {
          if (id !== imageId) patch.mutate({ image_generator: id });
        }}
      />
      <ModelSelect
        label="Видео"
        icon={<Video className="h-3.5 w-3.5 text-muted-foreground" />}
        currentId={videoId}
        choices={videoChoices}
        disabled={patch.isPending || catalog.isLoading}
        onPick={(id) => {
          if (id !== videoId) patch.mutate({ video_generator: id });
        }}
      />
    </div>
  );
}
