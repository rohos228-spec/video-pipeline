"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { useQuery } from "@tanstack/react-query";
import {
  ChevronDown,
  ChevronRight,
  Clapperboard,
  Loader2,
  MoreHorizontal,
  RefreshCw,
  Settings2,
  Trash2,
  Upload,
  X,
} from "lucide-react";
import { api } from "@/lib/api";
import type { MontageBoardFrame } from "@/lib/types";
import { cn } from "@/lib/utils";
import { Button } from "@/components/ui/button";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover";
import { NodeStepParamsPanel } from "@/components/studio/node-step-params-panel";

/** Единая ширина колонок кадров (+30% к v215). */
const FRAME_COL_REM = 15;
const FRAME_COL_CLASS = "w-[15rem] min-w-[15rem] max-w-[15rem]";
const ROW_LABEL_CLASS = "w-[11rem] min-w-[11rem] max-w-[11rem]";

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

type VideoTrim = { start: number; end: number };

function trimKey(frameId: number, shot: 1 | 2): string {
  return `${frameId}:${shot}`;
}

function voiceoverForFrame(fr: MontageBoardFrame): string {
  return (fr.voiceover_excel || fr.voiceover_text || "").trim();
}

function formatTs(sec: number | null | undefined): string {
  if (sec == null || Number.isNaN(sec)) return "—";
  const m = Math.floor(sec / 60);
  const s = sec - m * 60;
  return `${m}:${s.toFixed(2).padStart(5, "0")}`;
}

function noopAction(_label: string) {
  /* действия подключим позже */
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
      <div className="max-h-[92vh] max-w-[96vw]" onMouseDown={(e) => e.stopPropagation()}>
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

function MediaActionBar({
  kind,
  onDelete,
  onUpload,
}: {
  kind: "image" | "video";
  onDelete: () => void;
  onUpload: (file: File) => void;
}) {
  const inputRef = useRef<HTMLInputElement>(null);
  const imageActions = [
    "Перегенерация без редакции",
    "Редактировать промт",
    "Перегенерация существующего изображения",
  ] as const;
  const videoActions = ["Перегенерация без редакции", "Редактировать промт"] as const;
  const actions = kind === "image" ? imageActions : videoActions;

  return (
    <div className="mt-2 flex items-center gap-1">
      <DropdownMenu>
        <DropdownMenuTrigger asChild>
          <Button type="button" variant="outline" size="sm" className="h-7 flex-1 px-2 text-[10px]">
            <MoreHorizontal className="mr-1 h-3.5 w-3.5" />
            Действия
          </Button>
        </DropdownMenuTrigger>
        <DropdownMenuContent align="start" className="z-[10060] min-w-[14rem]">
          {actions.map((label) => (
            <DropdownMenuItem key={label} onSelect={() => noopAction(label)}>
              {label}
            </DropdownMenuItem>
          ))}
        </DropdownMenuContent>
      </DropdownMenu>
      <Button
        type="button"
        variant="outline"
        size="icon"
        className="h-7 w-7 shrink-0 text-destructive/80"
        title="Удалить"
        onClick={onDelete}
      >
        <Trash2 className="h-3.5 w-3.5" />
      </Button>
      <Button
        type="button"
        variant="outline"
        size="icon"
        className="h-7 w-7 shrink-0"
        title="Загрузить с компьютера"
        onClick={() => inputRef.current?.click()}
      >
        <Upload className="h-3.5 w-3.5" />
      </Button>
      <input
        ref={inputRef}
        type="file"
        accept={kind === "image" ? "image/*" : "video/*"}
        className="hidden"
        onChange={(e) => {
          const f = e.target.files?.[0];
          if (f) onUpload(f);
          e.target.value = "";
        }}
      />
    </div>
  );
}

function ClickableMedia({
  url,
  kind,
  label,
  onPreview,
  onDelete,
  onUpload,
}: {
  url: string | null;
  kind: "image" | "video";
  label: string;
  onPreview: (p: MediaPreview) => void;
  onDelete: () => void;
  onUpload: (file: File) => void;
}) {
  if (!url) {
    return (
      <div>
        <div className="flex h-32 w-full items-center justify-center rounded-lg border border-dashed border-white/15 bg-black/20 text-xs text-muted-foreground">
          нет файла
        </div>
        <MediaActionBar
          kind={kind}
          onDelete={onDelete}
          onUpload={onUpload}
        />
      </div>
    );
  }

  const open = () => onPreview({ url, kind, label });

  return (
    <div>
      {kind === "video" ? (
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
      ) : (
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
      )}
      <MediaActionBar kind={kind} onDelete={onDelete} onUpload={onUpload} />
    </div>
  );
}

function formatSecShort(sec: number): string {
  if (!Number.isFinite(sec)) return "—";
  return sec.toFixed(1);
}

function clampTrim(
  start: number,
  end: number,
  fileMax: number,
  maxSpan: number,
  minGap = 0.1,
): VideoTrim {
  let s = Math.max(0, start);
  let e = Math.max(s + minGap, end);
  e = Math.min(e, fileMax);
  s = Math.min(s, e - minGap);
  if (e - s > maxSpan) {
    e = s + maxSpan;
  }
  if (e > fileMax) {
    e = fileMax;
    s = Math.max(0, e - maxSpan);
  }
  return { start: s, end: e };
}

function DualRangeSlider({
  fileMax,
  maxSpan,
  trim,
  onTrimChange,
}: {
  fileMax: number;
  maxSpan: number;
  trim: VideoTrim;
  onTrimChange: (next: VideoTrim) => void;
}) {
  const trackRef = useRef<HTMLDivElement>(null);
  const [active, setActive] = useState<"start" | "end" | null>(null);

  const startPct = fileMax > 0 ? (trim.start / fileMax) * 100 : 0;
  const endPct = fileMax > 0 ? (trim.end / fileMax) * 100 : 100;

  const valueFromClientX = useCallback(
    (clientX: number) => {
      const track = trackRef.current;
      if (!track || fileMax <= 0) return 0;
      const rect = track.getBoundingClientRect();
      const ratio = Math.max(0, Math.min(1, (clientX - rect.left) / rect.width));
      return Math.round(ratio * fileMax * 10) / 10;
    },
    [fileMax],
  );

  useEffect(() => {
    if (!active) return;
    const onMove = (e: PointerEvent) => {
      const v = valueFromClientX(e.clientX);
      if (active === "start") {
        onTrimChange(clampTrim(v, trim.end, fileMax, maxSpan));
      } else {
        onTrimChange(clampTrim(trim.start, v, fileMax, maxSpan));
      }
    };
    const onUp = () => setActive(null);
    window.addEventListener("pointermove", onMove);
    window.addEventListener("pointerup", onUp);
    return () => {
      window.removeEventListener("pointermove", onMove);
      window.removeEventListener("pointerup", onUp);
    };
  }, [active, fileMax, maxSpan, onTrimChange, trim.end, trim.start, valueFromClientX]);

  if (fileMax <= 0) return null;

  return (
    <div className="relative mt-2 pt-1">
      <div
        ref={trackRef}
        className="relative h-2 rounded-full bg-white/10"
        role="presentation"
      >
        <div
          className="absolute top-0 h-2 rounded-full bg-amber-500/70"
          style={{ left: `${startPct}%`, width: `${Math.max(0, endPct - startPct)}%` }}
        />
        <button
          type="button"
          className={cn(
            "absolute top-1/2 z-10 h-4 w-4 -translate-x-1/2 -translate-y-1/2 rounded-full border-2 border-amber-400 bg-amber-200 shadow",
            active === "start" && "scale-110",
          )}
          style={{ left: `${startPct}%` }}
          aria-label="Начало фрагмента"
          onPointerDown={(e) => {
            e.preventDefault();
            setActive("start");
          }}
        />
        <button
          type="button"
          className={cn(
            "absolute top-1/2 z-10 h-4 w-4 -translate-x-1/2 -translate-y-1/2 rounded-full border-2 border-amber-400 bg-amber-200 shadow",
            active === "end" && "scale-110",
          )}
          style={{ left: `${endPct}%` }}
          aria-label="Конец фрагмента"
          onPointerDown={(e) => {
            e.preventDefault();
            setActive("end");
          }}
        />
      </div>
    </div>
  );
}

function VideoTrimSlider({
  fileDuration,
  sceneUse,
  trim,
  onTrimChange,
}: {
  fileDuration: number | null;
  sceneUse: number | null;
  trim: VideoTrim | undefined;
  onTrimChange: (next: VideoTrim) => void;
}) {
  if (sceneUse == null) {
    return (
      <p className="mt-2 text-[10px] text-muted-foreground">
        Нет меток озвучки для расчёта длительности сцены
      </p>
    );
  }

  const fileMax = fileDuration ?? sceneUse;
  const current = trim ?? clampTrim(0, Math.min(sceneUse, fileMax), fileMax, sceneUse);
  const usedLen = Math.max(0, current.end - current.start);

  return (
    <div className="mt-2 rounded-lg border border-white/10 bg-black/25 p-2">
      <p className="text-[11px] text-foreground">
        В сцене:{" "}
        <span className="font-mono font-semibold text-white">{formatSecShort(usedLen)}</span>{" "}
        с из{" "}
        <span className="font-mono font-semibold text-white">{formatSecShort(sceneUse)}</span> с
      </p>
      <DualRangeSlider
        fileMax={fileMax}
        maxSpan={sceneUse}
        trim={current}
        onTrimChange={onTrimChange}
      />
    </div>
  );
}

function VideoMediaCell({
  fr,
  shot,
  url,
  onPreview,
  trim,
  onTrimChange,
}: {
  fr: MontageBoardFrame;
  shot: 1 | 2;
  url: string | null;
  onPreview: (p: MediaPreview) => void;
  trim: VideoTrim | undefined;
  onTrimChange: (next: VideoTrim) => void;
}) {
  const isShot2 = shot === 2;
  const sceneUse = isShot2 ? fr.shot2_use_seconds : fr.shot1_use_seconds;
  const fileDur = isShot2 ? fr.video_shot2_duration : fr.video_shot1_duration;
  const label = `Видео ${shot} · кадр #${fr.number}`;

  if (isShot2 && !fr.has_shot2) {
    return (
      <p className="text-xs text-muted-foreground">Второй кадр не задан</p>
    );
  }

  return (
    <div>
      <ClickableMedia
        url={url}
        kind="video"
        label={label}
        onPreview={onPreview}
        onDelete={() => noopAction(`delete video ${shot}`)}
        onUpload={(file) => noopAction(`upload video ${shot}: ${file.name}`)}
      />
      <VideoTrimSlider
        fileDuration={fileDur}
        sceneUse={sceneUse}
        trim={trim}
        onTrimChange={onTrimChange}
      />
    </div>
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
      <p className="text-xs leading-snug text-muted-foreground">{fallback || "—"}</p>
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
  return (
    <div className="rounded-lg border border-white/10 bg-black/20 px-2.5 py-2">
      <div className="font-mono text-xs font-semibold text-amber-300">
        {formatTs(fr.start_ts)} → {formatTs(fr.end_ts)}
      </div>
      {fr.duration_seconds != null ? (
        <div className="mt-0.5 font-mono text-[11px] text-muted-foreground">
          {fr.duration_seconds.toFixed(1)} с
        </div>
      ) : null}
    </div>
  );
}

function buildDefaultTrims(frames: MontageBoardFrame[]): Record<string, VideoTrim> {
  const out: Record<string, VideoTrim> = {};
  for (const fr of frames) {
    for (const shot of [1, 2] as const) {
      if (shot === 2 && !fr.has_shot2) continue;
      const use = shot === 1 ? fr.shot1_use_seconds : fr.shot2_use_seconds;
      const file = shot === 1 ? fr.video_shot1_duration : fr.video_shot2_duration;
      if (use == null) continue;
      const fileMax = file ?? use;
      const end = Math.min(use, fileMax);
      out[trimKey(fr.frame_id, shot)] = clampTrim(0, end, fileMax, use);
    }
  }
  return out;
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
  const [trims, setTrims] = useState<Record<string, VideoTrim>>({});
  const [extrasOpen, setExtrasOpen] = useState(false);

  const contentScrollRef = useRef<HTMLDivElement>(null);
  const tableScrollRef = useRef<HTMLDivElement>(null);
  const hBarRef = useRef<HTMLDivElement>(null);
  const syncingScroll = useRef(false);

  const board = useQuery({
    queryKey: ["montage-board", projectId],
    queryFn: () => api.getMontageBoard(projectId!),
    enabled: open && projectId != null,
  });

  const frames = board.data?.frames ?? [];

  useEffect(() => {
    if (frames.length > 0) {
      setTrims(buildDefaultTrims(frames));
    }
  }, [board.dataUpdatedAt, frames.length]);

  const tableWidthPx = useMemo(() => {
    const rowLabel = 11 * 16;
    const col = FRAME_COL_REM * 16;
    return rowLabel + frames.length * col;
  }, [frames.length]);

  const syncScrollLeft = useCallback((from: HTMLDivElement, to: HTMLDivElement) => {
    if (syncingScroll.current) return;
    syncingScroll.current = true;
    to.scrollLeft = from.scrollLeft;
    requestAnimationFrame(() => {
      syncingScroll.current = false;
    });
  }, []);

  useEffect(() => {
    const tableWrap = tableScrollRef.current;
    const hBar = hBarRef.current;
    if (!tableWrap || !hBar) return;

    const onTable = () => syncScrollLeft(tableWrap, hBar);
    const onBar = () => syncScrollLeft(hBar, tableWrap);

    tableWrap.addEventListener("scroll", onTable);
    hBar.addEventListener("scroll", onBar);
    return () => {
      tableWrap.removeEventListener("scroll", onTable);
      hBar.removeEventListener("scroll", onBar);
    };
  }, [frames.length, syncScrollLeft]);

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

  const updateTrim = (key: string, next: VideoTrim) => {
    setTrims((prev) => ({ ...prev, [key]: next }));
  };

  if (!open || typeof document === "undefined") return null;

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
              variant="default"
              className="h-9 text-xs"
              onClick={() => noopAction("apply edits")}
            >
              Применить правки
            </Button>
            <Button
              type="button"
              size="sm"
              variant="outline"
              className="h-9 gap-1.5 text-xs"
              onClick={() => noopAction("montage run")}
            >
              <Clapperboard className="h-4 w-4" />
              Монтаж
            </Button>
            <Popover open={extrasOpen} onOpenChange={setExtrasOpen}>
              <PopoverTrigger asChild>
                <Button type="button" size="sm" variant="outline" className="h-9 gap-1.5 text-xs">
                  <Settings2 className="h-4 w-4" />
                  Доп. функции
                </Button>
              </PopoverTrigger>
              <PopoverContent
                align="end"
                className="z-[10060] max-h-[min(80vh,640px)] w-[min(96vw,420px)] overflow-y-auto p-3"
              >
                <h3 className="mb-3 text-sm font-semibold">Настройки сборки</h3>
                {projectId != null ? (
                  <NodeStepParamsPanel projectId={projectId} nodeType="assemble" />
                ) : (
                  <p className="text-xs text-muted-foreground">Проект не выбран</p>
                )}
              </PopoverContent>
            </Popover>
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

        <div ref={contentScrollRef} className="min-h-0 flex-1 overflow-y-auto overflow-x-hidden">
          <div className="p-4 pb-2">
            {board.isLoading && (
              <div className="flex h-40 items-center justify-center">
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
              <div
                ref={tableScrollRef}
                className="overflow-x-auto overflow-y-visible [-ms-overflow-style:none] [scrollbar-width:none] [&::-webkit-scrollbar]:hidden"
              >
                <table
                  className="border-collapse text-[13px]"
                  style={{ width: tableWidthPx, tableLayout: "fixed" }}
                >
                  <thead>
                    <tr>
                      <th
                        className={cn(
                          "sticky left-0 z-10 border-b border-r border-white/10 bg-card px-3 py-2 text-left text-xs font-medium text-muted-foreground",
                          ROW_LABEL_CLASS,
                        )}
                      >
                        Строка
                      </th>
                      {frames.map((fr) => (
                        <th
                          key={fr.frame_id}
                          className={cn(
                            "border-b border-white/10 px-3 py-2 text-center font-mono text-xs",
                            FRAME_COL_CLASS,
                          )}
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
                          <td
                            className={cn(
                              "sticky left-0 z-10 border-r border-white/10 bg-card/95 px-2 py-2 align-top",
                              ROW_LABEL_CLASS,
                            )}
                          >
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
                              className={cn("px-3 py-2 align-top", FRAME_COL_CLASS)}
                            >
                              {collapsed ? (
                                <div className="h-8 rounded-md bg-black/10" />
                              ) : row.key === "voiceover" ? (
                                <p className="whitespace-pre-wrap text-xs leading-snug text-foreground/90">
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
                                  onDelete={() => noopAction("delete image 1")}
                                  onUpload={(file) => noopAction(`upload image 1: ${file.name}`)}
                                />
                              ) : row.key === "image2" ? (
                                <ClickableMedia
                                  url={fr.image_shot2_url}
                                  kind="image"
                                  label={`Изображение 2 · кадр #${fr.number}`}
                                  onPreview={setPreview}
                                  onDelete={() => noopAction("delete image 2")}
                                  onUpload={(file) => noopAction(`upload image 2: ${file.name}`)}
                                />
                              ) : row.key === "video1" ? (
                                <VideoMediaCell
                                  fr={fr}
                                  shot={1}
                                  url={fr.video_shot1_url}
                                  onPreview={setPreview}
                                  trim={trims[trimKey(fr.frame_id, 1)]}
                                  onTrimChange={(t) =>
                                    updateTrim(trimKey(fr.frame_id, 1), t)
                                  }
                                />
                              ) : (
                                <VideoMediaCell
                                  fr={fr}
                                  shot={2}
                                  url={fr.video_shot2_url}
                                  onPreview={setPreview}
                                  trim={trims[trimKey(fr.frame_id, 2)]}
                                  onTrimChange={(t) =>
                                    updateTrim(trimKey(fr.frame_id, 2), t)
                                  }
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

        {frames.length > 0 && (
          <footer className="shrink-0 border-t border-white/10 bg-card px-4 py-2">
            <div
              ref={hBarRef}
              className="overflow-x-auto overflow-y-hidden"
              aria-label="Горизонтальная прокрутка таблицы"
            >
              <div style={{ width: tableWidthPx, height: 14 }} className="shrink-0" />
            </div>
          </footer>
        )}
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
