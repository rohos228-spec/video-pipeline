"use client";

import { useQueryClient } from "@tanstack/react-query";
import type { NodeResultSnapshot } from "@/lib/node-result-resolver";
import { getNodeSpec } from "@/lib/node-catalog";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { cn } from "@/lib/utils";
import { NodeResultViewBody } from "./node-result-views";

const WIDE_MODES = new Set([
  "xlsx_general_plan",
  "voiceover_wide",
  "xlsx_split_row",
  "frame_prompts",
  "frame_images",
  "frame_videos",
  "topic_edit",
]);

export function NodeResultPanel({
  open,
  onOpenChange,
  projectId,
  nodeType,
  snapshot,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  projectId: number;
  nodeType: string;
  snapshot: NodeResultSnapshot;
}) {
  const qc = useQueryClient();
  const spec = getNodeSpec(nodeType);
  const wide = WIDE_MODES.has(snapshot.viewMode);

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent
        className={cn(
          "flex flex-col overflow-hidden",
          wide
            ? "h-[92vh] max-h-[92vh] w-[96vw] max-w-[96vw] gap-2 p-4 sm:p-5"
            : "max-h-[85vh] max-w-2xl",
        )}
      >
        <DialogHeader className="shrink-0">
          <DialogTitle>Результат — {spec.label}</DialogTitle>
          <DialogDescription>{snapshot.summary}</DialogDescription>
        </DialogHeader>

        {!snapshot.hasResult && snapshot.viewMode === "default" ? (
          <p className="py-4 text-sm text-muted-foreground">
            Результат этого шага ещё не готов. Запустите ноду или дождитесь завершения генерации.
          </p>
        ) : (
          <NodeResultViewBody
            projectId={projectId}
            nodeType={nodeType}
            snapshot={snapshot}
            onHeroReplaced={() => {
              qc.invalidateQueries({ queryKey: ["project-assets", projectId] });
              qc.invalidateQueries({ queryKey: ["media-review", projectId] });
            }}
          />
        )}
      </DialogContent>
    </Dialog>
  );
}
