import type { BlockKind, BlockVariant, PromptTemplate } from "./types";

/** Гибкие критерии — не только «тип блока», а теги/семейства для сочетаемости */
export type CriteriaDimension = {
  id: string;
  label: string;
  description: string;
  /** Можно включать/выключать при сборке */
  toggleable: boolean;
};

export const CRITERIA_DIMENSIONS: CriteriaDimension[] = [
  { id: "style_family", label: "Семейство стиля", description: "pixelart, textile, clay, noir…", toggleable: true },
  { id: "media", label: "Носитель", description: "text, image, excel, animation", toggleable: true },
  { id: "tone", label: "Тон", description: "calm, dark, horror, playful", toggleable: true },
  { id: "world", label: "Мир", description: "cats, humans, abstract", toggleable: true },
  { id: "pipeline", label: "Шаг пайплайна", description: "plan, script, img_pr…", toggleable: false },
];

export type BlockCriteria = Partial<Record<string, string | string[]>>;

export type CompatibilityLevel = "great" | "ok" | "risky" | "blocked";

export type CompatibilityReason = {
  level: CompatibilityLevel;
  short: string;
  detail: string;
};

export type BlockCompatibility = {
  blockId: string;
  level: CompatibilityLevel;
  score: number;
  reasons: CompatibilityReason[];
  /** Промты, где этот блок уже стоит «родным» */
  nativeIn: string[];
};

export type ActiveContext = {
  /** criteriaId → values accumulated from selected blocks */
  values: Record<string, Set<string>>;
  blockIds: Set<string>;
};

export function blockCriteria(block: BlockVariant): BlockCriteria {
  return block.criteria ?? {};
}

export function criteriaValues(block: BlockVariant, dim: string): string[] {
  const raw = blockCriteria(block)[dim];
  if (!raw) return [];
  return Array.isArray(raw) ? raw : [raw];
}

export function buildContext(
  blocks: BlockVariant[],
  selection: Record<string, string>,
  template: PromptTemplate,
  enabledDims: Set<string>,
): ActiveContext {
  const values: Record<string, Set<string>> = {};
  const blockIds = new Set<string>();

  for (const slot of template.slots) {
    const id = selection[slot.slotId] ?? slot.defaultBlockId;
    blockIds.add(id);
    const b = blocks.find((x) => x.id === id);
    if (!b) continue;
    for (const dim of enabledDims) {
      for (const v of criteriaValues(b, dim)) {
        if (!values[dim]) values[dim] = new Set();
        values[dim].add(v);
      }
    }
  }
  if (enabledDims.has("pipeline")) {
    if (!values.pipeline) values.pipeline = new Set();
    values.pipeline.add(template.stepCode);
  }
  return { values, blockIds };
}

const LEVEL_SCORE: Record<CompatibilityLevel, number> = {
  great: 100,
  ok: 70,
  risky: 35,
  blocked: 0,
};

/** Жёсткие правила: конфликт семейств стиля, несовместимые блоки */
const STYLE_CONFLICTS: [string, string][] = [
  ["pixelart", "textile"],
  ["pixelart", "clay"],
  ["textile", "noir"],
  ["clay", "noir"],
  ["noir", "playful"],
];

const BLOCK_HARD_CONFLICTS: [string, string][] = [
  ["feat_anthro_cats", "feat_clay_plasticine"],
  ["neg_no_humans", "feat_clay_plasticine"],
  ["narr_documentary_calm", "narr_steven_king"],
];

export function evaluateBlock(
  candidate: BlockVariant,
  slotKind: BlockKind,
  template: PromptTemplate,
  context: ActiveContext,
  allBlocks: BlockVariant[],
  enabledDims: Set<string>,
): BlockCompatibility {
  const reasons: CompatibilityReason[] = [];
  let level: CompatibilityLevel = "great";
  let score = 100;

  const bump = (next: CompatibilityLevel, short: string, detail: string) => {
    if (LEVEL_SCORE[next] < LEVEL_SCORE[level]) {
      level = next;
    }
    reasons.push({ level: next, short, detail });
    score = Math.min(score, LEVEL_SCORE[next]);
  };

  // Шаг пайплайна
  if (!candidate.steps.includes(template.stepCode)) {
    bump("risky", "Другой шаг", `Обычно для ${candidate.steps.join(", ")}, не ${template.stepCode}`);
  }

  // Тип слота vs kind блока
  if (candidate.kind !== slotKind) {
    bump("risky", "Другой тип", `Слот «${slotKind}», блок «${candidate.kind}»`);
  }

  // Жёсткий конфликт блоков
  for (const otherId of context.blockIds) {
    if (otherId === candidate.id) continue;
    for (const [a, b] of BLOCK_HARD_CONFLICTS) {
      if (
        (candidate.id === a && otherId === b) ||
        (candidate.id === b && otherId === a)
      ) {
        bump("blocked", "Конфликт блоков", `Не сочетается с «${labelOf(allBlocks, otherId)}»`);
      }
    }
  }

  // Конфликт семейств стиля
  if (enabledDims.has("style_family")) {
    const candStyles = criteriaValues(candidate, "style_family");
    const ctxStyles = [...(context.values.style_family ?? [])];
    for (const cs of candStyles) {
      for (const ctx of ctxStyles) {
        for (const [x, y] of STYLE_CONFLICTS) {
          if ((cs === x && ctx === y) || (cs === y && ctx === x)) {
            bump(
              "blocked",
              "Стиль не сочетается",
              `${cs} + ${ctx} — разные визуальные семейства`,
            );
          }
        }
      }
    }
    if (candStyles.length && ctxStyles.length && candStyles.some((s) => ctxStyles.includes(s))) {
      reasons.unshift({
        level: "great",
        short: "Тот же стиль",
        detail: `Семейство: ${candStyles.filter((s) => ctxStyles.includes(s)).join(", ")}`,
      });
    }
  }

  // Тон
  if (enabledDims.has("tone")) {
    const candTone = criteriaValues(candidate, "tone");
    const ctxTone = [...(context.values.tone ?? [])];
    if (candTone.length && ctxTone.length) {
      const clash =
        (candTone.includes("horror") && ctxTone.includes("playful")) ||
        (candTone.includes("playful") && ctxTone.includes("horror")) ||
        (candTone.includes("dark") && ctxTone.includes("playful"));
      if (clash) bump("blocked", "Тон не сочетается", `${candTone.join("/")} vs ${ctxTone.join("/")}`);
      else if (candTone.some((t) => ctxTone.includes(t))) {
        reasons.unshift({ level: "great", short: "Тон совпадает", detail: candTone.join(", ") });
      }
    }
  }

  // Мир (cats vs humans)
  if (enabledDims.has("world")) {
    const cw = criteriaValues(candidate, "world");
    const ctxw = [...(context.values.world ?? [])];
    if (cw.includes("cats") && ctxw.includes("humans")) {
      bump("blocked", "Мир", "Коты и реалистичные люди в одном промте");
    }
  }

  // requires из meta блока
  if (candidate.requires) {
    for (const [dim, need] of Object.entries(candidate.requires)) {
      if (!enabledDims.has(dim)) continue;
      const needs = (Array.isArray(need) ? need : need ? [need] : []).filter(
        (n): n is string => Boolean(n),
      );
      const have = context.values[dim] ?? new Set();
      if (!needs.some((n) => have.has(n))) {
        bump(
          "risky",
          "Нужен контекст",
          `Лучше сначала выбрать ${dim}: ${needs.join(" или ")}`,
        );
      }
    }
  }

  // pairs_well — бонус
  if (candidate.pairsWell) {
    for (const pid of candidate.pairsWell) {
      if (context.blockIds.has(pid)) {
        reasons.unshift({
          level: "great",
          short: "Пара",
          detail: `Сочетается с «${labelOf(allBlocks, pid)}»`,
        });
        score = Math.max(score, 95);
      }
    }
  }

  const nativeIn: string[] = [];

  if (reasons.length === 0) {
    reasons.push({ level: "ok", short: "Допустимо", detail: "Нет явных конфликтов с текущим набором" });
    if (level === "great") level = "ok";
  }

  return { blockId: candidate.id, level, score, reasons, nativeIn };
}

function labelOf(blocks: BlockVariant[], id: string): string {
  return blocks.find((b) => b.id === id)?.label ?? id;
}

export function rankBlocksForSlot(
  slotKind: BlockKind,
  template: PromptTemplate,
  selection: Record<string, string>,
  allBlocks: BlockVariant[],
  templates: PromptTemplate[],
  enabledDims: Set<string>,
  /** exclude current slot from context */
  editingSlotId?: string,
): BlockCompatibility[] {
  const contextSelection = { ...selection };
  if (editingSlotId) {
    const slot = template.slots.find((s) => s.slotId === editingSlotId);
    if (slot) delete contextSelection[editingSlotId];
  }
  const context = buildContext(allBlocks, contextSelection, template, enabledDims);

  const candidates = allBlocks.filter(
    (b) => b.kind === slotKind || b.steps.includes(template.stepCode),
  );

  const ranked = candidates.map((b) => {
    const c = evaluateBlock(b, slotKind, template, context, allBlocks, enabledDims);
    c.nativeIn = templates.filter((t) => t.slots.some((s) => s.defaultBlockId === b.id)).map((t) => t.label);
    return c;
  });

  ranked.sort((a, b) => b.score - a.score || a.blockId.localeCompare(b.blockId));
  return ranked;
}

export const LEVEL_UI: Record<
  CompatibilityLevel,
  { label: string; className: string; dot: string }
> = {
  great: {
    label: "Отлично",
    className: "border-[hsl(var(--success))]/40 bg-[hsl(var(--success))]/10 text-[hsl(var(--success))]",
    dot: "bg-[hsl(var(--success))]",
  },
  ok: {
    label: "OK",
    className: "border-border bg-card/60 text-foreground",
    dot: "bg-muted-foreground",
  },
  risky: {
    label: "Риск",
    className: "border-[hsl(var(--warning))]/40 bg-[hsl(var(--warning))]/10 text-[hsl(var(--warning))]",
    dot: "bg-[hsl(var(--warning))]",
  },
  blocked: {
    label: "Нельзя",
    className: "border-destructive/40 bg-destructive/10 text-destructive",
    dot: "bg-destructive",
  },
};
