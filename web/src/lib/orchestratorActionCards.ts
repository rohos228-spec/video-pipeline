/**
 * Предварительные типы карточек действий WS2.
 *
 * #344 @ 85ffb76c — только аудит и белый список, форм результатов там нет.
 * Поле paid в реестре появится в R2. Точные pydantic-модели WS2 ещё не прислал.
 * Когда придёт черновой PR, сверять этот файл.
 *
 * Предположение о шине: отдельного типа события под результат нет.
 * Кладём объект в payload `step_finished` или `message`:
 * `{ action, metadata, result: { before, after, node_ref, change_id, html, report_id } }`.
 * Платное или деструктивное действие прячем, пока approval с тем же change_id
 * не станет approved. Путь approve — существующий POST /approvals/{id}/approve.
 */

export const ACTION_AUDIT_REF = "85ffb76c";

export interface ActionMetadata {
  name: string;
  /** Шкала аудита: none | low | medium | high. Чужие строки не режем. */
  risk: string;
  read_only: boolean;
  destructive: boolean;
  /** В #344 поля ещё нет. Для моков задаём явно. */
  paid: boolean;
}

export interface ActionNodeRef {
  node_key: string;
  project_id?: number | null;
}

export interface ActionResultBody {
  before?: unknown;
  after?: unknown;
  node_ref?: unknown;
  change_id?: string | null;
  html?: string | null;
  report_id?: string | null;
}

/** Имена, для которых есть своя карточка. Остальные — платные и деструктивные. */
export const KNOWN_ACTION_NAMES = ["node.create", "node.edit", "prompt.edit", "params.edit", "report.show"] as const;

export type KnownActionName = (typeof KNOWN_ACTION_NAMES)[number];

export type ActionCardKind = "create" | "node" | "prompt" | "params" | "report" | "neutral";

export interface TextDiffLine {
  kind: "same" | "add" | "remove";
  text: string;
}

export interface FieldDiffRow {
  key: string;
  before: string | null;
  after: string | null;
}

export interface ActionResultView {
  id: string;
  action: string;
  changeId: string | null;
  metadata: ActionMetadata;
  kind: ActionCardKind;
  beforeText: string | null;
  afterText: string | null;
  beforeFields: Record<string, string> | null;
  afterFields: Record<string, string> | null;
  nodeRef: ActionNodeRef | null;
  html: string | null;
  reportId: string | null;
}

export const REPORT_EDIT_ACTION = "report.edit";

/** Тело POST message для правки отчёта. Поля action в контракте #347 нет — предварительные. */
export interface ReportEditBody {
  text: string;
  action: {
    name: typeof REPORT_EDIT_ACTION;
    report_id: string;
    change_id?: string;
  };
}

export function isKnownActionName(name: string): name is KnownActionName {
  return (KNOWN_ACTION_NAMES as readonly string[]).includes(name);
}

export function actionCardKind(name: string): ActionCardKind {
  if (name === "node.create") return "create";
  if (name === "node.edit") return "node";
  if (name === "prompt.edit") return "prompt";
  if (name === "params.edit") return "params";
  if (name === "report.show") return "report";
  return "neutral";
}

function bool(value: unknown, fallback: boolean): boolean {
  return typeof value === "boolean" ? value : fallback;
}

function str(value: unknown, fallback = ""): string {
  return typeof value === "string" ? value : fallback;
}

/** Неизвестное имя принудительно paid и destructive. Известное имя берёт флаги из metadata. */
export function resolveActionMetadata(name: string, raw: unknown): ActionMetadata {
  const body = raw && typeof raw === "object" ? (raw as Record<string, unknown>) : {};
  const known = isKnownActionName(name);
  return {
    name,
    risk: str(body.risk, known ? "low" : "high"),
    read_only: known ? bool(body.read_only, false) : false,
    destructive: known ? bool(body.destructive, false) : true,
    paid: known ? bool(body.paid, false) : true,
  };
}

export function actionNeedsApproval(meta: ActionMetadata): boolean {
  return meta.paid || meta.destructive;
}

export function actionResultVisible(
  result: Pick<ActionResultView, "metadata" | "changeId">,
  approvals: { id: string; resolved: "approved" | "rejected" | "closed" | null }[],
): boolean {
  if (!actionNeedsApproval(result.metadata)) return true;
  if (!result.changeId) return false;
  const card = approvals.find((item) => item.id === result.changeId);
  return card?.resolved === "approved";
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

function plainObject(value: unknown): Record<string, unknown> | null {
  if (!value || typeof value !== "object" || Array.isArray(value)) return null;
  return value as Record<string, unknown>;
}

function fieldString(value: unknown): string {
  if (typeof value === "string") return value;
  if (value == null) return "";
  try {
    return JSON.stringify(value);
  } catch {
    return "";
  }
}

export function fieldMap(value: unknown): Record<string, string> | null {
  const raw = plainObject(value);
  if (!raw) return null;
  const out: Record<string, string> = {};
  for (const [key, item] of Object.entries(raw)) out[key] = fieldString(item);
  return out;
}

export function fieldDiff(before: Record<string, string> | null, after: Record<string, string> | null): FieldDiffRow[] {
  const keys = new Set([...Object.keys(before ?? {}), ...Object.keys(after ?? {})]);
  return [...keys].sort().map((key) => ({
    key,
    before: before && Object.prototype.hasOwnProperty.call(before, key) ? before[key] : null,
    after: after && Object.prototype.hasOwnProperty.call(after, key) ? after[key] : null,
  }));
}

function readNodeRef(value: unknown): ActionNodeRef | null {
  const raw = plainObject(value);
  if (!raw || typeof raw.node_key !== "string" || !raw.node_key.trim()) return null;
  const project = raw.project_id;
  const projectId = typeof project === "number" && Number.isFinite(project) ? project : null;
  return { node_key: raw.node_key.trim(), project_id: projectId };
}

function textOf(value: unknown): string | null {
  if (typeof value === "string") return value;
  if (value == null) return null;
  const fields = fieldMap(value);
  if (fields) return JSON.stringify(fields, null, 2);
  try {
    return JSON.stringify(value);
  } catch {
    return null;
  }
}

export function readActionResult(payload: Record<string, unknown>, seq: number): ActionResultView | null {
  const result = plainObject(payload.result);
  if (!result) return null;
  const metaRaw = plainObject(payload.metadata);
  const name = str(payload.action) || str(metaRaw?.name);
  if (!name) return null;
  const metadata = resolveActionMetadata(name, metaRaw);
  const kind = actionCardKind(name);
  const changeId = str(result.change_id) || str(payload.change_id) || null;
  const nodeRef = readNodeRef(result.node_ref);
  const reportId = str(result.report_id) || null;
  const html = typeof result.html === "string" ? result.html : null;
  const beforeFields = kind === "params" || (kind === "node" && plainObject(result.before)) ? fieldMap(result.before) : null;
  const afterFields =
    kind === "create" || kind === "params" || (kind === "node" && (plainObject(result.after) || plainObject(result.before)))
      ? fieldMap(result.after)
      : null;
  const useText = kind === "prompt" || kind === "neutral" || (kind === "node" && !beforeFields && !afterFields);
  const id = changeId || reportId || str(payload.step_id) || `action-${seq}`;
  return {
    id,
    action: name,
    changeId,
    metadata,
    kind,
    beforeText: useText ? textOf(result.before) : null,
    afterText: useText || (kind === "create" && !afterFields) ? textOf(result.after) : null,
    beforeFields: kind === "create" ? null : beforeFields,
    afterFields,
    nodeRef,
    html: kind === "report" ? html : null,
    reportId: kind === "report" ? reportId : null,
  };
}

export function reportEditBody(reportId: string, instruction: string, changeId?: string | null): ReportEditBody {
  const text = instruction.trim();
  const action: ReportEditBody["action"] = {
    name: REPORT_EDIT_ACTION,
    report_id: reportId,
  };
  if (changeId && changeId.trim()) action.change_id = changeId.trim();
  return { text, action };
}

/** Тот же способ, что панель оркестратора на канвасе. Страница /orchestrator слушателей не имеет. */
export function openStudioNode(ref: ActionNodeRef): void {
  if (typeof window === "undefined" || !ref.node_key) return;
  if (ref.project_id != null) {
    window.dispatchEvent(new CustomEvent("studio-select-project", { detail: { projectId: ref.project_id } }));
  }
  window.dispatchEvent(new CustomEvent("canvas-select-node", { detail: { nodeKey: ref.node_key } }));
  window.dispatchEvent(new CustomEvent("studio-open-node-prompts", { detail: { nodeKey: ref.node_key } }));
}

function meta(name: string, patch: Partial<ActionMetadata> = {}): ActionMetadata {
  return {
    name,
    risk: "low",
    read_only: false,
    destructive: false,
    paid: false,
    ...patch,
  };
}

export const EXAMPLE_PROMPT_EDIT = {
  step_id: "s-prompt",
  title: "правка промта",
  action: "prompt.edit",
  ok: true,
  metadata: meta("prompt.edit"),
  result: {
    before: "кадр у окна\nгерой молчит",
    after: "кадр у окна\nгерой говорит",
    node_ref: { node_key: "n_excel_gpt", project_id: 13 },
    change_id: "chg-prompt-1",
  },
};

export const EXAMPLE_NODE_CREATE = {
  step_id: "s-create",
  title: "новая нода",
  action: "node.create",
  ok: true,
  metadata: meta("node.create"),
  result: {
    after: { title: "Проверка", enabled: true },
    node_ref: { node_key: "n_check", project_id: 13 },
    change_id: "chg-create-1",
  },
};

export const EXAMPLE_NODE_EDIT = {
  step_id: "s-node",
  title: "правка ноды",
  action: "node.edit",
  ok: true,
  metadata: meta("node.edit"),
  result: {
    before: { title: "Старая" },
    after: { title: "Новая" },
    node_ref: { node_key: "n_excel_gpt", project_id: 13 },
    change_id: "chg-node-1",
  },
};

export const EXAMPLE_PARAMS_EDIT = {
  step_id: "s-params",
  title: "параметры",
  action: "params.edit",
  ok: true,
  metadata: meta("params.edit"),
  result: {
    before: { temperature: "0.2", shots: "3" },
    after: { temperature: "0.4", shots: "3" },
    node_ref: { node_key: "n_excel_gpt", project_id: 13 },
    change_id: "chg-params-1",
  },
};

export const EXAMPLE_REPORT = {
  step_id: "s-report",
  title: "отчёт",
  action: "report.show",
  ok: true,
  metadata: meta("report.show", { risk: "none", read_only: true }),
  result: {
    html: "<p>Герой молчит.</p><script>window.__xss=1</script>",
    report_id: "rpt-1",
    change_id: "chg-report-1",
    node_ref: { node_key: "n_excel_gpt", project_id: 13 },
  },
};

export const EXAMPLE_PAID_PROMPT = {
  step_id: "s-paid",
  title: "платная правка промта",
  action: "prompt.edit",
  ok: true,
  metadata: meta("prompt.edit", { risk: "high", paid: true, destructive: true }),
  result: {
    before: "было",
    after: "стало",
    node_ref: { node_key: "n_excel_gpt", project_id: 13 },
    change_id: "chg-paid-1",
  },
};

export const EXAMPLE_PAID_APPROVAL = {
  change_id: "chg-paid-1",
  approval_id: "chg-paid-1",
  title: "Платная правка промта",
  reason: "risk",
  risk: "high",
  action: "prompt.edit",
};

export const EXAMPLE_UNKNOWN = {
  step_id: "s-unknown",
  title: "неизвестное",
  action: "widget.spin",
  ok: true,
  metadata: meta("widget.spin", { risk: "none", read_only: true, paid: false, destructive: false }),
  result: {
    before: "старое",
    after: "новое",
    change_id: "chg-unknown-1",
    node_ref: { node_key: "n_excel_gpt", project_id: 13 },
  },
};

export const EXAMPLE_UNKNOWN_APPROVAL = {
  change_id: "chg-unknown-1",
  approval_id: "chg-unknown-1",
  title: "Неизвестное действие",
  reason: "risk",
  risk: "high",
  action: "widget.spin",
};
