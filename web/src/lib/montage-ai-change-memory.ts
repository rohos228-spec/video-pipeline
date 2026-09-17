/** Последний текст ИИзменение по слоту кадра — показывать при повторном открытии. */

export function montageAiChangeStorageKey(
  projectId: number,
  kind: "image" | "video",
  frameNumber: number,
  shot: 1 | 2,
): string {
  return `vp.montageAiChange.${projectId}.${kind}.${frameNumber}.${shot}`;
}

export function readMontageAiChangeText(
  projectId: number,
  kind: "image" | "video",
  frameNumber: number,
  shot: 1 | 2,
): string {
  if (typeof window === "undefined") return "";
  try {
    return (
      window.localStorage.getItem(
        montageAiChangeStorageKey(projectId, kind, frameNumber, shot),
      ) || ""
    ).trim();
  } catch {
    return "";
  }
}

export function writeMontageAiChangeText(
  projectId: number,
  kind: "image" | "video",
  frameNumber: number,
  shot: 1 | 2,
  text: string,
): void {
  if (typeof window === "undefined") return;
  const value = (text || "").trim();
  if (!value) return;
  try {
    window.localStorage.setItem(
      montageAiChangeStorageKey(projectId, kind, frameNumber, shot),
      value,
    );
  } catch {
    /* quota / private mode */
  }
}
