"use client";

/**
 * Рамки импортированных групп нод на канвасе: пунктирная обводка по
 * bounding box всех нод с одинаковым data.groupId + подпись группы.
 * Рендерится в координатах канваса (ViewportPortal), под нодами.
 */

import { useMemo } from "react";
import { ViewportPortal, useNodes, type Node } from "@xyflow/react";
import type { PipelineNodeData } from "./pipeline-node";
import { groupHue } from "@/lib/group-color";

const PAD_X = 26;
const PAD_TOP = 46;
const PAD_BOTTOM = 26;
const FALLBACK_W = 300;
const FALLBACK_H = 120;

interface GroupFrame {
  gid: string;
  title: string;
  x: number;
  y: number;
  w: number;
  h: number;
  runningCount: number;
}

export function GroupFrames() {
  const nodes = useNodes<Node<PipelineNodeData>>();

  const frames = useMemo<GroupFrame[]>(() => {
    const byGroup = new Map<string, { title: string; ns: Node<PipelineNodeData>[] }>();
    for (const n of nodes) {
      const gid = n.data?.groupId;
      if (!gid) continue;
      const entry = byGroup.get(gid) ?? {
        title: n.data.groupTitle || gid.split("#")[0],
        ns: [],
      };
      entry.ns.push(n);
      byGroup.set(gid, entry);
    }
    return [...byGroup.entries()].map(([gid, { title, ns }]) => {
      let x1 = Infinity;
      let y1 = Infinity;
      let x2 = -Infinity;
      let y2 = -Infinity;
      let runningCount = 0;
      for (const n of ns) {
        const w = n.measured?.width ?? FALLBACK_W;
        const h = n.measured?.height ?? FALLBACK_H;
        x1 = Math.min(x1, n.position.x);
        y1 = Math.min(y1, n.position.y);
        x2 = Math.max(x2, n.position.x + w);
        y2 = Math.max(y2, n.position.y + h);
        if (n.data?.status === "running") runningCount += 1;
      }
      return {
        gid,
        title,
        x: x1 - PAD_X,
        y: y1 - PAD_TOP,
        w: x2 - x1 + PAD_X * 2,
        h: y2 - y1 + PAD_TOP + PAD_BOTTOM,
        runningCount,
      };
    });
  }, [nodes]);

  if (frames.length === 0) return null;

  return (
    <ViewportPortal>
      {frames.map((f) => {
        const hue = groupHue(f.gid);
        const active = f.runningCount > 0;
        return (
          <div
            key={f.gid}
            className="pointer-events-none absolute"
            style={{ left: f.x, top: f.y, width: f.w, height: f.h, zIndex: -1 }}
          >
            <div
              className="h-full w-full rounded-[28px] border-2 border-dashed"
              style={{
                borderColor: active
                  ? `hsl(${hue} 85% 62% / 0.8)`
                  : `hsl(${hue} 70% 60% / 0.4)`,
                background: `hsl(${hue} 70% 60% / ${active ? 0.09 : 0.045})`,
                boxShadow: active ? `0 0 32px hsl(${hue} 85% 60% / 0.25)` : undefined,
              }}
            />
            <div
              className="absolute left-4 top-3 flex max-w-[calc(100%-2rem)] items-center gap-1.5 rounded-full border px-2 py-0.5 text-[10px] font-medium backdrop-blur-sm"
              style={{
                borderColor: `hsl(${hue} 70% 60% / 0.45)`,
                background: `hsl(${hue} 60% 16% / 0.75)`,
                color: `hsl(${hue} 85% 82%)`,
              }}
              title={`Импортированная группа «${f.title}» (${f.gid})`}
            >
              <span
                className="inline-block h-1.5 w-1.5 shrink-0 rounded-full"
                style={{ background: `hsl(${hue} 85% 65%)` }}
              />
              <span className="truncate">{f.title}</span>
              <span className="shrink-0 opacity-60">
                · группа{active ? ` · в работе: ${f.runningCount}` : ""}
              </span>
            </div>
          </div>
        );
      })}
    </ViewportPortal>
  );
}
