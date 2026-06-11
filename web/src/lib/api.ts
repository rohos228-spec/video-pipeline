/**
 * Тонкий fetch-обёртка для бэкенд-API. Все запросы относительные
 * (`/api/...`) — в dev next.config.ts проксирует на :8765, в проде FastAPI
 * сам отдаёт фронт + API из одного origin.
 */

import type {
  ArtifactDTO,
  ExcelHeroCharacter,
  FrameDTO,
  GenerationConfigPreset,
  GenerationConfigPresetSettings,
  HITLDTO,
  ProjectDetail,
  ProjectSummary,
  SidebarLayout,
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

export interface XlsxPreview {
  path: string;
  sheets: string[];
  active_sheet: string;
  headers: string[];
  rows: string[][];
  row?: number;
  cells?: string[];
}

export interface ProjectAsset {
  source: string;
  id: string;
  kind: string;
  path: string | null;
  preview_url: string | null;
  label?: string;
  frame_id?: number | null;
  meta?: Record<string, unknown>;
  voiceover?: string;
  description?: string | null;
  uuid?: string;
}

export class ApiError extends Error {
  constructor(public status: number, public detail: string | object) {
    super(formatApiError(detail));
    this.name = "ApiError";
  }
}

export function formatApiError(detail: string | object): string {
  if (typeof detail === "string") {
    try {
      const parsed = JSON.parse(detail) as { detail?: unknown };
      if (typeof parsed.detail === "string") return parsed.detail;
    } catch {
      return detail;
    }
    return detail;
  }
  if (detail && typeof detail === "object" && "detail" in detail) {
    const d = (detail as { detail?: unknown }).detail;
    if (typeof d === "string") return d;
    if (Array.isArray(d)) return d.map(String).join("; ");
  }
  return JSON.stringify(detail);
}

export const api = {
  // ── Workflows ────────────────────────────────────────────────────
  listWorkflows: () => http<WorkflowSummary[]>(`/api/workflows`),
  getWorkflow: (id: number) => http<WorkflowDetail>(`/api/workflows/${id}`),
  saveWorkflow: (id: number, body: { name?: string; description?: string; nodes: WorkflowNode[]; edges: WorkflowEdge[] }) =>
    http<WorkflowDetail>(`/api/workflows/${id}`, { method: "PUT", body: JSON.stringify(body) }),
  validateWorkflow: (body: { nodes: WorkflowNode[]; edges: WorkflowEdge[] }) =>
    http<{ valid: boolean; errors: string[]; warnings: string[] }>(`/api/workflows/validate`, {
      method: "POST",
      body: JSON.stringify({ nodes: body.nodes, edges: body.edges }),
    }),
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
  createProject: (body: {
    topic: string;
    hero_mode?: string;
    auto_mode?: boolean;
    sidebar_folder_id?: string | null;
  }) =>
    http<ProjectDetail>(`/api/projects`, { method: "POST", body: JSON.stringify(body) }),
  patchProject: (id: number, body: Partial<ProjectDetail>) =>
    http<ProjectDetail>(`/api/projects/${id}`, { method: "PATCH", body: JSON.stringify(body) }),
  deleteProject: (id: number) =>
    http<void>(`/api/projects/${id}`, { method: "DELETE" }),

  // ── Sidebar layout ───────────────────────────────────────────────
  getSidebarLayout: () => http<SidebarLayout>(`/api/sidebar-layout`),
  updateSidebarLayout: (body: Partial<SidebarLayout>) =>
    http<SidebarLayout>(`/api/sidebar-layout`, {
      method: "PUT",
      body: JSON.stringify(body),
    }),
  createSidebarFolder: (name: string) =>
    http<{ id: string; name: string; order: number }>(`/api/sidebar-layout/folders`, {
      method: "POST",
      body: JSON.stringify({ name }),
    }),
  renameSidebarFolder: (folderId: string, name: string) =>
    http<{ id: string; name: string }>(`/api/sidebar-layout/folders/${folderId}`, {
      method: "PATCH",
      body: JSON.stringify({ name }),
    }),
  deleteSidebarFolder: (folderId: string) =>
    http<{ ok: boolean }>(`/api/sidebar-layout/folders/${folderId}`, { method: "DELETE" }),
  toggleGenQueue: (projectId: number) =>
    http<{ gen_queue: number[]; gen_queue_positions: Record<number, number>; position: number | null }>(
      `/api/sidebar-layout/gen-queue/toggle`,
      { method: "POST", body: JSON.stringify({ project_id: projectId }) },
    ),
  listStepCatalog: () =>
    http<{ code: string; label: string; running_status: string; ready_status: string }[]>(
      `/api/projects/steps/catalog`
    ),
  runProjectStep: (projectId: number, stepCode: string, opts?: { dryRun?: boolean }) => {
    const q = opts?.dryRun ? "?dry_run=true" : "";
    return http<ProjectDetail>(`/api/projects/${projectId}/steps/${stepCode}/run${q}`, {
      method: "POST",
    });
  },

  // ── Excel-Hero (читает лист «Персонажи» из project.xlsx) ─────────
  getExcelHero: (projectId: number) =>
    http<{ loaded: boolean; characters: ExcelHeroCharacter[] }>(
      `/api/projects/${projectId}/excel-hero`
    ),
  loadExcelHero: (projectId: number) =>
    http<{ loaded: boolean; count: number; characters: ExcelHeroCharacter[] }>(
      `/api/projects/${projectId}/excel-hero/load`,
      { method: "POST" }
    ),
  clearExcelHero: (projectId: number) =>
    http<void>(`/api/projects/${projectId}/excel-hero`, { method: "DELETE" }),

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

  // ── Prompt studio (blocks v2) ────────────────────────────────────
  promptStudioCatalog: () =>
    http<{
      block_categories: Record<string, string[]>;
      steps: string[];
      node_type_to_step: Record<string, string>;
      style_presets: { id: string; label: string; description?: string }[];
    }>(`/api/prompt-studio/catalog`),
  composePrompt: (body: {
    node_type?: string;
    step_id?: string;
    project_id?: number;
    blocks?: Record<string, string>;
    vars?: Record<string, string | number>;
    style_preset?: string;
  }) =>
    http<{ text: string; blocks: Record<string, string>; vars: Record<string, string> }>(
      `/api/prompt-studio/compose`,
      { method: "POST", body: JSON.stringify(body) }
    ),
  patchProjectPromptConfig: (
    projectId: number,
    body: {
      style_profile?: string;
      blocks?: Record<string, string>;
      vars?: Record<string, string | number>;
      use_blocks_v2?: boolean;
      legacy?: Record<string, string>;
    }
  ) =>
    http<{ prompt_overrides: Record<string, unknown> }>(
      `/api/prompt-studio/projects/${projectId}/prompt-config`,
      { method: "PATCH", body: JSON.stringify(body) }
    ),
  getProjectGptText: (projectId: number, stepCode: string) =>
    http<{
      step_code: string;
      text: string;
      supported: boolean;
      is_override: boolean;
      human_name?: string;
    }>(`/api/prompt-studio/projects/${projectId}/gpt-text/${stepCode}`),
  saveProjectGptText: (projectId: number, stepCode: string, text: string) =>
    http<{ step_code: string; text: string; supported: boolean; is_override: boolean }>(
      `/api/prompt-studio/projects/${projectId}/gpt-text/${stepCode}`,
      { method: "PUT", body: JSON.stringify({ text }) }
    ),
  resetProjectGptText: (projectId: number, stepCode: string) =>
    http<{ step_code: string; text: string; supported: boolean; is_override: boolean }>(
      `/api/prompt-studio/projects/${projectId}/gpt-text/${stepCode}`,
      { method: "DELETE" }
    ),
  saveGptTextAsTemplate: (
    projectId: number,
    stepCode: string,
    body: { name: string; text?: string },
  ) =>
    http<{
      step_code: string;
      name: string;
      filename: string;
      path: string;
      size: number;
    }>(`/api/prompt-studio/projects/${projectId}/gpt-text/${stepCode}/save-template`, {
      method: "POST",
      body: JSON.stringify(body),
    }),
  getGptVerdictContext: (projectId: number, stepCode: string, template = "default") =>
    http<{
      step_code: string;
      supported: boolean;
      template: string;
      templates: string[];
      prompt: string;
      artifact_preview: string;
      attachments: string[];
    }>(
      `/api/prompt-studio/projects/${projectId}/gpt-verdict/${stepCode}?template=${encodeURIComponent(template)}`,
    ),
  listGptVerdictTemplates: (stepCode: string) =>
    http<{ step_code: string; templates: string[] }>(
      `/api/prompt-studio/verdict-templates/${stepCode}`,
    ),
  saveGptVerdictTemplate: (
    projectId: number,
    stepCode: string,
    body: { name: string; content: string },
  ) =>
    http<{ ok: boolean; name: string; path: string }>(
      `/api/prompt-studio/projects/${projectId}/gpt-verdict/${stepCode}/save-template`,
      {
        method: "POST",
        body: JSON.stringify(body),
      },
    ),
  deleteGptVerdictTemplate: (projectId: number, stepCode: string, name: string) =>
    http<{ ok: boolean; name: string; removed: boolean }>(
      `/api/prompt-studio/projects/${projectId}/gpt-verdict/${stepCode}/templates/${encodeURIComponent(name)}`,
      { method: "DELETE" },
    ),
  getStepAttachments: (projectId: number, stepCode: string) =>
    http<{ step_code: string; files: string[] }>(
      `/api/prompt-studio/projects/${projectId}/step-attachments/${stepCode}`,
    ),
  runGptVerdict: (projectId: number, stepCode: string, prompt: string) =>
    http<{
      approved: boolean;
      fix_applied: boolean;
      fix_path: string;
      advanced: boolean;
      status: string;
      rounds: number;
      fix_text: string;
      last_raw: string;
      history: string[];
    }>(`/api/prompt-studio/projects/${projectId}/gpt-verdict/${stepCode}/run`, {
      method: "POST",
      body: JSON.stringify({ prompt }),
    }),
  pauseProject: (projectId: number) =>
    http<ProjectDetail>(`/api/projects/${projectId}/pause`, { method: "POST" }),
  resumeProject: (projectId: number) =>
    http<ProjectDetail>(`/api/projects/${projectId}/resume`, { method: "POST" }),
  continueProject: (projectId: number) =>
    http<{
      project: ProjectDetail;
      action: string;
      status: string;
      advanced: boolean;
      cleared?: string[];
    }>(`/api/projects/${projectId}/continue`, { method: "POST" }),
  stopProject: (projectId: number) =>
    http<{
      project: ProjectDetail;
      message: string;
      generation_still_active: boolean;
      xlsx_stopped: string[];
    }>(`/api/projects/${projectId}/stop`, {
      method: "POST",
    }),
  finishMissingImages: (projectId: number) =>
    http<{
      ok: boolean;
      kind: string;
      missing: number[];
      queued: number;
      already_running: boolean;
      message: string;
      project: ProjectDetail;
    }>(`/api/projects/${projectId}/finish/images`, { method: "POST" }),
  finishMissingVideos: (projectId: number) =>
    http<{
      ok: boolean;
      kind: string;
      missing: number[];
      queued: number;
      already_running: boolean;
      message: string;
      project: ProjectDetail;
    }>(`/api/projects/${projectId}/finish/videos`, { method: "POST" }),
  finishMissingAnimationPrompts: (projectId: number) =>
    http<{
      ok: boolean;
      kind: string;
      missing: number[];
      queued: number;
      already_done?: number;
      synced_from_xlsx?: number;
      already_running: boolean;
      message: string;
      project: ProjectDetail;
    }>(`/api/projects/${projectId}/finish/animation-prompts`, { method: "POST" }),
  startMassLanes: (
    projectId: number,
    body: { count?: number; topics?: string[] },
  ) =>
    http<{
      created: { id: number; topic: string; slug?: string }[];
      count: number;
      queue_size?: number;
      remaining?: number;
      started_id?: number | null;
    }>(`/api/projects/${projectId}/mass-lanes/start`, {
      method: "POST",
      body: JSON.stringify(body),
    }),
  getMassFactoryStatus: (projectId: number) =>
    http<{
      active: boolean;
      topics: string[];
      cursor: number;
      revision: number;
      filename: string;
      factory: boolean;
      busy_child_id: number | null;
      children: { id: number; topic: string; slug: string; status: string; lane_position?: number }[];
      queued_after_current?: boolean;
    }>(`/api/projects/${projectId}/mass-factory/status`),
  parseMassTopicsXlsx: async (projectId: number, file: File) => {
    const fd = new FormData();
    fd.append("file", file);
    const res = await fetch(`/api/projects/${projectId}/mass-lanes/parse-topics`, {
      method: "POST",
      body: fd,
    });
    if (!res.ok) throw new ApiError(res.status, await res.text());
    return res.json() as Promise<{
      topics: string[];
      count: number;
      revision?: number;
      queued_after_current?: boolean;
    }>;
  },
  wizardCatalog: () =>
    http<{
      questions: { field: string; title: string; choices: { id: string; label: string }[]; cols: number }[];
    }>(`/api/generation-options/wizard`),
  listGenerationConfigPresets: () =>
    http<{
      presets: GenerationConfigPreset[];
      fields: string[];
    }>(`/api/generation-config-presets`),
  createGenerationConfigPreset: (body: {
    name: string;
    settings: GenerationConfigPresetSettings;
  }) =>
    http<GenerationConfigPreset>(`/api/generation-config-presets`, {
      method: "POST",
      body: JSON.stringify(body),
    }),
  deleteGenerationConfigPreset: (id: string) =>
    http<{ ok: boolean }>(`/api/generation-config-presets/${encodeURIComponent(id)}`, {
      method: "DELETE",
    }),
  resetProjectStep: (projectId: number, stepCode: string) =>
    http<ProjectDetail>(`/api/projects/${projectId}/steps/${stepCode}/reset`, {
      method: "POST",
    }),
  downloadProjectXlsx: (projectId: number) =>
    `/api/projects/${projectId}/xlsx`,
  reloadProjectXlsx: (projectId: number) =>
    http<ProjectDetail>(`/api/projects/${projectId}/xlsx/reload`, { method: "POST" }),
  uploadProjectXlsx: async (projectId: number, file: File) => {
    const fd = new FormData();
    fd.append("file", file);
    const res = await fetch(`/api/projects/${projectId}/xlsx/upload`, {
      method: "POST",
      body: fd,
    });
    if (!res.ok) throw new ApiError(res.status, await res.text());
    return res.json() as Promise<ProjectDetail>;
  },
  previewProjectXlsx: (
    projectId: number,
    opts?: {
      sheet?: string;
      maxRows?: number;
      maxCols?: number;
      startRow?: number;
      row?: number;
      raw?: boolean;
    },
  ) => {
    const q = new URLSearchParams();
    if (opts?.sheet) q.set("sheet", opts.sheet);
    if (opts?.maxRows != null) q.set("max_rows", String(opts.maxRows));
    if (opts?.maxCols != null) q.set("max_cols", String(opts.maxCols));
    if (opts?.startRow != null) q.set("start_row", String(opts.startRow));
    if (opts?.row != null) q.set("row", String(opts.row));
    if (opts?.raw) q.set("raw", "true");
    const qs = q.toString();
    return http<XlsxPreview>(`/api/projects/${projectId}/xlsx/preview${qs ? `?${qs}` : ""}`);
  },
  ensureProjectRun: (projectId: number) =>
    http<{ run_id: number }>(`/api/projects/${projectId}/ensure-run`, { method: "POST" }),

  listProjectAssets: (projectId: number, kind = "all") =>
    http<ProjectAsset[]>(`/api/projects/${projectId}/assets?kind=${kind}`),

  replaceHeroImage: async (projectId: number, file: File, replacePath?: string) => {
    const fd = new FormData();
    fd.append("file", file);
    const q = replacePath ? `?replace_path=${encodeURIComponent(replacePath)}` : "";
    const res = await fetch(`/api/projects/${projectId}/assets/hero/replace${q}`, {
      method: "POST",
      body: fd,
    });
    if (!res.ok) throw new ApiError(res.status, await res.text());
    return res.json() as Promise<{ path: string; preview_url: string; id: string }>;
  },

  listMediaReview: (projectId: number, kind: "images" | "videos") =>
    http<
      {
        frame_id: number;
        number: number;
        voiceover_text: string;
        image_prompt: string | null;
        animation_prompt: string | null;
        status: string;
        artifact_uuid: string | null;
        file_path: string | null;
        preview_url: string | null;
      }[]
    >(`/api/projects/${projectId}/media-review?kind=${kind}`),

  // ── Prompt files (prompts/<step>/*.md на диске) ────────────────────
  listPromptFiles: (stepCode: string) =>
    http<PromptFileInfo[]>(`/api/prompt-files/${stepCode}`),
  getPromptFile: (stepCode: string, name: string) =>
    http<PromptFileContent>(
      `/api/prompt-files/${stepCode}/${encodeURIComponent(name)}/content`,
    ),
  downloadPromptFileUrl: (stepCode: string, name: string) =>
    `/api/prompt-files/${stepCode}/${encodeURIComponent(name)}/download`,
  savePromptFile: (stepCode: string, name: string, content: string) =>
    http<PromptFileContent>(
      `/api/prompt-files/${stepCode}/${encodeURIComponent(name)}`,
      { method: "PUT", body: JSON.stringify({ content }) },
    ),
  deletePromptFile: (stepCode: string, name: string) =>
    http<{ removed: boolean }>(
      `/api/prompt-files/${stepCode}/${encodeURIComponent(name)}`,
      { method: "DELETE" },
    ),
  uploadPromptFile: async (
    stepCode: string,
    file: File,
    name?: string,
  ): Promise<PromptFileInfo> => {
    const fd = new FormData();
    fd.append("file", file);
    const q = name ? `?name=${encodeURIComponent(name)}` : "";
    const res = await fetch(`/api/prompt-files/${stepCode}/upload${q}`, {
      method: "POST",
      body: fd,
    });
    if (!res.ok) throw new ApiError(res.status, await res.text());
    return res.json() as Promise<PromptFileInfo>;
  },
  listPromptFileHistory: (stepCode: string, name: string) =>
    http<PromptVersionInfo[]>(
      `/api/prompt-files/${stepCode}/${encodeURIComponent(name)}/history`,
    ),
  getPromptFileHistory: (stepCode: string, name: string, versionId: string) =>
    http<PromptVersionContent>(
      `/api/prompt-files/${stepCode}/${encodeURIComponent(name)}/history/${encodeURIComponent(versionId)}/content`,
    ),
  renamePromptFile: (stepCode: string, name: string, newName: string) =>
    http<PromptFileInfo>(
      `/api/prompt-files/${stepCode}/${encodeURIComponent(name)}/rename`,
      { method: "PATCH", body: JSON.stringify({ new_name: newName }) },
    ),
  renamePromptVersionLabel: (
    stepCode: string,
    name: string,
    versionId: string,
    label: string,
  ) =>
    http<PromptVersionInfo>(
      `/api/prompt-files/${stepCode}/${encodeURIComponent(name)}/history/${encodeURIComponent(versionId)}`,
      { method: "PATCH", body: JSON.stringify({ label }) },
    ),
  restorePromptFileVersion: (stepCode: string, name: string, versionId: string) =>
    http<PromptFileContent>(
      `/api/prompt-files/${stepCode}/${encodeURIComponent(name)}/history/${encodeURIComponent(versionId)}/restore`,
      { method: "POST" },
    ),
};

export interface PromptFileInfo {
  name: string;
  filename: string;
  size: number;
  modified: number;
  is_default: boolean;
}

export interface PromptFileContent {
  name: string;
  filename: string;
  content: string;
  size: number;
  modified: number;
}

export interface PromptVersionInfo {
  id: string;
  label: string;
  saved_at: number;
  size: number;
}

export interface PromptVersionContent {
  id: string;
  label: string;
  content: string;
  saved_at: number;
  size: number;
}

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
