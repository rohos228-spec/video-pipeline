/** Сброс кадров и превью карточки НИИ-67 при смене проекта. */

export function clearNii67ProjectViews(
  previews: Record<string, string>,
  revoke: (url: string) => void = (url) => URL.revokeObjectURL(url),
): { frames: []; previews: Record<string, string> } {
  for (const url of Object.values(previews)) {
    if (url) revoke(url);
  }
  return { frames: [], previews: {} };
}
