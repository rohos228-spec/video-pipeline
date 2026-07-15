/**
 * Типы DTO с бэкенда. Шейп совпадает с app/web/schemas.py.
 */

export type ProjectStatus =
  | "new" | "planning" | "scripting" | "splitting"
  | "generating_hero" | "generating_items"
  | "enriching_1" | "enriching_2" | "enriching_3" | "enriching_4" | "enriching_5"
  | "generating_image_prompts" | "generating_images"
  | "generating_animation_prompts" | "generating_videos"
  | "generating_audio" | "generating_music" | "assembling" | "publishing"
  | "plan_ready" | "script_ready" | "frames_ready"
  | "hero_ready" | "items_ready"
  | "enrich_1_ready" | "enrich_2_ready" | "enrich_3_ready" | "enrich_4_ready" | "enrich_5_ready"
  | "image_prompts_ready" | "images_ready" | "animation_prompts_ready"
  | "videos_ready" | "audio_ready" | "music_ready"
  | "assembled" | "published" | "paused" | "failed";

export type NodeType =
  | "plan" | "script" | "split"
  | "hero" | "items"
  | "enrich_1" | "enrich_2" | "enrich_3" | "enrich"
  | "image_prompts" | "images"
  | "animation_prompts" | "videos"
  | "audio" | "music" | "assemble" | "publish"
  | "hitl_gate" | "hitl_hero" | "hitl_images" | "hitl_videos" | "hitl_final"
  | string; // допускаем кастомные типы

/** Один персонаж с листа «Персонажи» в project.xlsx. */
export interface ExcelHeroCharacter {
  id: string;
  name: string;
  look: string;
  clothes: string;
  char: string;
  rules: string;
  ref_ids: string[];
  prompt_name: string | null;
}

export type NodeRunStatus =
  | "pending" | "queued" | "running" | "waiting_hitl"
  | "done" | "failed" | "skipped";

export type WorkflowRunStatus =
  | "new" | "running" | "paused" | "waiting_hitl"
  | "done" | "failed" | "cancelled";

export interface WorkflowNode {
  id: string;
  type: NodeType;
  position: { x: number; y: number };
  data: Record<string, unknown> & { label?: string; description?: string };
}

export interface WorkflowEdge {
  id: string;
  source: string;
  target: string;
  sourceHandle?: string | null;
  targetHandle?: string | null;
}

export interface WorkflowSummary {
  id: number;
  name: string;
  description: string | null;
  version: number;
  is_default: boolean;
  created_at: string;
  updated_at: string;
}

export interface WorkflowDetail extends WorkflowSummary {
  nodes: WorkflowNode[];
  edges: WorkflowEdge[];
  meta: Record<string, unknown>;
}

export interface ProjectSummary {
  id: number;
  slug: string;
  topic: string;
  status: ProjectStatus;
  hero_mode: string;
  auto_mode: boolean;
  created_at: string;
  updated_at: string;
  mass_parent_id?: number | null;
  mass_factory?: boolean;
  mass_lane_position?: number | null;
  batch_id?: number | null;
  batch_position?: number | null;
  sidebar_folder_id?: string | null;
  sidebar_order?: number | null;
  gen_queue_position?: number | null;
}

export interface SidebarFolder {
  id: string;
  name: string;
  order: number;
  batch_id?: number | null;
  created_at?: string | null;
  updated_at?: string | null;
}

export interface BatchSidebarInfo {
  folder_id: string | null;
  name: string;
  status: string;
  progress: { done: number; total: number };
}

export interface GenQueueIdleInfo {
  project_id: number;
  position: number;
  reason: string;
  detail: string;
}

export interface SidebarLayout {
  folders: SidebarFolder[];
  project_layout: Record<string, { folder_id: string | null; order: number }>;
  gen_queue: number[];
  gen_queue_positions: Record<number, number>;
  gen_queue_idle?: GenQueueIdleInfo | null;
  batches?: Record<string, BatchSidebarInfo>;
}

export interface GenerationConfigPresetSettings {
  image_generator?: string | null;
  aspect_ratio?: string | null;
  image_resolution?: string | null;
  image_quality?: string | null;
  image_relax?: boolean | null;
  video_generator?: string | null;
  video_resolution?: string | null;
  video_relax?: boolean | null;
}

export interface GenerationConfigPreset {
  id: string;
  name: string;
  settings: GenerationConfigPresetSettings;
  created_at?: string | null;
  updated_at?: string | null;
}

export interface ProjectDetail extends ProjectSummary {
  general_plan: string | null;
  script_text: string | null;
  hero_description: string | null;
  image_generator: string | null;
  aspect_ratio: string | null;
  image_resolution: string | null;
  image_quality: string | null;
  image_relax: boolean | null;
  video_generator: string | null;
  video_resolution: string | null;
  video_relax: boolean | null;
  hero_count: number | null;
  hero_descriptions: string[];
  hero_variations: number[];
  hero_variation_modifiers: unknown[];
  item_descriptions: string[];
  item_variations: number[];
  enrich_slots_count: number;
  prompt_overrides: Record<string, unknown>;
  gpt_text_overrides: Record<string, string>;
  meta: Record<string, unknown>;
  montage_handoff_pending?: boolean;
  /** Воркер держит asyncio-task (даже если status уже откатили). */
  generation_active?: boolean;
}

export interface NodeRunDTO {
  id: number;
  workflow_run_id: number;
  node_key: string;
  node_type: NodeType;
  status: NodeRunStatus;
  progress: number;
  progress_text: string | null;
  error: string | null;
  hitl_request_id: number | null;
  attempts: number;
  started_at: string | null;
  finished_at: string | null;
  updated_at: string;
}

export interface WorkflowRunSummary {
  id: number;
  workflow_id: number;
  project_id: number;
  status: WorkflowRunStatus;
  started_at: string | null;
  finished_at: string | null;
  created_at: string;
  updated_at: string;
}

export interface WorkflowRunDetail extends WorkflowRunSummary {
  nodes_snapshot: WorkflowNode[];
  edges_snapshot: WorkflowEdge[];
  node_runs: NodeRunDTO[];
}

export interface FrameDTO {
  id: number;
  project_id: number;
  number: number;
  voiceover_text: string;
  meaning: string | null;
  transition_from: string | null;
  transition_to: string | null;
  duration_seconds: number | null;
  start_ts: number | null;
  end_ts: number | null;
  image_prompt: string | null;
  animation_prompt: string | null;
  status: string;
  attrs: Record<string, unknown>;
}

export interface MontageBoardCharacterRef {
  id: string;
  name: string;
  image_url: string | null;
}

export interface MontageBoardFrame {
  frame_id: number;
  number: number;
  voiceover_text: string;
  voiceover_excel: string;
  characters: string;
  character_refs: MontageBoardCharacterRef[];
  start_ts: number | null;
  end_ts: number | null;
  duration_seconds: number | null;
  has_shot2: boolean;
  shot1_use_seconds: number | null;
  shot2_use_seconds: number | null;
  shot1_timeline_start: number | null;
  shot1_timeline_end: number | null;
  shot2_timeline_start: number | null;
  shot2_timeline_end: number | null;
  video_shot1_duration: number | null;
  video_shot2_duration: number | null;
  image_shot1_url: string | null;
  image_shot2_url: string | null;
  video_shot1_url: string | null;
  video_shot2_url: string | null;
  plan_column: number;
}

export interface MontageBoardMeta {
  video_trims: Record<string, { start: number; end: number }>;
  stale_videos: string[];
  highlights: string[];
  corrections: Record<string, string>;
  applied_at: string | null;
}

export interface MontageBoardDTO {
  frames: MontageBoardFrame[];
  frame_count: number;
  meta: MontageBoardMeta;
}

export interface PromptDTO {
  id: number;
  key: string;
  version: number;
  text: string;
  active: boolean;
  created_at: string;
}

export type HITLKind =
  | "approve_plan" | "approve_script" | "approve_hero"
  | "approve_images" | "approve_videos" | "approve_final";

export type HITLDecisionValue =
  | "pending" | "approved" | "regenerate" | "edit_prompt" | "rejected";

export interface HITLDTO {
  id: number;
  project_id: number;
  frame_id: number | null;
  kind: HITLKind;
  decision: HITLDecisionValue;
  payload: Record<string, unknown>;
  decided_at: string | null;
  created_at: string;
}

export interface ArtifactDTO {
  id: number;
  project_id: number;
  frame_id: number | null;
  kind: string;
  uuid: string;
  path: string;
  meta: Record<string, unknown>;
  created_at: string;
}

export interface FleetTransferState {
  project_id: number;
  job?: string;
  phase: string;
  direction?: string;
  percent?: number;
  sent_mb?: number;
  total_mb?: number;
  message?: string;
  source_node?: string;
  target?: string;
  slug?: string;
  status: "active" | "done" | "error";
}

/**
 * Шейп событий из WebSocket (publish_node_event / publish_project_event / ...).
 */
export type BusEvent =
  | { type: "subscribed"; channel: string }
  | { type: "node_status_changed"; run_id: number; node_key: string; node_type: string; from: NodeRunStatus; to: NodeRunStatus; project_id?: number }
  | { type: "run_created"; run_id: number; project_id: number; workflow_id: number }
  | { type: "run_cancelled"; run_id: number }
  | { type: "project_created"; project_id: number; slug: string; topic: string }
  | { type: "project_updated"; project_id: number }
  | { type: "project_deleted"; project_id: number }
  | { type: "hitl_pending"; project_id: number; hitl_id: number; kind: string }
  | { type: "hitl_decided"; project_id: number; hitl_id: number; decision: string; kind: string }
  | { type: "log"; run_id: number; level: string; line: string }
  | (Record<string, unknown> & { type: string });
