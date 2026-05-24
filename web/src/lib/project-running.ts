import type { ProjectStatus } from "@/lib/types";

/** Статусы, в которых воркер выполняет шаг (как `is_running_status` на бэкенде). */
const RUNNING_PROJECT_STATUSES: ReadonlySet<ProjectStatus> = new Set([
  "planning",
  "scripting",
  "splitting",
  "generating_hero",
  "generating_items",
  "enriching_1",
  "enriching_2",
  "enriching_3",
  "enriching_4",
  "enriching_5",
  "generating_image_prompts",
  "generating_images",
  "generating_animation_prompts",
  "generating_videos",
  "generating_audio",
  "assembling",
  "publishing",
]);

export function isProjectRunningStatus(
  status: ProjectStatus | string | undefined | null,
): boolean {
  if (!status) return false;
  return RUNNING_PROJECT_STATUSES.has(status as ProjectStatus);
}
