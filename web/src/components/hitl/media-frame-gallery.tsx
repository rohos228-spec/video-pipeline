"use client";

import { useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { CheckCircle2, Loader2, PenLine } from "lucide-react";
import { toast } from "sonner";
import { api } from "@/lib/api";
import type { HITLDTO } from "@/lib/types";
import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/textarea";
import { Badge } from "@/components/ui/badge";
import { cn } from "@/lib/utils";

export type MediaFrameItem = {
  frame_id: number;
  number: number;
  voiceover_text?: string | null;
  image_prompt?: string | null;
  animation_prompt?: string | null;
  status?: string;
  preview_url?: string | null;
};

const COLS = 3;

export function MediaFrameGallery({
  projectId,
  kind,
  items,
  showApproveButtons = false,
  hitl,
  onFrameApproved,
}: {
  projectId: number;
  kind: "images" | "videos";
  items: MediaFrameItem[];
  showApproveButtons?: boolean;
  hitl?: HITLDTO | null;
  onFrameApproved?: () => void;
}) {
  const qc = useQueryClient();
  const [editFrame, setEditFrame] = useState<number | null>(null);
  const [editPrompt, setEditPrompt] = useState("");

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
    onError: (e) => toast.error(String(e)),
  });

  const approveFrame = useMutation({
    mutationFn: async (frameId: number) => {
      await api.patchFrame(projectId, frameId, { status: "image_approved" });
      const hitls = await api.listProjectHitl(projectId);
      const pending = hitls.find(
        (h) =>
          h.frame_id === frameId &&
          h.decision === "pending" &&
          h.kind === "approve_images",
      );
      if (pending) {
        await api.submitHitlDecision(pending.id, { decision: "approve" });
      }
    },
    onSuccess: async () => {
      qc.invalidateQueries({ queryKey: ["media-review", projectId, kind] });
      qc.invalidateQueries({ queryKey: ["hitl", projectId] });
      onFrameApproved?.();

      if (hitl && kind === "images") {
        const media = await api.listMediaReview(projectId, "images");
        const withPreview = media.filter((f) => f.preview_url);
        const allApproved =
          withPreview.length > 0 &&
          withPreview.every((f) => f.status === "image_approved");
        if (allApproved && hitl.decision === "pending") {
          await api.submitHitlDecision(hitl.id, { decision: "approve" });
          qc.invalidateQueries({ queryKey: ["hitl", projectId] });
          toast.success("Все картинки одобрены — пайплайн продолжит работу");
          onFrameApproved?.();
        }
      }
    },
    onError: (e) => toast.error(String(e)),
  });

  if (items.length === 0) {
    return (
      <p className="py-6 text-center text-sm text-muted-foreground">
        Нет кадров с превью. Дождись генерации или открой панель HITL.
      </p>
    );
  }

  return (
    <div
      className="grid gap-3"
      style={{ gridTemplateColumns: `repeat(${COLS}, minmax(0, 1fr))` }}
    >
      {items.map((frame) => {
        const approved = frame.status === "image_approved";
        return (
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
                    (kind === "images"
                      ? frame.image_prompt
                      : frame.animation_prompt) ?? "",
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
            {showApproveButtons && kind === "images" && (
              <div className="border-t border-border p-1.5">
                <Button
                  type="button"
                  size="sm"
                  className={cn(
                    "h-7 w-full text-[10px]",
                    approved && "bg-success/20 text-success hover:bg-success/30",
                  )}
                  variant={approved ? "outline" : "default"}
                  disabled={approveFrame.isPending || approved}
                  onClick={() => approveFrame.mutate(frame.frame_id)}
                >
                  {approveFrame.isPending ? (
                    <Loader2 className="h-3 w-3 animate-spin" />
                  ) : (
                    <CheckCircle2 className="h-3 w-3" />
                  )}
                  {approved ? "Одобрено" : "Одобрить"}
                </Button>
              </div>
            )}
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
                  className="mt-1 h-7 w-full text-[10px]"
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
        );
      })}
    </div>
  );
}
