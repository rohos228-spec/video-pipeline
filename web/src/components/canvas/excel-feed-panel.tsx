"use client";

import { useRef, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { FileSpreadsheet, Loader2 } from "lucide-react";
import { toast } from "sonner";
import { errorMessageFromUnknown } from "@/lib/error-message";
import { api } from "@/lib/api";
import { Button } from "@/components/ui/button";

type OptimisticExcel = {
  fileName: string;
  topics: string[];
};

export function ExcelFeedPanel({
  projectId,
  nodeKey,
}: {
  projectId: number;
  nodeKey: string;
}) {
  const fileRef = useRef<HTMLInputElement>(null);
  const qc = useQueryClient();
  const [optimistic, setOptimistic] = useState<OptimisticExcel | null>(null);

  const projectQ = useQuery({
    queryKey: ["project", projectId],
    queryFn: () => api.getProject(projectId),
  });

  const meta = (projectQ.data?.meta || {}) as Record<string, unknown>;
  const metaFile =
    typeof meta.mass_excel_file === "string" ? meta.mass_excel_file : null;
  const metaTopics = Array.isArray(meta.mass_excel_topics)
    ? (meta.mass_excel_topics as string[])
    : [];

  const fileName = optimistic?.fileName ?? metaFile;
  const topics = optimistic?.topics ?? metaTopics;
  const topicCount = topics.length;
  const topicsPreview = topics.slice(0, 5);

  const upload = useMutation({
    mutationFn: (file: File) => api.parseMassTopicsXlsx(projectId, file),
    onSuccess: async (r, file) => {
      setOptimistic({ fileName: file.name, topics: r.topics });
      const project = await api.getProject(projectId);
      const nextMeta: Record<string, unknown> = {
        ...((project.meta || {}) as Record<string, unknown>),
        mass_excel_topics: r.topics,
        mass_queue_topics: r.topics,
        mass_excel_file: file.name,
        mass_factory: true,
        excel_feed_node: nodeKey,
      };
      if (r.revision != null) nextMeta.mass_excel_revision = r.revision;
      await api.patchProject(projectId, { meta: nextMeta });
      await qc.invalidateQueries({ queryKey: ["project", projectId] });
      window.dispatchEvent(
        new CustomEvent("canvas-excel-topics-loaded", {
          detail: { topics: r.topics, nodeKey },
        }),
      );
      window.dispatchEvent(new CustomEvent("canvas-save-workflow"));
      toast.success(`Excel: ${r.count} тем — проведите связи к нодам «План»`);
    },
    onError: (e) => toast.error(errorMessageFromUnknown(e)),
  });

  return (
    <div
      className="nodrag nopan border-t border-emerald-500/20 bg-emerald-500/5 px-3 py-2"
      onMouseDown={(e) => e.stopPropagation()}
    >
      <input
        ref={fileRef}
        type="file"
        accept=".xlsx,application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        className="hidden"
        onChange={(e) => {
          const f = e.target.files?.[0];
          if (f) upload.mutate(f);
          e.target.value = "";
        }}
      />
      <div className="flex items-center justify-between gap-2">
        <span className="flex items-center gap-1 text-[10px] text-emerald-300">
          <FileSpreadsheet className="h-3 w-3" />
          {fileName ? `${fileName} (${topicCount})` : "topics.xlsx"}
        </span>
        <Button
          type="button"
          size="sm"
          variant="outline"
          className="h-6 text-[10px]"
          disabled={upload.isPending}
          onClick={() => fileRef.current?.click()}
        >
          {upload.isPending ? (
            <Loader2 className="h-3 w-3 animate-spin" />
          ) : (
            "Загрузить"
          )}
        </Button>
      </div>
      {topicsPreview.length > 0 && (
        <p className="mt-1 line-clamp-2 text-[9px] text-muted-foreground">
          {topicsPreview.join(" · ")}
          {topicCount > topicsPreview.length ? "…" : ""}
        </p>
      )}
    </div>
  );
}
