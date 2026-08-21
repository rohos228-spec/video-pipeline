import type { NodeRunStatus, ProjectStatus } from "@/lib/types";
import type { WorkflowDetail } from "@/lib/types";

/** Минимальная длина general_plan — как sync_after_plan / plan_validation на бэкенде. */
export const MIN_GENERAL_PLAN_CHARS = 200;

/** Линейные media-ноды: Project.status ↔ node_type (для badge sync). */
const NODE_RUNNING_STATUS: Record<string, ProjectStatus> = {
  image_prompts: "generating_image_prompts",
  images: "generating_images",
  animation_prompts: "generating_animation_prompts",
  videos: "generating_videos",
  audio: "generating_audio",
  music: "generating_music",
  assemble: "assembling",
};

const NODE_READY_STATUS: Record<string, ProjectStatus> = {
  image_prompts: "image_prompts_ready",
  images: "images_ready",
  animation_prompts: "animation_prompts_ready",
  videos: "videos_ready",
  audio: "audio_ready",
  music: "music_ready",
  assemble: "assembled",
};

/** Порядок media-цепочки для «Project уже дальше» → badge done. */
const MEDIA_PIPELINE_ORDER: string[] = [
  "image_prompts",
  "images",
  "animation_prompts",
  "videos",
  "audio",
  "music",
  "assemble",
];

function projectImpliesNodeRunning(
  nodeType: string,
  projectStatus: ProjectStatus | undefined,
): boolean {
  if (!projectStatus) return false;
  return NODE_RUNNING_STATUS[nodeType] === projectStatus;
}

function projectImpliesNodeDone(
  nodeType: string,
  projectStatus: ProjectStatus | undefined,
): boolean {
  if (!projectStatus) return false;
  const ready = NODE_READY_STATUS[nodeType];
  if (ready && projectStatus === ready) return true;
  const idx = MEDIA_PIPELINE_ORDER.indexOf(nodeType);
  if (idx < 0) return false;
  // Project на running/ready более поздней media-ноды → эта уже пройдена.
  for (let i = idx + 1; i < MEDIA_PIPELINE_ORDER.length; i++) {
    const later = MEDIA_PIPELINE_ORDER[i];
    if (
      projectStatus === NODE_RUNNING_STATUS[later] ||
      projectStatus === NODE_READY_STATUS[later]
    ) {
      return true;
    }
  }
  if (
    projectStatus === "assembled" ||
    projectStatus === "publishing" ||
    projectStatus === "published"
  ) {
    return true;
  }
  return false;
}

/**
 * Статус ноды на канвасе = NodeRun.status, с минимальным sync от Project
 * для линейных media: не рисовать «прервано», пока Project.generating_*
 * того же шага (NodeRun отстал после sidecar / startup).
 */
export function reconcileNodeRunStatus(
  nodeType: string,
  runStatus: NodeRunStatus,
  projectStatus?: ProjectStatus | unknown,
  _opts?: { slotIndex?: number },
): NodeRunStatus {
  const ps = projectStatus as ProjectStatus | undefined;
  if (
    (runStatus === "failed" || runStatus === "pending" || runStatus === "queued") &&
    projectImpliesNodeRunning(nodeType, ps)
  ) {
    return "running";
  }
  if (
    (runStatus === "failed" || runStatus === "pending" || runStatus === "queued") &&
    projectImpliesNodeDone(nodeType, ps)
  ) {
    return "done";
  }
  return runStatus;
}

/** Без node_run в ответе API — pending, кроме активного media-шага Project. */
export function inferNodeStatusFromProject(
  nodeType: string,
  projectStatus?: ProjectStatus | unknown,
): NodeRunStatus {
  const ps = projectStatus as ProjectStatus | undefined;
  if (projectImpliesNodeRunning(nodeType, ps)) return "running";
  if (projectImpliesNodeDone(nodeType, ps)) return "done";
  return "pending";
}

/** Ключ структуры графа — без updated_at и позиций (сохранение канваса не сбрасывает статусы). */
export function workflowStructureKey(wf: WorkflowDetail): string {
  const nodes = [...wf.nodes]
    .map((n) => {
      const data = (n.data ?? {}) as Record<string, unknown>;
      const slot =
        n.type === "excel_gpt" && typeof data.slotIndex === "number"
          ? `:s${data.slotIndex}`
          : "";
      return `${n.id}:${n.type}${slot}`;
    })
    .sort()
    .join(",");
  const edges = [...wf.edges]
    .map((e) => `${e.id}:${e.source}:${e.target}`)
    .sort()
    .join(",");
  return `${wf.id}|${nodes}|${edges}`;
}
