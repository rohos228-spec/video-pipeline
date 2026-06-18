"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  CheckCircle2,
  Loader2,
  RefreshCw,
  XCircle,
} from "lucide-react";
import { toast } from "sonner";
import { errorMessageFromUnknown } from "@/lib/error-message";
import { api } from "@/lib/api";
import {
  bulkVisualHitlDecision,
  type VisualHitlKind,
} from "@/lib/hitl-visual-bulk";
import type { HITLDTO } from "@/lib/types";
import { Button } from "@/components/ui/button";
import { MediaFrameGallery } from "@/components/hitl/media-frame-gallery";

export function VisualHitlGallery({
  hitl,
  onDecided,
}: {
  hitl: HITLDTO;
  onDecided: () => void;
}) {
  const kind = hitl.kind === "approve_videos" ? "videos" : "images";
  const visualKind: VisualHitlKind =
    hitl.kind === "approve_videos" ? "approve_videos" : "approve_images";
  const projectId = hitl.project_id;
  const qc = useQueryClient();

  const media = useQuery({
    queryKey: ["media-review", projectId, kind],
    queryFn: () => api.listMediaReview(projectId, kind),
    enabled: hitl.kind === "approve_images" || hitl.kind === "approve_videos",
  });

  const bulk = useMutation({
    mutationFn: (decision: "approve" | "reject" | "regenerate") =>
      bulkVisualHitlDecision(projectId, visualKind, decision),
    onSuccess: async (count, decision) => {
      await qc.invalidateQueries({ queryKey: ["hitl"] });
      await qc.invalidateQueries({ queryKey: ["media-review", projectId, kind] });
      await qc.invalidateQueries({ queryKey: ["projects"] });
      await qc.invalidateQueries({ queryKey: ["project-run"] });
      const labels: Record<string, string> = {
        approve: "Одобрено",
        reject: "Отклонено",
        regenerate: "Отправлено на перегенерацию",
      };
      toast.success(
        count > 0
          ? `${labels[decision]}: ${count} кадр(ов)`
          : "Нет ожидающих решений",
      );
      onDecided();
    },
    onError: (e) => toast.error(errorMessageFromUnknown(e)),
  });

  const items = (media.data ?? []).filter((f) => f.preview_url || f.file_path);

  if (media.isLoading) {
    return (
      <div className="flex justify-center py-12">
        <Loader2 className="h-6 w-6 animate-spin text-muted-foreground" />
      </div>
    );
  }

  return (
    <div className="flex flex-col gap-4">
      <MediaFrameGallery
        projectId={projectId}
        kind={kind}
        items={items}
        showApproveButtons={kind === "images"}
        visualKind={visualKind}
        onFrameDecided={() => {
          qc.invalidateQueries({ queryKey: ["hitl", projectId] });
          qc.invalidateQueries({ queryKey: ["media-review", projectId, kind] });
        }}
      />

      <div className="sticky bottom-0 z-10 flex flex-wrap justify-end gap-2 border-t border-border bg-background/95 py-2 backdrop-blur-sm">
        <Button
          variant="ghost"
          size="sm"
          className="h-8 text-destructive"
          disabled={bulk.isPending}
          onClick={() => bulk.mutate("reject")}
        >
          <XCircle className="h-3.5 w-3.5" />
          Отклонить все
        </Button>
        <Button
          variant="outline"
          size="sm"
          className="h-8"
          disabled={bulk.isPending}
          onClick={() => bulk.mutate("regenerate")}
        >
          <RefreshCw className="h-3.5 w-3.5" />
          Перегенерировать все
        </Button>
        <Button
          size="sm"
          className="h-8"
          disabled={bulk.isPending}
          onClick={() => bulk.mutate("approve")}
        >
          {bulk.isPending ? (
            <Loader2 className="h-3.5 w-3.5 animate-spin" />
          ) : (
            <CheckCircle2 className="h-3.5 w-3.5" />
          )}
          Одобрить все
        </Button>
      </div>
    </div>
  );
}
