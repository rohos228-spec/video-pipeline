"use client";

import { useMemo, useState } from "react";
import { cn } from "@/lib/utils";
import { EXCEL_SHEETS, groupedCellsForSheet, type CellUsageMap } from "@/lib/prompt-builder/excel-cells";

function ExcelCellChip({
  row,
  label,
  used,
  highlighted,
  selected,
  agentCount,
  title,
  onClick,
}: {
  row: number;
  label: string;
  used: boolean;
  highlighted: boolean;
  selected: boolean;
  agentCount: number;
  title?: string;
  onClick: () => void;
}) {
  return (
    <button
      type="button"
      title={title}
      onClick={onClick}
      className={cn(
        "pb-excel-chip",
        used && "pb-excel-chip-used",
        highlighted && "pb-excel-chip-highlight",
        selected && "pb-excel-chip-selected",
      )}
    >
      <span className="pb-excel-chip-row">{row}</span>
      <span className="pb-excel-chip-label">{label}</span>
      {used && agentCount > 0 && <span className="pb-excel-chip-count">{agentCount}</span>}
    </button>
  );
}

/** Ячейки Excel — часть пайплайна, без отдельной плашки */
export function ExcelCellsInline({
  usage,
  highlightCellIds,
  selectedCellId,
  onSelectCell,
}: {
  usage: CellUsageMap;
  highlightCellIds: string[];
  selectedCellId: string | null;
  onSelectCell: (cellId: string | null) => void;
}) {
  const [sheetId, setSheetId] = useState("plan");
  const highlightSet = useMemo(() => new Set(highlightCellIds), [highlightCellIds]);
  const sections = groupedCellsForSheet(sheetId);

  return (
    <div className="w-[118px] shrink-0 self-start pt-1">
      <div className="mb-2 flex flex-wrap gap-0.5">
        {EXCEL_SHEETS.map((s) => (
          <button
            key={s.id}
            type="button"
            onClick={() => setSheetId(s.id)}
            className={cn(
              "pb-excel-tab px-1 py-0.5 text-[7px]",
              sheetId === s.id && "pb-excel-tab-active",
            )}
          >
            {s.name}
          </button>
        ))}
      </div>

      <div className="space-y-2">
        {sections.map((section) => (
          <section key={section.groupId}>
            <h3 className="mb-0.5 text-[6px] font-bold uppercase tracking-widest pb-text-dim">
              {section.label}
            </h3>
            <div className="flex flex-wrap gap-0.5">
              {section.cells.map((cell) => {
                const entries = usage[cell.id] ?? [];
                const used = entries.length > 0;
                const agentNames = [...new Set(entries.map((e) => e.agentName ?? e.blockLabel))];

                return (
                  <ExcelCellChip
                    key={cell.id}
                    row={cell.row}
                    label={cell.label}
                    used={used}
                    highlighted={highlightSet.has(cell.id)}
                    selected={selectedCellId === cell.id}
                    agentCount={agentNames.length}
                    title={used ? agentNames.join(", ") : cell.label}
                    onClick={() => onSelectCell(selectedCellId === cell.id ? null : cell.id)}
                  />
                );
              })}
            </div>
          </section>
        ))}
      </div>
    </div>
  );
}

/** @deprecated use ExcelCellsInline */
export const ExcelCellsStrip = ExcelCellsInline;
