"use client";

import { useEffect, useState } from "react";
import { createPortal } from "react-dom";
import { useQuery } from "@tanstack/react-query";
import {
  ChevronDown,
  ChevronRight,
  Clapperboard,
  Loader2,
  RefreshCw,
  X,
} from "lucide-react";
import { api } from "@/lib/api";
import type { MontageBoardFrame } from "@/lib/types";
import { cn } from "@/lib/utils";
import { Button } from "@/components/ui/button";

type RowKey =
  | "voiceover"
  | "characters"
  | "image1"
  | "image2"
  | "video1"
  | "video2"
  | "timestamps";

const GRID_ROWS: { key: RowKey; label: string }[] = [
  { key: "voiceover", label: "Закадровый текст" },
  { key: "characters", label: "Персонажи" },
  { key: "image1", label: "Изображение 1" },
  { key: "image2", label: "Изображение 2" },
  { key: "video1", label: "Видео 1" },
  { key: "video2", label: "Видео 2" },
  { key: "timestamps", label: "Таймкоды" },
];

type MediaPreview = {
  url: string;
  kind: "image" | "video";
  label: string;
};

function voiceoverForFrame(fr: MontageBoardFrame): string {
  return (fr.voiceover_excel || fr.voiceover_text || "").trim();
}

function formatTs(sec: number | null | undefined): string {
  if (sec == null || Number.isNaN(sec)) return "—";
  const m = Math.floor(sec / 60);
  const s = sec - m * 60;
  return `${m}:${s.toFixed(2).padStart(5, "0")}`;
}

function MediaLightbox({
  preview,
  onClose,
}: {
  preview: MediaPreview | null;
  onClose: () => void;
}) {
  useEffect(() => {
    if (!preview) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [preview, onClose]);

  if (!preview) return null;

  return createPortal(
    <div
      className="fixed inset-0 z-[10100] flex items-center justify-center bg-black/90 p-4"
      onMouseDown={onClose}
    >
      <button
        type="button"
        className="absolute right-4 top-4 rounded-full bg-black/60 p-2 text-white hover:bg-black/80"
        onClick={onClose}
        aria-label="Закрыть просмотр"
      >
        <X className="h-5 w-5" />
      </button>
      <div
        className="max-h-[92vh] max-w-[96vw]"
        onMouseDown={(e) => e.stopPropagation()}
      >
        {preview.kind === "video" ? (
          <video
            src={preview.url}
            className="max-h-[92vh] max-w-[96vw] rounded-lg"
            controls
            autoPlay
          />
        ) : (
          // eslint-disable-next-line @next/next/no-img-element
          <img
            src={preview.url}
            alt={preview.label}
            className="max-h-[92vh] max-w-[96vw] rounded-lg object-contain"
          />
        )}
        <p className="mt-2 text-center text-sm text-white/80">{preview.label}</p>
      </div>
    </div>,
    document.body,
  );
}

function ClickableMedia({
  url,
  kind,
  label,
  onPreview,
}: {
  url: string | null;
  kind: "image" | "video";
  label: string;
  onPreview: (p: MediaPreview) => void;
}) {
  if (!url) {
    return (
      <div className="flex h-32 w-full items-center justify-center rounded-lg border border-dashed border-white/15 bg-black/20 text-xs text-muted-foreground">
        нет файла
      </div>
    );
  }

  const open = () => onPreview({ url, kind, label });

  if (kind === "video") {
    return (
      <button
        type="button"
        className="group relative block h-32 w-full overflow-hidden rounded-lg border border-white/10 bg-black"
        onClick={open}
        title={`Открыть ${label}`}
      >
        <video
          src={url}
          className="h-full w-full object-cover transition group-hover:brightness-110"
          preload="metadata"
          muted
        />
        <span className="pointer-events-none absolute inset-0 flex items-center justify-center bg-black/25 text-xs font-medium text-white opacity-0 transition group-hover:opacity-100">
          ▶ Открыть
        </span>
      </button>
    );
  }

  return (
    <button
      type="button"
      className="group block h-32 w-full overflow-hidden rounded-lg border border-white/10 bg-black"
      onClick={open}
      title={`Открыть ${label}`}
    >
      {/* eslint-disable-next-line @next/next/no-img-element */}
      <img
        src={url}
        alt={label}
        className="h-full w-full object-cover transition group-hover:scale-[1.02] group-hover:brightness-110"
      />
    </button>
  );
}

function CharactersCell({
  fr,
  onPreview,
}: {
  fr: MontageBoardFrame;
  onPreview: (p: MediaPreview) => void;
}) {
  const refs = fr.character_refs ?? [];
  const [expanded, setExpanded] = useState(false);

  if (refs.length === 0) {
    const fallback = (fr.characters || "").trim();
    return (
      <p className="text-xs leading-snug text-muted-foreground">
        {fallback || "—"}
      </p>
    );
  }

  const visible = expanded ? refs : refs.slice(0, 2);
  const hiddenCount = refs.length - 2;

  return (
    <div className="flex flex-col gap-2">
      <div className="flex flex-wrap gap-2">
        {visible.map((ch) => (
          <button
            key={ch.id}
            type="button"
            className="group flex w-[5.5rem] flex-col items-center gap-1 rounded-lg border border-white/10 bg-black/25 p-1.5 transition hover:border-amber-400/40 hover:bg-black/40"
            onClick={() => {
              if (ch.image_url) {
                onPreview({
                  url: ch.image_url,
                  kind: "image",
                  label: `${ch.name || ch.id} (${ch.id})`,
                });
              }
            }}
            disabled={!ch.image_url}
            title={ch.image_url ? `Открыть ${ch.id}` : `${ch.id} — нет фото`}
          >
            {ch.image_url ? (
              // eslint-disable-next-line @next/next/no-img-element
              <img
                src={ch.image_url}
                alt={ch.name || ch.id}
                className="h-16 w-full rounded-md object-cover transition group-hover:brightness-110"
              />
            ) : (
              <div className="flex h-16 w-full items-center justify-center rounded-md border border-dashed border-white/15 text-[10px] text-muted-foreground">
                нет фото
              </div>
            )}
            <span className="max-w-full truncate font-mono text-[10px] text-amber-200/90">
              {ch.id}
            </span>
            {ch.name && ch.name !== ch.id ? (
              <span className="max-w-full truncate text-[9px] text-muted-foreground">
                {ch.name}
              </span>
            ) : null}
          </button>
        ))}
      </div>
      {!expanded && hiddenCount > 0 ? (
        <button
          type="button"
          className="self-start rounded-md border border-white/15 px-2 py-1 text-[11px] text-muted-foreground transition hover:border-amber-400/40 hover:text-foreground"
          onClick={() => setExpanded(true)}
        >
          Ещё {hiddenCount}
        </button>
      ) : null}
      {expanded && refs.length > 2 ? (
        <button
          type="button"
          className="self-start rounded-md border border-white/15 px-2 py-1 text-[11px] text-muted-foreground transition hover:border-amber-400/40 hover:text-foreground"
          onClick={() => setExpanded(false)}
        >
          Свернуть
        </button>
      ) : null}
    </div>
  );
}

function TimestampCell({ fr }: { fr: MontageBoardFrame }) {
  const voice = voiceoverForFrame(fr);
  return (
    <div className="rounded-lg border border-white/10 bg-black/20 px-2.5 py-2">
      <div className="font-mono text-xs font-semibold text-amber-300">
        {formatTs(fr.start_ts)} → {formatTs(fr.end_ts)}
      </div>
      {fr.duration_seconds != null ? (
        <div className="mt-0.5 font-mono text-[11px] text-muted-foreground">
          {fr.duration_seconds.toFixed(2)} с
        </div>
      ) : null}
      {voice ? (
        <p className="mt-1 line-clamp-3 text-[11px] leading-snug text-foreground/80">
          {voice}
        </p>
      ) : null}
    </div>
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
  const [preview, setPreview] = useState<MediaPreview | null>(null);
  const [collapsedRows, setCollapsedRows] = useState<Set<RowKey>>(new Set());

  const board = useQuery({
    queryKey: ["montage-board", projectId],
    queryFn: () => api.getMontageBoard(projectId!),
    enabled: open && projectId != null,
  });

  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape" && !preview) onClose();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open, onClose, preview]);

  const toggleRow = (key: RowKey) => {
    setCollapsedRows((prev) => {
      const next = new Set(prev);
      if (next.has(key)) next.delete(key);
      else next.add(key);
      return next;
    });
  };

  if (!open || typeof document === "undefined") return null;

  const frames = board.data?.frames ?? [];

  return createPortal(
    <>
      <div className="fixed inset-0 z-[10050] flex flex-col bg-card">
        <header className="flex shrink-0 items-center justify-between border-b border-white/10 px-5 py-4">
          <div className="flex items-center gap-3">
            <Clapperboard className="h-7 w-7 text-amber-400" />
            <div>
              <h2 className="text-base font-semibold">Панель монтажа</h2>
              <p className="text-xs text-muted-foreground">
                Кадры ролика — озвучка, персонажи, медиа и таймкоды
              </p>
            </div>
          </div>
          <div className="flex items-center gap-2">
            <Button
              type="button"
              size="sm"
              variant="outline"
              className="h-9 text-xs"
              disabled={board.isFetching}
              onClick={() => board.refetch()}
            >
              {board.isFetching ? (
                <Loader2 className="h-4 w-4 animate-spin" />
              ) : (
                <RefreshCw className="h-4 w-4" />
              )}
              Обновить
            </Button>
            <button
              type="button"
              className="rounded-md p-2 text-muted-foreground hover:bg-white/10 hover:text-foreground"
              onClick={onClose}
              aria-label="Закрыть"
            >
              <X className="h-5 w-5" />
            </button>
          </div>
        </header>

        <div className="min-h-0 flex-1 overflow-auto p-4">
          {board.isLoading && (
            <div className="flex h-full items-center justify-center">
              <Loader2 className="h-8 w-8 animate-spin text-muted-foreground" />
            </div>
          )}
          {!board.isLoading && board.isError && (
            <p className="text-sm text-destructive">
              Не удалось загрузить данные монтажа.
            </p>
          )}
          {!board.isLoading && !board.isError && frames.length === 0 && (
            <p className="text-sm text-muted-foreground">
              Кадров нет — пройдите шаги сценария и разбивки.
            </p>
          )}

          {frames.length > 0 && (
            <div className="overflow-x-auto">
              <table className="min-w-max border-collapse text-[13px]">
                <thead>
                  <tr>
                    <th className="sticky left-0 z-10 min-w-[11rem] border-b border-r border-white/10 bg-card px-3 py-2 text-left text-xs font-medium text-muted-foreground">
                      Строка
                    </th>
                    {frames.map((fr) => (
                      <th
                        key={fr.frame_id}
                        className="min-w-[11.5rem] border-b border-white/10 px-3 py-2 text-center font-mono text-xs"
                      >
                        #{fr.number}
                      </th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {GRID_ROWS.map((row) => {
                    const collapsed = collapsedRows.has(row.key);
                    return (
                      <tr key={row.key} className="border-b border-white/5">
                        <td className="sticky left-0 z-10 border-r border-white/10 bg-card/95 px-2 py-2 align-top">
                          <button
                            type="button"
                            className={cn(
                              "flex w-full items-center gap-1.5 rounded-md px-2 py-1.5 text-left text-xs font-medium transition",
                              collapsed
                                ? "text-muted-foreground hover:bg-white/5"
                                : "text-foreground hover:bg-white/5",
                            )}
                            onClick={() => toggleRow(row.key)}
                            title={collapsed ? "Развернуть строку" : "Свернуть строку"}
                          >
                            {collapsed ? (
                              <ChevronRight className="h-4 w-4 shrink-0" />
                            ) : (
                              <ChevronDown className="h-4 w-4 shrink-0" />
                            )}
                            <span>{row.label}</span>
                          </button>
                        </td>
                        {frames.map((fr) => (
                          <td
                            key={`${fr.frame_id}-${row.key}`}
                            className="px-3 py-2 align-top"
                          >
                            {collapsed ? (
                              <div className="h-8 rounded-md bg-black/10" />
                            ) : row.key === "voiceover" ? (
                              <p className="max-w-[14rem] whitespace-pre-wrap text-xs leading-snug text-foreground/90">
                                {voiceoverForFrame(fr) || "—"}
                              </p>
                            ) : row.key === "characters" ? (
                              <CharactersCell fr={fr} onPreview={setPreview} />
                            ) : row.key === "timestamps" ? (
                              <TimestampCell fr={fr} />
                            ) : row.key === "image1" ? (
                              <ClickableMedia
                                url={fr.image_shot1_url}
                                kind="image"
                                label={`Изображение 1 · кадр #${fr.number}`}
                                onPreview={setPreview}
                              />
                            ) : row.key === "image2" ? (
                              <ClickableMedia
                                url={fr.image_shot2_url}
                                kind="image"
                                label={`Изображение 2 · кадр #${fr.number}`}
                                onPreview={setPreview}
                              />
                            ) : row.key === "video1" ? (
                              <ClickableMedia
                                url={fr.video_shot1_url}
                                kind="video"
                                label={`Видео 1 · кадр #${fr.number}`}
                                onPreview={setPreview}
                              />
                            ) : (
                              <ClickableMedia
                                url={fr.video_shot2_url}
                                kind="video"
                                label={`Видео 2 · кадр #${fr.number}`}
                                onPreview={setPreview}
                              />
                            )}
                          </td>
                        ))}
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          )}
        </div>
      </div>
      <MediaLightbox preview={preview} onClose={() => setPreview(null)} />
    </>,
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
