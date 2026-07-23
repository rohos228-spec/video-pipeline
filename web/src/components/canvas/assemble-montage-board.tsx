"use client";

import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
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
import { toast } from "sonner";
import { api, subscribeWS, type MontagePendingOp } from "@/lib/api";
import { errorMessageFromUnknown } from "@/lib/error-message";
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

type PromptModalState = {
  kind: "image" | "video";
  frameNumber: number;
  shot: 1 | 2;
  title: string;
  initialText: string;
  mode: "prompt" | "correction";
} | null;

function trimKey(frameNumber: number, shot: 1 | 2): string {
  return `${frameNumber}:${shot}`;
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

function PromptModal({
  state,
  onClose,
  onSubmit,
  busy,
}: {
  state: PromptModalState;
  onClose: () => void;
  onSubmit: (text: string) => void;
  busy: boolean;
}) {
  if (!state) return null;
  // key — remount с исходным промптом сразу в textarea (не пустой useState + useEffect).
  return (
    <PromptModalBody
      key={`${state.kind}:${state.frameNumber}:${state.shot}:${state.mode}`}
      state={state}
      onClose={onClose}
      onSubmit={onSubmit}
      busy={busy}
    />
  );
}

function PromptModalBody({
  state,
  onClose,
  onSubmit,
  busy,
}: {
  state: NonNullable<PromptModalState>;
  onClose: () => void;
  onSubmit: (text: string) => void;
  busy: boolean;
}) {
  const [text, setText] = useState(state.initialText);

  return createPortal(
    <div
      className="fixed inset-0 z-[10110] flex items-center justify-center bg-black/70 p-4"
      onMouseDown={onClose}
    >
      <div
        className="w-full max-w-lg rounded-xl border border-white/15 bg-card p-4 shadow-xl"
        onMouseDown={(e) => e.stopPropagation()}
      >
        <h3 className="text-sm font-semibold">{state.title}</h3>
        <p className="mt-1 text-[11px] text-muted-foreground">
          Исходный промт кадра — отредактируйте и поставьте в очередь.
        </p>
        <textarea
          className="mt-3 min-h-[160px] w-full rounded-lg border border-white/15 bg-black/30 p-3 text-sm"
          value={text}
          onChange={(e) => setText(e.target.value)}
          placeholder="Промт исходника не найден — вставьте текст вручную…"
          autoFocus
        />
        <div className="mt-3 flex justify-end gap-2">
          <Button type="button" variant="outline" size="sm" onClick={onClose} disabled={busy}>
            Отмена
          </Button>
          <Button
            type="button"
            size="sm"
            disabled={busy || !text.trim()}
            onClick={() => onSubmit(text.trim())}
          >
            {busy ? <Loader2 className="h-4 w-4 animate-spin" /> : "В очередь"}
          </Button>
        </div>
      </div>
    </div>,
    document.body,
  );
}

function MontageMediaExtras({
  onVoiceUpload,
  onMusicUpload,
}: {
  onVoiceUpload: (file: File) => void;
  onMusicUpload: (file: File) => void;
}) {
  const voiceRef = useRef<HTMLInputElement>(null);
  const musicRef = useRef<HTMLInputElement>(null);

  return (
    <div className="mb-4 space-y-2 rounded-lg border border-white/10 bg-black/20 p-3">
      <p className="text-xs font-medium text-foreground">Замена озвучки и музыки</p>
      <p className="text-[11px] leading-snug text-muted-foreground">
        Сохраняются как <code className="text-[10px]">audio/voice_full.*</code> и{" "}
        <code className="text-[10px]">music/bgm.*</code> (имена для монтажа).
      </p>
      <div className="flex flex-wrap gap-2">
        <Button
          type="button"
          variant="outline"
          size="sm"
          className="h-8 text-xs"
          onClick={() => voiceRef.current?.click()}
        >
          <Upload className="mr-1 h-3.5 w-3.5" />
          Голос с компьютера
        </Button>
        <Button
          type="button"
          variant="outline"
          size="sm"
          className="h-8 text-xs"
          onClick={() => musicRef.current?.click()}
        >
          <Upload className="mr-1 h-3.5 w-3.5" />
          Музыка с компьютера
        </Button>
      </div>
      <input
        ref={voiceRef}
        type="file"
        accept="audio/*"
        className="hidden"
        onChange={(e) => {
          const f = e.target.files?.[0];
          if (f) onVoiceUpload(f);
          e.target.value = "";
        }}
      />
      <input
        ref={musicRef}
        type="file"
        accept="audio/*"
        className="hidden"
        onChange={(e) => {
          const f = e.target.files?.[0];
          if (f) onMusicUpload(f);
          e.target.value = "";
        }}
      />
    </div>
  );
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
  onRegen,
  onEditPrompt,
  onRegenWithCorrection,
  onDelete,
  onUpload,
}: {
  kind: "image" | "video";
  onRegen: () => void;
  onEditPrompt: () => void;
  onRegenWithCorrection?: () => void;
  onDelete: () => void;
  onUpload: (file: File) => void;
}) {
  const inputRef = useRef<HTMLInputElement>(null);
  const imageActions = [
    { label: "Перегенерация без редакции", action: onRegen },
    { label: "Редактировать промт", action: onEditPrompt },
    ...(onRegenWithCorrection
      ? [{ label: "Перегенерация существующего изображения", action: onRegenWithCorrection }]
      : []),
  ];
  const videoActions = [
    { label: "Перегенерация без редакции", action: onRegen },
    { label: "Редактировать промт", action: onEditPrompt },
  ];
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
          {actions.map((item) => (
            <DropdownMenuItem key={item.label} onSelect={item.action}>
              {item.label}
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
  onRegen,
  onEditPrompt,
  onRegenWithCorrection,
  onDelete,
  onUpload,
  highlighted,
  stale,
}: {
  url: string | null;
  kind: "image" | "video";
  label: string;
  onPreview: (p: MediaPreview) => void;
  onRegen: () => void;
  onEditPrompt: () => void;
  onRegenWithCorrection?: () => void;
  onDelete: () => void;
  onUpload: (file: File) => void;
  highlighted?: boolean;
  stale?: boolean;
}) {
  const hostRef = useRef<HTMLDivElement>(null);
  // Не монтировать сотни <video>/<img> сразу — Chrome зависает на 150×2 клипах.
  const [inView, setInView] = useState(false);

  useEffect(() => {
    const el = hostRef.current;
    if (!el || !url) return;
    const io = new IntersectionObserver(
      (entries) => {
        if (entries.some((e) => e.isIntersecting)) setInView(true);
      },
      { root: null, rootMargin: "180px 240px", threshold: 0.01 },
    );
    io.observe(el);
    return () => io.disconnect();
  }, [url]);

  if (!url) {
    return (
      <div className={cn(highlighted && "ring-2 ring-emerald-400/60 rounded-lg")}>
        <div className="flex h-32 w-full items-center justify-center rounded-lg border border-dashed border-white/15 bg-black/20 text-xs text-muted-foreground">
          нет файла
        </div>
        <MediaActionBar
          kind={kind}
          onRegen={onRegen}
          onEditPrompt={onEditPrompt}
          onRegenWithCorrection={onRegenWithCorrection}
          onDelete={onDelete}
          onUpload={onUpload}
        />
      </div>
    );
  }

  const open = () => onPreview({ url, kind, label });

  return (
    <div
      ref={hostRef}
      className={cn(
        "rounded-lg",
        highlighted && "ring-2 ring-emerald-400/60",
        stale && "ring-2 ring-amber-500/50",
      )}
    >
      {kind === "video" ? (
        <button
          type="button"
          className="group relative block h-32 w-full overflow-hidden rounded-lg border border-white/10 bg-black"
          onClick={open}
          title={`Открыть ${label}`}
        >
          {inView ? (
            <video
              src={url}
              className="h-full w-full object-cover transition group-hover:brightness-110"
              preload="none"
              muted
              playsInline
            />
          ) : (
            <div className="flex h-full w-full items-center justify-center bg-black/40 text-2xl text-white/50">
              ▶
            </div>
          )}
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
          {inView ? (
            /* eslint-disable-next-line @next/next/no-img-element */
            <img
              src={url}
              alt={label}
              loading="lazy"
              decoding="async"
              className="h-full w-full object-cover transition group-hover:scale-[1.02] group-hover:brightness-110"
            />
          ) : (
            <div className="h-full w-full bg-black/30" />
          )}
        </button>
      )}
      <MediaActionBar
        kind={kind}
        onRegen={onRegen}
        onEditPrompt={onEditPrompt}
        onRegenWithCorrection={onRegenWithCorrection}
        onDelete={onDelete}
        onUpload={onUpload}
      />
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
  moved: "start" | "end",
  minGap = 0.1,
): VideoTrim {
  let s = Math.max(0, Math.min(start, fileMax));
  let e = Math.max(0, Math.min(end, fileMax));
  if (moved === "start") {
    s = Math.min(s, fileMax - minGap);
    if (e < s + minGap) e = s + minGap;
    if (e - s > maxSpan) e = Math.min(fileMax, s + maxSpan);
  } else {
    e = Math.max(minGap, e);
    if (e < s + minGap) s = e - minGap;
    if (e - s > maxSpan) s = Math.max(0, e - maxSpan);
  }
  s = Math.max(0, Math.min(s, fileMax - minGap));
  e = Math.max(s + minGap, Math.min(e, fileMax));
  if (e - s > maxSpan) {
    if (moved === "start") e = Math.min(fileMax, s + maxSpan);
    else s = Math.max(0, e - maxSpan);
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
        onTrimChange(clampTrim(v, trim.end, fileMax, maxSpan, "start"));
      } else {
        onTrimChange(clampTrim(trim.start, v, fileMax, maxSpan, "end"));
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
    <div className="relative isolate mt-2 overflow-hidden pt-1">
      <div
        ref={trackRef}
        className="relative mx-2 h-2 rounded-full bg-white/10"
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
  const current = trim ?? clampTrim(0, Math.min(sceneUse, fileMax), fileMax, sceneUse, "end");
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
  onRegen,
  onEditPrompt,
  onDelete,
  onUpload,
  highlighted,
  stale,
}: {
  fr: MontageBoardFrame;
  shot: 1 | 2;
  url: string | null;
  onPreview: (p: MediaPreview) => void;
  trim: VideoTrim | undefined;
  onTrimChange: (next: VideoTrim) => void;
  onRegen: () => void;
  onEditPrompt: () => void;
  onDelete: () => void;
  onUpload: (file: File) => void;
  highlighted?: boolean;
  stale?: boolean;
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
        onRegen={onRegen}
        onEditPrompt={onEditPrompt}
        onDelete={onDelete}
        onUpload={onUpload}
        highlighted={highlighted}
        stale={stale}
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
      out[trimKey(fr.number, shot)] = clampTrim(0, end, fileMax, use, "end");
    }
  }
  return out;
}

function mergeTrimsFromMeta(
  frames: MontageBoardFrame[],
  metaTrims: Record<string, { start: number; end: number }>,
): Record<string, VideoTrim> {
  const defaults = buildDefaultTrims(frames);
  const merged = { ...defaults };
  for (const [key, t] of Object.entries(metaTrims)) {
    if (t && typeof t.start === "number" && typeof t.end === "number") {
      merged[key] = { start: t.start, end: t.end };
    }
  }
  return merged;
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
  const queryClient = useQueryClient();
  const [preview, setPreview] = useState<MediaPreview | null>(null);
  const [collapsedRows, setCollapsedRows] = useState<Set<RowKey>>(new Set());
  const [trims, setTrims] = useState<Record<string, VideoTrim>>({});
  const [pendingOps, setPendingOps] = useState<MontagePendingOp[]>([]);
  const [promptModal, setPromptModal] = useState<PromptModalState>(null);
  const [extrasOpen, setExtrasOpen] = useState(false);
  const [highlights, setHighlights] = useState<string[]>([]);
  const [staleVideos, setStaleVideos] = useState<string[]>([]);
  const [montageRunning, setMontageRunning] = useState(false);
  const [applyRunning, setApplyRunning] = useState(false);
  const [recoverRunning, setRecoverRunning] = useState(false);
  const [applyProgress, setApplyProgress] = useState<{ done: number; total: number } | null>(
    null,
  );
  const pendingOpsRef = useRef<MontagePendingOp[]>([]);
  pendingOpsRef.current = pendingOps;
  /** Пользователь набрал очередь локально — не затирать пустым meta с сервера. */
  const localQueueDirtyRef = useRef(false);
  /** Мы сами отправили apply (started) — можно чистить/синхронизировать очередь. */
  const submittedApplyRef = useRef(false);
  const trimsDirtyRef = useRef(false);
  const lastApplyToastKeyRef = useRef("");
  const lastMontageToastKeyRef = useRef("");
  const lastRecoverToastKeyRef = useRef("");

  const contentScrollRef = useRef<HTMLDivElement>(null);
  const tableScrollRef = useRef<HTMLDivElement>(null);
  const hBarRef = useRef<HTMLDivElement>(null);

  const board = useQuery({
    queryKey: ["montage-board", projectId],
    queryFn: () => api.getMontageBoard(projectId!),
    enabled: open && projectId != null,
    retry: 2,
    retryDelay: (n) => Math.min(1000 * 2 ** n, 4000),
    // Не долбить API+ffprobe при каждом открытии панели (150+ клипов).
    refetchOnMount: true,
    staleTime: 60_000,
  });

  const frames = board.data?.frames ?? [];
  const meta = board.data?.meta;
  const pendingOpsKey = JSON.stringify(meta?.pending_ops ?? []);

  const parsePendingOps = useCallback((raw: unknown): MontagePendingOp[] => {
    if (!Array.isArray(raw)) return [];
    const restored: MontagePendingOp[] = [];
    for (const op of raw) {
      if (!op || typeof op !== "object") continue;
      const rec = op as Record<string, unknown>;
      const t = String(rec.type || "");
      if (
        t !== "image_regen" &&
        t !== "image_regen_prompt" &&
        t !== "image_regen_correction" &&
        t !== "video_regen" &&
        t !== "video_regen_prompt"
      ) {
        continue;
      }
      const frameNumber = Number(rec.frame_number);
      if (!Number.isFinite(frameNumber) || frameNumber < 1) continue;
      const shot = rec.shot === 2 ? 2 : 1;
      restored.push({
        type: t,
        frame_number: frameNumber,
        shot,
        prompt: typeof rec.prompt === "string" ? rec.prompt : undefined,
        correction: typeof rec.correction === "string" ? rec.correction : undefined,
      });
    }
    return restored;
  }, []);

  useEffect(() => {
    if (frames.length === 0) return;
    // Ждём settled fetch — иначе stale meta восстанавливает уже сделанные ops.
    if (board.isFetching) return;

    setHighlights(meta?.highlights ?? []);
    setStaleVideos(meta?.stale_videos ?? []);
    if (!trimsDirtyRef.current) {
      setTrims(mergeTrimsFromMeta(frames, meta?.video_trims ?? {}));
    }

    if (applyRunning) return;

    const restored = parsePendingOps(meta?.pending_ops);
    if (localQueueDirtyRef.current && restored.length === 0) {
      // Локальная очередь пользователя важнее пустого meta.
      return;
    }
    const nextKey = JSON.stringify(restored);
    if (nextKey === JSON.stringify(pendingOpsRef.current)) return;
    pendingOpsRef.current = restored;
    setPendingOps(restored);
    localQueueDirtyRef.current = false;
  }, [
    board.dataUpdatedAt,
    board.isFetching,
    frames.length,
    meta?.video_trims,
    meta?.highlights,
    meta?.stale_videos,
    pendingOpsKey,
    applyRunning,
    parsePendingOps,
  ]);

  const queueOp = useCallback((op: MontagePendingOp) => {
    localQueueDirtyRef.current = true;
    setPendingOps((prev) => {
      const next = [...prev, op];
      pendingOpsRef.current = next;
      return next;
    });
    toast.message("Операция в очереди — нажмите «Применить правки»");
  }, []);

  const applyMutation = useMutation({
    mutationFn: () => {
      const ops = pendingOpsRef.current;
      if (ops.length > 0) {
        toast.message(`Генерация: ${ops.length} операций… (Chrome :29229, outsee.io)`);
      }
      return api.applyMontageBoard(projectId!, {
        video_trims: trims,
        pending_ops: ops,
      });
    },
    onSuccess: (res) => {
      const queued = pendingOpsRef.current.length;
      if (res.started) {
        submittedApplyRef.current = true;
        localQueueDirtyRef.current = false;
        trimsDirtyRef.current = false;
        setPendingOps([]);
        pendingOpsRef.current = [];
        setApplyRunning(true);
        lastApplyToastKeyRef.current = "";
        toast.message(res.message || `Генерация ${queued} операций… смотрите outsee.io`);
        return;
      }
      if (res.already_running) {
        // Чужой/текущий job — НЕ чистим локальную очередь пользователя.
        setApplyRunning(true);
        toast.message("Генерация уже выполняется");
        return;
      }
      submittedApplyRef.current = false;
      localQueueDirtyRef.current = false;
      trimsDirtyRef.current = false;
      setPendingOps([]);
      pendingOpsRef.current = [];
      if (res.meta) {
        setHighlights(res.meta.highlights ?? []);
        setStaleVideos(res.meta.stale_videos ?? []);
        if (res.meta.video_trims) {
          setTrims(mergeTrimsFromMeta(frames, res.meta.video_trims));
        }
        const restored = parsePendingOps(res.meta.pending_ops);
        if (restored.length > 0) {
          pendingOpsRef.current = restored;
          setPendingOps(restored);
        }
      }
      void queryClient.invalidateQueries({ queryKey: ["montage-board", projectId] });
      if (!res.ok && res.errors?.length) {
        toast.error(res.errors.join("; "));
        return;
      }
      if (queued === 0) {
        toast.success("Trim сохранён");
      } else {
        toast.success("Генерация завершена");
      }
    },
    onError: (e) => toast.error(errorMessageFromUnknown(e)),
  });

  const montageMutation = useMutation({
    mutationFn: () => api.runMontageBoard(projectId!),
    onSuccess: (res) => {
      if (res.already_running) {
        toast.message("Монтаж уже выполняется");
        setMontageRunning(true);
        return;
      }
      if (res.started) {
        lastMontageToastKeyRef.current = "";
        setMontageRunning(true);
        toast.message("Монтаж запущен в фоне");
      }
    },
    onError: (e) => toast.error(errorMessageFromUnknown(e)),
  });

  const recoverOutseeMutation = useMutation({
    mutationFn: () => api.recoverMontageFromOutsee(projectId!),
    onSuccess: (res) => {
      if (res.started || res.already_running || res.job?.status === "running") {
        lastRecoverToastKeyRef.current = "";
        setRecoverRunning(true);
        toast.message(res.message || "Забираем правки из Outsee…");
        return;
      }
      // Совместимость со старым синхронным ответом (если бэкенд ещё не обновлён).
      const n = res.saved_count ?? res.saved?.length ?? 0;
      if (n > 0) {
        toast.success(`Забрано и заменено из Outsee: ${n} кадр(ов)`);
        localQueueDirtyRef.current = false;
        setPendingOps([]);
      } else if (res.errors?.length) {
        toast.error(res.errors.join("; "));
      } else {
        toast.message(
          `В истории Outsee нет карточек для выделенных правок (просмотрено ${res.hits_scanned ?? 0})`,
        );
      }
      void queryClient.invalidateQueries({ queryKey: ["montage-board", projectId] });
    },
    onError: (e) => toast.error(errorMessageFromUnknown(e)),
  });

  useEffect(() => {
    if (!open || projectId == null) return;
    void api.getMontageBoardStatus(projectId).then((st) => {
      if (st.job?.status === "running") setMontageRunning(true);
    }).catch(() => {});
    void api.getMontageApplyStatus(projectId).then((st) => {
      if (st.job?.status === "running") setApplyRunning(true);
    }).catch(() => {});
    void api.getMontageRecoverOutseeStatus(projectId).then((st) => {
      if (st.job?.status === "running") setRecoverRunning(true);
    }).catch(() => {});
  }, [open, projectId]);

  const handleApplyTerminal = useCallback(
    (status: string, errText?: string) => {
      const key = `${status}:${errText || ""}`;
      if (lastApplyToastKeyRef.current === key) return;
      lastApplyToastKeyRef.current = key;
      setApplyRunning(false);
      setApplyProgress(null);
      if (submittedApplyRef.current) {
        // Очередь подтянется из meta после refetch (remaining / пусто).
        localQueueDirtyRef.current = false;
        setPendingOps([]);
        pendingOpsRef.current = [];
        submittedApplyRef.current = false;
      }
      if (status === "done") toast.success("Генерация завершена");
      else if (status === "error") toast.error(errText || "Генерация не удалась");
      else if (status === "cancelled") toast.message("Генерация остановлена");
      void queryClient.invalidateQueries({ queryKey: ["montage-board", projectId] });
    },
    [projectId, queryClient],
  );

  const handleRecoverTerminal = useCallback(
    (status: string, errText?: string, savedCount?: number) => {
      const key = `${status}:${errText || ""}:${savedCount ?? ""}`;
      if (lastRecoverToastKeyRef.current === key) return;
      lastRecoverToastKeyRef.current = key;
      setRecoverRunning(false);
      const n = savedCount ?? 0;
      if (status === "done" && n > 0) {
        toast.success(`Забрано и заменено из Outsee: ${n} кадр(ов)`);
        localQueueDirtyRef.current = false;
        setPendingOps([]);
      } else if (status === "done") {
        toast.message(errText || "В истории Outsee нет подходящих карточек");
      } else if (status === "error") {
        toast.error(errText || "Не удалось забрать из Outsee");
      } else if (status === "cancelled") {
        toast.message("Забор из Outsee остановлен");
      }
      void queryClient.invalidateQueries({ queryKey: ["montage-board", projectId] });
    },
    [projectId, queryClient],
  );

  const handleMontageTerminal = useCallback(
    (status: string, errText?: string) => {
      const key = `${status}:${errText || ""}`;
      if (lastMontageToastKeyRef.current === key) return;
      lastMontageToastKeyRef.current = key;
      setMontageRunning(false);
      if (status === "done") {
        toast.success("Монтаж завершён");
        void queryClient.invalidateQueries({ queryKey: ["montage-board", projectId] });
      } else if (status === "error") toast.error(errText || "Монтаж не удался");
      else if (status === "cancelled") toast.message("Монтаж остановлен");
    },
    [projectId, queryClient],
  );

  useEffect(() => {
    if (!open || projectId == null) return;
    return subscribeWS(`projects.${projectId}`, (raw) => {
      const evt = raw as {
        type?: string;
        payload?: {
          stopped?: boolean;
          montage_board_montage?: boolean;
          montage_board_apply?: boolean;
          montage_outsee_recover?: boolean;
          status?: string;
          errors?: string[];
          error?: string;
          done_ops?: number;
          total_ops?: number;
          saved_count?: number;
          refresh_board?: boolean;
          highlight?: string;
        };
      };
      if (evt.payload?.stopped) {
        setMontageRunning(false);
        setApplyRunning(false);
        setRecoverRunning(false);
        setApplyProgress(null);
        return;
      }
      if (evt.payload?.montage_outsee_recover) {
        const status = evt.payload.status;
        if (status === "running") {
          setRecoverRunning(true);
          lastRecoverToastKeyRef.current = "";
        } else if (status === "done" || status === "error" || status === "cancelled") {
          handleRecoverTerminal(
            status,
            evt.payload.error ||
              (Array.isArray(evt.payload.errors)
                ? evt.payload.errors.join("; ")
                : undefined),
            typeof evt.payload.saved_count === "number"
              ? evt.payload.saved_count
              : undefined,
          );
        }
        return;
      }
      if (evt.payload?.montage_board_apply) {
        const status = evt.payload.status;
        const doneOps = evt.payload.done_ops as number | undefined;
        const totalOps = evt.payload.total_ops as number | undefined;
        if (status === "running") {
          setApplyRunning(true);
          if (typeof doneOps === "number" && typeof totalOps === "number") {
            setApplyProgress({ done: doneOps, total: totalOps });
          }
          // После каждой успешной op — сразу показать новый кадр на доске
          // (иначе UI держит старый PNG до конца всей очереди).
          if (evt.payload.refresh_board || typeof doneOps === "number") {
            void queryClient.invalidateQueries({
              queryKey: ["montage-board", projectId],
            });
          }
          const hl = evt.payload.highlight;
          if (typeof hl === "string" && hl) {
            setHighlights((prev) => (prev.includes(hl) ? prev : [...prev, hl]));
          }
        } else if (status === "done" || status === "error" || status === "cancelled") {
          const err =
            evt.payload.error ||
            (Array.isArray(evt.payload.errors) ? evt.payload.errors.join("; ") : undefined);
          handleApplyTerminal(status, err);
        }
        return;
      }
      if (!evt.payload?.montage_board_montage) return;
      const status = evt.payload.status;
      if (status === "running") {
        setMontageRunning(true);
        lastMontageToastKeyRef.current = "";
      } else if (status === "done" || status === "error" || status === "cancelled") {
        handleMontageTerminal(status, evt.payload.error);
      }
    });
  }, [open, projectId, queryClient, handleApplyTerminal, handleMontageTerminal, handleRecoverTerminal]);

  useEffect(() => {
    if (!open || projectId == null || !montageRunning) return;
    let cancelled = false;
    const poll = async () => {
      try {
        const st = await api.getMontageBoardStatus(projectId);
        const status = st.job?.status;
        if (cancelled) return;
        if (status === "running" || !status) return;
        handleMontageTerminal(status, st.job?.error || undefined);
      } catch {
        // Сетевой сбой — не сбрасываем running, ждём следующий poll.
      }
    };
    const id = window.setInterval(() => void poll(), 2500);
    void poll();
    return () => {
      cancelled = true;
      window.clearInterval(id);
    };
  }, [open, projectId, montageRunning, handleMontageTerminal]);

  useEffect(() => {
    if (!open || projectId == null || !applyRunning) return;
    let cancelled = false;
    let lastDone = -1;
    const poll = async () => {
      try {
        const st = await api.getMontageApplyStatus(projectId);
        const status = st.job?.status;
        const doneOps = st.job?.done_ops;
        const totalOps = st.job?.total_ops;
        if (cancelled) return;
        if (status === "running") {
          if (typeof doneOps === "number" && typeof totalOps === "number") {
            setApplyProgress({ done: doneOps, total: totalOps });
            if (doneOps !== lastDone) {
              lastDone = doneOps;
              void queryClient.invalidateQueries({
                queryKey: ["montage-board", projectId],
              });
            }
          }
          return;
        }
        if (!status) return;
        handleApplyTerminal(status, st.job?.error || undefined);
      } catch {
        // Сетевой сбой — не сбрасываем running.
      }
    };
    const id = window.setInterval(() => void poll(), 2500);
    void poll();
    return () => {
      cancelled = true;
      window.clearInterval(id);
    };
  }, [open, projectId, applyRunning, handleApplyTerminal, queryClient]);

  useEffect(() => {
    if (!open || projectId == null || !recoverRunning) return;
    let cancelled = false;
    const poll = async () => {
      try {
        const st = await api.getMontageRecoverOutseeStatus(projectId);
        const status = st.job?.status;
        if (cancelled) return;
        if (status === "running" || !status) return;
        handleRecoverTerminal(
          status,
          st.job?.error || undefined,
          typeof st.job?.saved_count === "number" ? st.job.saved_count : undefined,
        );
      } catch {
        // Сетевой сбой — не сбрасываем running.
      }
    };
    const id = window.setInterval(() => void poll(), 2000);
    void poll();
    return () => {
      cancelled = true;
      window.clearInterval(id);
    };
  }, [open, projectId, recoverRunning, handleRecoverTerminal]);

  const refreshBoard = useCallback(() => {
    void board.refetch();
  }, [board]);

  const handleDeleteImage = async (frameNumber: number, shot: 1 | 2) => {
    if (!projectId) return;
    try {
      await api.deleteMontageImage(projectId, frameNumber, shot);
      setStaleVideos((prev) =>
        prev.includes(trimKey(frameNumber, shot)) ? prev : [...prev, trimKey(frameNumber, shot)],
      );
      refreshBoard();
      toast.success("Изображение удалено");
    } catch (e) {
      toast.error(errorMessageFromUnknown(e));
    }
  };

  const handleDeleteVideo = async (frameNumber: number, shot: 1 | 2) => {
    if (!projectId) return;
    try {
      await api.deleteMontageVideo(projectId, frameNumber, shot);
      refreshBoard();
      toast.success("Видео удалено");
    } catch (e) {
      toast.error(errorMessageFromUnknown(e));
    }
  };

  const handleUploadImage = async (frameNumber: number, shot: 1 | 2, file: File) => {
    if (!projectId) return;
    try {
      await api.uploadMontageImage(projectId, frameNumber, shot, file);
      setStaleVideos((prev) =>
        prev.includes(trimKey(frameNumber, shot)) ? prev : [...prev, trimKey(frameNumber, shot)],
      );
      refreshBoard();
      toast.success("Изображение загружено");
    } catch (e) {
      toast.error(errorMessageFromUnknown(e));
    }
  };

  const handleUploadVideo = async (frameNumber: number, shot: 1 | 2, file: File) => {
    if (!projectId) return;
    try {
      await api.uploadMontageVideo(projectId, frameNumber, shot, file);
      setStaleVideos((prev) => prev.filter((k) => k !== trimKey(frameNumber, shot)));
      refreshBoard();
      toast.success("Видео загружено");
    } catch (e) {
      toast.error(errorMessageFromUnknown(e));
    }
  };

  const sourcePromptFor = (
    kind: "image" | "video",
    frameNumber: number,
    shot: 1 | 2,
  ): string => {
    const fr = frames.find((f) => f.number === frameNumber);
    if (!fr) return "";
    if (kind === "image") {
      return (
        (shot === 1 ? fr.image_prompt_shot1 : fr.image_prompt_shot2) ?? ""
      ).trim();
    }
    return (
      (shot === 1 ? fr.animation_prompt_shot1 : fr.animation_prompt_shot2) ?? ""
    ).trim();
  };

  const openPromptModal = (
    kind: "image" | "video",
    frameNumber: number,
    shot: 1 | 2,
    mode: "prompt" | "correction",
  ) => {
    // В textarea сразу кладём промт исходника (Excel/БД) — его и редактируют.
    // Для correction: если есть сохранённая заметка — она, иначе тоже исходник.
    const source = sourcePromptFor(kind, frameNumber, shot);
    const correction = meta?.corrections?.[trimKey(frameNumber, shot)] ?? "";
    const initialText =
      mode === "correction" ? (correction.trim() || source) : source;
    setPromptModal({
      kind,
      frameNumber,
      shot,
      mode,
      title:
        mode === "correction"
          ? `Корректировка · кадр #${frameNumber} · ${kind === "image" ? "изображение" : "видео"} ${shot}`
          : `Промт · кадр #${frameNumber} · ${kind === "image" ? "изображение" : "видео"} ${shot}`,
      initialText,
    });
  };

  const submitPromptModal = (text: string) => {
    if (!promptModal) return;
    const { kind, frameNumber, shot, mode } = promptModal;
    let op: MontagePendingOp;
    if (mode === "correction" && kind === "image") {
      op = {
        type: "image_regen_correction",
        frame_number: frameNumber,
        shot,
        correction: text,
      };
    } else if (kind === "image") {
      op = {
        type: "image_regen_prompt",
        frame_number: frameNumber,
        shot,
        prompt: text,
      };
    } else {
      op = {
        type: "video_regen_prompt",
        frame_number: frameNumber,
        shot,
        prompt: text,
      };
    }
    setPromptModal(null);
    queueOp(op);
  };

  const isHighlighted = (key: string) => highlights.includes(key);
  const isStaleVideo = (frameNumber: number, shot: 1 | 2) =>
    staleVideos.includes(trimKey(frameNumber, shot));

  const tableWidthPx = useMemo(() => {
    const rowLabel = 11 * 16;
    const col = FRAME_COL_REM * 16;
    return rowLabel + frames.length * col;
  }, [frames.length]);

  const syncScrollLeft = useCallback((from: HTMLDivElement, to: HTMLDivElement) => {
    if (Math.abs(to.scrollLeft - from.scrollLeft) < 0.5) return;
    to.scrollLeft = from.scrollLeft;
  }, []);

  useLayoutEffect(() => {
    if (!open) return;
    const tableWrap = tableScrollRef.current;
    const hBar = hBarRef.current;
    if (!tableWrap || !hBar) return;

    const onTable = () => syncScrollLeft(tableWrap, hBar);
    const onBar = () => syncScrollLeft(hBar, tableWrap);

    tableWrap.addEventListener("scroll", onTable, { passive: true });
    hBar.addEventListener("scroll", onBar, { passive: true });
    // Выровнять после mount (после close/reopen listeners иначе мертвы).
    hBar.scrollLeft = tableWrap.scrollLeft;
    return () => {
      tableWrap.removeEventListener("scroll", onTable);
      hBar.removeEventListener("scroll", onBar);
    };
  }, [open, frames.length, syncScrollLeft, tableWidthPx]);

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
    trimsDirtyRef.current = true;
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
              disabled={!projectId || applyMutation.isPending || applyRunning}
              onClick={() => applyMutation.mutate()}
            >
              {applyMutation.isPending || applyRunning ? (
                <Loader2 className="mr-1 h-4 w-4 animate-spin" />
              ) : null}
              Применить правки
              {applyRunning && applyProgress
                ? ` (${applyProgress.done}/${applyProgress.total})`
                : pendingOps.length > 0
                  ? ` (${pendingOps.length})`
                  : ""}
            </Button>
            <Button
              type="button"
              size="sm"
              variant="outline"
              className="h-9 gap-1.5 text-xs"
              disabled={
                !projectId ||
                recoverOutseeMutation.isPending ||
                recoverRunning ||
                applyRunning
              }
              title="Скачать из Outsee выделенные правки и заменить кадры"
              onClick={() => recoverOutseeMutation.mutate()}
            >
              {recoverOutseeMutation.isPending || recoverRunning ? (
                <Loader2 className="h-4 w-4 animate-spin" />
              ) : (
                <RefreshCw className="h-4 w-4" />
              )}
              {recoverRunning ? "Забираем из Outsee…" : "Забрать правки из Outsee"}
            </Button>
            <Button
              type="button"
              size="sm"
              variant="outline"
              className="h-9 gap-1.5 text-xs"
              disabled={!projectId || montageMutation.isPending || montageRunning}
              onClick={() => montageMutation.mutate()}
            >
              {montageMutation.isPending || montageRunning ? (
                <Loader2 className="h-4 w-4 animate-spin" />
              ) : (
                <Clapperboard className="h-4 w-4" />
              )}
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
                  <>
                    <MontageMediaExtras
                      onVoiceUpload={async (file) => {
                        try {
                          await api.uploadMontageVoice(projectId, file);
                          toast.success("Озвучка загружена → audio/voice_full.*");
                        } catch (e) {
                          toast.error(errorMessageFromUnknown(e));
                        }
                      }}
                      onMusicUpload={async (file) => {
                        try {
                          await api.uploadMontageMusic(projectId, file);
                          toast.success("Музыка загружена → music/bgm.*");
                        } catch (e) {
                          toast.error(errorMessageFromUnknown(e));
                        }
                      }}
                    />
                    <NodeStepParamsPanel projectId={projectId} nodeType="assemble" />
                  </>
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
              <div className="flex flex-col items-center gap-3 py-10 px-4 text-center">
                <p className="text-sm text-destructive">Не удалось загрузить данные монтажа</p>
                <p className="max-w-lg text-xs text-muted-foreground break-words whitespace-pre-wrap">
                  {board.error instanceof Error
                    ? board.error.message || String(board.error)
                    : String(board.error ?? "неизвестная ошибка")}
                  {projectId != null ? `\nproject #${projectId}` : ""}
                </p>
                <Button
                  type="button"
                  size="sm"
                  variant="outline"
                  onClick={() => {
                    void queryClient.resetQueries({ queryKey: ["montage-board", projectId] });
                    void board.refetch();
                  }}
                >
                  Повторить
                </Button>
              </div>
            )}
            {!board.isLoading && !board.isError && frames.length === 0 && (
              <p className="text-sm text-muted-foreground">
                Кадров нет — положите{" "}
                <code className="text-[11px]">project.xlsx</code> или файлы{" "}
                <code className="text-[11px]">scenes/frame_NNN_*.png</code> /{" "}
                <code className="text-[11px]">videos/clip_NNN_*.mp4</code> в папку
                проекта и обновите доску.
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
                              className={cn(
                                "relative isolate overflow-hidden px-3 py-2 align-top",
                                FRAME_COL_CLASS,
                              )}
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
                                  onRegen={() =>
                                    queueOp({
                                      type: "image_regen",
                                      frame_number: fr.number,
                                      shot: 1,
                                      prompt: sourcePromptFor("image", fr.number, 1),
                                    })
                                  }
                                  onEditPrompt={() => openPromptModal("image", fr.number, 1, "prompt")}
                                  onRegenWithCorrection={() =>
                                    openPromptModal("image", fr.number, 1, "correction")
                                  }
                                  onDelete={() => void handleDeleteImage(fr.number, 1)}
                                  onUpload={(file) => void handleUploadImage(fr.number, 1, file)}
                                  highlighted={isHighlighted(`${fr.number}:image1`)}
                                />
                              ) : row.key === "image2" ? (
                                !fr.has_shot2 ? (
                                  <p className="text-xs text-muted-foreground">Второй кадр не задан</p>
                                ) : (
                                <ClickableMedia
                                  url={fr.image_shot2_url}
                                  kind="image"
                                  label={`Изображение 2 · кадр #${fr.number}`}
                                  onPreview={setPreview}
                                  onRegen={() =>
                                    queueOp({
                                      type: "image_regen",
                                      frame_number: fr.number,
                                      shot: 2,
                                      prompt: sourcePromptFor("image", fr.number, 2),
                                    })
                                  }
                                  onEditPrompt={() => openPromptModal("image", fr.number, 2, "prompt")}
                                  onRegenWithCorrection={() =>
                                    openPromptModal("image", fr.number, 2, "correction")
                                  }
                                  onDelete={() => void handleDeleteImage(fr.number, 2)}
                                  onUpload={(file) => void handleUploadImage(fr.number, 2, file)}
                                  highlighted={isHighlighted(`${fr.number}:image2`)}
                                />
                                )
                              ) : row.key === "video1" ? (
                                <VideoMediaCell
                                  fr={fr}
                                  shot={1}
                                  url={fr.video_shot1_url}
                                  onPreview={setPreview}
                                  trim={trims[trimKey(fr.number, 1)]}
                                  onTrimChange={(t) => updateTrim(trimKey(fr.number, 1), t)}
                                  onRegen={() =>
                                    queueOp({
                                      type: "video_regen",
                                      frame_number: fr.number,
                                      shot: 1,
                                    })
                                  }
                                  onEditPrompt={() => openPromptModal("video", fr.number, 1, "prompt")}
                                  onDelete={() => void handleDeleteVideo(fr.number, 1)}
                                  onUpload={(file) => void handleUploadVideo(fr.number, 1, file)}
                                  highlighted={isHighlighted(trimKey(fr.number, 1))}
                                  stale={isStaleVideo(fr.number, 1)}
                                />
                              ) : (
                                <VideoMediaCell
                                  fr={fr}
                                  shot={2}
                                  url={fr.video_shot2_url}
                                  onPreview={setPreview}
                                  trim={trims[trimKey(fr.number, 2)]}
                                  onTrimChange={(t) => updateTrim(trimKey(fr.number, 2), t)}
                                  onRegen={() =>
                                    queueOp({
                                      type: "video_regen",
                                      frame_number: fr.number,
                                      shot: 2,
                                    })
                                  }
                                  onEditPrompt={() => openPromptModal("video", fr.number, 2, "prompt")}
                                  onDelete={() => void handleDeleteVideo(fr.number, 2)}
                                  onUpload={(file) => void handleUploadVideo(fr.number, 2, file)}
                                  highlighted={isHighlighted(trimKey(fr.number, 2))}
                                  stale={isStaleVideo(fr.number, 2)}
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
      <PromptModal
        state={promptModal}
        onClose={() => setPromptModal(null)}
        onSubmit={submitPromptModal}
        busy={applyMutation.isPending}
      />
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
