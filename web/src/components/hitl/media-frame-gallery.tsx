"use client";

import { useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { CheckCircle2, Loader2, PenLine, XCircle } from "lucide-react";
import { toast } from "sonner";
import { errorMessageFromUnknown } from "@/lib/error-message";
import { api } from "@/lib/api";
import {
  frameVisualHitlDecision,
  listPendingVisualHitl,
  type VisualHitlKind,
} from "@/lib/hitl-visual-bulk";
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
  visualKind = "approve_images",
  onFrameDecided,
}: {
  projectId: number;
  kind: "images" | "videos";
  items: MediaFrameItem[];
  showApproveButtons?: boolean;
  visualKind?: VisualHitlKind;
  onFrameDecided?: () => void;
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
    onError: (e) => toast.error(errorMessageFromUnknown(e)),
  });

  const frameDecision = useMutation({
    mutationFn: ({
      frameId,
      decision,
    }: {
      frameId: number;
      decision: "approve" | "reject";
    }) => frameVisualHitlDecision(projectId, frameId, visualKind, decision),
    onSuccess: async () => {
      qc.invalidateQueries({ queryKey: ["media-review", projectId, kind] });
      qc.invalidateQueries({ queryKey: ["hitl", projectId] });
      onFrameDecided?.();
      const pending = await listPendingVisualHitl(projectId, visualKind);
      if (pending.length === 0) {
        toast.success("Все кадры проверены — пайплайн может продолжить работу");
      }
    },
    onError: (e) => toast.error(errorMessageFromUnknown(e)),
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
        const approved =
          frame.status === "image_approved" || frame.status === "video_approved";
        const rejected = frame.status === "failed";
        return (
          <div
            key={frame.frame_id}
            className="flex flex-col overflow-hidden rounded-xl border border-white/10 bg-black/40 backdrop-blur-md shadow-sm"
          >
            <div className="flex items-center justify-between border-b border-white/10 bg-white/[0.02] px-2.5 py-1.5">
              <Badge variant="muted" className="font-mono text-[10px] text-cyan-300">
                #{frame.number}
              </Badge>
              <span className="text-[9px] text-muted-foreground">{frame.status}</span>
            </div>
            <div className="aspect-[9/16] max-h-48 bg-black/40">
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
            <p className="line-clamp-3 px-2.5 py-2 text-[11px] leading-snug text-zinc-300">
              {frame.voiceover_text}
            </p>
            <div className="flex gap-1.5 border-t border-white/10 bg-white/[0.01] p-1.5">
              <Button
                type="button"
                size="sm"
                variant="ghost"
                className="h-7 flex-1 px-1 text-[10px] text-zinc-400 hover:text-cyan-300 hover:bg-cyan-950/30 rounded-lg"
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
                  className="inline-flex h-7 flex-1 items-center justify-center rounded-lg text-[11px] font-bold text-cyan-400 hover:bg-cyan-950/30 transition-colors"
                >
                  ↓
                </a>
              )}
            </div>
            {showApproveButtons && kind === "images" && (
              <div className="flex gap-1.5 border-t border-white/10 bg-white/[0.01] p-1.5">
                <Button
                  type="button"
                  size="sm"
                  variant="outline"
                  className={cn(
                    "h-7 flex-1 text-[10px] font-semibold text-red-300 hover:text-red-200 bg-red-950/25 border-red-500/30 rounded-lg hover:bg-red-900/40 transition-all",
                    rejected && "border-red-500/50 bg-red-950/50",
                  )}
                  disabled={frameDecision.isPending || rejected}
                  onClick={() =>
                    frameDecision.mutate({
                      frameId: frame.frame_id,
                      decision: "reject",
                    })
                  }
                >
                  {frameDecision.isPending ? (
                    <Loader2 className="h-3 w-3 animate-spin" />
                  ) : (
                    <XCircle className="h-3 w-3 text-red-400" />
                  )}
                  {rejected ? "Отклонено" : "Отклонить"}
                </Button>
                <Button
                  type="button"
                  size="sm"
                  className={cn(
                    "h-7 flex-1 text-[10px] font-semibold text-emerald-200 bg-emerald-950/30 border border-emerald-500/40 rounded-lg hover:bg-emerald-900/50 shadow-sm shadow-emerald-500/20 transition-all",
                    approved && "bg-emerald-500/30 text-emerald-100 border-emerald-400",
                  )}
                  disabled={frameDecision.isPending || approved}
                  onClick={() =>
                    frameDecision.mutate({
                      frameId: frame.frame_id,
                      decision: "approve",
                    })
                  }
                >
                  {frameDecision.isPending ? (
                    <Loader2 className="h-3 w-3 animate-spin" />
                  ) : (
                    <CheckCircle2 className="h-3 w-3 text-emerald-400" />
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
