import { ApiError, http } from "./api";
import {
  errorCode, previewPlan, serverTextRu,
  type PlanPreview, type PlanPreviewResponse,
} from "./action-layer";

const BASE = "/api/change-control";

export type Tier = "T0" | "T1" | "T2" | "T3";
export type ProposalStatus =
  | "proposed" | "approved" | "applied" | "rolled_back" | "failed" | "rejected" | "developer_review";
export type ProposalKind =
  | "suggestion" | "prompt_variant" | "node_options" | "step_params" | "prompt_text" | "developer_review";
export type VersionKind = "baseline" | "apply" | "rollback" | "auto_rollback";

export interface Proposal {
  id: string;
  tier: Tier;
  project_id: number | null;
  node_key: string;
  kind: ProposalKind;
  target: string;
  base_version: number;
  base_digest: string;
  candidate: Record<string, unknown>;
  diff: string;
  digest: string;
  rationale: string;
  proposer: string;
  status: ProposalStatus;
  error: string | null;
  approved_by: string | null;
  approved_at: string | null;
  applied_version: number | null;
  rejected_by?: string | null;
  reject_reason?: string | null;
  rejected_at?: string | null;
  stale?: boolean;
  shadow: { name: string; ok: boolean; note: string }[] | null;
  applicable: boolean;
  can_apply: boolean;
  created_at: string | null;
  updated_at: string | null;
  applied_at: string | null;
  current_version?: number;
  deduplicated?: boolean;
}

export interface ConfigVersion {
  id: number;
  target: string;
  version: number;
  parent_version: number | null;
  kind: VersionKind;
  project_id: number | null;
  node_key: string;
  content: Record<string, unknown>;
  digest: string;
  source_proposal_id: string | null;
  applied_by: string;
  applied_at: string | null;
}

export interface AllowList {
  tiers: Record<Tier, { title_ru: string; applicable: boolean; kinds: string[] }>;
  node_types: Record<string, Record<string, boolean | string[]>>;
  step_param_limits: Record<string, Record<string, { type: string; min: number; max: number }>>;
  max_step_param_factor: number;
  prompt_limits: { min_chars: number; max_chars: number; min_ratio: number; max_ratio: number };
}

export interface ProposalIn {
  tier: Tier;
  kind: ProposalKind;
  rationale: string;
  candidate: Record<string, unknown>;
  project_id?: number;
  node_key?: string;
}


export const getAllowList = () => http<AllowList>(`${BASE}/allow-list`);

export const listProposals = (projectId: number, limit = 200) =>
  http<{ count: number; items: Proposal[] }>(`${BASE}/proposals?project_id=${projectId}&limit=${limit}`);

export const getProposal = (id: string) =>
  http<Proposal>(`${BASE}/proposals/${encodeURIComponent(id)}`);

export const createProposal = (body: ProposalIn) =>
  http<Proposal>(`${BASE}/proposals`, { method: "POST", body: JSON.stringify(body) });

export const listVersions = (target: string, limit = 100) =>
  http<{ target: string; current_version: number; items: ConfigVersion[] }>(
    `${BASE}/versions?target=${encodeURIComponent(target)}&limit=${limit}`);

export const REJECT_REASON_MIN = 3;
export const REJECT_REASON_MAX = 500;

export const rejectProposal = (id: string, reason: string) =>
  http<Proposal>(`${BASE}/proposals/${encodeURIComponent(id)}/reject`, {
    method: "POST", body: JSON.stringify({ reason: reason.trim() }),
  });

export const canReject = (p: Pick<Proposal, "status">) => p.status === "proposed" || p.status === "developer_review";

export type PromptBaseSource = "slot" | "project_override" | "global_active" | "default" | "missing";
export interface PromptBase {
  text: string;
  source: PromptBaseSource;
  variant: string | null;
  text_chars: number;
  truncated: boolean;
  base_text_sha256: string;
  missing_reason?: string | null;
  project_id: number;
  node_key: string;
  node_type: string;
  step: string;
}

export const getPromptBase = (projectId: number, nodeKey: string) =>
  http<PromptBase>(`${BASE}/prompt-base?project_id=${projectId}&node_key=${encodeURIComponent(nodeKey)}`);

export const PROMPT_SOURCE_RU: Record<string, string> = {
  slot: "выбран на самой ноде (слот ноды)",
  project_override: "явный выбор проекта (prompt_overrides)",
  global_active: "общий активный вариант шага",
  default: "вариант по умолчанию",
  missing: "файл не найден",
};
export const promptSourceRu = (s: string) => PROMPT_SOURCE_RU[s] ?? `неизвестный источник (${s})`;


export const previewApply = (p: Pick<Proposal, "id" | "digest">): Promise<PlanPreviewResponse> =>
  previewPlan(
    [{ action_id: "change.apply", params: { proposal_id: p.id, expected_digest: p.digest } }],
    `Применить предложение ${p.id.slice(0, 8)}`,
  );

export const previewRollback = (target: string, expectedVersion: number, reason = ""): Promise<PlanPreviewResponse> =>
  previewPlan(
    [{
      action_id: "change.rollback",
      params: { target, expected_version: expectedVersion, ...(reason.trim() ? { reason: reason.trim().slice(0, 500) } : {}) },
    }],
    `Откатить ${target}`,
  );

export function rollbackState(p: Proposal): { ok: boolean; reason: string } | null {
  if (p.status !== "applied") return { ok: false, reason: "" };
  if (p.current_version == null || p.applied_version == null) return null;
  if (p.current_version !== p.applied_version) {
    return {
      ok: false,
      reason: `После этого применения цель менялась (сейчас версия ${p.current_version}, применена была ${p.applied_version}): откат отсюда недоступен.`,
    };
  }
  if (p.applied_version < 2) return { ok: false, reason: "Версия применения меньше 2: откатывать не к чему." };
  return { ok: true, reason: "" };
}


export const TIER_LABEL_RU: Record<Tier, string> = {
  T0: "подсказка",
  T1: "параметр",
  T2: "текст промпта",
  T3: "на ревью разработчику",
};

export const TIER_HINT_RU: Record<Tier, string> = {
  T0: "Совет оператору. Не применяется: у него нет исполнителя.",
  T1: "Параметр из явного списка или выбор готового варианта промпта. Применяется после вашего подтверждения.",
  T2: "Новый текст промпта этой ноды (создаётся новый вариант, общие файлы не трогаются). Применяется после вашего подтверждения.",
  T3: "Код, логика, общие файлы: только запись для разработчика. Автоматически не применяется никогда.",
};

export const KIND_LABEL_RU: Record<string, string> = {
  suggestion: "совет",
  prompt_variant: "выбор готового варианта промпта",
  step_params: "параметры шага",
  node_options: "настройки ноды",
  prompt_text: "новый текст промпта",
  developer_review: "запись для разработчика",
};
export const kindLabel = (k: string) => KIND_LABEL_RU[k] ?? `неизвестный вид (${k})`;

export const STATUS_LABEL_RU: Record<ProposalStatus, string> = {
  proposed: "предложено",
  approved: "подтверждено, идёт применение",
  applied: "применено",
  rolled_back: "откачено",
  failed: "не применено (ошибка)",
  rejected: "отклонено оператором",
  developer_review: "ждёт разработчика",
};
export const statusLabel = (s: string) => STATUS_LABEL_RU[s as ProposalStatus] ?? `неизвестное состояние (${s})`;

export const VERSION_KIND_RU: Record<VersionKind, string> = {
  baseline: "исходное значение",
  apply: "применение",
  rollback: "откат",
  auto_rollback: "автоматический откат после неудачной проверки",
};
export const versionKindLabel = (k: string) => VERSION_KIND_RU[k as VersionKind] ?? `неизвестный вид (${k})`;

export const TARGET_PART_RU: Record<string, string> = {
  prompt: "промпт",
  options: "настройки ноды",
  step_params: "параметры шага",
};

export function describeTarget(target: string): string {
  const m = target.match(/^project:(\d+):([^:]+):(prompt|options|step_params)$/);
  if (!m) return target;
  return `нода ${m[2]} · ${TARGET_PART_RU[m[3]]}`;
}

export const shortDigest = (d: string | null | undefined, n = 12) => (d ? d.slice(0, n) : "—");

export type ProposerKind = "planner" | "operator" | "other";
export function describeProposer(proposer: string): { kind: ProposerKind; label: string } {
  if (proposer.startsWith("planner:")) return { kind: "planner", label: "планировщик" };
  if (proposer.startsWith("web_operator:") || proposer === "local_web_operator") return { kind: "operator", label: "оператор" };
  if (proposer.startsWith("model:") || proposer.startsWith("agent:") || proposer === "system") {
    return { kind: "other", label: proposer.split(":")[0] === "model" ? "модель" : proposer.split(":")[0] === "agent" ? "агент" : "система" };
  }
  return { kind: "other", label: proposer || "неизвестно" };
}

export function describeHuman(who: string | null | undefined): string {
  if (!who) return "неизвестно";
  if (who === "local_web_operator") return "локальный оператор";
  if (who === "auto_rollback") return "система (автооткат)";
  if (who === "baseline") return "система (снимок исходного значения)";
  if (who.startsWith("web_operator:")) return `оператор ${who.slice("web_operator:".length, "web_operator:".length + 6)}`;
  if (who.startsWith("planner:")) return `планировщик (${who.slice(8, 14)})`;
  return who;
}

export function formatWhen(iso: string | null | undefined): string {
  if (!iso) return "—";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  return d.toLocaleString("ru-RU", { day: "2-digit", month: "2-digit", year: "numeric", hour: "2-digit", minute: "2-digit", second: "2-digit" });
}


export type DiffLineKind = "add" | "del" | "ctx" | "hunk" | "meta";
export interface DiffLine { kind: DiffLineKind; text: string }

export function parseUnifiedDiff(diff: string): { lines: DiffLine[]; added: number; removed: number; truncated: boolean } {
  const out: DiffLine[] = [];
  let added = 0;
  let removed = 0;
  let truncated = false;
  let headerDone = false;
  for (const raw of diff.split("\n")) {
    if (/^\.\.\. \[diff truncated\]$/.test(raw)) { truncated = true; continue; }
    if (!headerDone && (raw.startsWith("--- ") || raw.startsWith("+++ "))) { out.push({ kind: "meta", text: raw }); continue; }
    if (raw.startsWith("@@")) { headerDone = true; out.push({ kind: "hunk", text: raw }); continue; }
    headerDone = true;
    if (raw.startsWith("+")) { added += 1; out.push({ kind: "add", text: raw.slice(1) }); }
    else if (raw.startsWith("-")) { removed += 1; out.push({ kind: "del", text: raw.slice(1) }); }
    else out.push({ kind: "ctx", text: raw.startsWith(" ") ? raw.slice(1) : raw });
  }
  return { lines: out, added, removed, truncated };
}

export interface ParamRow { name: string; before: string; after: string }

const PARAM_NAME_RU: Record<string, string> = {
  duration_seconds: "Длительность, сек.",
  cell_min_chars: "Минимум символов в ячейке",
  cell_max_chars: "Максимум символов в ячейке",
  cell_avg_min: "Среднее по ячейкам: минимум",
  cell_avg_max: "Среднее по ячейкам: максимум",
  outputMode: "Режим вывода",
  emitKinds: "Что выдаёт нода",
  checkFix: "Правка по результатам проверки",
  useSnapshot: "Использовать снимок",
  variant: "Вариант промпта",
};
const OUTPUT_MODE_RU: Record<string, string> = { text: "текст", project_file: "файл проекта", sidecar: "боковой файл" };
const EMIT_KIND_RU: Record<string, string> = { result: "результат", reply_txt: "ответ (txt)", analysis: "анализ", inputs: "входные данные" };

export function paramNameRu(name: string): string {
  const dot = name.indexOf(".");
  const field = dot >= 0 ? name.slice(dot + 1) : name;
  const base = PARAM_NAME_RU[field] ?? field;
  return dot >= 0 ? `${base} (шаг ${name.slice(0, dot)})` : base;
}

export function paramValueRu(raw: string, field = ""): string {
  const t = raw.trim();
  if (t === "True") return "да";
  if (t === "False") return "нет";
  if (t === "None" || t === "") return "не задано";
  const list = t.match(/^\[(.*)\]$/);
  if (list) {
    const items = list[1].split(",").map((x) => x.trim().replace(/^'|'$/g, "")).filter(Boolean);
    return items.map((x) => EMIT_KIND_RU[x] ?? x).join(", ") || "пусто";
  }
  const q = t.match(/^'(.*)'$/);
  if (q) return field.endsWith("outputMode") ? (OUTPUT_MODE_RU[q[1]] ?? q[1]) : q[1];
  return t;
}

export function parseParamDiff(diff: string): ParamRow[] | null {
  const rows: ParamRow[] = [];
  for (const line of diff.split("\n")) {
    if (!line.trim()) continue;
    const m = line.match(/^(.+?): (.*) -> (.*)$/);
    if (!m) return null;
    const fieldKey = m[1].includes(".") ? m[1].slice(m[1].indexOf(".") + 1) : m[1];
    rows.push({ name: paramNameRu(m[1]), before: paramValueRu(m[2], fieldKey), after: paramValueRu(m[3], fieldKey) });
  }
  return rows.length ? rows : null;
}

export function describeVersionContent(c: Record<string, unknown>): string {
  const parts: string[] = [];
  if (typeof c.variant === "string") parts.push(`вариант «${c.variant}»`);
  if (typeof c.text_chars === "number") parts.push(`текст ${c.text_chars} симв.`);
  if (c.values && typeof c.values === "object") {
    for (const [k, v] of Object.entries(c.values as Record<string, unknown>)) {
      const shown = v == null ? "не задано" : typeof v === "boolean" ? (v ? "да" : "нет")
        : Array.isArray(v) ? v.map((x) => EMIT_KIND_RU[String(x)] ?? String(x)).join(", ")
        : typeof v === "string" ? (OUTPUT_MODE_RU[v] ?? v) : String(v);
      parts.push(`${PARAM_NAME_RU[k] ?? k}: ${shown}`);
    }
  }
  return parts.join(" · ") || "содержимое не показано";
}


export const CC_ERROR_RU: Record<string, string> = {
  operator_required: "Применять, откатывать и отклонять предложения может только оператор-человек.",
  status_not_rejectable: "Это предложение уже нельзя отклонить: отклонять можно только предложенные и ожидающие разработчика.",
  proposal_not_found: "Предложение не найдено.",
  digest_mismatch: "Предложение изменилось после того, как вы его открыли (не совпал контрольный хэш). Обновите список и откройте его заново.",
  proposal_tampered: "Сохранённое предложение не совпадает со своим хэшем. Применять его нельзя: нужен разбор разработчиком.",
  tier_not_applicable: "Предложения этого уровня не применяются автоматически никогда (только T1 и T2).",
  status_not_applicable: "Предложение уже не в том состоянии, когда его можно применить.",
  project_busy: "В проекте сейчас выполняется шаг или другой план. Дождитесь окончания и повторите.",
  stale_base: "Предложение устарело: после его создания значение или версия цели изменились. Составьте предложение заново на актуальном значении.",
  lint_failed: "Проверка не пройдена.",
  shadow_failed: "Пробный прогон перед применением не пройден: ничего не записано.",
  verification_failed: "После записи значение не совпало с ожидаемым: прежнее значение возвращено автоматически, предложение помечено как неудавшееся.",
  rollback_failed: "Не удалось вернуть прежнее значение. Нужен ручной разбор: проверьте настройки ноды.",
  nothing_to_roll_back: "Откатывать нечего: цель на исходной версии.",
  bad_target: "Некорректная цель изменения.",
  node_not_found: "Нода не найдена на канвасе проекта.",
  project_not_found: "Проект не найден.",
  bad_tier: "Неизвестный уровень предложения.",
  kind_tier_mismatch: "Этот вид изменения не относится к выбранному уровню.",
  bad_rationale: "Обоснование обязательно (до 1000 символов).",
  proposer_required: "Не удалось определить автора предложения.",
  bad_candidate: "Содержимое предложения заполнено неверно.",
  project_required: "Для T1 и T2 нужны проект и нода.",
  bad_node_key: "Идентификатор ноды содержит недопустимые символы.",
  node_type_not_allowed: "Для этого типа ноды изменения не разрешены (разрешены: Каркас, Площадка, Шоты, Работа с GPT).",
  kind_not_allowed: "Этот вид изменения не разрешён на ноде такого типа.",
  field_not_allowed: "Это поле нельзя менять (его нет в списке разрешённых).",
  variant_not_found: "Такого готового варианта промпта нет у этого шага.",
  too_many_open: "У проекта слишком много открытых предложений (максимум 50). Примените или закройте часть.",
};

export function lintErrorRu(msg: string): string | null {
  let m: RegExpMatchArray | null;
  if ((m = msg.match(/^text shorter than (\d+) chars$/))) return `Текст короче ${m[1]} символов.`;
  if ((m = msg.match(/^text longer than (\d+) chars$/))) return `Текст длиннее ${m[1]} символов.`;
  if (msg === "text contains control characters") return "В тексте есть управляющие символы.";
  if (msg === "text equals the current prompt") return "Текст совпадает с текущим промптом: менять нечего.";
  if ((m = msg.match(/^text is ([\d.]+) of the current size \(min ([\d.]+)\)$/))) return `Текст слишком короткий: ${m[1]} от текущего размера, нужно не меньше ${m[2]}.`;
  if ((m = msg.match(/^text is ([\d.]+) of the current size \(max ([\d.]+)\)$/))) return `Текст слишком длинный: ${m[1]} от текущего размера, нужно не больше ${m[2]}.`;
  if ((m = msg.match(/^required placeholders lost: \[(.*)\]$/))) return `Потеряны обязательные подстановки: ${m[1].replace(/'/g, "")}.`;
  if ((m = msg.match(/^unknown placeholders added: \[(.*)\]$/))) return `Добавлены неизвестные подстановки: ${m[1].replace(/'/g, "")}.`;
  if ((m = msg.match(/^contract markers lost: \[(.*)\]$/))) return `Потеряны обязательные маркеры формата ответа: ${m[1].replace(/'/g, "")}.`;
  if (msg === "text contains credential-like content" || msg === "credential-like content" || msg === "diff contains credential-like content")
    return "Содержимое похоже на секрет (ключ, пароль, токен): такое не принимается.";
  if (msg === "current prompt text differs from the one the proposal was based on") return "Текущий текст промпта изменился после создания предложения.";
  if (msg === "variant name does not match the text") return "Имя варианта не соответствует тексту.";
  if (msg === "a different prompt file already has this variant name") return "Файл с таким именем варианта уже существует с другим содержимым.";
  if (msg === "variant name is not valid") return "Недопустимое имя варианта.";
  if (msg === "variant file does not exist") return "Файл варианта не существует.";
  if (msg === "variant file changed after the proposal was made") return "Файл варианта изменился после создания предложения.";
  if (msg === "variant is already selected") return "Этот вариант уже выбран на ноде.";
  if (/^project composes prompts from blocks/.test(msg)) return "Проект собирает промпт из блоков: смена варианта или текста не повлияла бы на запрос.";
  if ((m = msg.match(/^project override prompt_overrides\[(.+?)\]=(.+?) has precedence/))) return `В проекте явно выбран вариант ${m[2].replace(/'/g, "«").replace(/«(?=[^«]*$)/, "»")} для шага ${m[1].replace(/'/g, "")}: он важнее слота ноды, изменение не вступило бы в силу.`;
  if (msg === "values are already set") return "Эти значения уже установлены.";
  if ((m = msg.match(/^(\w+) would be coerced to (.+) by the node config$/))) return `Настройка ${paramNameRu(m[1])} будет подменена нодой на ${paramValueRu(m[2], m[1])}: изменение не вступит в силу.`;
  if ((m = msg.match(/^outputMode must be one of \[(.*)\]$/))) return `Режим вывода должен быть одним из: ${m[1].replace(/'/g, "")}.`;
  if ((m = msg.match(/^emitKinds must be a unique non-empty subset of \[(.*)\]$/))) return `Нужен непустой набор без повторов из: ${m[1].replace(/'/g, "")}.`;
  if ((m = msg.match(/^(\w+) must be a boolean$/))) return `${paramNameRu(m[1])}: нужно значение да или нет.`;
  if ((m = msg.match(/^(\w+) must be a number$/))) return `${paramNameRu(m[1])}: нужно число.`;
  if ((m = msg.match(/^(\w+) must be an integer$/))) return `${paramNameRu(m[1])}: нужно целое число.`;
  if ((m = msg.match(/^(\w+)=(\S+) is outside \[(\S+), (\S+)\]$/))) return `${paramNameRu(m[1])}: ${m[2]} вне допустимого диапазона от ${m[3]} до ${m[4]}.`;
  if ((m = msg.match(/^(\w+) has no current value: nothing to roll back to$/))) return `${paramNameRu(m[1])}: сейчас значение не задано, откатывать было бы не к чему.`;
  if ((m = msg.match(/^(\w+) already equals (\S+)$/))) return `${paramNameRu(m[1])} уже равно ${m[2]}.`;
  if ((m = msg.match(/^(\w+): change from (\S+) to (\S+) exceeds x([\d.]+)$/))) return `${paramNameRu(m[1])}: за одно применение значение можно менять не более чем в ${m[4].replace(/\.0$/, "")} раза (было ${m[2]}, нужно ${m[3]}).`;
  if ((m = msg.match(/^(\w+) must not exceed (\w+)$/))) return `${paramNameRu(m[1])} не должно быть больше, чем «${paramNameRu(m[2])}».`;
  if ((m = msg.match(/^current version is (\d+), proposal is based on (\d+)$/))) return `Сейчас у цели версия ${m[1]}, а предложение составлено на версии ${m[2]}.`;
  if ((m = msg.match(/^current version is (\d+), expected (\d+)$/))) return `Сейчас у цели версия ${m[1]}, а ожидалась ${m[2]}.`;
  if (msg === "the live value changed after the proposal was made") return "Значение в проекте изменилось после создания предложения.";
  if (msg === "the live value differs from the current version") return "Значение в проекте отличается от текущей версии (его меняли вручную).";
  if (msg === "base version changed") return "Версия цели изменилась во время применения.";
  if (msg === "step does not match the node") return "Шаг не соответствует ноде.";
  if ((m = msg.match(/^(\w+) is not an allowed parameter$/))) return `Параметр ${m[1]} менять нельзя.`;
  return null;
}

const CODES_BY_LENGTH = Object.keys(CC_ERROR_RU).sort((a, b) => b.length - a.length);

export function findChangeCode(text: string | null | undefined): string | null {
  if (!text) return null;
  let best: { code: string; at: number } | null = null;
  for (const code of CODES_BY_LENGTH) {
    const m = new RegExp(`(^|[^a-z_])${code}($|[^a-z_])`).exec(text);
    if (m && (best === null || m.index < best.at)) best = { code, at: m.index };
  }
  return best?.code ?? null;
}

export function changeErrorRu(text: string | null | undefined, errors: string[] = []): string {
  if (!text && errors.length === 0) return "";
  const code = findChangeCode(text) ?? (errors.length ? "lint_failed" : null);
  const reasons = (errors.length ? errors : splitLintText(text ?? "", code)).map((e) => lintErrorRu(e) ?? e);
  if (!code) return serverTextRu(text);
  const head = CC_ERROR_RU[code];
  const detail = code === "lint_failed" || code === "stale_base" || code === "shadow_failed" || code === "verification_failed"
    ? reasons.filter((r) => r && r !== code).join(" ") : "";
  return `${head}${detail ? ` ${detail}` : ""} (код: ${code})`;
}

function splitLintText(text: string, code: string | null): string[] {
  if (!code) return [];
  const idx = text.indexOf(code);
  const rest = text.slice(idx + code.length).replace(/^\s*:\s*/, "").trim();
  if (!rest) return [];
  return rest.split("; ").map((s) => s.trim()).filter(Boolean);
}

export function describeChangeError(e: unknown): string {
  if (e instanceof ApiError) {
    const root = e.detail as unknown;
    const d = root && typeof root === "object" && "detail" in (root as object) ? (root as { detail: unknown }).detail : root;
    if (d && typeof d === "object" && !Array.isArray(d)) {
      const o = d as { code?: unknown; message?: unknown; errors?: unknown };
      if (typeof o.code === "string") {
        const errs = Array.isArray(o.errors) ? o.errors.map(String) : [];
        if (o.code === "plan_invalid") return describePlanInvalid(root);
        if (o.code in CC_ERROR_RU) return changeErrorRu(`${o.code}: ${typeof o.message === "string" ? o.message : ""}`, errs);
      }
    }
    if (e.status === 422 && Array.isArray(d)) {
      return `Форма заполнена неверно: ${d.map((x) => (x && typeof x === "object" && "msg" in x ? `${(x as { loc?: unknown[] }).loc?.filter((p) => p !== "body").join(".")}: ${(x as { msg: string }).msg}` : String(x))).join("; ")}`;
    }
    const code = errorCode(e);
    if (code === "plan_invalid") return describePlanInvalid(root);
    if (code === "kill_switch_active") return "Включён аварийный стоп оркестратора: планы не составляются и не выполняются.";
    if (code === "project_busy") return CC_ERROR_RU.project_busy;
    if (code === "approval_required") return "Нужно подтверждение оператора перед запуском.";
    if (code === "plan_changed" || code === "stale_context") return "План изменился после предпросмотра. Составьте его заново.";
    if (code === "already_running") return "План уже выполняется.";
    if (e.status === 403 && !code) return "Нет доступа: изменения доступны только локальному оператору.";
    return e.message || `Ошибка сервера (${e.status}).`;
  }
  return e instanceof Error ? e.message : "Неизвестная ошибка.";
}

export function describePlanInvalid(root: unknown): string {
  const inner = root && typeof root === "object" && "detail" in (root as object) ? (root as { detail: unknown }).detail : root;
  const preview = (inner as { preview?: PlanPreview } | null)?.preview;
  if (!preview) return "План не прошёл проверку: изменение сейчас применить нельзя.";
  return describePreviewProblems(preview) || "План не прошёл проверку: изменение сейчас применить нельзя.";
}

export function describePreviewProblems(preview: PlanPreview): string {
  const parts: string[] = [];
  for (const s of preview.steps) {
    for (const f of s.precondition_failures) parts.push(changeErrorRu(f.reason));
    for (const er of s.errors) parts.push(changeErrorRu(er));
  }
  for (const er of preview.errors.filter((x) => !/^step \d+:/.test(x))) parts.push(changeErrorRu(er));
  return parts.filter(Boolean).join(" ");
}

export function describeProposalFailure(error: string | null | undefined): string {
  return changeErrorRu(error ?? "");
}


export function nodeKindOptions(allow: AllowList | null, nodeType: string | null): string[] {
  if (!allow || !nodeType) return [];
  return Object.keys(allow.node_types[nodeType] ?? {});
}

export function allowTypeOf(nodeType: string, allow: AllowList | null): string | null {
  const t = nodeType.startsWith("enrich_") ? "excel_gpt" : nodeType;
  return allow && t in allow.node_types ? t : null;
}

export const STEP_OF_NODE_TYPE: Record<string, string> = { plan: "plan", script: "script", split: "split", excel_gpt: "excel_gpt" };
