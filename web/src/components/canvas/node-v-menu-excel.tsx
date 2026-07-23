"use client";

import { useEffect, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { FileSpreadsheet, Loader2 } from "lucide-react";
import { api } from "@/lib/api";
import {
  pickDefaultSheetForNode,
  XLSX_PREVIEW_MAX_COLS,
  XLSX_PREVIEW_MAX_ROWS,
  xlsxRowsWithContent,
} from "@/lib/xlsx-sheets";
import { cn } from "@/lib/utils";

/** Компактное превью таблицы под чипами «Мастер-промты». */
export function NodeVMenuExcelPreview({
  open,
  projectId,
  nodeKey,
  nodeType,
  onOpen,
}: {
  open: boolean;
  projectId: number;
  nodeKey?: string | null;
  nodeType: string;
  onOpen: () => void;
}) {
  const project = useQuery({
    queryKey: ["project", projectId],
    queryFn: () => api.getProject(projectId),
    enabled: open,
  });

  const sheetsMeta = useQuery({
    queryKey: ["xlsx-sheets", projectId, nodeKey ?? "live"],
    queryFn: () =>
      api.previewProjectXlsx(projectId, {
        maxRows: 1,
        nodeKey: nodeKey ?? undefined,
      }),
    enabled: open,
  });

  const sheets = sheetsMeta.data?.sheets ?? [];
  const defaultSheet = pickDefaultSheetForNode(nodeType, sheets);
  const [selectedSheet, setSelectedSheet] = useState(defaultSheet);

  useEffect(() => {
    if (defaultSheet) setSelectedSheet(defaultSheet);
  }, [defaultSheet]);

  const sheet = selectedSheet || defaultSheet;
  const hasFile = sheets.length > 0;
  const startRow = 1;

  const preview = useQuery({
    queryKey: ["v-menu-xlsx-preview", projectId, nodeKey ?? "live", sheet],
    queryFn: () =>
      api.previewProjectXlsx(projectId, {
        sheet,
        raw: true,
        maxRows: XLSX_PREVIEW_MAX_ROWS,
        maxCols: XLSX_PREVIEW_MAX_COLS,
        startRow,
        nodeKey: nodeKey ?? undefined,
      }),
    enabled: open && hasFile && Boolean(sheet),
  });

  const rawRows = preview.data?.rows ?? [];
  const contentRows = xlsxRowsWithContent(rawRows);
  // Keep original Excel row numbers: map filtered rows back to raw indices.
  const rowsWithIndex: { excelRow: number; cells: string[] }[] =
    contentRows.length > 0
      ? contentRows.map((cells) => {
          const idx = rawRows.indexOf(cells);
          return { excelRow: startRow + (idx >= 0 ? idx : 0), cells };
        })
      : hasFile
        ? rawRows.slice(0, 5).map((cells, i) => ({ excelRow: startRow + i, cells }))
        : [];
  const planText = nodeType === "plan" ? project.data?.general_plan?.trim() : undefined;
  const loading = sheetsMeta.isLoading || (hasFile && preview.isLoading);
  const sheetEmpty =
    !loading && hasFile && Boolean(sheet) && rowsWithIndex.length === 0 && !planText;
  const truncated =
    rawRows.length >= XLSX_PREVIEW_MAX_ROWS ||
    (rawRows[0]?.length ?? 0) >= XLSX_PREVIEW_MAX_COLS;

  return (
    <div
      className={cn(
        "mb-3 w-full rounded-xl border border-emerald-500/25 bg-emerald-500/5 p-2.5 text-left transition",
        "hover:border-emerald-400/45 hover:bg-emerald-500/10",
      )}
    >
      <button
        type="button"
        onClick={(e) => {
          e.stopPropagation();
          onOpen();
        }}
        className="w-full text-left"
      >
        <div className="mb-1.5 flex items-center gap-2">
          <FileSpreadsheet className="h-3.5 w-3.5 text-emerald-400" />
          <span className="text-[10px] font-medium text-emerald-200">
            {sheet
              ? `Лист «${sheet}»`
              : sheetsMeta.data?.xlsx_snapshot || "project.xlsx"}
          </span>
          {truncated ? (
            <span className="ml-auto text-[8px] text-emerald-300/70">
              ≤{XLSX_PREVIEW_MAX_ROWS}×{XLSX_PREVIEW_MAX_COLS}
            </span>
          ) : null}
        </div>

        {loading && (
          <div className="flex justify-center py-3">
            <Loader2 className="h-4 w-4 animate-spin text-muted-foreground" />
          </div>
        )}

        {!loading && rowsWithIndex.length > 0 && (
          <div className="max-h-[min(40vh,320px)] overflow-auto rounded-md border border-white/10 bg-black/30">
            <table className="min-w-max border-collapse text-[8px]">
              <tbody>
                {rowsWithIndex.map(({ excelRow, cells }) => (
                  <tr key={excelRow} className="border-b border-white/5 last:border-0">
                    <td className="sticky left-0 border-r border-white/10 bg-black/50 px-1 text-muted-foreground">
                      {excelRow}
                    </td>
                    {cells.map((cell, ci) => (
                      <td
                        key={ci}
                        className="min-w-[48px] max-w-[160px] whitespace-pre-wrap px-1 py-0.5 text-muted-foreground"
                      >
                        {cell || "—"}
                      </td>
                    ))}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}

        {!loading && rowsWithIndex.length === 0 && planText && (
          <p className="line-clamp-3 text-[9px] leading-snug text-foreground/80">{planText}</p>
        )}

        {sheetEmpty && (
          <p className="text-[9px] text-muted-foreground">Лист „{sheet}" пуст</p>
        )}

        {!loading && rowsWithIndex.length === 0 && !planText && !sheetEmpty && (
          <p className="text-[9px] text-muted-foreground">
            {hasFile ? "Шаблон Excel — нажмите для просмотра" : "Файл создаётся при открытии"}
          </p>
        )}
      </button>

      {hasFile && sheets.length > 1 && (
        <select
          className="nodrag nopan mt-2 h-6 w-full rounded border border-white/15 bg-black/40 px-1.5 text-[9px] text-emerald-100"
          value={sheet}
          onMouseDown={(e) => e.stopPropagation()}
          onClick={(e) => e.stopPropagation()}
          onChange={(e) => setSelectedSheet(e.target.value)}
        >
          {sheets.map((s) => (
            <option key={s} value={s}>
              {s}
            </option>
          ))}
        </select>
      )}

      {hasFile && (
        <button
          type="button"
          className="nodrag nopan mt-1.5 text-[9px] text-emerald-300/90 underline-offset-2 hover:text-emerald-200 hover:underline"
          onMouseDown={(e) => e.stopPropagation()}
          onClick={(e) => {
            e.stopPropagation();
            onOpen();
          }}
        >
          Открыть полностью
        </button>
      )}
    </div>
  );
}

/** @deprecated use NodeVMenuExcelPreview */
export const NodeVMenuExcelBlock = NodeVMenuExcelPreview;
