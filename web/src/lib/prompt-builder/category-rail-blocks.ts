import { allBlockIdsForKindFromPresets } from "./preset-block-index";
import type { StepPresetsFile } from "./prompt-presets";

export type RailBlockRow = {
  id: string;
  kind: string;
  label: string;
  body?: string;
};

type CatalogBlockDTO = {
  category: string;
  id: string;
  label: string;
  body?: string;
};

/** Полный список блоков: шаг + API + индекс файлов на диске. */
export function mergeFullCatalogBlocks(
  stepBlocks: RailBlockRow[],
  catalogBlocks: CatalogBlockDTO[] | undefined,
  blockCategoryIndex: Record<string, string[]> | undefined,
): RailBlockRow[] {
  const byKey = new Map<string, RailBlockRow>();
  const add = (row: RailBlockRow) => byKey.set(`${row.kind}:${row.id}`, row);

  for (const b of stepBlocks) add(b);
  for (const item of catalogBlocks ?? []) {
    add({ id: item.id, kind: item.category, label: item.label, body: item.body });
  }
  for (const [kind, names] of Object.entries(blockCategoryIndex ?? {})) {
    for (const name of names) {
      const key = `${kind}:${name}`;
      if (!byKey.has(key)) add({ id: name, kind, label: name });
    }
  }
  return [...byKey.values()];
}

/** Все варианты блока в категории — из индекса каталога, не только из пресетов. */
export function blockVariantsForKind(
  kind: string,
  fullCatalogBlocks: RailBlockRow[],
  blockCategoryIndex: Record<string, string[]>,
  stepPresets: StepPresetsFile | null | undefined,
  resolveRow: (kind: string, blockId: string) => RailBlockRow,
): RailBlockRow[] {
  const byId = new Map<string, RailBlockRow>();

  for (const name of blockCategoryIndex[kind] ?? []) {
    const row =
      fullCatalogBlocks.find((b) => b.kind === kind && b.id === name) ??
      resolveRow(kind, name);
    byId.set(name, row);
  }
  for (const b of fullCatalogBlocks) {
    if (b.kind === kind) byId.set(b.id, b);
  }
  for (const bid of allBlockIdsForKindFromPresets(stepPresets, kind)) {
    if (!byId.has(bid)) byId.set(bid, resolveRow(kind, bid));
  }
  return [...byId.values()].sort((a, b) =>
    (a.label || a.id).localeCompare(b.label || b.id, "ru"),
  );
}

export function categoryKindIdsForRail(
  composeStepId: string | undefined,
  stepBlockCategories: Record<string, string[]> | undefined,
  categoryKinds: { id: string }[],
): string[] {
  if (composeStepId && stepBlockCategories?.[composeStepId]?.length) {
    return stepBlockCategories[composeStepId];
  }
  return categoryKinds.map((k) => k.id);
}

export function categoryKindIdsForStep(
  composeStepId: string | undefined,
  stepBlockCategories: Record<string, string[]> | undefined,
  categoryKinds: { id: string }[],
  stepPresets: StepPresetsFile | null | undefined,
  allSlots: { kind: string }[],
): string[] {
  const ids = new Set<string>();
  const stepKinds = composeStepId && stepBlockCategories?.[composeStepId];
  if (stepKinds?.length) {
    for (const k of stepKinds) ids.add(k);
  } else {
    for (const k of categoryKinds) ids.add(k.id);
  }
  if (stepPresets?.presets) {
    for (const preset of Object.values(stepPresets.presets)) {
      for (const k of Object.keys(preset.blocks ?? {})) ids.add(k);
      for (const o of preset.omit_slots ?? []) {
        if (typeof o === "string") ids.add(o);
      }
    }
  }
  for (const slot of allSlots) ids.add(slot.kind);
  return [...ids];
}

/** Подпись блока в правой колонке: id, если label повторяется или слишком общий. */
export function railBlockDisplay(
  block: RailBlockRow,
  variants: RailBlockRow[],
): { title: string; subtitle: string | null } {
  const id = block.id;
  const label = (block.label || "").trim();
  const labels = variants.map((v) => (v.label || v.id).trim());
  const duplicateLabel = Boolean(label && labels.filter((l) => l === label).length > 1);
  const genericLabel = !label || label === id || /^content v\d+$/i.test(label);

  if (duplicateLabel || genericLabel) {
    return {
      title: id,
      subtitle: label && label !== id ? label : null,
    };
  }
  return { title: label, subtitle: id };
}
