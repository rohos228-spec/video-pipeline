import type { NodeRunStatus } from "@/lib/types";
import type { WorkflowDetail } from "@/lib/types";

/** Минимальная длина general_plan — как sync_after_plan / plan_validation на бэкенде. */
export const MIN_GENERAL_PLAN_CHARS = 200;

/**
 * Статус ноды на канвасе = NodeRun.status из API/WS.
 * Project.status больше не участвует — иначе «все ноды running» / ложный done.
 */
export function reconcileNodeRunStatus(
  _nodeType: string,
  runStatus: NodeRunStatus,
  _projectStatus?: unknown,
  _opts?: { slotIndex?: number },
): NodeRunStatus {
  return runStatus;
}

/** Без node_run в ответе API — всегда «ожидание». */
export function inferNodeStatusFromProject(
  _nodeType: string,
  _projectStatus?: unknown,
): NodeRunStatus {
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
