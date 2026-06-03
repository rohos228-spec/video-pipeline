"use client";

import { useQuery } from "@tanstack/react-query";
import { FileSpreadsheet, Loader2 } from "lucide-react";
import { api } from "@/lib/api";
import {
  pickDefaultSheetForNode,
  xlsxRowsWithContent,
} from "@/lib/xlsx-sheets";
import { cn } from "@/lib/utils";

/** Компактное превью таблицы под чипами «Мастер-промты». */
export function NodeVMenuExcelPreview({
  open,
  projectId,
  nodeType,
  onOpen,
}: {
  open: boolean;
  projectId: number;
  nodeType: string;
  onOpen: () => void;
}) {
  const project = useQuery({
    queryKey: ["project", projectId],
    queryFn: () => api.getProject(projectId),
    enabled: open,
  });

  const sheetsMeta = useQuery({
    queryKey: ["xlsx-sheets", projectId],
    queryFn: () => api.previewProjectXlsx(projectId, { maxRows: 1 }),
    enabled: open,
  });

  const sheets = sheetsMeta.data?.sheets ?? [];
  const sheet = pickDefaultSheetForNode(nodeType, sheets);
  const hasFile = sheets.length > 0;

  const preview = useQuery({
    queryKey: ["v-menu-xlsx-preview", projectId, sheet],
    queryFn: () =>
      api.previewProjectXlsx(projectId, {
        sheet,
        raw: true,
        maxRows: 500,
        maxCols: 200,
        startRow: 1,
      }),
    enabled: open && hasFile && Boolean(sheet),
  });

  const contentRows = xlsxRowsWithContent(preview.data?.rows ?? []);
  const rows =
    contentRows.length > 0
      ? contentRows
      : hasFile
        ? (preview.data?.rows ?? []).slice(0, 5)
        : [];
  const planText = nodeType === "plan" ? project.data?.general_plan?.trim() : undefined;
  const loading = sheetsMeta.isLoading || (hasFile && preview.isLoading);

  return (
    <button
      type="button"
      onClick={(e) => {
        e.stopPropagation();
        onOpen();
      }}
      className={cn(
        "mb-3 w-full rounded-xl border border-emerald-500/25 bg-emerald-500/5 p-2.5 text-left transition",
        "hover:border-emerald-400/45 hover:bg-emerald-500/10",
      )}
    >
      <div className="mb-1.5 flex items-center gap-2">
        <FileSpreadsheet className="h-3.5 w-3.5 text-emerald-400" />
        <span className="text-[10px] font-medium text-emerald-200">
          {sheet ? `Лист «${sheet}»` : "project.xlsx"}
        </span>
      </div>

      {loading && (
        <div className="flex justify-center py-3">
          <Loader2 className="h-4 w-4 animate-spin text-muted-foreground" />
        </div>
      )}

      {!loading && rows.length > 0 && (
        <div className="max-h-48 overflow-auto rounded-md border border-white/10 bg-black/30">
          <table className="min-w-max border-collapse text-[8px]">
            <tbody>
              {rows.map((row, ri) => (
                <tr key={ri} className="border-b border-white/5 last:border-0">
                  <td className="sticky left-0 border-r border-white/10 bg-black/50 px-1 text-muted-foreground">
                    {ri + 1}
                  </td>
                  {row.map((cell, ci) => (
                    <td
                      key={ci}
                      className="min-w-[48px] max-w-[120px] whitespace-pre-wrap px-1 py-0.5 text-muted-foreground"
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

      {!loading && rows.length === 0 && planText && (
        <p className="line-clamp-3 text-[9px] leading-snug text-foreground/80">{planText}</p>
      )}

      {!loading && rows.length === 0 && !planText && (
        <p className="text-[9px] text-muted-foreground">
          {hasFile ? "Шаблон Excel — нажмите для просмотра" : "Файл создаётся при открытии"}
        </p>
      )}
    </button>
  );
}

/** @deprecated use NodeVMenuExcelPreview */
export const NodeVMenuExcelBlock = NodeVMenuExcelPreview;
