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
  const projectId = hitl.project_id;
  const qc = useQueryClient();

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
        hitl={hitl}
        onFrameApproved={() => qc.invalidateQueries({ queryKey: ["hitl", projectId] })}
      />

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
    </div>
  );
}

