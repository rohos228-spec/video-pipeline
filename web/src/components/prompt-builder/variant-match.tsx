"use client";

import { useMemo, useState } from "react";
import { Search, Sparkles } from "lucide-react";
import { cn } from "@/lib/utils";
import { Input } from "@/components/ui/input";
import { BlockKindBadge } from "./block-kind-badge";
import { PromptPreviewPanel } from "./prompt-preview-panel";
import { ContextStrip, CriteriaToggles } from "./context-strip";
import { BlockPicker, BorrowFromPrompt } from "./block-picker";
import { MOCK_BLOCKS, MOCK_TEMPLATES, MOCK_VARS } from "@/lib/prompt-builder/mock-data";
import { composePrompt, defaultSelection } from "@/lib/prompt-builder/compose";
import {
  buildContext,
  CRITERIA_DIMENSIONS,
  rankBlocksForSlot,
} from "@/lib/prompt-builder/compatibility";
import type { PromptSelection } from "@/lib/prompt-builder/types";

const DEFAULT_ENABLED = new Set(
  CRITERIA_DIMENSIONS.filter((d) => d.toggleable).map((d) => d.id),
);

/** Вариант D: гибкий подбор с критериями и сочетаемостью */
export function VariantMatch() {
  const [templateId, setTemplateId] = useState("tpl_img_knitted");
  const [selection, setSelection] = useState<PromptSelection>(() =>
    defaultSelection(MOCK_TEMPLATES.find((t) => t.id === "tpl_img_knitted")!),
  );
  const [enabledDims, setEnabledDims] = useState(DEFAULT_ENABLED);
  const [showRisky, setShowRisky] = useState(false);
  const [search, setSearch] = useState("");
  const [focusSlot, setFocusSlot] = useState<string | null>(null);

  const template = MOCK_TEMPLATES.find((t) => t.id === templateId)!;

  const context = useMemo(
    () =>
      buildContext(
        MOCK_BLOCKS,
        selection.slots,
        template,
        new Set([...enabledDims, "pipeline"]),
      ),
    [selection.slots, template, enabledDims],
  );

  const result = useMemo(() => {
    const base = composePrompt(template, MOCK_BLOCKS, selection, MOCK_VARS);
    for (const slot of template.slots) {
      const blockId = selection.slots[slot.slotId] ?? slot.defaultBlockId;
      const ranked = rankBlocksForSlot(
        slot.kind,
        template,
        selection.slots,
        MOCK_BLOCKS,
        MOCK_TEMPLATES,
        new Set([...enabledDims, "pipeline"]),
        slot.slotId,
      );
      const compat = ranked.find((r) => r.blockId === blockId);
      if (compat?.level === "blocked") {
        base.warnings.push({
          level: "error",
          message: `${slot.slotId}: ${compat.reasons.find((r) => r.level === "blocked")?.detail ?? "конфликт"}`,
        });
      } else if (compat?.level === "risky") {
        base.warnings.push({
          level: "warn",
          message: `${slot.slotId}: ${compat.reasons[0]?.detail ?? "рискованное сочетание"}`,
        });
      }
    }
    return base;
  }, [template, selection, enabledDims]);

  const pickTemplate = (id: string) => {
    const t = MOCK_TEMPLATES.find((x) => x.id === id)!;
    setTemplateId(id);
    setSelection(defaultSelection(t));
    setFocusSlot(null);
  };

  const setBlock = (slotId: string, blockId: string) => {
    setSelection((s) => ({ ...s, slots: { ...s.slots, [slotId]: blockId } }));
  };

  const filtered = MOCK_TEMPLATES.filter(
    (t) =>
      !search ||
      t.label.toLowerCase().includes(search.toLowerCase()) ||
      t.stepCode.includes(search),
  );

  return (
    <div className="grid h-full min-h-0 grid-cols-[200px_1fr_300px] divide-x divide-border">
      <aside className="flex min-h-0 flex-col bg-card/20">
        <div className="border-b border-border p-2">
          <p className="mb-2 text-[10px] font-medium uppercase tracking-wider text-muted-foreground">Промты</p>
          <Input
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            placeholder="Поиск…"
            className="h-7 text-xs"
          />
        </div>
        <div className="min-h-0 flex-1 overflow-y-auto p-1.5">
          {filtered.map((t) => (
            <button
              key={t.id}
              type="button"
              onClick={() => pickTemplate(t.id)}
              className={cn(
                "mb-1 w-full rounded-lg px-2 py-2 text-left text-xs",
                templateId === t.id ? "bg-primary/15 ring-1 ring-primary/40" : "hover:bg-muted/50",
              )}
            >
              {t.label}
            </button>
          ))}
        </div>
      </aside>

      <main className="flex min-h-0 flex-col">
        <div className="space-y-2 border-b border-border px-4 py-3">
          <div className="flex flex-wrap items-center justify-between gap-2">
            <h2 className="flex items-center gap-2 text-sm font-semibold">
              <Sparkles className="h-4 w-4 text-primary" />
              {template.label}
            </h2>
            <label className="flex cursor-pointer items-center gap-1.5 text-[10px] text-muted-foreground">
              <input
                type="checkbox"
                checked={showRisky}
                onChange={(e) => setShowRisky(e.target.checked)}
                className="rounded"
              />
              Показать риск
            </label>
          </div>
          <CriteriaToggles enabled={enabledDims} onChange={setEnabledDims} />
          <ContextStrip context={context} />
        </div>

        <div className="min-h-0 flex-1 overflow-y-auto p-4">
          <p className="mb-4 text-[11px] leading-relaxed text-muted-foreground">
            Меняйте критерии сверху — подбор пересчитывается.{" "}
            <span className="text-[hsl(var(--success))]">Зелёный</span> = хорошо сочетается,{" "}
            <span className="text-[hsl(var(--warning))]">жёлтый</span> = риск,{" "}
            <span className="text-destructive">красный</span> = конфликт. Блок из другого промта можно
            взять через «Взять блок из промта».
          </p>

          <div className="space-y-4">
            {template.slots.map((slot, idx) => (
              <section
                key={slot.slotId}
                className={cn(
                  "rounded-xl border p-3 transition-shadow",
                  focusSlot === slot.slotId ? "border-primary/50 shadow-sm shadow-primary/10" : "border-border",
                )}
                onFocus={() => setFocusSlot(slot.slotId)}
              >
                <div className="mb-2 flex items-center gap-2">
                  <span className="font-mono text-[10px] text-muted-foreground">{idx + 1}</span>
                  <BlockKindBadge kind={slot.kind} />
                  {slot.required && <span className="text-[9px] text-destructive/80">обяз.</span>}
                </div>

                <BlockPicker
                  slotId={slot.slotId}
                  slotKind={slot.kind}
                  template={template}
                  selection={selection.slots}
                  selectedBlockId={selection.slots[slot.slotId] ?? slot.defaultBlockId}
                  enabledDims={new Set([...enabledDims, "pipeline"])}
                  onSelect={(id) => setBlock(slot.slotId, id)}
                  showRisky={showRisky}
                />

                <BorrowFromPrompt
                  template={template}
                  slotId={slot.slotId}
                  slotKind={slot.kind}
                  selection={selection.slots}
                  enabledDims={new Set([...enabledDims, "pipeline"])}
                  onApply={(id) => setBlock(slot.slotId, id)}
                />
              </section>
            ))}
          </div>
        </div>
      </main>

      <aside className="min-h-0 bg-card/20">
        <PromptPreviewPanel result={result} className="h-full" />
      </aside>
    </div>
  );
}

export function VariantMatchLegend() {
  return (
    <p className="text-xs text-muted-foreground">
      <strong className="text-foreground">Подбор</strong> — гибкие критерии (стиль, тон, мир…), автоматическая
      оценка сочетаемости, перенос блоков между промтами с проверкой конфликтов.
    </p>
  );
}
