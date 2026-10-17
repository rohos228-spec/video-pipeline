/**
 * Карточки результатов действий по контракту WS2.
 *
 * ws2-r2.0 — PR #378 @ ae950ce6 (база #344, сейчас ead3f7e8).
 * Схема: web/src/lib/orchestratorActionResults.schema.json
 * sha256 850ad63329195bd6d58b7b313550e2cc3994521616c57476df655ddd7c1337aa
 *
 * Моки без пометки r2.1 — JSON из docs/fable/action_results_contract.md.
 * Поля snapshot_id / version / base_version / warnings / skipped / partial
 * в r2.0 нет: они опциональны и помечены как r2.1-предварительные.
 * Когда WS2 выложит ws2-r2.1, сверять этот файл, флаг версии и sha256.
 *
 * Шина (в contracts.py события нет): конверт ActionResult лежит в payload
 * step_finished или message.
 *
 * ApprovalRequired не становится кнопкой одобрения и его change_id
 * не используется. Строка «Ждёт подтверждения» ждёт событие ядра
 * approval_required / approval_decided. ActionMetadata — только подпись риска.
 *
 * Тело POST «Править» и «Восстановить» моделью не задано.
 */

export const ACTION_CONTRACT_VERSION = "ws2-r2.0";
export const ACTION_CONTRACT_REF = "ae950ce6c81892c9b8b3fc07b095780b5f26c075";
export const ACTION_SCHEMA_SHA256 = "850ad63329195bd6d58b7b313550e2cc3994521616c57476df655ddd7c1337aa";
export const REPORT_EDIT_ACTION = "update_report";

export interface ActionMetadata {
  name: string;
  risk: "none" | "low" | "medium" | "high";
  read_only: boolean;
  destructive: boolean;
  paid: boolean;
}

export interface ActionNodeRef {
  project_id: number;
  node_id: string;
  kind: "node" | "prompt" | "params" | "report";
}

export interface TextDiffLine {
  kind: "same" | "add" | "remove";
  text: string;
}

export interface FieldDiffRow {
  key: string;
  before: string | null;
  after: string | null;
}

export type ActionCardKind = "create" | "node" | "prompt" | "params" | "report" | "mark" | "error" | "waiting" | "neutral";

export interface ActionResultView {
  id: string;
  action: string;
  /** Change_id конверта правки или пометки. У ApprovalRequired всегда null. */
  changeId: string | null;
  metadata: ActionMetadata;
  kind: ActionCardKind;
  unified: TextDiffLine[] | null;
  fields: FieldDiffRow[] | null;
  hideBefore: boolean;
  nodeRef: ActionNodeRef | null;
  html: string | null;
  reportId: number | null;
  editAction: string | null;
  marked: boolean;
  restoreAction: string | null;
  error: string | null;
  /** r2.1-предварительные. Пустые, если в конверте полей нет. */
  warnings: string[];
  skipped: string[];
  partial: boolean;
  snapshotId: string | null;
  snapshotUrl: string | null;
  version: number | null;
  baseVersion: number | null;
  conflict: boolean;
}

export interface ReportEditBody {
  text: string;
  action: {
    name: typeof REPORT_EDIT_ACTION;
    report_id: number;
  };
}

export interface RestoreBody {
  text: string;
  action: {
    name: string;
    change_id?: string;
    snapshot_id?: string;
  };
}

const R2_METADATA: Record<string, ActionMetadata> = {
  read_pipeline_code: { name: "read_pipeline_code", risk: "none", read_only: true, destructive: false, paid: false },
  rag_search: { name: "rag_search", risk: "none", read_only: true, destructive: false, paid: false },
  run_validators: { name: "run_validators", risk: "none", read_only: true, destructive: false, paid: false },
  stop_node: { name: "stop_node", risk: "medium", read_only: false, destructive: false, paid: false },
  resume_node: { name: "resume_node", risk: "medium", read_only: false, destructive: false, paid: true },
  create_node: { name: "create_node", risk: "medium", read_only: false, destructive: false, paid: false },
  edit_node_prompt: { name: "edit_node_prompt", risk: "medium", read_only: false, destructive: false, paid: false },
  edit_node_params: { name: "edit_node_params", risk: "medium", read_only: false, destructive: false, paid: false },
  create_report: { name: "create_report", risk: "low", read_only: false, destructive: false, paid: false },
  update_report: { name: "update_report", risk: "low", read_only: false, destructive: false, paid: false },
  export_nodes_snapshot: { name: "export_nodes_snapshot", risk: "low", read_only: true, destructive: false, paid: false },
  revert_node_to_git: { name: "revert_node_to_git", risk: "medium", read_only: false, destructive: false, paid: false },
  "prompt.delete_master": { name: "prompt.delete_master", risk: "high", read_only: false, destructive: true, paid: false },
  "prompt.create_master": { name: "prompt.create_master", risk: "medium", read_only: false, destructive: true, paid: false },
  "montage.suggest_scene_variants": {
    name: "montage.suggest_scene_variants",
    risk: "high",
    read_only: false,
    destructive: false,
    paid: true,
  },
  "history.rollback": { name: "history.rollback", risk: "high", read_only: false, destructive: true, paid: false },
};

export function isKnownActionName(name: string): boolean {
  return Object.prototype.hasOwnProperty.call(R2_METADATA, name);
}

/** Имя вне таблицы — платное и деструктивное. Сырые флаги конверта не доверяем. */
export function metadataOf(name: string, _raw?: unknown): ActionMetadata {
  const known = R2_METADATA[name];
  if (known) return known;
  return { name, risk: "high", read_only: false, destructive: true, paid: true };
}

export function lineDiff(before: string, after: string): TextDiffLine[] {
  const left = before.split("\n");
  const right = after.split("\n");
  const n = left.length;
  const m = right.length;
  const dp: number[][] = Array.from({ length: n + 1 }, () => Array<number>(m + 1).fill(0));
  for (let i = n - 1; i >= 0; i -= 1) {
    for (let j = m - 1; j >= 0; j -= 1) {
      dp[i][j] = left[i] === right[j] ? dp[i + 1][j + 1] + 1 : Math.max(dp[i + 1][j], dp[i][j + 1]);
    }
  }
  const lines: TextDiffLine[] = [];
  let i = 0;
  let j = 0;
  while (i < n && j < m) {
    if (left[i] === right[j]) {
      lines.push({ kind: "same", text: left[i] });
      i += 1;
      j += 1;
    } else if (dp[i + 1][j] >= dp[i][j + 1]) {
      lines.push({ kind: "remove", text: left[i] });
      i += 1;
    } else {
      lines.push({ kind: "add", text: right[j] });
      j += 1;
    }
  }
  while (i < n) {
    lines.push({ kind: "remove", text: left[i] });
    i += 1;
  }
  while (j < m) {
    lines.push({ kind: "add", text: right[j] });
    j += 1;
  }
  return lines;
}

export function unifiedLines(unified: string): TextDiffLine[] {
  const raw = unified.split("\n");
  if (raw.length > 0 && raw[raw.length - 1] === "") raw.pop();
  return raw.map((line) => {
    if (line.startsWith("+") && !line.startsWith("+++")) return { kind: "add" as const, text: line.slice(1) };
    if (line.startsWith("-") && !line.startsWith("---")) return { kind: "remove" as const, text: line.slice(1) };
    return { kind: "same" as const, text: line };
  });
}

function plainObject(value: unknown): Record<string, unknown> | null {
  if (!value || typeof value !== "object" || Array.isArray(value)) return null;
  return value as Record<string, unknown>;
}

function str(value: unknown, fallback = ""): string {
  return typeof value === "string" ? value : fallback;
}

function num(value: unknown): number | null {
  return typeof value === "number" && Number.isFinite(value) ? value : null;
}

function stringList(value: unknown): string[] {
  if (!Array.isArray(value)) return [];
  return value.filter((item): item is string => typeof item === "string" && item.trim() !== "");
}

function fieldString(value: unknown): string | null {
  if (value == null) return null;
  if (typeof value === "string") return value;
  if (typeof value === "number" || typeof value === "boolean") return String(value);
  try {
    return JSON.stringify(value);
  } catch {
    return null;
  }
}

function readNodeRef(value: unknown): ActionNodeRef | null {
  const raw = plainObject(value);
  if (!raw || typeof raw.node_id !== "string" || !raw.node_id.trim()) return null;
  const project = raw.project_id;
  if (typeof project !== "number" || !Number.isFinite(project)) return null;
  const kind = raw.kind;
  if (kind !== "node" && kind !== "prompt" && kind !== "params" && kind !== "report") return null;
  return { project_id: project, node_id: raw.node_id.trim(), kind };
}

function readFields(value: unknown): FieldDiffRow[] | null {
  const raw = plainObject(value);
  if (!raw) return null;
  const rows: FieldDiffRow[] = [];
  for (const key of Object.keys(raw).sort()) {
    const cell = plainObject(raw[key]);
    if (!cell) continue;
    rows.push({ key, before: fieldString(cell.before), after: fieldString(cell.after) });
  }
  return rows.length ? rows : null;
}

function cardKind(action: string, nodeKind: ActionNodeRef["kind"] | null): ActionCardKind {
  if (action === "create_node") return "create";
  if (action === "edit_node_prompt" || nodeKind === "prompt") return "prompt";
  if (action === "edit_node_params" || nodeKind === "params") return "params";
  if (action === "create_report" || action === "update_report" || nodeKind === "report") return "report";
  if (nodeKind === "node" || action === "revert_node_to_git") return "node";
  return "neutral";
}

const QUIET: ActionMetadata = { name: "", risk: "low", read_only: true, destructive: false, paid: false };

function blankView(id: string, metadata: ActionMetadata, kind: ActionCardKind): ActionResultView {
  return {
    id,
    action: metadata.name,
    changeId: null,
    metadata,
    kind,
    unified: null,
    fields: null,
    hideBefore: false,
    nodeRef: null,
    html: null,
    reportId: null,
    editAction: null,
    marked: false,
    restoreAction: null,
    error: null,
    warnings: [],
    skipped: [],
    partial: false,
    snapshotId: null,
    snapshotUrl: null,
    version: null,
    baseVersion: null,
    conflict: false,
  };
}

function applyEnvelopeExtras(view: ActionResultView, payload: Record<string, unknown>, result: Record<string, unknown> | null): void {
  view.warnings = stringList(payload.warnings);
  view.skipped = stringList(payload.skipped);
  view.partial = payload.partial === true;
  const source = result ?? {};
  view.snapshotId = str(source.snapshot_id).trim() || str(payload.snapshot_id).trim() || null;
  view.snapshotUrl = str(source.snapshot_url).trim() || str(payload.snapshot_url).trim() || null;
  view.version = num(source.version) ?? num(payload.version);
  view.baseVersion = num(source.base_version) ?? num(payload.base_version);
  const statusCode = num(payload.status_code);
  const error = str(payload.error);
  view.conflict =
    payload.status === "failed" &&
    (statusCode === 409 || (view.version != null && view.baseVersion != null && view.version !== view.baseVersion) || /409|конфликт версий/i.test(error));
}

export function readActionEnvelope(payload: Record<string, unknown>, seq: number): ActionResultView | null {
  const status = payload.status;
  if (status !== "ok" && status !== "approval_required" && status !== "failed") return null;
  const result = plainObject(payload.result);
  if (status === "approval_required") {
    const metaRaw = result ? plainObject(result.metadata) : null;
    const name = str(metaRaw?.name).trim() || "unknown";
    const metadata = metadataOf(name, metaRaw);
    const view = blankView(`wait-${seq}`, metadata, "waiting");
    view.action = metadata.name;
    applyEnvelopeExtras(view, payload, result);
    view.changeId = null;
    view.conflict = false;
    return view;
  }
  if (status === "failed") {
    const error = str(payload.error).trim();
    const action = result ? str(result.action).trim() : "";
    if (result && (action || num(result.version) != null || num(result.base_version) != null)) {
      const view = readEdit(result, seq);
      view.error = error || null;
      applyEnvelopeExtras(view, payload, result);
      if (!view.conflict && error) view.kind = view.unified || view.fields ? view.kind : "error";
      return view;
    }
    if (!error) return null;
    const view = blankView(`failed-${seq}`, QUIET, "error");
    view.error = error;
    applyEnvelopeExtras(view, payload, null);
    return view;
  }
  if (!result) return null;
  const reportId = num(result.report_id);
  const html = typeof result.html === "string" ? result.html : null;
  if (reportId != null && html != null && result.edit_action === REPORT_EDIT_ACTION) {
    const view = blankView(`report-${reportId}`, metadataOf("create_report"), "report");
    view.action = "create_report";
    view.html = html;
    view.reportId = reportId;
    view.editAction = REPORT_EDIT_ACTION;
    applyEnvelopeExtras(view, payload, result);
    return view;
  }
  const changeId = str(result.change_id).trim();
  if (result.marked === true && changeId) {
    const restore = str(result.restore_action).trim() || "history.rollback";
    const view = blankView(changeId, metadataOf(restore), "mark");
    view.action = restore;
    view.changeId = changeId;
    view.marked = true;
    view.restoreAction = restore;
    applyEnvelopeExtras(view, payload, result);
    view.conflict = false;
    return view;
  }
  if (!str(result.action).trim() && !plainObject(result.diff)) return null;
  const view = readEdit(result, seq);
  applyEnvelopeExtras(view, payload, result);
  view.conflict = false;
  return view;
}

function readEdit(result: Record<string, unknown>, seq: number): ActionResultView {
  const action = str(result.action).trim();
  const metadata = action ? metadataOf(action) : QUIET;
  const nodeRef = readNodeRef(result.node_ref);
  const diff = plainObject(result.diff);
  const unified = diff && typeof diff.unified === "string" ? unifiedLines(diff.unified) : null;
  const fields = diff ? readFields(diff.fields) : null;
  const fallback =
    !unified && !fields && (typeof result.before === "string" || typeof result.after === "string")
      ? lineDiff(typeof result.before === "string" ? result.before : "", typeof result.after === "string" ? result.after : "")
      : null;
  const changeId = str(result.change_id).trim() || null;
  const view = blankView(changeId || `action-${seq}`, metadata, action ? cardKind(action, nodeRef?.kind ?? null) : "neutral");
  view.action = action || metadata.name;
  view.changeId = changeId;
  view.unified = unified ?? fallback;
  view.fields = fields;
  view.hideBefore = action === "create_node" || result.before == null;
  view.nodeRef = nodeRef && nodeRef.kind !== "report" ? nodeRef : null;
  return view;
}

export function reportEditBody(reportId: number, instruction: string): ReportEditBody {
  return {
    text: instruction.trim(),
    action: { name: REPORT_EDIT_ACTION, report_id: reportId },
  };
}

export function restoreBody(restoreAction: string, changeId: string | null, snapshotId: string | null): RestoreBody {
  const action: RestoreBody["action"] = { name: restoreAction };
  if (changeId) action.change_id = changeId;
  if (snapshotId) action.snapshot_id = snapshotId;
  return { text: "Восстановить", action };
}

export function openStudioNode(ref: ActionNodeRef): void {
  if (typeof window === "undefined" || !ref.node_id) return;
  window.dispatchEvent(new CustomEvent("studio-select-project", { detail: { projectId: ref.project_id } }));
  window.dispatchEvent(new CustomEvent("canvas-select-node", { detail: { nodeKey: ref.node_id } }));
  window.dispatchEvent(new CustomEvent("studio-open-node-prompts", { detail: { nodeKey: ref.node_id } }));
}

function stepPayload(stepId: string, title: string, envelope: Record<string, unknown>): Record<string, unknown> {
  return { step_id: stepId, title, ok: envelope.status !== "failed", ...envelope };
}

export const EXAMPLE_NODE_CREATE = stepPayload("s-create", "новая нода", {
  status: "ok",
  result: {
    action: "create_node",
    change_id: "orch-appr-13-1",
    node_ref: { project_id: 13, node_id: "n_scene_writer", kind: "node" },
    before: null,
    after: { title: "Сцены", group: "script_frames_qc" },
    diff: { fields: { title: { before: null, after: "Сцены" } } },
    open_url: "/api/projects/13",
    reversible: true,
  },
  error: null,
});

export const EXAMPLE_PROMPT_EDIT = stepPayload("s-prompt", "правка промта", {
  status: "ok",
  result: {
    action: "edit_node_prompt",
    change_id: "orch-appr-13-2",
    node_ref: { project_id: 13, node_id: "n_scene_writer", kind: "prompt" },
    before: { prompt_template: "было" },
    after: { prompt_template: "стало" },
    diff: { unified: "--- a/prompt\n+++ b/prompt\n@@\n-было\n+стало\n" },
    open_url: "/api/projects/13",
    reversible: true,
  },
  error: null,
});

export const EXAMPLE_PARAMS_EDIT = stepPayload("s-params", "параметры", {
  status: "ok",
  result: {
    action: "edit_node_params",
    change_id: "orch-appr-13-3",
    node_ref: { project_id: 13, node_id: "n_scene_writer", kind: "params" },
    before: { temperature: 0.2 },
    after: { temperature: 0.4 },
    diff: { fields: { temperature: { before: 0.2, after: 0.4 } } },
    open_url: "/api/projects/13",
    reversible: true,
  },
  error: null,
});

export const EXAMPLE_NODE_EDIT = stepPayload("s-revert", "откат к git", {
  status: "ok",
  result: {
    action: "revert_node_to_git",
    change_id: "orch-appr-13-5",
    node_ref: { project_id: 13, node_id: "n_scene_writer", kind: "node" },
    before: { source: "db", db_version: 4 },
    after: { source: "git", db_version: null },
    diff: { fields: { source: { before: "db", after: "git" } } },
    open_url: "/api/projects/13",
    reversible: true,
  },
  error: null,
});

export const EXAMPLE_REPORT = stepPayload("s-report", "отчёт", {
  status: "ok",
  result: {
    report_id: 7,
    html: "<h1>Сводка</h1><p>Кадры на месте.</p>",
    version: 1,
    edit_action: "update_report",
  },
  error: null,
});

export const EXAMPLE_FAILED = stepPayload("s-failed", "сбой", {
  status: "failed",
  result: null,
  error: "нода n_scene_writer уже есть",
});

export const EXAMPLE_DELETE_OK = stepPayload("s-delete-ok", "пометка промта", {
  status: "ok",
  result: {
    change_id: "orch-appr-13-6",
    marked: true,
    restore_action: "history.rollback",
  },
  error: null,
});

export const EXAMPLE_PAID_APPROVAL = stepPayload("s-paid", "платный вызов", {
  status: "approval_required",
  result: {
    change_id: "orch-appr-13-7",
    metadata: {
      name: "montage.suggest_scene_variants",
      risk: "high",
      read_only: false,
      destructive: false,
      paid: true,
    },
    summary: "Один вызов LLM за вариантами сцены",
    estimate: 0,
  },
  error: null,
});

export const EXAMPLE_UNKNOWN_APPROVAL = stepPayload("s-unknown", "неизвестное", {
  status: "approval_required",
  result: {
    change_id: "orch-appr-13-9",
    metadata: {
      name: "widget.spin",
      risk: "none",
      read_only: true,
      destructive: false,
      paid: false,
    },
    summary: "Неизвестное действие",
    estimate: 0,
  },
  error: null,
});

/** Ядро ws3, не конверт действия. change_id нарочно другой, чем в EXAMPLE_PAID_APPROVAL. */
export const EXAMPLE_CORE_APPROVAL = {
  change_id: "orch-appr-99-3",
  approval_id: "orch-appr-99-3",
  title: "Один вызов LLM за вариантами сцены",
  reason: "risk",
  risk: "high",
  action: "montage.suggest_scene_variants",
};

/**
 * r2.1-предварительный мок. В ws2-r2.0 этих полей нет.
 * Снимок и версии станут обязательными, когда выйдет схема r2.1.
 */
export const EXAMPLE_R21_CONFLICT = stepPayload("s-conflict", "конфликт версий", {
  status: "failed",
  status_code: 409,
  warnings: ["черновик устарел"],
  skipped: ["group"],
  partial: true,
  result: {
    action: "edit_node_prompt",
    change_id: "orch-appr-13-2",
    snapshot_id: "snap-r21-1",
    version: 5,
    base_version: 4,
    node_ref: { project_id: 13, node_id: "n_scene_writer", kind: "prompt" },
    before: { prompt_template: "было" },
    after: { prompt_template: "стало" },
    diff: { unified: "--- a/prompt\n+++ b/prompt\n@@\n-было\n+стало\n" },
    open_url: "/api/projects/13",
    reversible: false,
  },
  error: "конфликт версий",
});

/** r2.1-предварительный: ссылка на снимок у мягкого удаления. */
export const EXAMPLE_R21_DELETE = stepPayload("s-r21-delete", "пометка со снимком", {
  status: "ok",
  result: {
    change_id: "orch-appr-13-6",
    marked: true,
    restore_action: "history.rollback",
    snapshot_id: "snap-r21-6",
    snapshot_url: "/api/projects/13/snapshots/snap-r21-6",
    version: 2,
  },
  error: null,
});
