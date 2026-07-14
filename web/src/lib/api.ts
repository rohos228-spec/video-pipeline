/**
 * Тонкий fetch-обёртка для бэкенд-API. Все запросы относительные
 * (`/api/...`) — в dev next.config.ts проксирует на :8765, в проде FastAPI
 * сам отдаёт фронт + API из одного origin.
 */

import type {
  ArtifactDTO,
  ExcelHeroCharacter,
  FrameDTO,
  MontageBoardDTO,
  MontageBoardMeta,
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
import type { BlockSelection } from "./prompt-styles";

export interface StepTemplateBlock {
  number: number;
  title: string;
  body: string;
}

export interface LibraryItemDTO {
  id: number;
  kind: string;
  key: string;
  title: string;
  file_path: string;
  active_version: number;
  content_hash: string;
  meta: Record<string, unknown>;
  created_at: string;
  updated_at: string;
}

export interface LibraryItemDetailDTO extends LibraryItemDTO {
  content: string;
}

export interface LibraryVersionDTO {
  id: number;
  item_id: number;
  version: number;
  content_hash: string;
  message?: string | null;
  author?: string | null;
  source?: string | null;
  file_path: string;
  meta: Record<string, unknown>;
  created_at: string;
}

export interface BlockActivityEntry {
  id: number;
  event_type: string;
  category?: string | null;
  block_id?: string | null;
  path?: string | null;
  payload: Record<string, unknown>;
  created_at: string;
}

export interface LibraryConfigDTO {
  id: number;
  name: string;
  project_id?: number | null;
  snapshot: Record<string, unknown>;
  content_hash: string;
  meta: Record<string, unknown>;
  created_at: string;
}

async function http<T>(
  path: string,
  options: RequestInit = {},
  timeoutMs = 30_000,
): Promise<T> {
  const controller = new AbortController();
  const timer = window.setTimeout(() => controller.abort(), timeoutMs);
  try {
    const res = await fetch(path, {
      ...options,
      signal: controller.signal,
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
  } catch (e) {
    if (e instanceof DOMException && e.name === "AbortError") {
      throw new ApiError(
        0,
        "Сервер не ответил за 30 с — проверьте окно BACKEND (Uvicorn на :8765)",
      );
    }
    throw e;
  } finally {
    window.clearTimeout(timer);
  }
}

export interface MontagePendingOp {
  type:
    | "image_regen"
    | "image_regen_prompt"
    | "image_regen_correction"
    | "video_regen"
    | "video_regen_prompt";
  frame_number: number;
  shot: 1 | 2;
  prompt?: string;
  correction?: string;
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
    super(formatApiError(detail, status));
    this.name = "ApiError";
  }
}

export function formatApiError(
  detail: string | object,
  status?: number,
): string {
  if (status === 405) {
    return "API устарел (Method not allowed) — закройте Studio и запустите RUN-STUDIO.ps1 после git pull / UPDATE";
  }
  if (typeof detail === "string") {
    try {
      const parsed = JSON.parse(detail) as { detail?: unknown };
      if (typeof parsed.detail === "string") return parsed.detail;
    } catch {
      return detail;
    }
    return detail;
  }
  if (detail && typeof detail === "object") {
    const d = detail as Record<string, unknown>;
    if (Array.isArray(d.errors) && d.errors.length > 0) {
      return d.errors.map(String).join("; ");
    }
    if (typeof d.error === "string" && d.error.trim()) return d.error;
    if (typeof d.message === "string" && d.message.trim()) return d.message;
    if ("detail" in d) {
      const inner = d.detail;
      if (typeof inner === "string") return inner;
      if (Array.isArray(inner)) return inner.map(String).join("; ");
      if (inner && typeof inner === "object") {
        const nested = inner as Record<string, unknown>;
        if (Array.isArray(nested.errors) && nested.errors.length > 0) {
          return nested.errors.map(String).join("; ");
        }
        if (typeof nested.error === "string") return nested.error;
      }
    }
  }
  return "Ошибка операции";
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
  createChildProject: (parentId: number) =>
    http<ProjectDetail>(`/api/projects/${parentId}/child`, { method: "POST" }),

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
  enqueueGenQueue: (body: {
    project_id: number;
    mode: "full" | "until_node";
    target_node_key?: string;
    target_node_type?: string;
  }) =>
    http<{
      gen_queue: number[];
      gen_queue_positions: Record<number, number>;
      position: number | null;
      gen_queue_run?: Record<string, unknown> | null;
    }>(`/api/sidebar-layout/gen-queue/enqueue`, {
      method: "POST",
      body: JSON.stringify(body),
    }),
  listStepCatalog: () =>
    http<{ code: string; label: string; running_status: string; ready_status: string }[]>(
      `/api/projects/steps/catalog`
    ),
  runProjectStep: (
    projectId: number,
    stepCode: string,
    opts?: { dryRun?: boolean; nodeKey?: string },
  ) => {
    const params = new URLSearchParams();
    if (opts?.dryRun) params.set("dry_run", "true");
    if (opts?.nodeKey) params.set("node_key", opts.nodeKey);
    const q = params.toString() ? `?${params.toString()}` : "";
    return http<ProjectDetail>(`/api/projects/${projectId}/steps/${stepCode}/run${q}`, {
      method: "POST",
    });
  },
  patchExcelGptConfig: (
    projectId: number,
    nodeKey: string,
    patch: {
      label?: string;
      inputSource?: string;
      uploadedFileName?: string;
      slotIndex?: number;
    },
  ) =>
    http<{ ok: boolean; config: Record<string, unknown> }>(
      `/api/projects/${projectId}/excel-gpt/${encodeURIComponent(nodeKey)}`,
      { method: "PATCH", body: JSON.stringify(patch) },
    ),
  uploadExcelGptFile: (projectId: number, nodeKey: string, file: File) => {
    const fd = new FormData();
    fd.append("file", file);
    return http<{ ok: boolean; fileName: string; path: string }>(
      `/api/projects/${projectId}/excel-gpt/${encodeURIComponent(nodeKey)}/upload`,
      { method: "POST", body: fd },
    );
  },
  remapExcelGptNodes: (projectId: number, mapping: Record<string, string>) =>
    http<{ ok: boolean; remapped: string[] }>(
      `/api/projects/${projectId}/excel-gpt/remap-keys`,
      { method: "POST", body: JSON.stringify({ mapping }) },
    ),

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

  getMontageBoard: (projectId: number) =>
    http<MontageBoardDTO>(`/api/projects/${projectId}/montage-board`),

  applyMontageBoard: (
    projectId: number,
    body: {
      video_trims: Record<string, { start: number; end: number }>;
      pending_ops: MontagePendingOp[];
    },
  ) =>
    http<{
      ok: boolean;
      started?: boolean;
      already_running?: boolean;
      message?: string;
      meta?: MontageBoardMeta;
      errors?: string[];
      job?: { status?: string; total_ops?: number; error?: string | null };
    }>(`/api/projects/${projectId}/montage-board/apply`, {
      method: "POST",
      body: JSON.stringify(body),
    }),

  getMontageApplyStatus: (projectId: number) =>
    http<{
      job: {
        status?: string;
        error?: string | null;
        total_ops?: number;
        done_ops?: number;
      };
    }>(`/api/projects/${projectId}/montage-board/apply-status`),

  runMontageBoard: (projectId: number) =>
    http<{ started: boolean; already_running?: boolean; job?: Record<string, unknown> }>(
      `/api/projects/${projectId}/montage-board/montage`,
      { method: "POST" },
    ),

  getMontageBoardStatus: (projectId: number) =>
    http<{ job: { status?: string; error?: string | null } }>(
      `/api/projects/${projectId}/montage-board/montage-status`,
    ),

  deleteMontageImage: (projectId: number, frameNumber: number, shot: 1 | 2) =>
    http<{ ok: boolean }>(
      `/api/projects/${projectId}/montage-board/delete-image?frame_number=${frameNumber}&shot=${shot}`,
      { method: "POST" },
    ),

  deleteMontageVideo: (projectId: number, frameNumber: number, shot: 1 | 2) =>
    http<{ ok: boolean }>(
      `/api/projects/${projectId}/montage-board/delete-video?frame_number=${frameNumber}&shot=${shot}`,
      { method: "POST" },
    ),

  uploadMontageImage: async (projectId: number, frameNumber: number, shot: 1 | 2, file: File) => {
    const fd = new FormData();
    fd.append("file", file);
    const res = await fetch(
      `/api/projects/${projectId}/montage-board/upload-image?frame_number=${frameNumber}&shot=${shot}`,
      { method: "POST", body: fd },
    );
    if (!res.ok) throw new ApiError(res.status, await res.text());
    return res.json() as Promise<{ ok: boolean; preview_url: string }>;
  },

  uploadMontageVideo: async (projectId: number, frameNumber: number, shot: 1 | 2, file: File) => {
    const fd = new FormData();
    fd.append("file", file);
    const res = await fetch(
      `/api/projects/${projectId}/montage-board/upload-video?frame_number=${frameNumber}&shot=${shot}`,
      { method: "POST", body: fd },
    );
    if (!res.ok) throw new ApiError(res.status, await res.text());
    return res.json() as Promise<{ ok: boolean; preview_url: string }>;
  },

  uploadMontageVoice: async (projectId: number, file: File) => {
    const fd = new FormData();
    fd.append("file", file);
    const res = await fetch(`/api/projects/${projectId}/montage-board/upload-voice`, {
      method: "POST",
      body: fd,
    });
    if (!res.ok) throw new ApiError(res.status, await res.text());
    return res.json() as Promise<{ ok: boolean; path: string }>;
  },

  uploadMontageMusic: async (projectId: number, file: File) => {
    const fd = new FormData();
    fd.append("file", file);
    const res = await fetch(`/api/projects/${projectId}/montage-board/upload-music`, {
      method: "POST",
      body: fd,
    });
    if (!res.ok) throw new ApiError(res.status, await res.text());
    return res.json() as Promise<{ ok: boolean; path: string }>;
  },

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
      blocks?: {
        category: string;
        id: string;
        label: string;
        preview: string;
        body: string;
      }[];
      steps: string[];
      step_block_categories: Record<string, string[]>;
      node_type_to_step: Record<string, string>;
      style_presets: { id: string; label: string; description?: string }[];
    }>(`/api/prompt-studio/catalog`),
  promptStudioStepMeta: (stepId: string) =>
    http<{ step_id: string; block_categories: string[]; vars: string[] }>(
      `/api/prompt-studio/steps/${stepId}/meta`,
    ),
  promptStudioStepPresets: (stepCode: string) =>
    http<import("@/lib/prompt-builder/prompt-presets").StepPresetsFile>(
      `/api/prompt-studio/step-presets/${encodeURIComponent(stepCode)}`,
    ),
  patchStepPreset: (
    stepCode: string,
    presetId: string,
    body: { label?: string; description?: string; blocks?: Record<string, string | null> },
  ) =>
    http<import("@/lib/prompt-builder/prompt-presets").PromptStepPreset>(
      `/api/prompt-studio/step-presets/${encodeURIComponent(stepCode)}/presets/${encodeURIComponent(presetId)}`,
      { method: "PATCH", body: JSON.stringify(body) },
    ),
  createStepPreset: (
    stepCode: string,
    presetId: string,
    body: { label?: string; description?: string; blocks?: Record<string, string | null> },
  ) =>
    http<import("@/lib/prompt-builder/prompt-presets").PromptStepPreset>(
      `/api/prompt-studio/step-presets/${encodeURIComponent(stepCode)}/presets/${encodeURIComponent(presetId)}`,
      { method: "POST", body: JSON.stringify(body) },
    ),
  deleteStepPreset: (stepCode: string, presetId: string) =>
    http<{ step_code: string; id: string; deleted: boolean }>(
      `/api/prompt-studio/step-presets/${encodeURIComponent(stepCode)}/presets/${encodeURIComponent(presetId)}`,
      { method: "DELETE" },
    ),
  composePrompt: (body: {
    node_type?: string;
    step_id?: string;
    project_id?: number;
    blocks?: Record<string, BlockSelection>;
    vars?: Record<string, string | number>;
    style_preset?: string;
  }) =>
    http<{ text: string; blocks: Record<string, BlockSelection>; vars: Record<string, string> }>(
      `/api/prompt-studio/compose`,
      { method: "POST", body: JSON.stringify(body) }
    ),
  patchProjectPromptConfig: (
    projectId: number,
    body: {
      style_profile?: string;
      blocks?: Record<string, BlockSelection>;
      vars?: Record<string, string | number>;
      use_blocks_v2?: boolean;
      legacy?: Record<string, string>;
    }
  ) =>
    http<{ prompt_overrides: Record<string, unknown> }>(
      `/api/prompt-studio/projects/${projectId}/prompt-config`,
      { method: "PATCH", body: JSON.stringify(body) }
    ),
  // Блочный редактор шаблона шага (steps/<id>/template.md, карточки 1..N).
  getStepTemplate: (stepId: string) =>
    http<{ step_id: string; blocks: StepTemplateBlock[] }>(
      `/api/prompt-studio/step-template/${stepId}`
    ),
  saveStepTemplate: (stepId: string, blocks: StepTemplateBlock[]) =>
    http<{ step_id: string; blocks: StepTemplateBlock[] }>(
      `/api/prompt-studio/step-template/${stepId}`,
      { method: "PUT", body: JSON.stringify({ blocks }) }
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
  promptStudioSyncBlocks: () =>
    http<{
      categories: number;
      blocks_total: number;
      discovered: { category: string; block_id: string }[];
      discovered_count: number;
    }>(`/api/prompt-studio/blocks/sync`, { method: "POST" }),
  promptStudioBlockActivity: (params?: { limit?: number; category?: string }) => {
    const q = new URLSearchParams();
    if (params?.limit) q.set("limit", String(params.limit));
    if (params?.category) q.set("category", params.category);
    const qs = q.toString();
    return http<BlockActivityEntry[]>(
      `/api/prompt-studio/block-activity${qs ? `?${qs}` : ""}`,
    );
  },
  promptStudioLogBlockActivity: (body: {
    event_type: "block_selected" | "block_viewed";
    category: string;
    block_id: string;
    project_id?: number;
    step_id?: string;
    step_code?: string;
    prompt_variant?: string | null;
  }) =>
    http<{ ok: boolean }>(`/api/prompt-studio/block-activity`, {
      method: "POST",
      body: JSON.stringify(body),
    }),
  getPromptBlock: (category: string, blockId: string) =>
    http<{ category: string; id: string; body: string }>(
      `/api/prompt-studio/blocks/${encodeURIComponent(category)}/${encodeURIComponent(blockId)}`,
    ),
  savePromptBlock: (
    category: string,
    blockId: string,
    body: { content: string; message?: string },
  ) =>
    http<{ category: string; id: string; label: string; version: number; library_item_id: number }>(
      `/api/prompt-studio/blocks/${encodeURIComponent(category)}/${encodeURIComponent(blockId)}`,
      { method: "PUT", body: JSON.stringify(body) },
    ),
  createPromptBlock: (
    category: string,
    body: { block_id: string; content?: string; message?: string },
  ) =>
    http<{ category: string; id: string; label: string; version: number; library_item_id: number }>(
      `/api/prompt-studio/blocks/${encodeURIComponent(category)}`,
      { method: "POST", body: JSON.stringify(body) },
    ),
  deletePromptBlock: (category: string, blockId: string) =>
    http<{ category: string; id: string; deleted: boolean }>(
      `/api/prompt-studio/blocks/${encodeURIComponent(category)}/${encodeURIComponent(blockId)}`,
      { method: "DELETE" },
    ),
  renamePromptBlock: (
    category: string,
    blockId: string,
    body: { new_block_id: string; message?: string },
  ) =>
    http<{ category: string; id: string; renamed_from?: string; label: string }>(
      `/api/prompt-studio/blocks/${encodeURIComponent(category)}/${encodeURIComponent(blockId)}/rename`,
      { method: "POST", body: JSON.stringify(body) },
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
  getStepAttachments: (projectId: number, stepCode: string, nodeKey?: string) => {
    const q = nodeKey ? `?node_key=${encodeURIComponent(nodeKey)}` : "";
    return http<{ step_code: string; node_key?: string; files: string[] }>(
      `/api/prompt-studio/projects/${projectId}/step-attachments/${stepCode}${q}`,
    );
  },
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
  getGlobalActivePrompts: () =>
    http<Record<string, string>>("/api/prompt-files/global-active"),
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

  // ── Local library (data/library + SQLite versions) ────────────────
  listLibraryItems: (params?: { kind?: string; q?: string }) => {
    const q = new URLSearchParams();
    if (params?.kind) q.set("kind", params.kind);
    if (params?.q) q.set("q", params.q);
    const qs = q.toString();
    return http<LibraryItemDTO[]>(`/api/library/items${qs ? `?${qs}` : ""}`);
  },
  getLibraryItem: (id: number) =>
    http<LibraryItemDetailDTO>(`/api/library/items/${id}`),
  createLibraryItem: (body: {
    kind: string;
    key?: string;
    title?: string;
    file_path?: string;
    content: string;
    message?: string;
    meta?: Record<string, unknown>;
  }) =>
    http<LibraryItemDetailDTO>(`/api/library/items`, {
      method: "POST",
      body: JSON.stringify(body),
    }),
  updateLibraryItem: (
    id: number,
    body: { title?: string; content: string; message?: string; meta?: Record<string, unknown> },
  ) =>
    http<LibraryItemDetailDTO>(`/api/library/items/${id}`, {
      method: "PUT",
      body: JSON.stringify(body),
    }),
  listLibraryVersions: (id: number) =>
    http<LibraryVersionDTO[]>(`/api/library/items/${id}/versions`),
  restoreLibraryVersion: (id: number, version: number) =>
    http<LibraryItemDetailDTO>(`/api/library/items/${id}/restore/${version}`, {
      method: "POST",
    }),
  downloadLibraryItemUrl: (id: number) => `/api/library/items/${id}/download`,
  saveLibraryConfig: (body: {
    name?: string;
    project_id?: number;
    snapshot?: Record<string, unknown>;
  }) =>
    http<LibraryConfigDTO>(`/api/library/configs/save`, {
      method: "POST",
      body: JSON.stringify(body),
    }),
  savePromptBundle: (body: {
    project_id?: number;
    step_id?: string;
    step_code?: string;
    node_type?: string;
    source_name?: string;
    title?: string;
    source_prompt?: string;
    processed_prompt?: string;
    blocks?: { kind: string; label: string; body: string }[];
  }) =>
    http<{ ok: boolean; items: Record<string, unknown> }>(`/api/library/prompt-bundles/save`, {
      method: "POST",
      body: JSON.stringify(body),
    }),
};

export interface PromptFileInfo {
  name: string;
  filename: string;
  size: number;
  modified: number | null;
  is_default: boolean;
}

export interface PromptFileContent {
  name: string;
  filename: string;
  content: string;
  size: number;
  modified: number | null;
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
