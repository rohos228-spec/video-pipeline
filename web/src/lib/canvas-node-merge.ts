/**
 * Слияние структуры графа (canvas_graph / workflow) с runtime-полями UI.
 * Позиции ВСЕГДА из source — иначе при поздней загрузке meta заводской layout
 * залипает и потом перезаписывает canvas_graph.
 */

export type CanvasRuntimeData = {
  status?: unknown;
  progress?: unknown;
  progressText?: unknown;
  error?: unknown;
  attempts?: unknown;
};

export type MergeableCanvasNode<T extends CanvasRuntimeData = CanvasRuntimeData> = {
  id: string;
  position: { x: number; y: number };
  selected?: boolean;
  data: T;
};

export function mergeGraphNodesWithRuntime<T extends CanvasRuntimeData>(
  sourceNodes: MergeableCanvasNode<T>[],
  prevNodes: MergeableCanvasNode<T>[],
): MergeableCanvasNode<T>[] {
  const prevById = new Map(prevNodes.map((n) => [n.id, n]));
  return sourceNodes.map((n) => {
    const old = prevById.get(n.id);
    if (!old) return n;
    return {
      ...n,
      // Источник истины для координат — canvas_graph / workflow payload.
      position: n.position,
      selected: old.selected,
      data: {
        ...n.data,
        status: old.data.status ?? n.data.status,
        progress: old.data.progress ?? n.data.progress,
        progressText: old.data.progressText ?? n.data.progressText,
        error: old.data.error ?? n.data.error,
        attempts: old.data.attempts ?? n.data.attempts,
      },
    };
  });
}
