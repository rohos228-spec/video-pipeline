/** Контракт оператора GPT: роли, стрелки, фактические файлы. */

export type OperatorEdgeKind = "after" | "feed" | "review" | "gate";

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

export interface OperatorResolve {
  nodeKey: string;
  role: OperatorRole;
  outputMode: OperatorOutputMode;
  useSnapshot: boolean;
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
  }[];
  outgoingEdges: {
    id: string;
    source: string;
    target: string;
    kind: OperatorEdgeKind;
  }[];
  errors: string[];
  warnings: string[];
  consistent: boolean;
  canRun: boolean;
  lastResult?: Record<string, unknown>;
  config?: Record<string, unknown>;
}

export const EDGE_KIND_OPTIONS: {
  value: OperatorEdgeKind;
  title: string;
  short: string;
  hint: string;
}[] = [
  {
    value: "after",
    title: "После",
    short: "после",
    hint: "Только порядок: сначала эта нода, потом следующая. Файлы не передаёт.",
  },
  {
    value: "feed",
    title: "Файлы",
    short: "файлы",
    hint: "Передаёт результат/файлы в следующую ноду как вход.",
  },
  {
    value: "review",
    title: "Проверка",
    short: "проверка",
    hint: "Следующая нода проверяет результат этой (снимок/файлы).",
  },
  {
    value: "gate",
    title: "Если ok",
    short: "если ok",
    hint: "Дальше только если вердикт pass. При fail — стоп.",
  },
];

export const ROLE_OPTIONS: { value: OperatorRole; title: string; hint: string }[] = [
  { value: "assist", title: "Участвует", hint: "Обычный шаг пайплайна" },
  { value: "review", title: "Проверяет", hint: "Ок / не ок" },
  { value: "transform", title: "Переделывает", hint: "Вход → другой вид" },
  { value: "extract", title: "Достаёт данные", hint: "Структура из файла" },
  { value: "compare", title: "Сравнивает", hint: "Два входа" },
  { value: "gate", title: "Шлагбаум", hint: "Пустить / стоп" },
];

export const OUTPUT_OPTIONS: { value: OperatorOutputMode; title: string }[] = [
  { value: "text", title: "Текст" },
  { value: "project_file", title: "Файл проекта" },
  { value: "sidecar", title: "Рядом, не ломая" },
];

/** 15 пунктов временного меню */
export const OPERATOR_MENU_ACTIONS = [
  { id: "upload", group: "in", title: "Загрузить файл(ы)" },
  { id: "from_edge", group: "in", title: "Взять с входящей стрелки (feed)" },
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

export function edgeKindLabel(kind?: string | null): string {
  const found = EDGE_KIND_OPTIONS.find((o) => o.value === kind);
  return found?.short ?? "после";
}

export function roleChip(role?: string | null): string {
  const found = ROLE_OPTIONS.find((o) => o.value === role);
  return found?.title ?? "Участвует";
}

export function nextEdgeKind(kind?: string | null): OperatorEdgeKind {
  const order: OperatorEdgeKind[] = ["after", "feed", "review", "gate"];
  const i = order.indexOf((kind as OperatorEdgeKind) || "after");
  return order[(i + 1) % order.length];
}
