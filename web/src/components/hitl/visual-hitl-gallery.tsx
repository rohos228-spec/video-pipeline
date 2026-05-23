"use client";

import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  CheckCircle2,
  Loader2,
  PenLine,
  RefreshCw,
  XCircle,
} from "lucide-react";
import { toast } from "sonner";
import { api } from "@/lib/api";
import type { HITLDTO } from "@/lib/types";
import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/input";
import { Badge } from "@/components/ui/badge";
const COLS = 3;

export function VisualHitlGallery({
  hitl,
  onDecided,
}: {
  hitl: HITLDTO;
  onDecided: () => void;
}) {
  const kind = hitl.kind === "approve_videos" ? "videos" : "images";
  const projectId = hitl.project_id;
  const qc = useQueryClient();
  const [editFrame, setEditFrame] = useState<number | null>(null);
  const [editPrompt, setEditPrompt] = useState("");

  const media = useQuery({
    queryKey: ["media-review", projectId, kind],
    queryFn: () => api.listMediaReview(projectId, kind),
    enabled: hitl.kind === "approve_images" || hitl.kind === "approve_videos",
  });

  const submit = useMutation({
    mutationFn: (body: { decision: string; edited_prompt?: string }) =>
      api.submitHitlDecision(hitl.id, body),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["hitl"] });
      toast.success("Решение записано");
      onDecided();
    },
    onError: (e) => toast.error(String(e)),
  });

  const saveFramePrompt = useMutation({
    mutationFn: ({
      frameId,
      prompt,
    }: {
      frameId: number;
      prompt: string;
    }) =>
      api.patchFrame(projectId, frameId, {
        ...(kind === "images"
          ? { image_prompt: prompt }
          : { animation_prompt: prompt }),
      }),
    onSuccess: () => {
      toast.success("Промт кадра сохранён");
      qc.invalidateQueries({ queryKey: ["media-review", projectId, kind] });
      setEditFrame(null);
    },
  });

  const items = (media.data ?? []).filter((f) => f.preview_url || f.file_path);

  if (media.isLoading) {
    return (
      <div className="flex justify-center py-12">
        <Loader2 className="h-6 w-6 animate-spin text-muted-foreground" />
      </div>
    );
  }

  if (items.length === 0) {
    return (
      <p className="py-6 text-center text-sm text-muted-foreground">
        Нет кадров с превью. Одобри через Telegram или дождись генерации.
      </p>
    );
  }

  return (
    <div className="flex flex-col gap-4">
      <div className="grid gap-3" style={{ gridTemplateColumns: `repeat(${COLS}, minmax(0, 1fr))` }}>
        {items.map((frame) => (
          <div
            key={frame.frame_id}
            className="flex flex-col overflow-hidden rounded-lg border border-border bg-card/40"
          >
            <div className="flex items-center justify-between border-b border-border px-2 py-1">
              <Badge variant="muted" className="font-mono text-[10px]">
                #{frame.number}
              </Badge>
              <span className="text-[9px] text-muted-foreground">{frame.status}</span>
            </div>
            <div className="aspect-[9/16] max-h-48 bg-muted/30">
              {kind === "videos" ? (
                <video
                  src={frame.preview_url!}
                  controls
                  className="h-full w-full object-cover"
                />
              ) : (
                <img
                  src={frame.preview_url!}
                  alt={`Кадр ${frame.number}`}
                  className="h-full w-full object-cover"
                />
              )}
            </div>
            <p className="line-clamp-3 px-2 py-1.5 text-[10px] leading-snug text-muted-foreground">
              {frame.voiceover_text}
            </p>
            <div className="flex gap-1 border-t border-border p-1.5">
              <Button
                type="button"
                size="sm"
                variant="ghost"
                className="h-7 flex-1 px-1 text-[10px]"
                onClick={() => {
                  setEditFrame(frame.frame_id);
                  setEditPrompt(
                    (kind === "images" ? frame.image_prompt : frame.animation_prompt) ?? "",
                  );
                }}
              >
                <PenLine className="h-3 w-3" />
              </Button>
              {frame.preview_url && (
                <a
                  href={frame.preview_url}
                  download
                  className="inline-flex h-7 flex-1 items-center justify-center rounded-md text-[10px] text-primary hover:bg-accent"
                >
                  ↓
                </a>
              )}
            </div>
            {editFrame === frame.frame_id && (
              <div className="border-t border-border p-2">
                <Textarea
                  value={editPrompt}
                  onChange={(e) => setEditPrompt(e.target.value)}
                  rows={3}
                  className="text-[10px]"
                />
                <Button
                  size="sm"
                  className="mt-1 w-full h-7 text-[10px]"
                  onClick={() =>
                    saveFramePrompt.mutate({
                      frameId: frame.frame_id,
                      prompt: editPrompt,
                    })
                  }
                >
                  Сохранить промт
                </Button>
              </div>
            )}
          </div>
        ))}
      </div>

      {editFrame === null && (
        <div className="flex flex-wrap justify-end gap-2 border-t border-border pt-3">
          <Button
            variant="ghost"
            size="sm"
            className="text-destructive"
            disabled={submit.isPending}
            onClick={() => submit.mutate({ decision: "reject" })}
          >
            <XCircle className="h-3.5 w-3.5" />
            Отклонить все
          </Button>
          <Button
            variant="outline"
            size="sm"
            disabled={submit.isPending}
            onClick={() => submit.mutate({ decision: "regenerate" })}
          >
            <RefreshCw className="h-3.5 w-3.5" />
            Перегенерировать
          </Button>
          <Button
            size="sm"
            disabled={submit.isPending}
            onClick={() => submit.mutate({ decision: "approve" })}
          >
            {submit.isPending ? (
              <Loader2 className="h-3.5 w-3.5 animate-spin" />
            ) : (
              <CheckCircle2 className="h-3.5 w-3.5" />
            )}
            Одобрить все
          </Button>
        </div>
      )}
    </div>
  );
}
