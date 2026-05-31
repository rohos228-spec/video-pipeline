import type { NodeRunStatus, ProjectStatus } from "@/lib/types";
import { isProjectRunningStatus } from "@/lib/project-running";
import type { WorkflowDetail } from "@/lib/types";

/** Порядок рабочих нод — как NODE_TYPE_ORDER на бэкенде (без topic). */
const NODE_TYPE_ORDER: readonly string[] = [
  "plan",
  "script",
  "split",
  "hero",
  "items",
  "enrich_1",
  "enrich_2",
  "enrich_3",
  "enrich_4",
  "enrich_5",
  "image_prompts",
  "images",
  "animation_prompts",
  "videos",
  "audio",
  "assemble",
  "publish",
];

/** ProjectStatus → (активная нода, её целевой NodeRunStatus). Как STATUS_TO_NODE в run_sync.py. */
const STATUS_TO_NODE: Record<
  string,
  { type: string; state: Extract<NodeRunStatus, "pending" | "running" | "done"> }
> = {
  new: { type: "plan", state: "pending" },
  planning: { type: "plan", state: "running" },
  plan_ready: { type: "plan", state: "done" },
  scripting: { type: "script", state: "running" },
  script_ready: { type: "script", state: "done" },
  splitting: { type: "split", state: "running" },
  frames_ready: { type: "split", state: "done" },
  generating_hero: { type: "hero", state: "running" },
  hero_ready: { type: "hero", state: "done" },
  generating_items: { type: "items", state: "running" },
  items_ready: { type: "items", state: "done" },
  enriching_1: { type: "enrich_1", state: "running" },
  enrich_1_ready: { type: "enrich_1", state: "done" },
  enriching_2: { type: "enrich_2", state: "running" },
  enrich_2_ready: { type: "enrich_2", state: "done" },
  enriching_3: { type: "enrich_3", state: "running" },
  enrich_3_ready: { type: "enrich_3", state: "done" },
  enriching_4: { type: "enrich_4", state: "running" },
  enrich_4_ready: { type: "enrich_4", state: "done" },
  enriching_5: { type: "enrich_5", state: "running" },
  enrich_5_ready: { type: "enrich_5", state: "done" },
  generating_image_prompts: { type: "image_prompts", state: "running" },
  image_prompts_ready: { type: "image_prompts", state: "done" },
  generating_images: { type: "images", state: "running" },
  images_ready: { type: "images", state: "done" },
  generating_animation_prompts: { type: "animation_prompts", state: "running" },
  animation_prompts_ready: { type: "animation_prompts", state: "done" },
  generating_videos: { type: "videos", state: "running" },
  videos_ready: { type: "videos", state: "done" },
  generating_audio: { type: "audio", state: "running" },
  audio_ready: { type: "audio", state: "done" },
  assembling: { type: "assemble", state: "running" },
  assembled: { type: "assemble", state: "done" },
  publishing: { type: "publish", state: "running" },
  published: { type: "publish", state: "done" },
};

/**
 * NodeRun в БД ещё `running`, а проект уже ушёл в *_ready — раньше UI показывал «Ожидание».
 * По чекпоинту Project.status выводим done для завершённых шагов.
 */
export function reconcileNodeRunStatus(
  nodeType: string,
  runStatus: NodeRunStatus,
  projectStatus: ProjectStatus | string | null | undefined,
): NodeRunStatus {
  if (runStatus !== "running") return runStatus;
  if (isProjectRunningStatus(projectStatus)) return runStatus;

  const checkpoint = projectStatus ? STATUS_TO_NODE[projectStatus] : undefined;
  if (!checkpoint) return "pending";

  const nodeIdx = NODE_TYPE_ORDER.indexOf(nodeType);
  const targetIdx = NODE_TYPE_ORDER.indexOf(checkpoint.type);
  if (nodeIdx < 0 || targetIdx < 0) return "pending";

  if (nodeIdx < targetIdx) return "done";
  if (nodeIdx === targetIdx) {
    return checkpoint.state === "done" ? "done" : "pending";
  }
  return "pending";
}

/** Ключ структуры графа — без updated_at и позиций (сохранение канваса не сбрасывает статусы). */
export function workflowStructureKey(wf: WorkflowDetail): string {
  const nodes = [...wf.nodes]
    .map((n) => `${n.id}:${n.type}`)
    .sort()
    .join(",");
  const edges = [...wf.edges]
    .map((e) => `${e.id}:${e.source}:${e.target}`)
    .sort()
    .join(",");
  return `${wf.id}|${nodes}|${edges}`;
}
