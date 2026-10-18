/**
 * Карточки результатов действий по контракту WS2.
 *
 * ws2-r2.3 — PR #378 @ fdd8591499004d8c3c8c6f0b6f788ef708aec370.
 * Схема: app/orchestrator/action_contracts
 * Копия для моков: web/src/lib/orchestratorActionResults.schema.json
 * sha256 d341d5b286af170c43b122fa0c5f57b1c3ce43040305443113622b7c13fd7d10
 *
 * Формат результатов тот же, что у r2.2. version строго больше base_version;
 * пустой snapshot_id и archive_ref недействительны; непустой skipped включает
 * partial. Иначе карточка «Некорректный ответ действия», интерфейс не падает.
 * create_node@new_group — риск high, destructive, нужно подтверждение.
 *
 * Моки — JSON этой версии (#378).
 * Обязательны: version и base_version у правки, before или snapshot_id,
 * у мягкого удаления ссылка на снимок (snapshot_id или archive_ref) и
 * marked=true при ok, warnings у конверта, skipped вместе с partial.
 *
 * Шина (в contracts.py события нет): конверт ActionResult лежит в payload
 * step_finished или message.
 *
 * ApprovalRequired не становится кнопкой одобрения. change_id у него нет.
 * Строка «Ждёт подтверждения» ждёт событие ядра approval_required /
 * approval_decided. ActionMetadata — только подпись риска.
 *
 * Тело POST «Править» и «Восстановить» моделью не задано.
 */

export const ACTION_CONTRACT_VERSION = "ws2-r2.3";
export const ACTION_CONTRACT_REF = "fdd8591499004d8c3c8c6f0b6f788ef708aec370";
export const ACTION_SCHEMA_PATH = "app/orchestrator/action_contracts";
export const ACTION_SCHEMA_SHA256 = "d341d5b286af170c43b122fa0c5f57b1c3ce43040305443113622b7c13fd7d10";
export const INVALID_ACTION_TEXT = "Некорректный ответ действия";
export const REPORT_EDIT_ACTION = "update_report";
const CHANGE_ID = /^orch-appr-[0-9]+-[0-9]+$/;

/** Мягкое удаление возвращает только свой kind. history.rollback эти четыре не восстанавливает. */
const SOFT_DELETE_RESTORE: Record<string, string> = {
  "montage.delete_frame": "frame.restore",
  "montage.delete_child_frame": "frame.restore",
  "group.delete": "group.restore",
  "prompt.delete_master": "prompt.restore_master",
};

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
  op: "set" | "removed";
  before: string | null;
  after: string | null;
}

export type ActionCardKind = "create" | "node" | "prompt" | "params" | "report" | "mark" | "error" | "waiting" | "neutral" | "invalid";

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
  /** Всегда массив, в том числе пустой. */
  warnings: string[];
  /** Пустой, если частичного пропуска нет. partial=true без списка не принимается. */
  skipped: string[];
  partial: boolean;
  snapshotId: string | null;
  archiveRef: string | null;
  snapshotUrl: string | null;
  /** У правки и отчёта — целое ≥ 1. Иначе карточка успеха не строится. */
  version: number | null;
  baseVersion: number | null;
  conflict: boolean;
  /** null — оценки нет, не ноль. 0 — бесплатно. */
  estimate: number | null;
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
    snapshot_id?: string;
    archive_ref?: string;
  };
}

export interface ConflictRetryBody {
  text: string;
  action: {
    name: string;
    base_version?: number;
  };
}

const R2_METADATA: Record<string, ActionMetadata> = {
  read_pipeline_code: { name: "read_pipeline_code", risk: "none", read_only: true, destructive: false, paid: false },
  rag_search: { name: "rag_search", risk: "none", read_only: true, destructive: false, paid: false },
  run_validators: { name: "run_validators", risk: "none", read_only: true, destructive: false, paid: false },
  stop_node: { name: "stop_node", risk: "medium", read_only: false, destructive: false, paid: false },
  resume_node: { name: "resume_node", risk: "high", read_only: false, destructive: true, paid: true },
  create_node: { name: "create_node", risk: "medium", read_only: false, destructive: false, paid: false },
  "create_node@new_group": {
    name: "create_node@new_group",
    risk: "high",
    read_only: false,
    destructive: true,
    paid: false,
  },
  edit_node_prompt: { name: "edit_node_prompt", risk: "medium", read_only: false, destructive: true, paid: false },
  edit_node_params: { name: "edit_node_params", risk: "medium", read_only: false, destructive: true, paid: false },
  create_report: { name: "create_report", risk: "low", read_only: false, destructive: false, paid: false },
  update_report: { name: "update_report", risk: "low", read_only: false, destructive: true, paid: false },
  export_nodes_snapshot: { name: "export_nodes_snapshot", risk: "low", read_only: true, destructive: false, paid: false },
  revert_node_to_git: { name: "revert_node_to_git", risk: "medium", read_only: false, destructive: true, paid: false },
  "frame.restore": { name: "frame.restore", risk: "high", read_only: false, destructive: true, paid: false },
  "group.restore": { name: "group.restore", risk: "high", read_only: false, destructive: true, paid: false },
  "prompt.restore_master": { name: "prompt.restore_master", risk: "high", read_only: false, destructive: true, paid: false },
  "group.delete": { name: "group.delete", risk: "high", read_only: false, destructive: true, paid: false },
  "montage.delete_frame": { name: "montage.delete_frame", risk: "high", read_only: false, destructive: true, paid: false },
  "montage.delete_child_frame": { name: "montage.delete_child_frame", risk: "high", read_only: false, destructive: true, paid: false },
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

/** Платное или деструктивное действие ждёт подтверждения. Имя вне таблицы — оба флага. */
export function needsApproval(metadata: ActionMetadata): boolean {
  return metadata.paid || metadata.destructive;
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

function intGe1(value: unknown): number | null {
  if (typeof value !== "number" || !Number.isInteger(value) || value < 1) return null;
  return value;
}

function validChangeId(value: unknown): string | null {
  const id = str(value).trim();
  return CHANGE_ID.test(id) ? id : null;
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
    const op = cell.op === "removed" ? "removed" : "set";
    rows.push({
      key,
      op,
      before: fieldString(cell.before),
      after: op === "removed" ? null : fieldString(cell.after),
    });
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
    archiveRef: null,
    snapshotUrl: null,
    version: null,
    baseVersion: null,
    conflict: false,
    estimate: null,
  };
}

function diffShape(result: Record<string, unknown>): "unified" | "fields" | null {
  const diff = plainObject(result.diff);
  if (!diff) return null;
  const hasUnified = typeof diff.unified === "string" && diff.unified.trim() !== "";
  const hasFields = plainObject(diff.fields) != null;
  if (hasUnified === hasFields) return null;
  return hasUnified ? "unified" : "fields";
}

function partialPair(result: Record<string, unknown> | null, payload: Record<string, unknown>): { partial: boolean; skipped: string[] } {
  const fromResult = result ? stringList(result.skipped) : [];
  const skipped = fromResult.length ? fromResult : stringList(payload.skipped);
  let partial = result?.partial === true || payload.partial === true;
  if (skipped.length > 0) partial = true;
  return { partial, skipped };
}

function blankRef(value: unknown): boolean {
  return typeof value === "string" && value.trim() === "";
}

/** snapshot_url нет в схеме r2.2. Ссылка только на свой API, без схемы и javascript:. */
function snapshotUrlOf(value: unknown): string | null {
  const url = str(value).trim();
  if (!url.startsWith("/api/orchestrator/")) return null;
  if (url.includes("\\") || url.includes("://")) return null;
  return url;
}

function invalidView(seq: number, message = INVALID_ACTION_TEXT): ActionResultView {
  const view = blankView(`invalid-${seq}`, QUIET, "invalid");
  view.error = message;
  return view;
}

/** Правка ws2-r2.1: версия, база, before или снимок, ровно один diff. */
function editSatisfied(result: Record<string, unknown>): boolean {
  if (!str(result.action).trim() || !validChangeId(result.change_id)) return false;
  if (!readNodeRef(result.node_ref)) return false;
  if (intGe1(result.version) == null || intGe1(result.base_version) == null) return false;
  if (result.before == null && !str(result.snapshot_id).trim()) return false;
  if (!diffShape(result)) return false;
  if (!str(result.open_url).trim() || typeof result.reversible !== "boolean") return false;
  const pair = partialPair(result, {});
  if (pair.partial && pair.skipped.length === 0) return false;
  return true;
}

/** ok-пометка: marked=true, restore своего kind, снимок или архив. */
function softDeleteSatisfied(result: Record<string, unknown>): boolean {
  const action = str(result.action).trim();
  const restore = str(result.restore_action).trim();
  if (SOFT_DELETE_RESTORE[action] !== restore) return false;
  if (result.marked !== true || !validChangeId(result.change_id)) return false;
  if (!str(result.snapshot_id).trim() && !str(result.archive_ref).trim()) return false;
  const pair = partialPair(result, {});
  if (pair.partial && pair.skipped.length === 0) return false;
  return true;
}

/** null и отсутствие поля — оценки нет. 0 остаётся нулём. */
function readEstimate(result: Record<string, unknown> | null): number | null {
  const preview = result ? plainObject(result.preview) : null;
  const source = preview ?? result;
  if (!source || !Object.prototype.hasOwnProperty.call(source, "estimate")) return null;
  const value = source.estimate;
  if (typeof value === "number" && Number.isFinite(value)) return value;
  return null;
}

function applyEnvelopeExtras(view: ActionResultView, payload: Record<string, unknown>, result: Record<string, unknown> | null): void {
  const pair = partialPair(result, payload);
  view.warnings = stringList(payload.warnings);
  view.skipped = pair.skipped;
  view.partial = pair.partial;
  const source = result ?? {};
  view.snapshotId = str(source.snapshot_id).trim() || str(payload.snapshot_id).trim() || null;
  view.archiveRef = str(source.archive_ref).trim() || str(payload.archive_ref).trim() || null;
  view.snapshotUrl = snapshotUrlOf(source.snapshot_url) ?? snapshotUrlOf(payload.snapshot_url);
  view.version = intGe1(source.version) ?? intGe1(payload.version);
  view.baseVersion = intGe1(source.base_version) ?? intGe1(payload.base_version);
  const statusCode = num(payload.status_code);
  const error = str(payload.error);
  view.conflict =
    payload.status === "failed" &&
    (statusCode === 409 || error.trim().toLowerCase() === "conflict" || /409|конфликт версий/i.test(error));
}

export function readActionEnvelope(payload: Record<string, unknown>, seq: number): ActionResultView | null {
  try {
    return readActionEnvelopeInner(payload, seq);
  } catch {
    const status = payload.status;
    if (status !== "ok" && status !== "approval_required" && status !== "failed") return null;
    return invalidView(seq);
  }
}

function readActionEnvelopeInner(payload: Record<string, unknown>, seq: number): ActionResultView | null {
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
    view.estimate = readEstimate(result);
    view.changeId = null;
    view.conflict = false;
    return view;
  }
  if (status === "failed") {
    const error = str(payload.error).trim();
    if (result && editSatisfied(result)) {
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
  if (blankRef(result.snapshot_id) || blankRef(result.archive_ref)) return invalidView(seq);
  const version = intGe1(result.version);
  const baseVersion = intGe1(result.base_version);
  if (version != null && baseVersion != null && version <= baseVersion) return invalidView(seq);
  const reportId = intGe1(result.report_id);
  const html = typeof result.html === "string" ? result.html : null;
  const editRaw = result.edit_action;
  const editAction =
    editRaw == null || (typeof editRaw === "string" && editRaw.trim() === "") ? REPORT_EDIT_ACTION : str(editRaw).trim();
  if (reportId != null && html != null && editAction === REPORT_EDIT_ACTION) {
    const reportVersion = intGe1(result.version);
    if (reportVersion == null || !html.trim()) return null;
    const view = blankView(`report-${reportId}-v${reportVersion}`, metadataOf("create_report"), "report");
    view.action = "create_report";
    view.html = html;
    view.reportId = reportId;
    view.editAction = REPORT_EDIT_ACTION;
    applyEnvelopeExtras(view, payload, result);
    return view;
  }
  const deleteAction = str(result.action).trim();
  if (result.marked === true || Object.prototype.hasOwnProperty.call(SOFT_DELETE_RESTORE, deleteAction)) {
    const hasRef = Boolean(str(result.snapshot_id).trim() || str(result.archive_ref).trim());
    if (Object.prototype.hasOwnProperty.call(SOFT_DELETE_RESTORE, deleteAction) && (result.marked !== true || !hasRef)) {
      return invalidView(seq, "пометка не подтверждена");
    }
    if (!softDeleteSatisfied(result)) return null;
    const changeId = validChangeId(result.change_id);
    const restore = str(result.restore_action).trim();
    const view = blankView(changeId || `mark-${seq}`, metadataOf(deleteAction), "mark");
    view.action = deleteAction;
    view.changeId = changeId;
    view.marked = true;
    view.restoreAction = restore;
    applyEnvelopeExtras(view, payload, result);
    view.conflict = false;
    return view;
  }
  if (!editSatisfied(result)) return null;
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

export function restoreBody(restoreAction: string, snapshotId: string | null, archiveRef: string | null): RestoreBody {
  const action: RestoreBody["action"] = { name: restoreAction };
  if (snapshotId) action.snapshot_id = snapshotId;
  if (archiveRef) action.archive_ref = archiveRef;
  return { text: "Восстановить", action };
}

export function conflictRetryBody(actionName: string, baseVersion: number | null): ConflictRetryBody {
  const action: ConflictRetryBody["action"] = { name: actionName.trim() || "conflict" };
  if (baseVersion != null) action.base_version = baseVersion;
  return { text: "Обновить и повторить", action };
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
    snapshot_id: "snap-13-1",
    version: 2,
    base_version: 1,
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
    version: 2,
    base_version: 1,
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
    version: 3,
    base_version: 2,
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
    version: 5,
    base_version: 4,
    after: { source: "git", db_version: null },
    diff: {
      fields: {
        source: { op: "set", before: "db", after: "git" },
        db_version: { op: "removed", before: 4 },
      },
    },
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
    action: "prompt.delete_master",
    marked: true,
    restore_action: "prompt.restore_master",
    snapshot_id: "snap-13-6",
  },
  error: null,
});

export const EXAMPLE_PAID_APPROVAL = stepPayload("s-paid", "платный вызов", {
  status: "approval_required",
  result: {
    action: "montage.suggest_scene_variants",
    metadata: {
      name: "montage.suggest_scene_variants",
      risk: "high",
      read_only: false,
      destructive: false,
      paid: true,
    },
    reason: "Платный вызов LLM",
    preview: {
      change: "Один вызов LLM за вариантами сцены",
      estimate: null,
    },
  },
  error: null,
});

export const EXAMPLE_UNKNOWN_APPROVAL = stepPayload("s-unknown", "неизвестное", {
  status: "approval_required",
  result: {
    action: "widget.spin",
    metadata: {
      name: "widget.spin",
      risk: "none",
      read_only: true,
      destructive: false,
      paid: false,
    },
    reason: "Имя вне реестра",
    preview: {
      change: "Неизвестное действие",
      estimate: null,
    },
  },
  error: null,
});

export const EXAMPLE_FREE_APPROVAL = stepPayload("s-free", "бесплатное подтверждение", {
  status: "approval_required",
  result: {
    action: "prompt.delete_master",
    metadata: {
      name: "prompt.delete_master",
      risk: "high",
      read_only: false,
      destructive: true,
      paid: false,
    },
    reason: "Удаление общего промта необратимо без снимка",
    preview: {
      change: "Пометить вариант промта удалённым, файл не стирать",
      estimate: 0,
    },
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

export const EXAMPLE_PARTIAL_EDIT = stepPayload("s-partial", "частичная правка", {
  status: "ok",
  warnings: ["shot 2 пропущен"],
  result: {
    action: "edit_node_params",
    change_id: "orch-appr-13-10",
    node_ref: { project_id: 13, node_id: "n_scene_writer", kind: "params" },
    before: { temperature: 0.2 },
    version: 4,
    base_version: 3,
    after: { temperature: 0.4 },
    diff: { fields: { temperature: { before: 0.2, after: 0.4 } } },
    open_url: "/api/projects/13",
    reversible: true,
    partial: true,
    skipped: ["shot-2"],
  },
  error: null,
});

/** Конверт reject_stale_edit: failed, result пустой, error=conflict, HTTP 409. */
export const EXAMPLE_VERSION_CONFLICT = stepPayload("s-409", "конфликт версий", {
  status: "failed",
  result: null,
  error: "conflict",
});

/** Устаревшая правка с телом: карточка остаётся правкой и предлагает повтор. */
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

export const EXAMPLE_GROUP_DELETE = stepPayload("s-group-del", "пометка группы", {
  status: "ok",
  result: {
    change_id: "orch-appr-13-8",
    action: "group.delete",
    marked: true,
    restore_action: "group.restore",
    archive_ref: "old/scenes/13",
  },
  error: null,
});

export const EXAMPLE_FRAME_DELETE = stepPayload("s-frame-del", "пометка кадра", {
  status: "ok",
  result: {
    change_id: "orch-appr-13-9",
    action: "montage.delete_frame",
    marked: true,
    restore_action: "frame.restore",
    snapshot_id: "snap-frame-9",
  },
  error: null,
});

/** Мягкое удаление со снимком. restore_action — kind этой пометки. */
export const EXAMPLE_R21_DELETE = stepPayload("s-r21-delete", "пометка со снимком", {
  status: "ok",
  result: {
    change_id: "orch-appr-13-6",
    action: "prompt.delete_master",
    marked: true,
    restore_action: "prompt.restore_master",
    snapshot_id: "snap-r21-6",
  },
  error: null,
});
