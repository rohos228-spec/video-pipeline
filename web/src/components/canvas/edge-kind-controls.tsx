"use client";

import { useMemo } from "react";
import { useInternalNode, ViewportPortal, type Edge } from "@xyflow/react";
import { toast } from "sonner";
import { useQueryClient } from "@tanstack/react-query";
import { api } from "@/lib/api";
import { errorMessageFromUnknown } from "@/lib/error-message";
import {
  edgeKindLabel,
  nextEdgeKind,
  type OperatorEdgeKind,
} from "@/lib/gpt-operator";
import { useCanvasActionsOptional } from "./canvas-actions-context";
import { cn } from "@/lib/utils";

function EdgeKindMarker({
  edge,
  onCycle,
}: {
  edge: Edge;
  onCycle: (edgeId: string, next: OperatorEdgeKind) => void;
}) {
  const source = useInternalNode(edge.source);
  const target = useInternalNode(edge.target);
  if (!source?.internals?.positionAbsolute || !target?.internals?.positionAbsolute) {
    return null;
  }

  const sw = source.measured?.width ?? 108;
  const sh = source.measured?.height ?? 90;
  const th = target.measured?.height ?? 90;

  const sx = source.internals.positionAbsolute.x + sw;
  const sy = source.internals.positionAbsolute.y + sh / 2;
  const tx = target.internals.positionAbsolute.x;
  const ty = target.internals.positionAbsolute.y + th / 2;
  const x = (sx + tx) / 2;
  const y = (sy + ty) / 2 - 14;

  const kind = ((edge.data as { kind?: string } | undefined)?.kind ||
    "after") as OperatorEdgeKind;
  const fileCount = Number((edge.data as { fileCount?: number } | undefined)?.fileCount || 0);
  const label =
    kind === "feed" || kind === "review"
      ? `${edgeKindLabel(kind)}${fileCount ? ` · ${fileCount}` : ""}`
      : edgeKindLabel(kind);

  return (
    <button
      type="button"
      title="Тип связи: клик — следующий (после → файлы → проверка → если ok)"
      className={cn(
        "nodrag nopan pointer-events-auto absolute -translate-x-1/2 -translate-y-1/2 rounded-full border px-2 py-0.5 text-[9px] font-semibold uppercase tracking-wide shadow-md backdrop-blur transition hover:scale-105",
        kind === "after" && "border-white/20 bg-black/70 text-muted-foreground",
        kind === "feed" && "border-emerald-400/40 bg-emerald-950/80 text-emerald-100",
        kind === "review" && "border-violet-400/40 bg-violet-950/80 text-violet-100",
        kind === "gate" && "border-amber-400/40 bg-amber-950/80 text-amber-100",
      )}
      style={{ left: x, top: y, zIndex: 1001 }}
      onMouseDown={(e) => e.stopPropagation()}
      onClick={(e) => {
        e.stopPropagation();
        onCycle(edge.id, nextEdgeKind(kind));
      }}
    >
      {label}
    </button>
  );
}

export function EdgeKindControls({
  edges,
  onEdgesLocal,
}: {
  edges: Edge[];
  onEdgesLocal: (updater: (prev: Edge[]) => Edge[]) => void;
}) {
  const actions = useCanvasActionsOptional();
  const qc = useQueryClient();
  const visible = useMemo(() => edges.filter((e) => e.source && e.target), [edges]);

  if (!actions?.projectId) return null;

  const cycle = async (edgeId: string, next: OperatorEdgeKind) => {
    const projectId = actions.projectId!;
    onEdgesLocal((prev) =>
      prev.map((e) =>
        e.id === edgeId
          ? {
              ...e,
              data: { ...(e.data as object), kind: next },
              label: edgeKindLabel(next),
            }
          : e,
      ),
    );
    try {
      await api.patchCanvasEdgeKind(projectId, edgeId, next);
      void qc.invalidateQueries({ queryKey: ["project", projectId] });
      window.setTimeout(() => {
        window.dispatchEvent(new CustomEvent("canvas-save-workflow"));
      }, 40);
      toast.message(`Связь: ${edgeKindLabel(next)}`);
    } catch (e) {
      toast.error(errorMessageFromUnknown(e));
    }
  };

  return (
    <ViewportPortal>
      {visible.map((edge) => (
        <EdgeKindMarker key={edge.id} edge={edge} onCycle={cycle} />
      ))}
    </ViewportPortal>
  );
}

/** Подтянуть fileCount на feed/review из resolve целевой excel_gpt ноды. */
export function applyResolveFileCountsToEdges(
  edges: Edge[],
  resolveByTarget: Record<string, { incomingEdges?: { id: string; fileCount: number }[] }>,
): Edge[] {
  return edges.map((e) => {
    const target = (e.target as string) || "";
    const res = resolveByTarget[target];
    if (!res?.incomingEdges) return e;
    const hit = res.incomingEdges.find((x) => x.id === e.id);
    if (!hit) return e;
    return {
      ...e,
      data: { ...(e.data as object), kind: (e.data as { kind?: string })?.kind, fileCount: hit.fileCount },
    };
  });
}
