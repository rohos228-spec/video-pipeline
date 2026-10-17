/**
 * Черновик реестра действий WS2 (`routers/orchestrator_actions.py`).
 * Форма provisional: SHA R2 ещё не опубликован, `registry_sha` мока — `provisional-r2`.
 *
 * GET `/api/orchestrator/actions/metadata`
 *   `{ actions: ActionMetadata[], registry_sha: string }`
 * GET `/api/orchestrator/actions/{name}/metadata`
 *   один `ActionMetadata`, 404 если имени нет.
 *
 * Клиент берёт список один раз и кэширует его по `registry_sha`.
 * Имени нет в списке — то же, что 404 на `{name}/metadata`: paid и destructive.
 * Сам список недоступен (404 маршрута, сеть, битое тело) — fallback на `step_started.paid`.
 */

import { getOrchActionMetadata, OrchApiError, type OrchClientConfig } from "./orchestratorApi";

export const ACTION_RISKS = ["none", "low", "medium", "high"] as const;
export type ActionRisk = (typeof ACTION_RISKS)[number];

export interface ActionMetadata {
  name: string;
  risk: ActionRisk;
  read_only: boolean;
  destructive: boolean;
  paid: boolean;
}

export interface ActionMetadataList {
  actions: ActionMetadata[];
  registry_sha: string;
}

export type ActionRegistryState =
  | { status: "loading" }
  | { status: "missing" }
  | { status: "ready"; actions: ActionMetadata[] };

export interface KillHonesty {
  paidBoundary: boolean;
  destructive: boolean;
}

interface StepPaid {
  action: string;
  paid: boolean;
  status: "running" | "done" | "failed";
}

const shaCache = new Map<string, ActionMetadata[]>();
let pending: Promise<ActionMetadata[] | null> | null = null;

function isRisk(value: unknown): value is ActionRisk {
  return typeof value === "string" && (ACTION_RISKS as readonly string[]).includes(value);
}

export function parseActionMetadata(value: unknown): ActionMetadata | null {
  if (!value || typeof value !== "object") return null;
  const row = value as Record<string, unknown>;
  if (typeof row.name !== "string" || !row.name) return null;
  if (!isRisk(row.risk)) return null;
  if (typeof row.read_only !== "boolean" || typeof row.destructive !== "boolean" || typeof row.paid !== "boolean") {
    return null;
  }
  return {
    name: row.name,
    risk: row.risk,
    read_only: row.read_only,
    destructive: row.destructive,
    paid: row.paid,
  };
}

export function parseActionMetadataList(value: unknown): ActionMetadataList | null {
  if (!value || typeof value !== "object") return null;
  const body = value as Record<string, unknown>;
  if (typeof body.registry_sha !== "string" || !body.registry_sha || !Array.isArray(body.actions)) return null;
  const actions: ActionMetadata[] = [];
  for (const item of body.actions) {
    const parsed = parseActionMetadata(item);
    if (!parsed) return null;
    actions.push(parsed);
  }
  const cached = shaCache.get(body.registry_sha);
  if (cached) return { registry_sha: body.registry_sha, actions: cached };
  shaCache.set(body.registry_sha, actions);
  return { registry_sha: body.registry_sha, actions };
}

/** Список один раз на страницу. Повтор с тем же `registry_sha` отдаёт кэш. 401 пробрасывается. */
export function loadActionRegistry(cfg: OrchClientConfig): Promise<ActionMetadata[] | null> {
  if (!pending) {
    pending = pullActionRegistry(cfg).catch((error: unknown) => {
      pending = null;
      throw error;
    });
  }
  return pending;
}

async function pullActionRegistry(cfg: OrchClientConfig): Promise<ActionMetadata[] | null> {
  try {
    const body = await getOrchActionMetadata(cfg);
    const parsed = parseActionMetadataList(body);
    return parsed ? parsed.actions : null;
  } catch (error) {
    if (error instanceof OrchApiError && error.status === 401) throw error;
    return null;
  }
}

/**
 * Реестр есть: `paidBoundary = metadata.paid`, неизвестное имя — paid и destructive.
 * Реестра нет, но шаг уже стартовал: `step_started.paid`.
 * Реестра нет и шага нет: как сейчас, оговорка kill остаётся (нечего читать).
 */
export function resolveKillHonesty(steps: StepPaid[], registry: ActionRegistryState): KillHonesty {
  const running = steps.filter((step) => step.status === "running");
  const source = running.length > 0 ? running : steps;
  if (registry.status !== "ready") {
    if (registry.status === "missing" && source.length > 0) {
      return { paidBoundary: source.some((step) => step.paid), destructive: false };
    }
    return { paidBoundary: true, destructive: false };
  }
  if (source.length === 0) return { paidBoundary: false, destructive: false };
  const byName = new Map(registry.actions.map((item) => [item.name, item]));
  let paidBoundary = false;
  let destructive = false;
  for (const step of source) {
    const meta = step.action ? byName.get(step.action) : undefined;
    if (!meta) return { paidBoundary: true, destructive: true };
    if (meta.paid) paidBoundary = true;
    if (meta.destructive) destructive = true;
  }
  return { paidBoundary, destructive };
}
