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
  /** UI/meta — не терять при reload структуры графа. */
  role?: unknown;
  workMode?: unknown;
  label?: unknown;
  inputSource?: unknown;
  uploadedFileName?: unknown;
  modelId?: unknown;
  modelChannel?: unknown;
  imageResolution?: unknown;
  imageQuality?: unknown;
  aspectRatio?: unknown;
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
        // Роль «Ок / не ок» живёт в UI; source может быть без неё после autosave.
        role: n.data.role ?? old.data.role,
        workMode: n.data.workMode ?? old.data.workMode,
        label: n.data.label ?? old.data.label,
        inputSource: n.data.inputSource ?? old.data.inputSource,
        uploadedFileName: n.data.uploadedFileName ?? old.data.uploadedFileName,
        modelId: n.data.modelId ?? old.data.modelId,
        modelChannel: n.data.modelChannel ?? old.data.modelChannel,
        imageResolution: n.data.imageResolution ?? old.data.imageResolution,
        imageQuality: n.data.imageQuality ?? old.data.imageQuality,
        aspectRatio: n.data.aspectRatio ?? old.data.aspectRatio,
      },
    };
  });
}
