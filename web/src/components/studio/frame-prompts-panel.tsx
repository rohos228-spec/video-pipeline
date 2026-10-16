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
  const framesQuery = useQuery({
    queryKey: ["frames", projectId],
    queryFn: () => api.listFrames(projectId),
  });

  const mediaReviewQuery = useQuery({
    queryKey: ["media-review", projectId, "images"],
    queryFn: () => api.listMediaReview(projectId, "images"),
    enabled: Boolean(projectId),
  });

  const items: NodeResultItem[] = useMemo(() => {
    const frames = framesQuery.data ?? [];
    const mediaList = mediaReviewQuery.data ?? [];
    const previewByNumber = new Map<number, string>();
    const previewByFrameId = new Map<number, string>();
    for (const m of mediaList) {
      if (m.preview_url) {
        if (m.number != null) previewByNumber.set(m.number, m.preview_url);
        if (m.frame_id != null) previewByFrameId.set(m.frame_id, m.preview_url);
      }
    }

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
        previewUrl:
          previewByNumber.get(f.number) ?? previewByFrameId.get(f.id) ?? null,
        content:
          (field === "image_prompt" ? f.image_prompt : f.animation_prompt) ??
          "",
        frameNumber: f.number,
      }));
  }, [framesQuery.data, mediaReviewQuery.data, field]);

  if (framesQuery.isLoading || mediaReviewQuery.isLoading) {
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
