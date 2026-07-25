"use client";

import { useEffect, useMemo, useState } from "react";
import { useInternalNode, ViewportPortal, type Edge } from "@xyflow/react";
import { Check, X } from "lucide-react";
import { toast } from "sonner";
import { useQueryClient } from "@tanstack/react-query";
import { api } from "@/lib/api";
import { errorMessageFromUnknown } from "@/lib/error-message";
import {
  EDGE_KIND_OPTIONS,
  edgeKindLabel,
  type OperatorEdgeKind,
} from "@/lib/gpt-operator";
import { useCanvasActionsOptional } from "./canvas-actions-context";
import { cn } from "@/lib/utils";

function EdgeKindMarker({
  edge,
  open,
  onOpen,
  onClose,
  onSelect,
}: {
  edge: Edge;
  open: boolean;
  onOpen: () => void;
  onClose: () => void;
  onSelect: (edgeId: string, next: OperatorEdgeKind) => void;
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

  const current = EDGE_KIND_OPTIONS.find((o) => o.value === kind);

  return (
    <div
      className="pointer-events-none absolute"
      style={{ left: x, top: y, zIndex: open ? 10050 : 1001 }}
    >
      <button
        type="button"
        title={current ? `${current.title}: ${current.hint}` : "Тип связи"}
        className={cn(
          "nodrag nopan pointer-events-auto absolute -translate-x-1/2 -translate-y-1/2 rounded-full border px-2.5 py-1 text-[9px] font-semibold uppercase tracking-wide shadow-md backdrop-blur transition hover:scale-105",
          kind === "after" && "border-white/20 bg-black/75 text-muted-foreground",
          kind === "feed" && "border-emerald-400/40 bg-emerald-950/85 text-emerald-100",
          kind === "review" && "border-violet-400/40 bg-violet-950/85 text-violet-100",
          kind === "gate" && "border-amber-400/40 bg-amber-950/85 text-amber-100",
          open && "ring-2 ring-primary/50",
        )}
        onMouseDown={(e) => e.stopPropagation()}
        onClick={(e) => {
          e.stopPropagation();
          if (open) onClose();
          else onOpen();
        }}
      >
        {label}
      </button>

      {open ? (
        <div
          className="nodrag nopan pointer-events-auto absolute left-1/2 top-3 z-[10051] w-[240px] -translate-x-1/2 animate-in fade-in zoom-in-95 duration-150"
          onMouseDown={(e) => e.stopPropagation()}
          onClick={(e) => e.stopPropagation()}
        >
          <div className="rounded-xl border border-white/15 bg-gradient-to-b from-[hsl(240_10%_10%/0.98)] to-[hsl(240_12%_6%/0.99)] p-2 shadow-2xl shadow-black/70 backdrop-blur-xl">
            <div className="mb-1.5 flex items-center justify-between gap-2 px-1">
              <span className="text-[9px] font-semibold uppercase tracking-widest text-amber-400/90">
                Тип связи
              </span>
              <button
                type="button"
                className="flex h-5 w-5 items-center justify-center rounded text-muted-foreground hover:bg-white/10 hover:text-foreground"
                onClick={(e) => {
                  e.stopPropagation();
                  onClose();
                }}
                title="Закрыть"
              >
                <X className="h-3 w-3" />
              </button>
            </div>
            <div className="flex flex-col gap-1">
              {EDGE_KIND_OPTIONS.map((opt) => {
                const active = opt.value === kind;
                return (
                  <button
                    key={opt.value}
                    type="button"
                    onClick={(e) => {
                      e.stopPropagation();
                      onSelect(edge.id, opt.value);
                      onClose();
                    }}
                    className={cn(
                      "flex w-full items-start gap-2 rounded-lg border px-2.5 py-2 text-left transition",
                      active
                        ? "border-primary/40 bg-primary/15"
                        : "border-white/8 bg-black/25 hover:border-white/20 hover:bg-white/[0.04]",
                    )}
                  >
                    <span
                      className={cn(
                        "mt-0.5 flex h-4 w-4 shrink-0 items-center justify-center rounded-full border",
                        active
                          ? "border-primary/60 bg-primary/30 text-primary"
                          : "border-white/15 text-transparent",
                      )}
                    >
                      <Check className="h-2.5 w-2.5" />
                    </span>
                    <span className="min-w-0 flex-1">
                      <span className="block text-[11px] font-semibold text-foreground">
                        {opt.title}
                      </span>
                      <span className="mt-0.5 block text-[10px] leading-snug text-muted-foreground">
                        {opt.hint}
                      </span>
                    </span>
                  </button>
                );
              })}
            </div>
          </div>
        </div>
      ) : null}
    </div>
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
  const [openEdgeId, setOpenEdgeId] = useState<string | null>(null);
  const [mounted, setMounted] = useState(false);
  const visible = useMemo(() => edges.filter((e) => e.source && e.target), [edges]);

  useEffect(() => setMounted(true), []);

  useEffect(() => {
    if (!openEdgeId) return;
    const close = (ev: Event) => {
      const t = ev.target as HTMLElement;
      if (t.closest("[data-edge-kind-menu]")) return;
      setOpenEdgeId(null);
    };
    document.addEventListener("pointerdown", close, true);
    return () => document.removeEventListener("pointerdown", close, true);
  }, [openEdgeId]);

  if (!actions?.projectId || !mounted) return null;

  const selectKind = async (edgeId: string, next: OperatorEdgeKind) => {
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
      const opt = EDGE_KIND_OPTIONS.find((o) => o.value === next);
      toast.message(opt ? `${opt.title}: ${opt.hint}` : `Связь: ${edgeKindLabel(next)}`);
    } catch (e) {
      toast.error(errorMessageFromUnknown(e));
    }
  };

  const portal = (
    <ViewportPortal>
      {visible.map((edge) => (
        <div key={edge.id} data-edge-kind-menu>
          <EdgeKindMarker
            edge={edge}
            open={openEdgeId === edge.id}
            onOpen={() => setOpenEdgeId(edge.id)}
            onClose={() => setOpenEdgeId(null)}
            onSelect={selectKind}
          />
        </div>
      ))}
    </ViewportPortal>
  );

  return portal;
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
      data: {
        ...(e.data as object),
        kind: (e.data as { kind?: string })?.kind,
        fileCount: hit.fileCount,
      },
    };
  });
}
