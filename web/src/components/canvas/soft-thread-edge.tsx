"use client";

import {
  BaseEdge,
  getBezierPath,
  type EdgeProps,
  type Edge,
} from "@xyflow/react";

/** Мягкая пунктирная «нить»: чистый bezier без ломаных углов smoothstep. */
export function SoftThreadEdge({
  id,
  sourceX,
  sourceY,
  targetX,
  targetY,
  sourcePosition,
  targetPosition,
  style,
  markerEnd,
  markerStart,
}: EdgeProps<Edge>) {
  const [path] = getBezierPath({
    sourceX,
    sourceY,
    sourcePosition,
    targetX,
    targetY,
    targetPosition,
    /** Чуть выше дефолта — дуга мягче, без «ломаных» колен. */
    curvature: 0.45,
  });

  return (
    <BaseEdge
      id={id}
      path={path}
      markerEnd={markerEnd}
      markerStart={markerStart}
      style={style}
      className="soft-thread-edge-path"
    />
  );
}
