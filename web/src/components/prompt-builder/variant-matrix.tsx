"use client";

import { useMemo, useState } from "react";
import { cn } from "@/lib/utils";
import { BlockKindBadge } from "./block-kind-badge";
import { MOCK_BLOCKS, MOCK_TEMPLATES, MOCK_PRESETS } from "@/lib/prompt-builder/mock-data";
import { BLOCK_KINDS, type BlockKind } from "@/lib/prompt-builder/types";

/** Вариант C: матрица — какие блоки у каких промтов, где совпадают */
export function VariantMatrix() {
  const [presetId, setPresetId] = useState(MOCK_PRESETS[0].id);
  const [hoverCell, setHoverCell] = useState<string | null>(null);

  const preset = MOCK_PRESETS.find((p) => p.id === presetId)!;
  const templates = MOCK_TEMPLATES.filter((t) => preset.templates.includes(t.id));

  const kindsUsed = useMemo(() => {
    const set = new Set<BlockKind>();
    for (const t of templates) {
      for (const s of t.slots) set.add(s.kind);
    }
    return BLOCK_KINDS.filter((k) => set.has(k.id));
  }, [templates]);

  const cellBlockIds = (templateId: string, kind: BlockKind) => {
    const t = templates.find((x) => x.id === templateId)!;
    return t.slots.filter((s) => s.kind === kind).map((s) => s.defaultBlockId);
  };

  const blockLabels = (ids: string[]) => {
    if (ids.length === 0) return "—";
    return ids.map((id) => MOCK_BLOCKS.find((b) => b.id === id)?.label ?? id).join(" + ");
  };

  /** Блоки, которые повторяются в 2+ промтах пресета */
  const sharedInPreset = useMemo(() => {
    const counts = new Map<string, number>();
    for (const t of templates) {
      const seen = new Set<string>();
      for (const s of t.slots) {
        if (!seen.has(s.defaultBlockId)) {
          seen.add(s.defaultBlockId);
          counts.set(s.defaultBlockId, (counts.get(s.defaultBlockId) ?? 0) + 1);
        }
      }
    }
    return new Set([...counts.entries()].filter(([, c]) => c > 1).map(([id]) => id));
  }, [templates]);

  return (
    <div className="flex h-full min-h-0 flex-col">
      <header className="flex flex-wrap items-center gap-3 border-b border-border px-4 py-3">
        <span className="text-xs text-muted-foreground">Пресет пайплайна:</span>
        <select
          className="h-8 rounded-md border border-input bg-background px-2 text-xs"
          value={presetId}
          onChange={(e) => setPresetId(e.target.value)}
        >
          {MOCK_PRESETS.map((p) => (
            <option key={p.id} value={p.id}>
              {p.label}
            </option>
          ))}
        </select>
        <span className="text-[10px] text-muted-foreground">
          {templates.length} промтов · подсветка = общий блок между шагами
        </span>
      </header>

      <div className="min-h-0 flex-1 overflow-auto p-4">
        <table className="w-full min-w-[720px] border-collapse text-left">
          <thead>
            <tr>
              <th className="sticky left-0 z-10 bg-background px-2 py-2 text-[10px] font-medium text-muted-foreground">
                Промт / тип блока
              </th>
              {kindsUsed.map((k) => (
                <th key={k.id} className="px-2 py-2">
                  <BlockKindBadge kind={k.id} compact />
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {templates.map((t) => (
              <tr key={t.id} className="border-t border-border/60">
                <td className="sticky left-0 z-10 bg-background px-2 py-2">
                  <div className="text-xs font-medium">{t.label}</div>
                  <div className="text-[9px] text-muted-foreground">{t.stepCode}</div>
                </td>
                {kindsUsed.map((k) => {
                  const bids = cellBlockIds(t.id, k.id);
                  const primary = bids[0] ?? null;
                  const shared = primary ? sharedInPreset.has(primary) : false;
                  const cellKey = `${t.id}-${k.id}`;
                  return (
                    <td
                      key={k.id}
                      className="px-1 py-1"
                      onMouseEnter={() => setHoverCell(cellKey)}
                      onMouseLeave={() => setHoverCell(null)}
                    >
                      <div
                        className={cn(
                          "rounded-md px-2 py-1.5 text-[10px] transition-colors",
                          bids.length === 0 && "text-muted-foreground/40",
                          bids.length > 0 && !shared && "bg-card border border-border",
                          bids.length > 0 && shared && "bg-primary/10 border border-primary/30 text-primary",
                          hoverCell === cellKey && bids.length > 0 && "ring-1 ring-ring",
                        )}
                        title={
                          primary
                            ? bids
                                .map((id) => MOCK_BLOCKS.find((b) => b.id === id)?.body)
                                .join("\n---\n")
                            : undefined
                        }
                      >
                        {blockLabels(bids)}
                      </div>
                    </td>
                  );
                })}
              </tr>
            ))}
          </tbody>
        </table>

        <div className="mt-6 grid gap-3 sm:grid-cols-2">
          <section className="rounded-xl border border-border p-3">
            <h3 className="mb-2 text-xs font-semibold">Общие блоки в пресете</h3>
            <ul className="space-y-1">
              {[...sharedInPreset].map((id) => {
                const b = MOCK_BLOCKS.find((x) => x.id === id)!;
                return (
                  <li key={id} className="flex items-center gap-2 text-[10px]">
                    <BlockKindBadge kind={b.kind} compact />
                    <span>{b.label}</span>
                  </li>
                );
              })}
            </ul>
          </section>
          <section className="rounded-xl border border-border p-3">
            <h3 className="mb-2 text-xs font-semibold">Уникальные (только один промт)</h3>
            <ul className="max-h-32 space-y-1 overflow-y-auto">
              {templates.flatMap((t) =>
                t.slots
                  .filter((s) => !sharedInPreset.has(s.defaultBlockId))
                  .map((s) => {
                    const b = MOCK_BLOCKS.find((x) => x.id === s.defaultBlockId)!;
                    return (
                      <li key={`${t.id}-${s.defaultBlockId}`} className="text-[10px] text-muted-foreground">
                        <span className="text-foreground">{t.label}</span> → {b.label}
                      </li>
                    );
                  }),
              )}
            </ul>
          </section>
        </div>
      </div>
    </div>
  );
}

export function VariantMatrixLegend() {
  return (
    <p className="text-xs text-muted-foreground">
      <strong className="text-foreground">Матрица</strong> — видно, где блоки совпадают между промтами (технический,
      правила), а где у каждого шага свои особенности. Помогает переносить блок из одного промта в другой.
    </p>
  );
}
