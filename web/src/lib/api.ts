/**
 * Тонкий fetch-обёртка для бэкенд-API. Все запросы относительные
 * (`/api/...`) — в dev next.config.ts проксирует на :8765, в проде FastAPI
 * сам отдаёт фронт + API из одного origin.
 */

import type {
  ArtifactDTO,
  FrameDTO,
  HITLDTO,
  ProjectDetail,
  ProjectSummary,
  PromptDTO,
  WorkflowDetail,
  WorkflowNode,
  WorkflowEdge,
  WorkflowRunDetail,
  WorkflowSummary,
} from "./types";

async function http<T>(
  path: string,
  options: RequestInit = {}
): Promise<T> {
  const res = await fetch(path, {
    ...options,
    headers: {
      "Content-Type": "application/json",
      ...(options.headers || {}),
    },
  });
  if (!res.ok) {
    let detail: string | object = await res.text();
    try {
      detail = JSON.parse(detail as string);
    } catch {
      // оставляем как text
    }
    throw new ApiError(res.status, detail);
  }
  if (res.status === 204) return undefined as T;
  return res.json() as Promise<T>;
}

export class ApiError extends Error {
  constructor(public status: number, public detail: string | object) {
    super(typeof detail === "string" ? detail : JSON.stringify(detail));
    this.name = "ApiError";
  }
}

export const api = {
  // ── Workflows ────────────────────────────────────────────────────
  listWorkflows: () => http<WorkflowSummary[]>(`/api/workflows`),
  getWorkflow: (id: number) => http<WorkflowDetail>(`/api/workflows/${id}`),
  saveWorkflow: (id: number, body: { name?: string; description?: string; nodes: WorkflowNode[]; edges: WorkflowEdge[] }) =>
    http<WorkflowDetail>(`/api/workflows/${id}`, { method: "PUT", body: JSON.stringify(body) }),
  createWorkflow: (body: { name?: string; description?: string; nodes: WorkflowNode[]; edges: WorkflowEdge[] }) =>
    http<WorkflowDetail>(`/api/workflows`, { method: "POST", body: JSON.stringify(body) }),
  duplicateWorkflow: (id: number) =>
    http<WorkflowDetail>(`/api/workflows/${id}/duplicate`, { method: "POST" }),
  deleteWorkflow: (id: number) =>
    http<void>(`/api/workflows/${id}`, { method: "DELETE" }),
  resetDefaultWorkflow: () =>
    http<WorkflowDetail>(`/api/workflows/default/reset`, { method: "POST" }),

  // ── Projects ─────────────────────────────────────────────────────
  listProjects: () => http<ProjectSummary[]>(`/api/projects`),
  getProject: (id: number) => http<ProjectDetail>(`/api/projects/${id}`),
  createProject: (body: { topic: string; hero_mode?: string; auto_mode?: boolean }) =>
    http<ProjectDetail>(`/api/projects`, { method: "POST", body: JSON.stringify(body) }),
  patchProject: (id: number, body: Partial<ProjectDetail>) =>
    http<ProjectDetail>(`/api/projects/${id}`, { method: "PATCH", body: JSON.stringify(body) }),
  deleteProject: (id: number) =>
    http<void>(`/api/projects/${id}`, { method: "DELETE" }),

  // ── Frames ───────────────────────────────────────────────────────
  listFrames: (projectId: number) =>
    http<FrameDTO[]>(`/api/projects/${projectId}/frames`),
  patchFrame: (projectId: number, frameId: number, body: Partial<FrameDTO>) =>
    http<FrameDTO>(`/api/projects/${projectId}/frames/${frameId}`, {
      method: "PATCH",
      body: JSON.stringify(body),
    }),

  // ── Runs ─────────────────────────────────────────────────────────
  listRuns: () => http<WorkflowRunDetail[]>(`/api/runs`),
  getRun: (id: number) => http<WorkflowRunDetail>(`/api/runs/${id}`),
  startRunFromWorkflow: (workflowId: number, body: { project_id?: number; topic?: string; hero_mode?: string }) =>
    http<WorkflowRunDetail>(`/api/runs/from-workflow/${workflowId}`, {
      method: "POST",
      body: JSON.stringify(body),
    }),
  cancelRun: (id: number) =>
    http<WorkflowRunDetail>(`/api/runs/${id}/cancel`, { method: "POST" }),

  // ── HITL ─────────────────────────────────────────────────────────
  listPendingHitl: () => http<HITLDTO[]>(`/api/hitl/pending`),
  listProjectHitl: (projectId: number) =>
    http<HITLDTO[]>(`/api/hitl/project/${projectId}`),
  submitHitlDecision: (
    hitlId: number,
    body: { decision: string; edited_prompt?: string }
  ) =>
    http<HITLDTO>(`/api/hitl/${hitlId}/decision`, {
      method: "POST",
      body: JSON.stringify(body),
    }),

  // ── Prompts ──────────────────────────────────────────────────────
  listPrompts: () => http<PromptDTO[]>(`/api/prompts`),
  patchPrompt: (id: number, body: { text?: string; active?: boolean }) =>
    http<PromptDTO>(`/api/prompts/${id}`, {
      method: "PATCH",
      body: JSON.stringify(body),
    }),

  // ── Artifacts ────────────────────────────────────────────────────
  listArtifacts: (params: { project_id?: number; frame_id?: number; kind?: string }) => {
    const q = new URLSearchParams();
    if (params.project_id != null) q.set("project_id", String(params.project_id));
    if (params.frame_id != null) q.set("frame_id", String(params.frame_id));
    if (params.kind) q.set("kind", params.kind);
    return http<ArtifactDTO[]>(`/api/artifacts?${q.toString()}`);
  },
  artifactFileUrl: (uuid: string) => `/api/artifacts/${uuid}/file`,
};

/**
 * WebSocket подписка на канал. Возвращает функцию отписки.
 * channel: "global" | "runs.<id>" | "projects.<id>" | "hitl.<id>" | "logs.<id>"
 */
export function subscribeWS(
  channel: string,
  onMessage: (event: unknown) => void,
  onClose?: (reason: string) => void
): () => void {
  let ws: WebSocket | null = null;
  let closed = false;
  let reconnectTimer: ReturnType<typeof setTimeout> | null = null;

  const connect = () => {
    if (closed) return;
    const protocol = location.protocol === "https:" ? "wss" : "ws";
    const url = `${protocol}://${location.host}/ws/${channel}`;
    ws = new WebSocket(url);
    ws.addEventListener("message", (ev) => {
      try {
        const data = JSON.parse(ev.data);
        onMessage(data);
      } catch (e) {
        console.warn("ws parse error", e);
      }
    });
    ws.addEventListener("close", () => {
      if (closed) {
        onClose?.("closed");
        return;
      }
      // backoff reconnect
      reconnectTimer = setTimeout(connect, 1500);
    });
    ws.addEventListener("error", () => {
      // close handler сделает reconnect
    });
  };

  connect();

  return () => {
    closed = true;
    if (reconnectTimer) clearTimeout(reconnectTimer);
    if (ws && ws.readyState === WebSocket.OPEN) ws.close();
  };
}
