"use client";

import { useEffect, useState } from "react";
import { createPortal } from "react-dom";
import { useQuery } from "@tanstack/react-query";
import { Clapperboard, Loader2, RefreshCw, X } from "lucide-react";
import { api } from "@/lib/api";
import type { MontageBoardFrame } from "@/lib/types";
import { cn } from "@/lib/utils";
import { Button } from "@/components/ui/button";

type BoardTab = "grid" | "timestamps" | "voiceover";

const GRID_ROWS: {
  key: keyof MontageBoardFrame | "voiceover_display";
  label: string;
  kind: "text" | "media";
}[] = [
  { key: "voiceover_display", label: "Закадровый текст", kind: "text" },
  { key: "characters", label: "Персонажи", kind: "text" },
  { key: "image_shot1_url", label: "Изображение 1", kind: "media" },
  { key: "image_shot2_url", label: "Изображение 2", kind: "media" },
  { key: "video_shot1_url", label: "Видео 1", kind: "media" },
  { key: "video_shot2_url", label: "Видео 2", kind: "media" },
];

function voiceoverForFrame(fr: MontageBoardFrame): string {
  return (fr.voiceover_excel || fr.voiceover_text || "").trim();
}

function formatTs(sec: number | null | undefined): string {
  if (sec == null || Number.isNaN(sec)) return "—";
  const m = Math.floor(sec / 60);
  const s = sec - m * 60;
  return `${m}:${s.toFixed(2).padStart(5, "0")}`;
}

function MediaCell({
  url,
  kind,
  label,
}: {
  url: string | null;
  kind: "image" | "video";
  label: string;
}) {
  if (!url) {
    return (
      <div className="flex h-24 w-full items-center justify-center rounded-md border border-dashed border-white/15 bg-black/20 text-[9px] text-muted-foreground">
        нет файла
      </div>
    );
  }
  if (kind === "video") {
    return (
      <video
        src={url}
        className="h-24 w-full rounded-md border border-white/10 bg-black object-cover"
        controls
        preload="metadata"
        title={label}
      />
    );
  }
  return (
    // eslint-disable-next-line @next/next/no-img-element
    <img
      src={url}
      alt={label}
      className="h-24 w-full rounded-md border border-white/10 bg-black object-cover"
    />
  );
}

export function AssembleMontageBoard({
  open,
  projectId,
  onClose,
}: {
  open: boolean;
  projectId: number | null;
  onClose: () => void;
}) {
  const [tab, setTab] = useState<BoardTab>("grid");
  const board = useQuery({
    queryKey: ["montage-board", projectId],
    queryFn: () => api.getMontageBoard(projectId!),
    enabled: open && projectId != null,
  });

  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open, onClose]);

  if (!open || typeof document === "undefined") return null;

  const frames = board.data?.frames ?? [];

  return createPortal(
    <div
      className="fixed inset-0 z-[10050] flex items-center justify-center bg-black/70 p-3 backdrop-blur-sm"
      onMouseDown={(e) => {
        if (e.target === e.currentTarget) onClose();
      }}
    >
      <div
        className="nodrag nopan flex h-[min(92vh,880px)] w-[min(96vw,1280px)] flex-col overflow-hidden rounded-2xl border border-white/15 bg-card shadow-2xl"
        onMouseDown={(e) => e.stopPropagation()}
      >
        <header className="flex shrink-0 items-center justify-between border-b border-white/10 px-4 py-3">
          <div className="flex items-center gap-2">
            <Clapperboard className="h-5 w-5 text-amber-400" />
            <div>
              <h2 className="text-sm font-semibold">Панель монтажа</h2>
              <p className="text-[10px] text-muted-foreground">
                Кадры ролика — озвучка, персонажи, shot 1 / shot 2
              </p>
            </div>
          </div>
          <div className="flex items-center gap-2">
            <Button
              type="button"
              size="sm"
              variant="outline"
              className="h-8 text-xs"
              disabled={board.isFetching}
              onClick={() => board.refetch()}
            >
              {board.isFetching ? (
                <Loader2 className="h-3.5 w-3.5 animate-spin" />
              ) : (
                <RefreshCw className="h-3.5 w-3.5" />
              )}
              Обновить
            </Button>
            <button
              type="button"
              className="rounded-md p-1.5 text-muted-foreground hover:bg-white/10 hover:text-foreground"
              onClick={onClose}
              aria-label="Закрыть"
            >
              <X className="h-4 w-4" />
            </button>
          </div>
        </header>

        <div className="flex min-h-0 flex-1">
          {/* Вкладки — вертикальное деление */}
          <nav className="flex w-36 shrink-0 flex-col gap-1 border-r border-white/10 bg-black/20 p-2">
            {(
              [
                ["grid", "Сетка кадров"],
                ["voiceover", "Озвучка"],
                ["timestamps", "Таймкоды"],
              ] as const
            ).map(([id, label]) => (
              <button
                key={id}
                type="button"
                className={cn(
                  "rounded-lg px-2.5 py-2 text-left text-[11px] font-medium transition",
                  tab === id
                    ? "bg-primary/20 text-primary"
                    : "text-muted-foreground hover:bg-white/5 hover:text-foreground",
                )}
                onClick={() => setTab(id)}
              >
                {label}
              </button>
            ))}
          </nav>

          {/* Контент — горизонтальное деление: сетка сверху, таймкоды снизу */}
          <div className="flex min-h-0 min-w-0 flex-1 flex-col">
            <div className="min-h-0 flex-1 overflow-auto p-3">
              {board.isLoading && (
                <div className="flex h-full items-center justify-center">
                  <Loader2 className="h-6 w-6 animate-spin text-muted-foreground" />
                </div>
              )}
              {!board.isLoading && board.isError && (
                <p className="text-sm text-destructive">Не удалось загрузить данные монтажа.</p>
              )}
              {!board.isLoading && !board.isError && frames.length === 0 && (
                <p className="text-sm text-muted-foreground">
                  Кадров нет — пройдите шаги сценария и разбивки.
                </p>
              )}

              {tab === "grid" && frames.length > 0 && (
                <div className="overflow-x-auto">
                  <table className="min-w-max border-collapse text-[10px]">
                    <thead>
                      <tr>
                        <th className="sticky left-0 z-10 min-w-[120px] border-b border-r border-white/10 bg-card px-2 py-1.5 text-left font-medium text-muted-foreground">
                          Строка
                        </th>
                        {frames.map((fr) => (
                          <th
                            key={fr.frame_id}
                            className="min-w-[140px] border-b border-white/10 px-2 py-1.5 text-center font-mono text-[10px]"
                          >
                            #{fr.number}
                          </th>
                        ))}
                      </tr>
                    </thead>
                    <tbody>
                      {GRID_ROWS.map((row) => (
                        <tr key={row.label} className="border-b border-white/5">
                          <td className="sticky left-0 z-10 border-r border-white/10 bg-card/95 px-2 py-2 align-top font-medium text-muted-foreground">
                            {row.label}
                          </td>
                          {frames.map((fr) => (
                            <td key={`${fr.frame_id}-${row.key}`} className="px-2 py-2 align-top">
                              {row.kind === "text" ? (
                                <p className="max-w-[200px] whitespace-pre-wrap leading-snug text-foreground/90">
                                  {row.key === "voiceover_display"
                                    ? voiceoverForFrame(fr) || "—"
                                    : String(fr[row.key as keyof MontageBoardFrame] || "—")}
                                </p>
                              ) : (
                                <MediaCell
                                  url={fr[row.key as keyof MontageBoardFrame] as string | null}
                                  kind={String(row.key).includes("video") ? "video" : "image"}
                                  label={`${row.label} #${fr.number}`}
                                />
                              )}
                            </td>
                          ))}
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}

              {tab === "voiceover" && frames.length > 0 && (
                <div className="grid gap-2 sm:grid-cols-2 lg:grid-cols-3">
                  {frames.map((fr) => (
                    <div
                      key={fr.frame_id}
                      className="rounded-xl border border-white/10 bg-black/20 p-3"
                    >
                      <div className="mb-1 font-mono text-[10px] text-amber-400/90">
                        Кадр #{fr.number}
                      </div>
                      <p className="whitespace-pre-wrap text-[11px] leading-relaxed">
                        {voiceoverForFrame(fr) || "—"}
                      </p>
                    </div>
                  ))}
                </div>
              )}

              {tab === "timestamps" && frames.length > 0 && (
                <table className="w-full border-collapse text-[11px]">
                  <thead>
                    <tr className="border-b border-white/10 text-left text-muted-foreground">
                      <th className="px-2 py-2">Кадр</th>
                      <th className="px-2 py-2">Начало</th>
                      <th className="px-2 py-2">Конец</th>
                      <th className="px-2 py-2">Длительность</th>
                      <th className="px-2 py-2">Озвучка</th>
                    </tr>
                  </thead>
                  <tbody>
                    {frames.map((fr) => (
                      <tr key={fr.frame_id} className="border-b border-white/5">
                        <td className="px-2 py-2 font-mono">#{fr.number}</td>
                        <td className="px-2 py-2 font-mono">{formatTs(fr.start_ts)}</td>
                        <td className="px-2 py-2 font-mono">{formatTs(fr.end_ts)}</td>
                        <td className="px-2 py-2 font-mono">
                          {fr.duration_seconds != null
                            ? `${fr.duration_seconds.toFixed(2)} с`
                            : "—"}
                        </td>
                        <td className="max-w-md truncate px-2 py-2 text-muted-foreground">
                          {voiceoverForFrame(fr) || "—"}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              )}
            </div>

            {/* Нижняя полоса — таймкоды (всегда видна на вкладке «Сетка») */}
            {tab === "grid" && frames.length > 0 && (
              <div className="shrink-0 border-t border-white/10 bg-black/30 px-3 py-2">
                <div className="mb-1 text-[9px] font-semibold uppercase tracking-wider text-muted-foreground">
                  Временные метки закадрового голоса
                </div>
                <div className="flex gap-2 overflow-x-auto pb-1">
                  {frames.map((fr) => (
                    <div
                      key={fr.frame_id}
                      className="shrink-0 rounded-lg border border-white/10 bg-card/80 px-2.5 py-1.5"
                    >
                      <div className="font-mono text-[10px] font-medium text-amber-300">
                        #{fr.number}{" "}
                        {formatTs(fr.start_ts)} → {formatTs(fr.end_ts)}
                      </div>
                      <div className="mt-0.5 max-w-[160px] truncate text-[9px] text-muted-foreground">
                        {voiceoverForFrame(fr) || "—"}
                      </div>
                    </div>
                  ))}
                </div>
              </div>
            )}
          </div>
        </div>
      </div>
    </div>,
    document.body,
  );
}

/** Кнопка над нодой «Сборка» (монтаж). */
export function AssembleMontageTrigger({
  onClick,
  active,
}: {
  onClick: () => void;
  active?: boolean;
}) {
  return (
    <button
      type="button"
      title="Панель монтажа"
      className={cn(
        "nodrag nopan nowheel absolute left-1/2 z-40 flex -translate-x-1/2 items-center gap-1 rounded-full border px-2.5 py-1 text-[10px] font-semibold shadow-md backdrop-blur transition",
        "-top-9",
        active
          ? "border-amber-400/60 bg-amber-500/25 text-amber-100"
          : "border-amber-400/40 bg-amber-500/15 text-amber-200 hover:border-amber-300/70 hover:bg-amber-500/25",
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
      Монтаж
    </button>
  );
}
