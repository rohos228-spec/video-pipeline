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

/** Единая ширина колонок кадров (≈ первый кадр). */
const FRAME_COL_CLASS = "w-[11.5rem] min-w-[11.5rem] max-w-[11.5rem]";
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

function VideoTrimSlider({
  fileDuration,
  sceneUse,
  timelineStart,
  timelineEnd,
  trim,
  onTrimChange,
  shotLabel,
  integratedShot2,
}: {
  fileDuration: number | null;
  sceneUse: number | null;
  timelineStart: number | null;
  timelineEnd: number | null;
  trim: VideoTrim | undefined;
  onTrimChange: (next: VideoTrim) => void;
  shotLabel: string;
  integratedShot2?: boolean;
}) {
  if (sceneUse == null) {
    return (
      <p className="mt-2 text-[10px] text-muted-foreground">
        Нет меток озвучки для расчёта длительности сцены
      </p>
    );
  }

  const fileMax = fileDuration ?? sceneUse;
  const start = trim?.start ?? 0;
  const end = trim?.end ?? Math.min(sceneUse, fileMax);
  const usedLen = Math.max(0, end - start);

  const setStart = (v: number) => {
    const nextStart = Math.max(0, Math.min(v, fileMax - 0.05));
    const nextEnd = Math.max(nextStart + 0.05, end);
    onTrimChange({ start: nextStart, end: Math.min(nextEnd, fileMax) });
  };
  const setEnd = (v: number) => {
    const nextEnd = Math.max(start + 0.05, Math.min(v, fileMax));
    onTrimChange({ start, end: nextEnd });
  };

  return (
    <div className="mt-2 space-y-2 rounded-lg border border-white/10 bg-black/25 p-2">
      <div className="text-[10px] leading-snug text-muted-foreground">
        <div>
          <span className="text-amber-200/90">{shotLabel}</span>
          {integratedShot2 ? (
            <span className="ml-1 text-[9px] text-sky-300/80">· shot 2 в кадре</span>
          ) : null}
        </div>
        <div className="mt-0.5 font-mono text-foreground/90">
          В сцене: {usedLen.toFixed(2)} с из {sceneUse.toFixed(2)} с
        </div>
        {timelineStart != null && timelineEnd != null ? (
          <div className="font-mono text-[9px]">
            Озвучка: {formatTs(timelineStart)} → {formatTs(timelineEnd)}
          </div>
        ) : null}
        {fileDuration != null ? (
          <div className="font-mono text-[9px]">
            Файл: {formatTs(start)} → {formatTs(end)} / {formatTs(fileDuration)}
          </div>
        ) : null}
      </div>
      {fileDuration != null ? (
        <div className="space-y-1.5">
          <label className="flex flex-col gap-0.5 text-[9px] text-muted-foreground">
            Начало в файле
            <input
              type="range"
              min={0}
              max={Math.max(0, fileMax - 0.05)}
              step={0.05}
              value={start}
              className="h-2 w-full accent-amber-500"
              onChange={(e) => setStart(Number(e.target.value))}
            />
          </label>
          <label className="flex flex-col gap-0.5 text-[9px] text-muted-foreground">
            Конец в файле
            <input
              type="range"
              min={0.05}
              max={fileMax}
              step={0.05}
              value={end}
              className="h-2 w-full accent-amber-500"
              onChange={(e) => setEnd(Number(e.target.value))}
            />
          </label>
        </div>
      ) : null}
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
  const tStart = isShot2 ? fr.shot2_timeline_start : fr.shot1_timeline_start;
  const tEnd = isShot2 ? fr.shot2_timeline_end : fr.shot1_timeline_end;
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
        timelineStart={tStart}
        timelineEnd={tEnd}
        trim={trim}
        onTrimChange={onTrimChange}
        shotLabel={`Видео ${shot}`}
        integratedShot2={isShot2 && fr.has_shot2}
      />
      {shot === 1 && fr.has_shot2 && fr.shot2_use_seconds != null ? (
        <p className="mt-1 text-[9px] text-sky-300/70">
          + shot 2: {fr.shot2_use_seconds.toFixed(2)} с (
          {formatTs(fr.shot2_timeline_start)} → {formatTs(fr.shot2_timeline_end)})
        </p>
      ) : null}
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
      {fr.has_shot2 && fr.shot1_use_seconds != null && fr.shot2_use_seconds != null ? (
        <div className="mt-1 space-y-0.5 font-mono text-[9px] text-sky-300/80">
          <div>v1: {fr.shot1_use_seconds.toFixed(2)} с</div>
          <div>v2: {fr.shot2_use_seconds.toFixed(2)} с</div>
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
      out[trimKey(fr.frame_id, shot)] = { start: 0, end: Math.max(0.05, end) };
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
    const col = 11.5 * 16;
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
