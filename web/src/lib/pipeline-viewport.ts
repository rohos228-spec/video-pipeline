/** Панорама/зум пайплайна по проекту — не терять при переключении. */

export type PipelineViewport = { x: number; y: number; zoom: number };

const PREFIX = "vp.pipelineViewport.";

export function pipelineViewportStorageKey(projectId: number): string {
  return `${PREFIX}${projectId}`;
}

export function readPipelineViewport(projectId: number): PipelineViewport | null {
  if (typeof window === "undefined") return null;
  try {
    const raw = window.localStorage.getItem(pipelineViewportStorageKey(projectId));
    if (!raw) return null;
    const parsed = JSON.parse(raw) as Partial<PipelineViewport>;
    const x = Number(parsed.x);
    const y = Number(parsed.y);
    const zoom = Number(parsed.zoom);
    if (![x, y, zoom].every(Number.isFinite) || zoom <= 0) return null;
    return { x, y, zoom };
  } catch {
    return null;
  }
}

export function writePipelineViewport(
  projectId: number,
  viewport: PipelineViewport,
): void {
  if (typeof window === "undefined") return;
  const x = Number(viewport.x);
  const y = Number(viewport.y);
  const zoom = Number(viewport.zoom);
  if (![x, y, zoom].every(Number.isFinite) || zoom <= 0) return;
  try {
    window.localStorage.setItem(
      pipelineViewportStorageKey(projectId),
      JSON.stringify({ x, y, zoom }),
    );
  } catch {
    /* quota / private mode */
  }
}
