"use client";

import { useState } from "react";
import { Archive, Download, FileText } from "lucide-react";
import { api } from "@/lib/api";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";

/** Панель ноды отчёта — как «Хранилище»: файл на карточке, клик открывает внутри Студии. */
export function ShotsReportPanel({
  projectId,
}: {
  projectId: number;
  nodeKey: string;
}) {
  const [openList, setOpenList] = useState(true);
  const [viewerOpen, setViewerOpen] = useState(false);
  const url = api.projectShotsReportUrl(projectId);

  return (
    <div
      className="nodrag nopan border-t border-sky-500/25 bg-gradient-to-b from-sky-500/10 to-black/20 px-3 py-2.5"
      onMouseDown={(e) => e.stopPropagation()}
      onClick={(e) => e.stopPropagation()}
    >
      <button
        type="button"
        className="flex w-full items-center gap-2.5 text-left"
        onClick={() => setOpenList((v) => !v)}
      >
        <span className="flex h-9 w-9 shrink-0 items-center justify-center rounded-xl border border-sky-400/35 bg-sky-500/20">
          <Archive className="h-4 w-4 text-sky-200" />
        </span>
        <span className="min-w-0 flex-1">
          <span className="block text-[12px] font-semibold tracking-tight text-foreground">
            Отчёт
          </span>
          <span className="mt-0.5 block text-[9px] text-muted-foreground">
            1 файл · клик по имени — открыть внутри
            {" · "}
            {openList ? "свернуть" : "показать"}
          </span>
        </span>
      </button>

      {openList ? (
        <div className="mt-2.5 space-y-2">
          <div className="flex flex-wrap gap-1">
            <Button
              type="button"
              size="sm"
              variant="outline"
              className="h-7 text-[10px]"
              onClick={() => setViewerOpen(true)}
            >
              <FileText className="h-3 w-3" />
              Открыть
            </Button>
            <Button type="button" size="sm" variant="secondary" className="h-7 text-[10px]" asChild>
              <a href={url} download="shots-report.html" title="Скачать HTML">
                <Download className="h-3 w-3" />
                Скачать
              </a>
            </Button>
          </div>

          <div className="rounded-xl border border-sky-400/20 bg-black/40">
            <div className="flex items-center justify-between border-b border-white/8 px-2.5 py-1.5">
              <span className="text-[9px] font-semibold uppercase tracking-wider text-sky-200/90">
                Содержимое
              </span>
              <span className="font-mono text-[9px] text-muted-foreground">1</span>
            </div>
            <ul className="px-1.5 py-1.5">
              <li>
                <button
                  type="button"
                  className="flex w-full cursor-pointer items-start gap-2 rounded-lg border border-white/6 bg-white/[0.03] px-2 py-1.5 text-left transition hover:border-sky-400/35 hover:bg-sky-500/10"
                  title="Открыть отчёт внутри Студии"
                  onClick={() => setViewerOpen(true)}
                >
                  <span className="mt-0.5 flex h-8 w-8 shrink-0 items-center justify-center rounded bg-white/5 font-mono text-[8px] uppercase text-muted-foreground">
                    htm
                  </span>
                  <span className="min-w-0 flex-1">
                    <span className="block break-all font-mono text-[10px] leading-snug text-foreground">
                      shots-report.html
                    </span>
                    <span className="mt-0.5 block text-[9px] text-muted-foreground">
                      HTML · кадры + промты + QC
                    </span>
                  </span>
                </button>
              </li>
            </ul>
          </div>
        </div>
      ) : null}

      <Dialog open={viewerOpen} onOpenChange={setViewerOpen}>
        <DialogContent className="flex h-[92vh] max-h-[92vh] w-[96vw] max-w-[96vw] flex-col gap-0 overflow-hidden bg-black p-0">
          <DialogHeader className="shrink-0 px-4 pb-2 pt-4">
            <DialogTitle>shots-report.html</DialogTitle>
            <DialogDescription>Отчёт кадров · тот же HTML, чёрный фон</DialogDescription>
          </DialogHeader>
          <div className="min-h-0 flex-1 overflow-auto bg-black">
            <iframe
              title="Отчёт кадров"
              src={url}
              className="block h-[calc(92vh-72px)] min-h-[70vh] w-full border-0 bg-black"
            />
          </div>
        </DialogContent>
      </Dialog>
    </div>
  );
}
