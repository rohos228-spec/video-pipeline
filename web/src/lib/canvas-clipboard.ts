/** Буфер подграфа канваса (ноды + связи между ними) — sessionStorage для вставки в другой проект. */

import type { WorkflowEdge, WorkflowNode } from "./types";

export type CanvasClipboardPayload = {
  version: 1;
  sourceProjectId: number | null;
  copiedAt: number;
  nodes: WorkflowNode[];
  edges: WorkflowEdge[];
};

const STORAGE_KEY = "video-pipeline:canvas-clipboard";

export function readCanvasClipboard(): CanvasClipboardPayload | null {
  if (typeof window === "undefined") return null;
  try {
    const raw = sessionStorage.getItem(STORAGE_KEY);
    if (!raw) return null;
    const data = JSON.parse(raw) as CanvasClipboardPayload;
    if (data?.version !== 1 || !Array.isArray(data.nodes)) return null;
    return data;
  } catch {
    return null;
  }
}

export function writeCanvasClipboard(payload: CanvasClipboardPayload): void {
  sessionStorage.setItem(STORAGE_KEY, JSON.stringify(payload));
}

export function clearCanvasClipboard(): void {
  sessionStorage.removeItem(STORAGE_KEY);
}

export function isEditableTarget(target: EventTarget | null): boolean {
  if (!(target instanceof HTMLElement)) return false;
  const tag = target.tagName;
  if (tag === "INPUT" || tag === "TEXTAREA" || tag === "SELECT") return true;
  if (target.isContentEditable) return true;
  return !!target.closest("[contenteditable='true']");
}
