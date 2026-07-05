"use client";

import { useMemo, useState } from "react";
import { Search, Link2, Layers } from "lucide-react";
import { cn } from "@/lib/utils";
import { Input } from "@/components/ui/input";
import { BlockKindBadge } from "./block-kind-badge";
import { PromptPreviewPanel } from "./prompt-preview-panel";
import {
  MOCK_BLOCKS,
  MOCK_TEMPLATES,
  MOCK_VARS,
} from "@/lib/prompt-builder/mock-data";
import {
  blocksForKind,
  composePrompt,
  defaultSelection,
} from "@/lib/prompt-builder/compose";
import type { PromptSelection } from "@/lib/prompt-builder/types";

/** Вариант A: промт = список слотов по типу блока */
export function VariantSlots() {
  const [templateId, setTemplateId] = useState(MOCK_TEMPLATES[0].id);
  const [selection, setSelection] = useState<PromptSelection>(() =>
    defaultSelection(MOCK_TEMPLATES[0]),
  );
  const [search, setSearch] = useState("");

  const template = MOCK_TEMPLATES.find((t) => t.id === templateId)!;

  const result = useMemo(
    () => composePrompt(template, MOCK_BLOCKS, selection, MOCK_VARS),
    [template, selection],
  );

  const filteredTemplates = MOCK_TEMPLATES.filter(
    (t) =>
      !search ||
      t.label.toLowerCase().includes(search.toLowerCase()) ||
      t.category.includes(search),
  );

  const pickTemplate = (id: string) => {
    const t = MOCK_TEMPLATES.find((x) => x.id === id)!;
    setTemplateId(id);
    setSelection(defaultSelection(t));
  };

  const setBlock = (slotId: string, blockId: string) => {
    setSelection((s) => ({ ...s, slots: { ...s.slots, [slotId]: blockId } }));
  };

  return (
    <div className="grid h-full min-h-0 grid-cols-[220px_1fr_320px] gap-0 divide-x divide-border">
      {/* Промты */}
      <aside className="flex min-h-0 flex-col bg-card/20">
        <div className="border-b border-border p-2">
          <p className="mb-2 text-[10px] font-medium uppercase tracking-wider text-muted-foreground">
            Промты ({MOCK_TEMPLATES.length})
          </p>
          <div className="relative">
            <Search className="absolute left-2 top-1/2 h-3 w-3 -translate-y-1/2 text-muted-foreground" />
            <Input
              value={search}
              onChange={(e) => setSearch(e.target.value)}
              placeholder="Поиск…"
              className="h-7 pl-7 text-xs"
            />
          </div>
        </div>
        <div className="min-h-0 flex-1 overflow-y-auto p-1.5">
          {filteredTemplates.map((t) => (
            <button
              key={t.id}
              type="button"
              onClick={() => pickTemplate(t.id)}
              className={cn(
                "mb-1 w-full rounded-lg px-2 py-2 text-left transition-colors",
                templateId === t.id
                  ? "bg-primary/15 ring-1 ring-primary/40"
                  : "hover:bg-muted/50",
              )}
            >
              <div className="text-xs font-medium">{t.label}</div>
              <div className="mt-0.5 text-[10px] text-muted-foreground">{t.category}</div>
              <div className="mt-1 flex items-center gap-1 text-[9px] text-muted-foreground">
                <Layers className="h-2.5 w-2.5" />
                {t.slots.length} блоков
              </div>
            </button>
          ))}
        </div>
      </aside>

      {/* Слоты сборки */}
      <main className="flex min-h-0 flex-col overflow-hidden">
        <header className="border-b border-border px-4 py-3">
          <h2 className="text-sm font-semibold">{template.label}</h2>
          <p className="mt-0.5 text-xs text-muted-foreground">{template.description}</p>
          {template.legacyFile && (
            <code className="mt-1 block text-[10px] text-muted-foreground/80">
              prompts/{template.category}/{template.legacyFile}
            </code>
          )}
        </header>

        <div className="min-h-0 flex-1 overflow-y-auto p-4">
          <p className="mb-3 text-[11px] text-muted-foreground">
            Промт собирается из блоков по порядку. Каждый слот — тип: роль, технический, особенности, правила…
          </p>

          <div className="space-y-3">
            {template.slots.map((slot, idx) => {
              const options = blocksForKind(MOCK_BLOCKS, slot.kind, template.stepCode);
              const selectedId = selection.slots[slot.slotId] ?? slot.defaultBlockId;
              const selected = MOCK_BLOCKS.find((b) => b.id === selectedId);
              const isShared = (selected?.sharedCount ?? 0) > 1;

              return (
                <div
                  key={slot.slotId}
                  className="rounded-xl border border-border bg-card/40 p-3"
                >
                  <div className="mb-2 flex items-center justify-between">
                    <div className="flex items-center gap-2">
                      <span className="font-mono text-[10px] text-muted-foreground">
                        {String(idx + 1).padStart(2, "0")}
                      </span>
                      <BlockKindBadge kind={slot.kind} />
                      {slot.required && (
                        <span className="text-[9px] text-destructive/80">обяз.</span>
                      )}
                    </div>
                    {isShared && (
                      <span className="flex items-center gap-1 text-[9px] text-[hsl(var(--info))]">
                        <Link2 className="h-2.5 w-2.5" />
                        общий ×{selected?.sharedCount}
                      </span>
                    )}
                  </div>

                  <select
                    className="h-9 w-full rounded-md border border-input bg-background px-2 text-xs"
                    value={selectedId}
                    onChange={(e) => setBlock(slot.slotId, e.target.value)}
                  >
                    {options.map((b) => (
                      <option key={b.id} value={b.id}>
                        {b.label}
                        {b.sharedCount && b.sharedCount > 1 ? ` · общий (${b.sharedCount})` : ""}
                      </option>
                    ))}
                  </select>

                  {selected && (
                    <p className="mt-2 line-clamp-2 text-[10px] leading-relaxed text-muted-foreground">
                      {selected.body}
                    </p>
                  )}
                </div>
              );
            })}
          </div>
        </div>
      </main>

      {/* Preview */}
      <aside className="min-h-0 bg-card/20">
        <PromptPreviewPanel result={result} className="h-full" />
      </aside>
    </div>
  );
}

export function VariantSlotsLegend() {
  return (
    <p className="text-xs text-muted-foreground">
      <strong className="text-foreground">Слоты</strong> — выбираете промт слева, для каждого типа блока подбираете
      вариант. Общие блоки (технический, правила) повторяются между промтами; особенности — чаще уникальны.
    </p>
  );
}
