"use client";

/**
 * Визуальное превью дизайна группы нод: read-only мини-канвас со своими
 * нодами (позиции из spec'а) и связями (kind → цвет/подпись). Входы группы
 * помечены изумрудным кольцом, выход — небесным.
 */

import { useMemo } from "react";
import {
  ReactFlow,
  Background,
  BackgroundVariant,
  type Node,
  type Edge,
  type NodeProps,
  Handle,
  Position,
} from "@xyflow/react";
import type { NodeGroupDetail } from "@/lib/types";
import { getNodeSpec } from "@/lib/node-catalog";
import { getNodeIcon } from "@/lib/node-icons";

interface PreviewNodeData extends Record<string, unknown> {
  label: string;
  nodeType: string;
  promptVariant: string | null;
  isEntry: boolean;
  isExit: boolean;
}

function PreviewNode({ data }: NodeProps) {
  const d = data as PreviewNodeData;
  const spec = getNodeSpec(d.nodeType);
  const Icon = getNodeIcon(spec.iconKey);
  return (
    <div
      className="relative w-[150px] rounded-xl border bg-card/95 px-2 py-1.5 shadow-md shadow-black/40"
      style={{
        borderColor: `hsl(${spec.accent} / 0.45)`,
        boxShadow: d.isEntry
          ? "0 0 0 2px hsl(150 70% 50% / 0.55)"
          : d.isExit
            ? "0 0 0 2px hsl(200 80% 55% / 0.55)"
            : undefined,
      }}
    >
      <Handle type="target" position={Position.Left} className="!opacity-0" />
      <Handle type="source" position={Position.Right} className="!opacity-0" />
      <div className="flex items-center gap-1.5">
        <span
          className="flex h-5 w-5 shrink-0 items-center justify-center rounded-full"
          style={{
            background: `linear-gradient(145deg, hsl(${spec.accent} / 0.3), hsl(${spec.accent} / 0.08))`,
            color: `hsl(${spec.accent})`,
          }}
        >
          <Icon className="h-3 w-3" />
        </span>
        <span className="truncate text-[10px] font-semibold leading-tight">
          {d.label}
        </span>
      </div>
      <div className="mt-0.5 flex items-center gap-1">
        <span className="truncate text-[8.5px] text-muted-foreground">
          {spec.label}
        </span>
        {d.promptVariant && (
          <span className="rounded-full border border-violet-400/25 bg-violet-500/10 px-1 text-[8px] text-violet-200">
            {d.promptVariant}
          </span>
        )}
      </div>
      {(d.isEntry || d.isExit) && (
        <span
          className="absolute -top-2 right-1 rounded-full px-1 text-[8px] font-medium"
          style={{
            background: d.isEntry ? "hsl(150 70% 40% / 0.9)" : "hsl(200 80% 45% / 0.9)",
            color: "white",
          }}
        >
          {d.isEntry ? "вход" : "выход"}
        </span>
      )}
    </div>
  );
}

const previewNodeTypes = { groupPreview: PreviewNode };

const EDGE_KIND_STYLE: Record<string, { stroke: string; label: string }> = {
  after: { stroke: "hsl(210 18% 62% / 0.6)", label: "" },
  pass: { stroke: "hsl(150 70% 50% / 0.8)", label: "Ок" },
  fail: { stroke: "hsl(0 72% 55% / 0.8)", label: "Не ок" },
};

export function GroupPreview({ detail }: { detail: NodeGroupDetail }) {
  const { nodes, edges } = useMemo(() => {
    const entry = new Set(detail.entry_keys);
    const nodes: Node<PreviewNodeData>[] = detail.nodes.map((n) => ({
      id: n.key,
      type: "groupPreview",
      position: { x: n.dx, y: n.dy },
      draggable: false,
      selectable: false,
      connectable: false,
      data: {
        label: n.label,
        nodeType: n.type,
        promptVariant: n.prompt_variant,
        isEntry: entry.has(n.key),
        isExit: detail.exit_key === n.key,
      },
    }));
    const edges: Edge[] = detail.internal_edges.map((e, i) => {
      const st = EDGE_KIND_STYLE[e.kind] ?? EDGE_KIND_STYLE.after;
      return {
        id: `pv_${i}`,
        source: e.source,
        target: e.target,
        animated: e.kind !== "after",
        label: st.label || undefined,
        labelStyle: { fontSize: 9, fill: st.stroke },
        labelBgStyle: { fill: "hsl(0 0% 5% / 0.8)" },
        style: { stroke: st.stroke, strokeWidth: 1.4 },
      };
    });
    return { nodes, edges };
  }, [detail]);

  return (
    <div className="h-[260px] w-full overflow-hidden rounded-xl border border-white/10 bg-black/30">
      <ReactFlow
        nodes={nodes}
        edges={edges}
        nodeTypes={previewNodeTypes}
        fitView
        fitViewOptions={{ padding: 0.25, maxZoom: 0.9 }}
        minZoom={0.1}
        maxZoom={1.2}
        nodesDraggable={false}
        nodesConnectable={false}
        elementsSelectable={false}
        edgesFocusable={false}
        panOnDrag
        zoomOnScroll
        proOptions={{ hideAttribution: true }}
        colorMode="dark"
      >
        <Background
          variant={BackgroundVariant.Dots}
          gap={16}
          size={1}
          color="hsl(0 0% 100% / 0.08)"
        />
      </ReactFlow>
    </div>
  );
}
