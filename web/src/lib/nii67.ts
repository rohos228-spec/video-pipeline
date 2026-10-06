import type { ProjectDetail } from "@/lib/types";

/**
 * Нода сценария импортирует этот модуль, но файла не было в дереве housepc —
 * без него `next build` не собирает Studio.
 * Настоящая разметка «НИИ 67» сюда не входит: пока флаг выключен.
 */
export function projectUsesNii67Script(_project: ProjectDetail | null): boolean {
  return false;
}
