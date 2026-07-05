"use client";

import { useState } from "react";
import { LayoutList, Pencil, Settings2, User } from "lucide-react";
import { cn } from "@/lib/utils";
import {
  ORCHESTRATOR_FIELDS_BY_KIND,
  type OrchestratorField,
} from "@/lib/prompt-builder/orchestrator-vars";
import { cellById, type CellUsageMap } from "@/lib/prompt-builder/excel-cells";
import { MOCK_BLOCKS } from "@/lib/prompt-builder/mock-data";
import {
  BLOCK_KINDS,
  type BlockKind,
  type BlockKindMeta,
  type BlockVariant,
  type PromptTemplate,
  type PromptSlot,
} from "@/lib/prompt-builder/types";
import { agentForBlock } from "@/lib/prompt-builder/agents-catalog";
import { isSlotEmpty, resolveSlotBlockId } from "@/lib/prompt-builder/compose";

type RightTab = "settings" | "blocks";

function kindIcon(kind: BlockKind) {
  if (kind === "role") return User;
  return LayoutList;
}

export function PromptRightPanel({
  template,
  allSlots,
  selection,
  activeSlotId,
  activeBlockId,
  previewDescription,
  previewTitle,
  previewSubtitle,
  previewKind,
  highlightCellIds,
  activeAgentCount,
  vars,
  onChangeVar,
  selectedCellId,
  usage,
  onSelectSlot,
  onOpenEditor,
  blocks = MOCK_BLOCKS,
  categoryKinds = BLOCK_KINDS,
}: {
  template: PromptTemplate;
  allSlots: PromptSlot[];
  selection: Record<string, string>;
  activeSlotId: string | null;
  activeBlockId: string | null;
  previewDescription?: string;
  previewTitle?: string;
  previewSubtitle?: string;
  previewKind?: BlockKind;
  highlightCellIds: string[];
  activeAgentCount?: number;
  vars: Record<string, string | number>;
  onChangeVar: (key: string, value: string | number) => void;
  selectedCellId: string | null;
  usage: CellUsageMap;
  onSelectSlot: (slotId: string) => void;
  onOpenEditor: (slotId: string) => void;
  blocks?: BlockVariant[];
  categoryKinds?: BlockKindMeta[];
}) {
  const [tab, setTab] = useState<RightTab>("settings");

  const activeSlot = activeSlotId
    ? template.slots.find((s) => s.slotId === activeSlotId)
    : null;
  const activeBlock = activeBlockId ? blocks.find((b) => b.id === activeBlockId) : null;
  const fields = activeSlot ? (ORCHESTRATOR_FIELDS_BY_KIND[activeSlot.kind] ?? []) : [];

  const selectedCell = selectedCellId ? cellById(selectedCellId) : null;
  const selectedUsage = selectedCellId ? (usage[selectedCellId] ?? []) : [];

  const PreviewIcon = previewKind ? kindIcon(previewKind) : Settings2;

  return (
    <aside className="relative flex h-full min-w-0 flex-col border-l border-[var(--pb-border)] bg-[var(--pb-panel)]">
      <div className="flex shrink-0 gap-1 px-2 pt-2 pb-1">
        <button
          type="button"
          title="Настройки"
          aria-label="Настройки"
          onClick={() => setTab("settings")}
          className={cn("pb-right-icon", tab === "settings" && "pb-right-icon-active")}
        >
          <Settings2 className="h-3.5 w-3.5" strokeWidth={1.75} />
        </button>
        <button
          type="button"
          title="Блоки промта"
          aria-label="Блоки промта"
          onClick={() => setTab("blocks")}
          className={cn("pb-right-icon", tab === "blocks" && "pb-right-icon-active")}
        >
          <LayoutList className="h-3.5 w-3.5" strokeWidth={1.75} />
        </button>
      </div>

      <div className="relative min-h-0 flex-1 overflow-hidden">
        <div className="h-full overflow-y-auto px-2.5 pb-4 pt-1">
          {tab === "settings" && (
            <div className="space-y-4">
              {!previewTitle && !activeBlock && !selectedCell && (
                <p className="text-[10px] leading-relaxed pb-text-muted">
                  Выберите блок на пайплайне
                </p>
              )}

              {(previewTitle || activeBlock) && (
                <section className={cn(activeBlock && "pb-glow-soft rounded-lg p-2")}>
                  <div className="flex gap-2.5">
                    <PreviewIcon className="mt-0.5 h-4 w-4 shrink-0 pb-text-dim" strokeWidth={1.5} />
                    <div className="min-w-0">
                      <p className="text-[12px] font-medium leading-snug pb-text">
                        {previewTitle ?? activeBlock?.label}
                      </p>
                      {(previewSubtitle || activeSlot) && (
                        <p className="mt-0.5 text-[9px] pb-text-muted">
                          {previewSubtitle ??
                            categoryKinds.find((k) => k.id === activeSlot?.kind)?.label}
                        </p>
                      )}
                    </div>
                  </div>
                  {(previewDescription || activeBlock?.body) && (
                    <p className="mt-2.5 text-[10px] leading-relaxed pb-text-muted">
                      {previewDescription ?? activeBlock?.body}
                    </p>
                  )}
                  {highlightCellIds.length > 0 && (
                    <p className="mt-2 text-[8px] pb-text-dim">
                      {highlightCellIds.length} яч. · {activeAgentCount ?? 0} аг.
                    </p>
                  )}
                </section>
              )}

              {activeSlot && fields.length > 0 && (
                <section>
                  <div className="space-y-3">
                    {fields.map((f) => (
                      <FieldControl key={f.key} field={f} value={vars[f.key]} onChange={onChangeVar} />
                    ))}
                  </div>
                </section>
              )}

              {selectedCell && (
                <section>
                  <p className="text-[9px] pb-text-dim">
                    R{selectedCell.row} · {selectedCell.label}
                  </p>
                  {selectedUsage.length > 0 && (
                    <div className="mt-2 flex flex-wrap gap-1">
                      {selectedUsage.map((e) => (
                        <span key={`${e.blockId}-${e.slotKind}`} className="pb-excel-agent-tag">
                          {e.agentName ?? e.blockLabel}
                        </span>
                      ))}
                    </div>
                  )}
                </section>
              )}
            </div>
          )}

          {tab === "blocks" && (
            <div className="space-y-3">
              {categoryKinds.filter((k) => allSlots.some((s) => s.kind === k.id)).map((kind) => {
                const slots = allSlots.filter((s) => s.kind === kind.id);
                return (
                  <section key={kind.id} className="pb-editor-cat-box">
                    <header className="pb-editor-cat-head px-2 pt-1.5 pb-1">
                      <p className="pb-editor-cat-label">{kind.label}</p>
                      <p className="pb-editor-cat-desc">{kind.description}</p>
                    </header>
                    <div className="px-2 pb-2 pt-1">
                      {slots.map((slot) => {
                        const empty = isSlotEmpty(selection, slot);
                        const blockId = empty ? "" : resolveSlotBlockId(selection, slot);
                        const block = blockId ? blocks.find((b) => b.id === blockId) : null;
                        const agent = slot.kind === "role" && blockId ? agentForBlock(blockId) : null;
                        const active = activeSlotId === slot.slotId;
                        return (
                          <div
                            key={slot.slotId}
                            className={cn(
                              "group flex items-center gap-1 rounded-md py-0.5",
                              active && "pb-glow-soft",
                            )}
                          >
                            <button
                              type="button"
                              onClick={() => onSelectSlot(slot.slotId)}
                              className="min-w-0 flex-1 text-left"
                            >
                              <p className={cn("text-[10px] font-medium", empty ? "pb-text-dim italic" : "pb-text")}>
                                {empty ? "— пусто —" : (agent?.name ?? block?.label)}
                              </p>
                            </button>
                            <button
                              type="button"
                              title="Редактировать"
                              aria-label="Редактировать"
                              onClick={() => onOpenEditor(slot.slotId)}
                              className="pb-icon-btn shrink-0 p-1 opacity-35 hover:opacity-100"
                            >
                              <Pencil className="h-3 w-3" />
                            </button>
                          </div>
                        );
                      })}
                    </div>
                  </section>
                );
              })}
            </div>
          )}
        </div>
      </div>
    </aside>
  );
}

function FieldControl({
  field,
  value,
  onChange,
}: {
  field: OrchestratorField;
  value: string | number | undefined;
  onChange: (key: string, value: string | number) => void;
}) {
  const v = value ?? "";

  return (
    <label className="block">
      <span className="mb-1 flex items-center justify-between text-[9px] pb-text-muted">
        {field.label}
        {field.type === "slider" && <span className="font-mono pb-text">{String(v)}</span>}
      </span>
      {field.type === "slider" && (
        <input
          type="range"
          min={field.min ?? 0}
          max={field.max ?? 100}
          step={field.step ?? 1}
          value={typeof v === "number" ? v : Number(v) || 0}
          onChange={(e) => onChange(field.key, Number(e.target.value))}
          className="pb-range w-full"
        />
      )}
      {field.type === "number" && (
        <input
          type="number"
          min={field.min}
          max={field.max}
          value={v}
          onChange={(e) => onChange(field.key, Number(e.target.value))}
          className="pb-field-input w-full"
        />
      )}
      {field.type === "text" && (
        <input
          type="text"
          value={String(v)}
          placeholder={field.placeholder}
          onChange={(e) => onChange(field.key, e.target.value)}
          className="pb-field-input w-full"
        />
      )}
    </label>
  );
}
