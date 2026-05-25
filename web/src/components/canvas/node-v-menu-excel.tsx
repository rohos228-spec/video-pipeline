"use client";

import { useQuery } from "@tanstack/react-query";
import { FileSpreadsheet, Loader2 } from "lucide-react";
import { api } from "@/lib/api";
import type { NodePromptSlot } from "@/lib/node-prompts";
import { pickDefaultSheetForNode, xlsxRowsWithContent } from "@/lib/xlsx-sheets";
import { cn } from "@/lib/utils";

export function NodeVMenuExcelBlock({
  open,
  projectId,
  nodeType,
  excelSlot,
  onOpenExcel,
}: {
  open: boolean;
  projectId: number | null;
  nodeType: string;
  excelSlot: NodePromptSlot;
  onOpenExcel: () => void;
}) {
  const project = useQuery({
    queryKey: ["project", projectId],
    queryFn: () => api.getProject(projectId!),
    enabled: open && projectId != null,
  });

  const sheetsMeta = useQuery({
    queryKey: ["xlsx-sheets", projectId],
    queryFn: () => api.previewProjectXlsx(projectId!, { maxRows: 1 }),
    enabled: open && projectId != null,
  });

  const sheets = sheetsMeta.data?.sheets ?? [];
  const sheet = pickDefaultSheetForNode(nodeType, sheets);
  const hasFile = sheets.length > 0;

  const preview = useQuery({
    queryKey: ["v-menu-xlsx-preview", projectId, sheet],
    queryFn: () =>
      api.previewProjectXlsx(projectId!, {
        sheet,
        raw: true,
        maxRows: 8,
        maxCols: 6,
      }),
    enabled: open && projectId != null && hasFile && Boolean(sheet),
  });

  const contentRows = xlsxRowsWithContent(preview.data?.rows ?? []);
  const rows =
    contentRows.length > 0
      ? contentRows
      : hasFile
        ? (preview.data?.rows ?? []).slice(0, 5)
        : [];
  const planText =
    nodeType === "plan" ? project.data?.general_plan?.trim() : undefined;
  const loading = sheetsMeta.isLoading || (hasFile && preview.isLoading);

  return (
    <button
      type="button"
      onClick={(e) => {
        e.stopPropagation();
        onOpenExcel();
      }}
      className={cn(
        "mb-3 w-full rounded-xl border border-emerald-500/30 bg-emerald-500/10 p-3 text-left transition",
        "hover:border-emerald-400/50 hover:bg-emerald-500/15",
      )}
    >
      <div className="mb-2 flex items-center gap-2">
        <FileSpreadsheet className="h-4 w-4 shrink-0 text-emerald-400" />
        <div className="min-w-0 flex-1">
          <div className="text-[11px] font-semibold text-emerald-100">{excelSlot.title}</div>
          <div className="text-[9px] text-muted-foreground">
            project.xlsx
            {sheet ? ` · лист «${sheet}»` : hasFile ? "" : " · создаётся при открытии…"}
          </div>
        </div>
      </div>

      {loading && (
        <div className="flex items-center justify-center py-4">
          <Loader2 className="h-4 w-4 animate-spin text-muted-foreground" />
        </div>
      )}

      {!loading && rows.length > 0 && (
        <div className="overflow-hidden rounded-lg border border-white/10 bg-black/25">
          <table className="w-full border-collapse text-[8px]">
            <tbody>
              {rows.slice(0, 5).map((row, ri) => (
                <tr key={ri} className="border-b border-white/5 last:border-0">
                  {row.slice(0, 5).map((cell, ci) => (
                    <td key={ci} className="max-w-[72px] truncate px-1.5 py-1 text-muted-foreground">
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
        <div className="max-h-24 overflow-hidden rounded-lg border border-white/10 bg-black/25 p-2">
          <p className="mb-1 text-[8px] uppercase tracking-wide text-muted-foreground">Текст плана</p>
          <p className="line-clamp-4 whitespace-pre-wrap text-[9px] leading-snug text-foreground/85">
            {planText}
          </p>
        </div>
      )}

      {!loading && rows.length === 0 && !planText && (
        <p className="py-2 text-[9px] leading-snug text-muted-foreground">
          {hasFile
            ? "Шаблон Excel загружен — запустите шаг «План» или загрузите файл"
            : "Excel появится после первого шага или загрузки project.xlsx"}
        </p>
      )}

      <p className="mt-2 text-[9px] font-medium text-emerald-300/90">Нажмите для полного просмотра →</p>
    </button>
  );
}
