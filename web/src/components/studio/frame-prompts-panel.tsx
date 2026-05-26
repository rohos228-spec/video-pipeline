"use client";

import { useMemo } from "react";
import { useQuery } from "@tanstack/react-query";
import { Loader2 } from "lucide-react";
import { api } from "@/lib/api";
import { FramePromptsView } from "@/components/canvas/node-result-views";
import type { NodeResultItem } from "@/lib/node-result-resolver";

export function FramePromptsPanel({
  projectId,
  field,
}: {
  projectId: number;
  field: "image_prompt" | "animation_prompt";
}) {
  const project = useQuery({
    queryKey: ["project", projectId],
    queryFn: () => api.getProject(projectId),
  });

  const items: NodeResultItem[] = useMemo(() => {
    const frames = project.data?.frames ?? [];
    return frames
      .filter((f) => {
        const text =
          field === "image_prompt" ? f.image_prompt : f.animation_prompt;
        return Boolean(text?.trim());
      })
      .map((f) => ({
        id: `frame_${f.id}`,
        label: `Кадр ${f.number}`,
        kind: "text" as const,
        content:
          (field === "image_prompt" ? f.image_prompt : f.animation_prompt) ??
          "",
        frameNumber: f.number,
      }));
  }, [project.data?.frames, field]);

  if (project.isLoading) {
    return (
      <div className="flex justify-center py-12">
        <Loader2 className="h-6 w-6 animate-spin text-muted-foreground" />
      </div>
    );
  }

  if (!items.length) {
    return (
      <p className="text-sm text-muted-foreground">
        {field === "image_prompt"
          ? "Промты кадров пусты — сначала выполните шаг «Промты картинок» (6) или перечитайте Excel."
          : "Промты анимации пусты — выполните шаг «Промты анимации»."}
      </p>
    );
  }

  return (
    <div className="flex min-h-[50vh] flex-col gap-2">
      <p className="text-xs text-muted-foreground">
        {items.length} кадр(ов) — эти тексты уходят в outsee при генерации.
      </p>
      <FramePromptsView items={items} />
    </div>
  );
}
