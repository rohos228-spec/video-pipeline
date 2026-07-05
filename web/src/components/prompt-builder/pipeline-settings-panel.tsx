"use client";

import { cn } from "@/lib/utils";
import {
  ORCHESTRATOR_FIELDS_BY_KIND,
  type OrchestratorField,
} from "@/lib/prompt-builder/orchestrator-vars";
import { cellById, type CellUsageMap } from "@/lib/prompt-builder/excel-cells";
import type { BlockKind } from "@/lib/prompt-builder/types";

export function PipelineSettingsPanel({
  templateLabel,
  activeBlockLabel,
  activeSlotLabel,
  activeSlotKind,
  highlightCellIds,
  activeAgentCount,
  vars,
  onChangeVar,
  selectedCellId,
  usage,
  onClearSelection,
}: {
  templateLabel: string;
  activeBlockLabel?: string;
  activeSlotLabel?: string;
  activeSlotKind?: BlockKind;
  highlightCellIds: string[];
  activeAgentCount?: number;
  vars: Record<string, string | number>;
  onChangeVar: (key: string, value: string | number) => void;
  selectedCellId: string | null;
  usage: CellUsageMap;
  onClearSelection: () => void;
}) {
  const fields = activeSlotKind ? (ORCHESTRATOR_FIELDS_BY_KIND[activeSlotKind] ?? []) : [];
  const selectedCell = selectedCellId ? cellById(selectedCellId) : null;
  const selectedUsage = selectedCellId ? (usage[selectedCellId] ?? []) : [];

  const hasBlock = Boolean(activeBlockLabel && activeSlotKind);
  const hasCell = Boolean(selectedCell);

  return (
    <aside className="flex h-full min-w-0 flex-col border-l border-[var(--pb-border)] bg-[var(--pb-panel)]">
      <div className="shrink-0 border-b border-[var(--pb-border)] px-3 py-2.5">
        <p className="pb-panel-title">Настройки пайплайна</p>
        <p className="mt-1 truncate text-[10px] pb-text-muted">{templateLabel}</p>
      </div>

      <div className="min-h-0 flex-1 overflow-y-auto px-3 py-3">
        {!hasBlock && !hasCell && (
          <p className="text-[10px] leading-relaxed pb-text-muted">
            Выберите блок или ячейку Excel слева на пайплайне — здесь появятся параметры оркестратора и
            список агентов.
          </p>
        )}

        {hasBlock && (
          <section className="pb-settings-section">
            <div className="mb-2 flex items-start justify-between gap-2">
              <div>
                <p className="text-[11px] font-semibold pb-text">{activeBlockLabel}</p>
                <p className="text-[9px] pb-text-muted">{activeSlotLabel}</p>
                {highlightCellIds.length > 0 && (
                  <p className="mt-1 text-[8px] pb-text-dim">
                    {highlightCellIds.length} яч. · {activeAgentCount ?? 0} аг.
                  </p>
                )}
              </div>
              <button type="button" className="pb-icon-btn text-[12px]" onClick={onClearSelection}>
                ×
              </button>
            </div>
            <p className="mb-2 text-[9px] leading-relaxed pb-text-dim">
              Параметры уходят оркестратору. Финальный агент собирается после всех блоков.
            </p>
            <div className="space-y-3">
              {fields.map((f) => (
                <FieldControl key={f.key} field={f} value={vars[f.key]} onChange={onChangeVar} />
              ))}
            </div>
          </section>
        )}

        {hasCell && (
          <section className={cn(hasBlock && "mt-4 border-t border-[var(--pb-border)] pt-4")}>
            <p className="pb-panel-title mb-2">Ячейка Excel</p>
            <p className="text-[11px] font-medium pb-text">
              R{selectedCell!.row} · {selectedCell!.label}
            </p>
            {selectedUsage.length === 0 ? (
              <p className="mt-1 text-[9px] pb-text-dim">Не используется в текущем скелете</p>
            ) : (
              <ul className="mt-2 space-y-1.5">
                {selectedUsage.map((entry) => (
                  <li key={`${entry.blockId}-${entry.slotKind}`} className="pb-settings-agent-row">
                    <p className="text-[10px] font-medium pb-text">
                      {entry.agentName ?? entry.blockLabel}
                    </p>
                    <p className="text-[8px] pb-text-dim">
                      {entry.blockLabel}
                      {entry.slotKind ? ` · ${entry.slotKind}` : ""}
                    </p>
                  </li>
                ))}
              </ul>
            )}
            {selectedUsage.length > 1 && (
              <p className="mt-2 text-[8px] text-amber-400/80">
                {selectedUsage.length} агента воздействуют на эту ячейку
              </p>
            )}
          </section>
        )}
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
