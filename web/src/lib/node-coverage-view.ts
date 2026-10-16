
export type CoverageLevel = "managed" | "observed" | "human_only";

export interface NodeCoverageRow {
  node_type: string;
  title: string;
  level: CoverageLevel;
  level_label?: string;
  verifies: string[];
  limits?: string[];
  recovery: { actions: string[]; cap_per_node: number; profile_compatible?: boolean };
  forbidden: { code: string | null; meaning: string | null; reason: string | null } | null;
}

export interface NodeCoverage {
  nodes: NodeCoverageRow[];
  summary: { managed: number; observed: number; human_only: number; total: number; with_automatic_recovery?: number };
  uncovered?: string[];
  automatic_actions?: boolean;
  semantic_quality_verified?: boolean;
}

export const COVERAGE_LEVEL_RU: Record<CoverageLevel, string> = {
  managed: "Под управлением",
  observed: "Под наблюдением",
  human_only: "Только человек",
};

export const COVERAGE_LEVEL_CLASS: Record<CoverageLevel, string> = {
  managed: "border-emerald-400/50 text-emerald-200",
  observed: "border-amber-400/50 text-amber-200",
  human_only: "border-red-400/50 text-red-300",
};

export function coverageLevelLabel(level: string): string {
  return COVERAGE_LEVEL_RU[level as CoverageLevel] ?? "Неизвестно";
}

export function coverageSummaryLine(summary: NodeCoverage["summary"] | null | undefined): string {
  if (!summary) return "Покрытие не прочитано.";
  return `управляется ${summary.managed} из ${summary.total}, наблюдается ${summary.observed}, только человек ${summary.human_only}`;
}

export function forbiddenText(row: Pick<NodeCoverageRow, "forbidden" | "recovery">): string {
  if (row.recovery.actions.length > 0) {
    const n = row.recovery.cap_per_node;
    return `Автоповтор разрешён: ${row.recovery.actions.join(", ")}${n ? `, не более ${n} за запуск` : ""}.`;
  }
  const f = row.forbidden;
  const text = f?.reason?.trim() || f?.meaning?.trim();
  return text || "Причина запрета в реестре не указана.";
}

export function verifiesText(row: Pick<NodeCoverageRow, "verifies">): string {
  return row.verifies.length ? row.verifies.join("; ") : "Только общий журнал попыток, проверки результата нет.";
}
