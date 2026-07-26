/** Контракт оператора GPT: роли, стрелки, фактические файлы. */

/** Связь: порядок + кандидат на вход. Файлы — настройка приёмника (takeFromEdges). */
export type OperatorEdgeKind = "after" | "gate" | "pass" | "fail" | "feed" | "review";

export type OperatorRole =
  | "assist"
  | "review"
  | "transform"
  | "extract"
  | "compare"
  | "gate";

export type OperatorOutputMode = "text" | "project_file" | "sidecar";

export interface OperatorFileProbe {
  name: string;
  path: string;
  exists: boolean;
  size: number;
  ok: boolean;
  kind: string;
  origin: string;
  fromNode?: string | null;
  preview_url?: string | null;
  error?: string | null;
}

export interface OperatorEdgeSummary {
  id: string;
  source: string;
  target: string;
  kind: OperatorEdgeKind;
  fileCount?: number;
  ok?: boolean;
  errors?: string[];
  takesFiles?: boolean;
}

export interface OperatorBranching {
  enabled: boolean;
  passEdges: OperatorEdgeSummary[];
  failEdges: OperatorEdgeSummary[];
  hasPass: boolean;
  hasFail: boolean;
  verdict?: "pass" | "fail" | null;
}

export interface OperatorResolve {
  nodeKey: string;
  role: OperatorRole;
  outputMode: OperatorOutputMode;
  useSnapshot: boolean;
  takeFromEdges: boolean;
  transport: string;
  label: string;
  files: OperatorFileProbe[];
  okFileCount: number;
  incomingEdges: {
    id: string;
    source: string;
    target: string;
    kind: OperatorEdgeKind;
    fileCount: number;
    ok: boolean;
    errors: string[];
    takesFiles?: boolean;
  }[];
  outgoingEdges: OperatorEdgeSummary[];
  branching?: OperatorBranching;
  errors: string[];
  warnings: string[];
  consistent: boolean;
  canRun: boolean;
  lastResult?: Record<string, unknown>;
  config?: Record<string, unknown>;
}

/** Только семантика стрелки — без отдельной кнопки «Файлы». */
export const EDGE_KIND_OPTIONS: {
  value: "after" | "pass" | "fail";
  title: string;
  short: string;
  hint: string;
}[] = [
  {
    value: "after",
    title: "Связь",
    short: "связь",
    hint: "Порядок шагов. Файлы подтянет приёмник, если у него включён вход от прошлых нод.",
  },
  {
    value: "pass",
    title: "Ок",
    short: "ок",
    hint: "Ветка «всё хорошо»: дальше только при вердикте pass.",
  },
  {
    value: "fail",
    title: "Не ок",
    short: "не ок",
    hint: "Ветка «нужно чинить»: дальше только при вердикте fail.",
  },
];

export const EDGE_KIND_MENU_OPTIONS = EDGE_KIND_OPTIONS;

export const ROLE_OPTIONS: { value: OperatorRole; title: string; hint: string }[] = [
  { value: "assist", title: "Участвует", hint: "Обычный шаг пайплайна" },
  { value: "review", title: "Проверяет", hint: "Ок / не ок — две ветки" },
  { value: "transform", title: "Переделывает", hint: "Вход → другой вид" },
  { value: "extract", title: "Достаёт данные", hint: "Структура из файла" },
  { value: "compare", title: "Сравнивает", hint: "Два входа" },
  { value: "gate", title: "Шлагбаум", hint: "Ок / не ок — две ветки" },
];

export const OUTPUT_OPTIONS: { value: OperatorOutputMode; title: string }[] = [
  { value: "text", title: "Текст" },
  { value: "project_file", title: "Файл проекта" },
  { value: "sidecar", title: "Рядом, не ломая" },
];

export const BRANCHING_ROLES: OperatorRole[] = ["review", "gate", "compare"];

export const ROLE_DEFAULT_LABELS: Record<OperatorRole, string> = {
  assist: "Работа с GPT",
  review: "Ок / не ок",
  transform: "Переделывает",
  extract: "Достаёт данные",
  compare: "Сравнивает",
  gate: "Ок / не ок",
};

export const OPERATOR_MENU_ACTIONS = [
  { id: "upload", group: "in", title: "Загрузить файл(ы)" },
  { id: "from_edge", group: "in", title: "Взять от прошлых нод (стрелки)" },
  { id: "multi", group: "in", title: "Несколько файлов" },
  { id: "snapshot", group: "in", title: "Снимок результата, не live" },
  { id: "role_assist", group: "role", title: "Роль: участвует" },
  { id: "role_review", group: "role", title: "Роль: проверяет" },
  { id: "role_transform", group: "role", title: "Роль: переделывает" },
  { id: "role_extract", group: "role", title: "Роль: достаёт данные" },
  { id: "role_compare", group: "role", title: "Роль: сравнивает" },
  { id: "role_gate", group: "role", title: "Роль: шлагбаум" },
  { id: "out_text", group: "out", title: "Выход: текст" },
  { id: "out_project", group: "out", title: "Выход: файл проекта" },
  { id: "out_sidecar", group: "out", title: "Выход: сохранить рядом" },
  { id: "prompts", group: "always", title: "Промт + короткий текст" },
  { id: "resolve", group: "always", title: "Показать файлы / пересверить" },
] as const;

export function normalizeEdgeKind(raw?: string | null): OperatorEdgeKind {
  const s = String(raw || "after").trim().toLowerCase();
  if (s === "gate") return "gate";
  if (s === "pass" || s === "ok" || s === "если ok") return "pass";
  if (s === "fail" || s === "не ок" || s === "неok" || s === "not_ok") return "fail";
  // legacy feed/review → обычная связь
  if (s === "feed" || s === "review" || s === "after") return "after";
  return "after";
}

export function isPassEdgeKind(kind?: string | null): boolean {
  const k = normalizeEdgeKind(kind);
  return k === "pass" || k === "gate";
}

export function isFailEdgeKind(kind?: string | null): boolean {
  return normalizeEdgeKind(kind) === "fail";
}

export function edgeKindLabel(kind?: string | null): string {
  const k = normalizeEdgeKind(kind);
  if (k === "gate") return "ок";
  const found = EDGE_KIND_OPTIONS.find((o) => o.value === k);
  return found?.short ?? "связь";
}

export function roleChip(role?: string | null): string {
  const found = ROLE_OPTIONS.find((o) => o.value === role);
  return found?.title ?? "Участвует";
}

export function defaultLabelForRole(role?: string | null): string {
  const r = (role || "assist") as OperatorRole;
  return ROLE_DEFAULT_LABELS[r] ?? "Работа с GPT";
}

export function isBranchingRole(role?: string | null): boolean {
  return BRANCHING_ROLES.includes((role || "") as OperatorRole);
}

export function nextEdgeKind(kind?: string | null): "after" | "pass" | "fail" {
  const order: Array<"after" | "pass" | "fail"> = ["after", "pass", "fail"];
  const cur = normalizeEdgeKind(kind);
  const mapped = cur === "gate" ? "pass" : cur === "pass" || cur === "fail" ? cur : "after";
  const i = order.indexOf(mapped);
  return order[(i + 1) % order.length];
}
