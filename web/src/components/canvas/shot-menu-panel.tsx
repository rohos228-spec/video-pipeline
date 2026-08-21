"use client";

import { useMemo } from "react";
import { useQuery } from "@tanstack/react-query";
import { Clapperboard, Loader2 } from "lucide-react";
import { api } from "@/lib/api";
import { cn } from "@/lib/utils";
import { Button } from "@/components/ui/button";

export function ShotMenuTrigger({
  active,
  onClick,
}: {
  active?: boolean;
  onClick: () => void;
}) {
  return (
    <button
      type="button"
      title="Лента меню съёмки"
      className={cn(
        "nodrag nopan nowheel absolute left-1/2 z-40 flex -translate-x-1/2 items-center gap-1 rounded-full border px-2.5 py-1 text-[10px] font-semibold shadow-md backdrop-blur transition",
        "-top-9",
        active
          ? "border-sky-400/60 bg-sky-500/25 text-sky-100"
          : "border-sky-400/40 bg-sky-500/15 text-sky-200 hover:border-sky-300/70 hover:bg-sky-500/25",
      )}
      onPointerDown={(e) => e.stopPropagation()}
      onMouseDown={(e) => e.stopPropagation()}
      onClick={(e) => {
        e.stopPropagation();
        e.preventDefault();
        onClick();
      }}
    >
      <Clapperboard className="h-3.5 w-3.5" />
      Лента
    </button>
  );
}

export function ShotMenuPanel({
  projectId,
  onOpenBoard,
}: {
  projectId: number;
  onOpenBoard: (cellIndex?: number) => void;
}) {
  const q = useQuery({
    queryKey: ["shot-menu", projectId],
    queryFn: () => api.shotMenu(projectId),
    staleTime: 4000,
    refetchInterval: 12_000,
  });

  const cells = q.data?.cells ?? [];
  const summary = q.data?.summary;
  const preview = useMemo(() => cells.slice(0, 6), [cells]);

  return (
    <div
      className="nodrag nopan nowheel border-t border-sky-500/20 bg-sky-500/5 px-3 py-2"
      onMouseDown={(e) => e.stopPropagation()}
      onPointerDown={(e) => e.stopPropagation()}
    >
      {q.isLoading ? (
        <div className="flex items-center gap-2 py-2 text-[11px] text-muted-foreground">
          <Loader2 className="h-3.5 w-3.5 animate-spin" />
          Кадры из БД…
        </div>
      ) : (
        <>
          <p className="text-[10px] leading-snug text-sky-100/80">
            {summary
              ? `${summary.vo_cells} ячеек · ${summary.shots} шотов · ${summary.duration_clock}`
              : "Нет кадров в БД"}
          </p>
          <p className="mt-0.5 text-[9px] text-muted-foreground">
            Меню: ячейка закадра → шоты. Соседний VO не склеивается.
          </p>
          <Button
            type="button"
            size="sm"
            className="mt-2 h-7 w-full text-[11px]"
            onClick={() => onOpenBoard()}
          >
            Открыть ленту
          </Button>
          {preview.length > 0 ? (
            <ul className="mt-2 max-h-36 space-y-0.5 overflow-y-auto">
              {preview.map((cell) => (
                <li key={cell.index}>
                  <button
                    type="button"
                    className="flex w-full items-start gap-1.5 rounded px-1 py-0.5 text-left hover:bg-white/5"
                    onClick={() => onOpenBoard(cell.index)}
                  >
                    <span className="mt-px shrink-0 font-mono text-[9px] text-sky-300/90">
                      {cell.index}
                    </span>
                    <span className="line-clamp-2 text-[10px] leading-snug text-foreground/85">
                      {cell.voiceover || cell.title || "—"}
                    </span>
                  </button>
                </li>
              ))}
            </ul>
          ) : null}
          {cells.length > preview.length ? (
            <button
              type="button"
              className="mt-1 text-[10px] text-sky-200/80 hover:underline"
              onClick={() => onOpenBoard()}
            >
              ещё {cells.length - preview.length} ячеек →
            </button>
          ) : null}
        </>
      )}
    </div>
  );
}
