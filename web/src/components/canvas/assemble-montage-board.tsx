"use client";

import {
  memo,
  startTransition,
  useCallback,
  useEffect,
  useLayoutEffect,
  useMemo,
  useRef,
  useState,
  type DragEvent as ReactDragEvent,
  type RefObject,
} from "react";
import { createPortal } from "react-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  ArrowLeftRight,
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
import type { MontageBoardDTO, MontageBoardFrame } from "@/lib/types";
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
import { AudioAlignPopover } from "@/components/studio/audio-align-dialog";

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

/** Ключ слота: image → `N:imageS`, video → `N:S`. */
function slotKeyFromOp(op: Pick<MontagePendingOp, "type" | "frame_number" | "shot">): string {
  if (String(op.type || "").startsWith("image_")) {
    return `${op.frame_number}:image${op.shot}`;
  }
  return trimKey(op.frame_number, op.shot);
}

/** Мгновенный preview URL после regen (без ждать полный refetch доски). */
function filesUrlFromAbsPath(absPath: string): string {
  return `/api/files?path=${encodeURIComponent(absPath)}&v=${Date.now()}`;
}

function patchBoardFrameMedia(
  data: MontageBoardDTO,
  frameNumber: number,
  shot: 1 | 2,
  absPath: string,
  highlight: string,
): MontageBoardDTO {
  const url = filesUrlFromAbsPath(absPath);
  const isImage = highlight.includes(":image");
  let changed = false;
  const frames = data.frames.map((fr) => {
    if (fr.number !== frameNumber) return fr;
    changed = true;
    if (isImage) {
      return shot === 2
        ? { ...fr, image_shot2_url: url }
        : { ...fr, image_shot1_url: url };
    }
    return shot === 2
      ? { ...fr, video_shot2_url: url }
      : { ...fr, video_shot1_url: url };
  });
  return changed ? { ...data, frames } : data;
}

type SlotTone = "applied" | "pending" | "failed";

function slotToneRing(tone: SlotTone | undefined): string | false {
  if (tone === "failed") return "ring-2 ring-rose-500/80";
  if (tone === "pending") return "ring-2 ring-amber-400/70";
  if (tone === "applied") return "ring-2 ring-emerald-400/60";
  return false;
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

/** Своё состояние open — клик не перерисовывает всю таблицу кадров. */
function MontageExtrasPopover({
  projectId,
}: {
  projectId: number | null | undefined;
}) {
  const [open, setOpen] = useState(false);
  return (
    <Popover open={open} onOpenChange={setOpen}>
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
        {open ? (
          <>
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
          </>
        ) : null}
      </PopoverContent>
    </Popover>
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

type SwapSlotPick = {
  kind: "image" | "video";
  frameNumber: number;
  shot: 1 | 2;
};

const MediaActionBar = memo(function MediaActionBar({
  kind,
  onRegen,
  onEditPrompt,
  onAiChange,
  onRegenWithCorrection,
  onDelete,
  onUpload,
  onSwapPick,
  swapSelected,
  swapBusy,
}: {
  kind: "image" | "video";
  onRegen: () => void;
  onEditPrompt: () => void;
  onAiChange: () => void;
  onRegenWithCorrection?: () => void;
  onDelete: () => void;
  onUpload: (file: File) => void;
  /** Кнопка обмена: первый клик — выбор, второй — swap с другим слотом. */
  onSwapPick?: () => void;
  swapSelected?: boolean;
  swapBusy?: boolean;
}) {
  const inputRef = useRef<HTMLInputElement>(null);
  const imageActions = [
    { label: "Перегенерация без редакции", action: onRegen },
    { label: "Редактировать промт", action: onEditPrompt },
    { label: "ИИзменение", action: onAiChange },
    ...(onRegenWithCorrection
      ? [{ label: "Перегенерация существующего изображения", action: onRegenWithCorrection }]
      : []),
  ];
  const videoActions = [
    { label: "Перегенерация без редакции", action: onRegen },
    { label: "Редактировать промт", action: onEditPrompt },
    { label: "ИИзменение", action: onAiChange },
  ];
  const actions = kind === "image" ? imageActions : videoActions;

  return (
    <div className="mt-2 flex items-center gap-1">
      {onSwapPick && (
        <Button
          type="button"
          variant="outline"
          size="icon"
          disabled={swapBusy}
          className={cn(
            "h-7 w-7 shrink-0",
            swapSelected
              ? "border-amber-400/70 bg-amber-500/25 text-amber-100"
              : "text-muted-foreground hover:border-amber-400/50 hover:text-amber-200",
          )}
          title={
            swapSelected
              ? "Выбрано — нажмите ↔ на другом слоте того же типа"
              : "Обмен: нажмите ↔ на двух слотах (картинка↔картинка или видео↔видео)"
          }
          onClick={onSwapPick}
        >
          {swapBusy ? (
            <Loader2 className="h-3.5 w-3.5 animate-spin" />
          ) : (
            <ArrowLeftRight className="h-3.5 w-3.5" />
          )}
        </Button>
      )}
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
});

const MONTAGE_IMAGE_DRAG = "application/x-vp-montage-image";

type ImageSlotRef = { frameNumber: number; shot: 1 | 2 };

/** Chrome часто не отдаёт custom mime в dragOver.types — держим активный drag здесь. */
let activeImageDrag: ImageSlotRef | null = null;

function parseImageDrag(e: ReactDragEvent): ImageSlotRef | null {
  if (activeImageDrag) return activeImageDrag;
  try {
    const raw =
      e.dataTransfer.getData(MONTAGE_IMAGE_DRAG) ||
      e.dataTransfer.getData("text/plain");
    if (!raw) return null;
    const parsed = JSON.parse(raw) as ImageSlotRef;
    if (
      typeof parsed.frameNumber === "number" &&
      (parsed.shot === 1 || parsed.shot === 2)
    ) {
      return parsed;
    }
  } catch {
    /* ignore */
  }
  return null;
}

const ClickableMedia = memo(function ClickableMedia({
  url,
  kind,
  label,
  onPreview,
  onRegen,
  onEditPrompt,
  onAiChange,
  onRegenWithCorrection,
  onDelete,
  onUpload,
  slotTone,
  stale,
  scrollRootRef,
  imageSlot,
  onImageDrop,
  onSwapPick,
  swapSelected,
  swapBusy,
}: {
  url: string | null;
  kind: "image" | "video";
  label: string;
  onPreview: (p: MediaPreview) => void;
  onRegen: () => void;
  onEditPrompt: () => void;
  onAiChange: () => void;
  onRegenWithCorrection?: () => void;
  onDelete: () => void;
  onUpload: (file: File) => void;
  /** applied=зелёный, pending=янтарь (очередь), failed=красный. */
  slotTone?: SlotTone;
  stale?: boolean;
  scrollRootRef?: RefObject<HTMLDivElement | null>;
  /** Слот картинки: drag с файла + drop в пустую/занятую ячейку. */
  imageSlot?: ImageSlotRef;
  onImageDrop?: (from: ImageSlotRef) => void;
  onSwapPick?: () => void;
  swapSelected?: boolean;
  swapBusy?: boolean;
}) {
  const hostRef = useRef<HTMLDivElement>(null);
  // Не монтировать сотни <video>/<img> сразу — Chrome зависает на 150×2 клипах.
  const [inView, setInView] = useState(false);
  const [dragOver, setDragOver] = useState(false);
  const canDropImage = kind === "image" && !!imageSlot && !!onImageDrop;

  useEffect(() => {
    const el = hostRef.current;
    if (!el || !url) return;
    const root = scrollRootRef?.current ?? null;
    const io = new IntersectionObserver(
      (entries) => {
        if (entries.some((e) => e.isIntersecting)) setInView(true);
      },
      { root, rootMargin: "120px 160px", threshold: 0.01 },
    );
    io.observe(el);
    return () => io.disconnect();
  }, [url, scrollRootRef]);

  const dropHandlers = canDropImage
    ? {
        onDragOver: (e: ReactDragEvent) => {
          if (!activeImageDrag) return;
          e.preventDefault();
          e.dataTransfer.dropEffect = "move";
          setDragOver(true);
        },
        onDragLeave: () => setDragOver(false),
        onDrop: (e: ReactDragEvent) => {
          e.preventDefault();
          setDragOver(false);
          const from = parseImageDrag(e);
          activeImageDrag = null;
          if (!from || !imageSlot || !onImageDrop) return;
          if (from.frameNumber === imageSlot.frameNumber && from.shot === imageSlot.shot) {
            return;
          }
          onImageDrop(from);
        },
      }
    : {};

  if (!url) {
    return (
      <div
        className={cn(
          "rounded-lg",
          slotToneRing(slotTone),
          swapSelected && "ring-2 ring-amber-400/70",
          dragOver && "ring-2 ring-sky-400/70",
        )}
        {...dropHandlers}
      >
        <div
          className={cn(
            "flex h-32 w-full items-center justify-center rounded-lg border border-dashed bg-black/20 text-xs text-muted-foreground",
            dragOver ? "border-sky-400/60 bg-sky-500/10 text-sky-100" : "border-white/15",
            swapSelected && "border-amber-400/50 bg-amber-500/10",
          )}
        >
          {dragOver ? "отпустить сюда" : canDropImage ? "нет файла · можно бросить" : "нет файла"}
        </div>
        <MediaActionBar
          kind={kind}
          onRegen={onRegen}
          onEditPrompt={onEditPrompt}
          onAiChange={onAiChange}
          onRegenWithCorrection={onRegenWithCorrection}
          onDelete={onDelete}
          onUpload={onUpload}
          onSwapPick={onSwapPick}
          swapSelected={swapSelected}
          swapBusy={swapBusy}
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
        slotToneRing(slotTone),
        // stale только если нет статуса apply (иначе failed/pending важнее).
        !slotTone && stale && "ring-2 ring-amber-500/50",
        swapSelected && "ring-2 ring-amber-400/70",
        dragOver && "ring-2 ring-sky-400/70",
      )}
      {...dropHandlers}
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
              key={url}
              src={url}
              className="h-full w-full object-cover transition group-hover:brightness-110"
              preload="metadata"
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
          draggable={!!imageSlot}
          className="group block h-32 w-full cursor-grab overflow-hidden rounded-lg border border-white/10 bg-black active:cursor-grabbing"
          onClick={open}
          title={
            imageSlot
              ? `Открыть ${label} · перетащи в другую ячейку (в т.ч. пустую)`
              : `Открыть ${label}`
          }
          onDragStart={(e) => {
            if (!imageSlot) return;
            activeImageDrag = imageSlot;
            const payload = JSON.stringify(imageSlot);
            e.dataTransfer.setData(MONTAGE_IMAGE_DRAG, payload);
            e.dataTransfer.setData("text/plain", payload);
            e.dataTransfer.effectAllowed = "move";
          }}
          onDragEnd={() => {
            activeImageDrag = null;
            setDragOver(false);
          }}
        >
          {inView ? (
            /* eslint-disable-next-line @next/next/no-img-element */
            <img
              key={url}
              src={url}
              alt={label}
              loading="lazy"
              decoding="async"
              draggable={false}
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
        onAiChange={onAiChange}
        onRegenWithCorrection={onRegenWithCorrection}
        onDelete={onDelete}
        onUpload={onUpload}
        onSwapPick={onSwapPick}
        swapSelected={swapSelected}
        swapBusy={swapBusy}
      />
    </div>
  );
});

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
    <div className="relative isolate mt-2 px-1 py-2">
      <div
        ref={trackRef}
        className="relative mx-1.5 h-2.5 rounded-full bg-white/15"
        role="presentation"
      >
        <div
          className="absolute top-0 h-2.5 rounded-full bg-amber-500/80 shadow-sm"
          style={{ left: `${startPct}%`, width: `${Math.max(0, endPct - startPct)}%` }}
        />
        <button
          type="button"
          className={cn(
            "absolute top-1/2 z-10 h-4.5 w-4.5 -translate-x-1/2 -translate-y-1/2 rounded-full border-2 border-amber-400 bg-amber-200 shadow-md transition-transform",
            active === "start" && "scale-125 ring-2 ring-amber-400/50",
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
            "absolute top-1/2 z-10 h-4.5 w-4.5 -translate-x-1/2 -translate-y-1/2 rounded-full border-2 border-amber-400 bg-amber-200 shadow-md transition-transform",
            active === "end" && "scale-125 ring-2 ring-amber-400/50",
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
      <p className="mt-2 text-xs text-muted-foreground">
        Нет меток озвучки для расчёта длительности сцены
      </p>
    );
  }

  const fileMax = fileDuration ?? sceneUse;
  const current = trim ?? clampTrim(0, Math.min(sceneUse, fileMax), fileMax, sceneUse, "end");
  const usedLen = Math.max(0, current.end - current.start);

  return (
    <div className="mt-2 rounded-xl border border-zinc-800 bg-zinc-950/70 p-2.5 pb-3 shadow-inner">
      <p className="text-xs text-zinc-300 font-medium">
        В сцене:{" "}
        <span className="font-mono font-bold text-amber-300">{formatSecShort(usedLen)}</span>{" "}
        из{" "}
        <span className="font-mono font-bold text-zinc-100">{formatSecShort(sceneUse)}</span> с
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

const VideoMediaCell = memo(function VideoMediaCell({
  fr,
  shot,
  url,
  onPreview,
  trim,
  onTrimChange,
  onRegen,
  onEditPrompt,
  onAiChange,
  onDelete,
  onUpload,
  slotTone,
  stale,
  scrollRootRef,
  onSwapPick,
  swapSelected,
  swapBusy,
}: {
  fr: MontageBoardFrame;
  shot: 1 | 2;
  url: string | null;
  onPreview: (p: MediaPreview) => void;
  trim: VideoTrim | undefined;
  onTrimChange: (next: VideoTrim) => void;
  onRegen: () => void;
  onEditPrompt: () => void;
  onAiChange: () => void;
  onDelete: () => void;
  onUpload: (file: File) => void;
  slotTone?: SlotTone;
  stale?: boolean;
  scrollRootRef?: RefObject<HTMLDivElement | null>;
  onSwapPick?: () => void;
  swapSelected?: boolean;
  swapBusy?: boolean;
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
        onAiChange={onAiChange}
        onDelete={onDelete}
        onUpload={onUpload}
        slotTone={slotTone}
        stale={stale}
        scrollRootRef={scrollRootRef}
        onSwapPick={onSwapPick}
        swapSelected={swapSelected}
        swapBusy={swapBusy}
      />
      <VideoTrimSlider
        fileDuration={fileDur}
        sceneUse={sceneUse}
        trim={trim}
        onTrimChange={onTrimChange}
      />
    </div>
  );
});

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
  montageBusy = false,
  onClose,
}: {
  open: boolean;
  projectId: number | null;
  montageBusy?: boolean;
  onClose: () => void;
}) {
  const queryClient = useQueryClient();
  const [preview, setPreview] = useState<MediaPreview | null>(null);
  const [collapsedRows, setCollapsedRows] = useState<Set<RowKey>>(new Set());
  const [trims, setTrims] = useState<Record<string, VideoTrim>>({});
  const [pendingOps, setPendingOps] = useState<MontagePendingOp[]>([]);
  const [promptModal, setPromptModal] = useState<PromptModalState>(null);
  const [highlights, setHighlights] = useState<string[]>([]);
  const [failedHighlights, setFailedHighlights] = useState<string[]>([]);
  const [staleVideos, setStaleVideos] = useState<string[]>([]);
  const [montageRunning, setMontageRunning] = useState(false);
  const [applyRunning, setApplyRunning] = useState(false);
  const [recoverRunning, setRecoverRunning] = useState(false);
  const [swapPick, setSwapPick] = useState<SwapSlotPick | null>(null);
  const [swapBusy, setSwapBusy] = useState(false);
  const [moveImageBusy, setMoveImageBusy] = useState(false);
  const [applyProgress, setApplyProgress] = useState<{ done: number; total: number } | null>(
    null,
  );
  const pendingOpsRef = useRef<MontagePendingOp[]>([]);
  const montageActive = montageRunning || montageBusy;
  pendingOpsRef.current = pendingOps;
  /** Пользователь набрал очередь локально — не затирать пустым meta с сервера. */
  const localQueueDirtyRef = useRef(false);
  /** Мы сами отправили apply (started) — можно чистить/синхронизировать очередь. */
  const submittedApplyRef = useRef(false);
  const trimsDirtyRef = useRef(false);
  const lastApplyToastKeyRef = useRef("");
  /** Не принимать done/error, пока хотя бы раз не увидели running (гонка stale meta). */
  const applySeenRunningRef = useRef(false);
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
    refetchOnMount: false,
    staleTime: 60_000,
    // НЕ placeholderData/keepPreviousData: иначе очередь/кадры чужого проекта
    // остаются на кнопке «Применить правки» при переключении.
  });

  // Сброс локальной очереди при смене проекта — СНАЧАЛА flush старой очереди.
  const prevProjectIdRef = useRef<number | null>(null);
  useEffect(() => {
    const prev = prevProjectIdRef.current;
    prevProjectIdRef.current = projectId;
    if (prev != null && prev !== projectId && localQueueDirtyRef.current) {
      const ops = pendingOpsRef.current;
      void api.saveMontageQueue(prev, { pending_ops: ops }).catch(() => {});
    }
    setPendingOps([]);
    pendingOpsRef.current = [];
    localQueueDirtyRef.current = false;
    trimsDirtyRef.current = false;
    submittedApplyRef.current = false;
    applySeenRunningRef.current = false;
    setTrims({});
    setHighlights([]);
    setFailedHighlights([]);
    setStaleVideos([]);
    setApplyRunning(false);
    setApplyProgress(null);
    setMontageRunning(false);
    setRecoverRunning(false);
    setPromptModal(null);
    setPreview(null);
    setSwapPick(null);
    setSwapBusy(false);
    lastApplyToastKeyRef.current = "";
  }, [projectId]);

  // Прогрев «Доп. функции» — getProject не блокирует первый клик.
  useEffect(() => {
    if (!open || projectId == null) return;
    void queryClient.prefetchQuery({
      queryKey: ["project", projectId],
      queryFn: () => api.getProject(projectId),
      staleTime: 30_000,
    });
    // После regen staleTime/кэш иначе показывает старые клипы и пустую подсветку.
    void queryClient.resetQueries({ queryKey: ["montage-board", projectId] });
  }, [open, projectId, queryClient]);

  // При закрытии панели — сразу сохранить очередь (не ждать debounce).
  useEffect(() => {
    if (open || projectId == null) return;
    if (!localQueueDirtyRef.current) return;
    const ops = pendingOpsRef.current;
    void api
      .saveMontageQueue(projectId, { pending_ops: ops })
      .then(() => {
        localQueueDirtyRef.current = false;
      })
      .catch(() => {});
  }, [open, projectId]);

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
        t !== "image_ai_change" &&
        t !== "video_regen" &&
        t !== "video_regen_prompt" &&
        t !== "video_ai_change"
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
    setFailedHighlights(meta?.failed_highlights ?? []);
    setStaleVideos(meta?.stale_videos ?? []);
    if (!trimsDirtyRef.current) {
      setTrims(mergeTrimsFromMeta(frames, meta?.video_trims ?? {}));
    }

    if (applyRunning) return;

    // Данные доски должны быть от текущего projectId (не stale кэш чужого).
    if (board.data == null) return;

    const restored = parsePendingOps(meta?.pending_ops);
    // Пока пользователь набирает очередь (dirty) — НИКОГДА не затирать её
    // серверным meta. Иначе: локально 40+, на сервере старые 8 → «стало 8».
    if (localQueueDirtyRef.current) {
      return;
    }
    const nextKey = JSON.stringify(restored);
    if (nextKey === JSON.stringify(pendingOpsRef.current)) return;
    pendingOpsRef.current = restored;
    setPendingOps(restored);
  }, [
    projectId,
    board.data,
    board.dataUpdatedAt,
    board.isFetching,
    frames.length,
    meta?.video_trims,
    meta?.highlights,
    meta?.failed_highlights,
    meta?.stale_videos,
    pendingOpsKey,
    applyRunning,
    parsePendingOps,
  ]);

  const showPreview = useCallback((p: MediaPreview) => {
    setPreview(p);
  }, []);

  const queueSaveTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  const persistQueue = useCallback(
    (ops: MontagePendingOp[]) => {
      if (projectId == null) return;
      // Во время apply очередь пишет сам apply (_finish_op) — клиентский
      // debounce с [] или устаревшим списком иначе затирает remaining.
      if (applyRunning) return;
      if (queueSaveTimerRef.current) clearTimeout(queueSaveTimerRef.current);
      queueSaveTimerRef.current = setTimeout(() => {
        void api
          .saveMontageQueue(projectId, {
            pending_ops: ops,
            video_trims: trimsDirtyRef.current ? trims : undefined,
          })
          .catch(() => {
            // Не мешаем набору очереди — при следующем add/retry сохранится.
          });
      }, 400);
    },
    [projectId, trims, applyRunning],
  );

  const queueOp = useCallback(
    (op: MontagePendingOp) => {
      localQueueDirtyRef.current = true;
      setPendingOps((prev) => {
        const next = [...prev, op];
        pendingOpsRef.current = next;
        persistQueue(next);
        return next;
      });
      toast.message("Операция в очереди — нажмите «Применить правки»");
    },
    [persistQueue],
  );

  const applyMutation = useMutation({
    mutationFn: () => {
      const ops = pendingOpsRef.current;
      if (ops.length > 0) {
        toast.message(`Генерация: ${ops.length} операций… (Outsee/Grsai API)`);
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
        if (queueSaveTimerRef.current) {
          clearTimeout(queueSaveTimerRef.current);
          queueSaveTimerRef.current = null;
        }
        setPendingOps([]);
        pendingOpsRef.current = [];
        setFailedHighlights([]);
        applySeenRunningRef.current = false;
        setApplyRunning(true);
        lastApplyToastKeyRef.current = "";
        toast.message(res.message || `Генерация ${queued} операций через API…`);
        return;
      }
      if (res.already_running) {
        // Чужой/текущий job — НЕ чистим локальную очередь пользователя.
        applySeenRunningRef.current = false;
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
        setFailedHighlights(res.meta.failed_highlights ?? []);
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
        // Фоновый job должен вернуть started/already_running. Иначе не врём «завершено».
        toast.message(res.message || "Генерация принята — ждём статус…");
        setApplyRunning(true);
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
    (status: string, errText?: string, progressKey?: string) => {
      // Тост дедупим; invalidate/highlights — всегда (иначе повторный done:
      // ключ "done:" совпадает с прошлым прогоном → зелёная подсветка не подтягивается).
      const toastKey = `${status}:${errText || ""}:${progressKey || ""}`;
      const showToast = lastApplyToastKeyRef.current !== toastKey;
      if (showToast) lastApplyToastKeyRef.current = toastKey;

      setApplyRunning(false);
      setApplyProgress(null);
      if (submittedApplyRef.current) {
        // Очередь подтянется из meta после refetch (remaining / пусто).
        localQueueDirtyRef.current = false;
        setPendingOps([]);
        pendingOpsRef.current = [];
        submittedApplyRef.current = false;
      }
      if (showToast) {
        if (status === "done") toast.success("Генерация завершена");
        else if (status === "error") toast.error(errText || "Генерация не удалась");
        else if (status === "cancelled") toast.message("Генерация остановлена");
      }
      void queryClient
        .resetQueries({ queryKey: ["montage-board", projectId] })
        .then(() =>
          queryClient.fetchQuery({
            queryKey: ["montage-board", projectId],
            queryFn: () => api.getMontageBoard(projectId!),
          }),
        )
        .then((data) => {
          const hl = data?.meta?.highlights;
          if (Array.isArray(hl)) setHighlights(hl.map(String));
          const failed = data?.meta?.failed_highlights;
          if (Array.isArray(failed)) setFailedHighlights(failed.map(String));
          const stale = data?.meta?.stale_videos;
          if (Array.isArray(stale)) setStaleVideos(stale.map(String));
          const restored = parsePendingOps(data?.meta?.pending_ops);
          pendingOpsRef.current = restored;
          setPendingOps(restored);
        })
        .catch(() => {
          void queryClient.invalidateQueries({ queryKey: ["montage-board", projectId] });
        });
    },
    [projectId, queryClient, parsePendingOps],
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
          frame_number?: number;
          shot?: number;
          path?: string;
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
          applySeenRunningRef.current = true;
          setApplyRunning(true);
          if (typeof doneOps === "number" && typeof totalOps === "number") {
            startTransition(() => {
              setApplyProgress({ done: doneOps, total: totalOps });
            });
          }
          const hl = evt.payload.highlight;
          if (typeof hl === "string" && hl) {
            setHighlights((prev) => (prev.includes(hl) ? prev : [...prev, hl]));
            setFailedHighlights((prev) => prev.filter((k) => k !== hl));
          }
          // Сразу подменить превью кадра из path — полный refetch доски слишком редкий/тяжёлый.
          const absPath = evt.payload.path;
          const frNum = Number(evt.payload.frame_number);
          const shotRaw = Number(evt.payload.shot);
          const shot: 1 | 2 = shotRaw === 2 ? 2 : 1;
          if (
            typeof absPath === "string" &&
            absPath &&
            Number.isFinite(frNum) &&
            frNum >= 1 &&
            typeof hl === "string" &&
            hl
          ) {
            queryClient.setQueryData<MontageBoardDTO>(
              ["montage-board", projectId],
              (old) =>
                old
                  ? patchBoardFrameMedia(old, frNum, shot, absPath, hl)
                  : old,
            );
          } else if (evt.payload.refresh_board) {
            void queryClient.invalidateQueries({
              queryKey: ["montage-board", projectId],
            });
          }
        } else if (status === "done" || status === "error" || status === "cancelled") {
          if (!applySeenRunningRef.current && status !== "cancelled") return;
          const err =
            evt.payload.error ||
            (Array.isArray(evt.payload.errors) ? evt.payload.errors.join("; ") : undefined);
          const pk =
            typeof evt.payload.done_ops === "number" &&
            typeof evt.payload.total_ops === "number"
              ? `${evt.payload.done_ops}/${evt.payload.total_ops}`
              : undefined;
          handleApplyTerminal(status, err, pk);
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
          applySeenRunningRef.current = true;
          if (typeof doneOps === "number" && typeof totalOps === "number") {
            // Не invalidate доски на каждый тик — иначе UI лагает (ffprobe/xlsx).
            // Обновление кадров приходит по WS (refresh_board).
            if (doneOps !== lastDone) {
              lastDone = doneOps;
              startTransition(() => {
                setApplyProgress({ done: doneOps, total: totalOps });
              });
            }
          }
          return;
        }
        if (!status || status === "idle") return;
        // Stale done/error до первого running — ждём, не автозавершаем.
        if (!applySeenRunningRef.current) return;
        const pk =
          typeof doneOps === "number" && typeof totalOps === "number"
            ? `${doneOps}/${totalOps}`
            : undefined;
        handleApplyTerminal(status, st.job?.error || undefined, pk);
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

  const handleMoveImage = async (from: ImageSlotRef, to: ImageSlotRef) => {
    if (!projectId || moveImageBusy) return;
    if (from.frameNumber === to.frameNumber && from.shot === to.shot) return;
    setMoveImageBusy(true);
    try {
      const res = await api.moveMontageImage(
        projectId,
        from.frameNumber,
        from.shot,
        to.frameNumber,
        to.shot,
      );
      setStaleVideos((prev) => {
        const next = new Set(prev);
        next.add(trimKey(from.frameNumber, from.shot));
        next.add(trimKey(to.frameNumber, to.shot));
        return [...next];
      });
      refreshBoard();
      toast.success(
        res.mode === "move"
          ? `Картинка → #${to.frameNumber} shot${to.shot}`
          : `Обмен #${from.frameNumber}.${from.shot} ↔ #${to.frameNumber}.${to.shot}`,
      );
    } catch (e) {
      toast.error(errorMessageFromUnknown(e));
    } finally {
      setMoveImageBusy(false);
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

  const isSwapSelected = (kind: "image" | "video", frameNumber: number, shot: 1 | 2) =>
    swapPick?.kind === kind &&
    swapPick.frameNumber === frameNumber &&
    swapPick.shot === shot;

  const handleSwapPick = async (slot: SwapSlotPick) => {
    if (!projectId || swapBusy) return;
    if (!swapPick) {
      setSwapPick(slot);
      toast.message(
        slot.kind === "image"
          ? `Выбрано изображение #${slot.frameNumber}.${slot.shot} — нажмите ↔ на другом`
          : `Выбрано видео #${slot.frameNumber}.${slot.shot} — нажмите ↔ на другом`,
      );
      return;
    }
    if (
      swapPick.kind === slot.kind &&
      swapPick.frameNumber === slot.frameNumber &&
      swapPick.shot === slot.shot
    ) {
      setSwapPick(null);
      return;
    }
    if (swapPick.kind !== slot.kind) {
      setSwapPick(slot);
      toast.message(
        slot.kind === "image"
          ? `Выбрано изображение #${slot.frameNumber}.${slot.shot} — нажмите ↔ на другом`
          : `Выбрано видео #${slot.frameNumber}.${slot.shot} — нажмите ↔ на другом`,
      );
      return;
    }
    setSwapBusy(true);
    try {
      const res = await api.swapMontageSlots(projectId, slot.kind, swapPick, slot);
      if (slot.kind === "video") {
        setTrims((prev) => {
          const k1 = trimKey(swapPick.frameNumber, swapPick.shot);
          const k2 = trimKey(slot.frameNumber, slot.shot);
          const t1 = prev[k1];
          const t2 = prev[k2];
          if (t1 == null && t2 == null) return prev;
          const next = { ...prev };
          if (t2 == null) delete next[k1];
          else next[k1] = t2;
          if (t1 == null) delete next[k2];
          else next[k2] = t1;
          return next;
        });
      }
      setStaleVideos((prev) => {
        const next = new Set(prev);
        next.add(trimKey(swapPick.frameNumber, swapPick.shot));
        next.add(trimKey(slot.frameNumber, slot.shot));
        return [...next];
      });
      setSwapPick(null);
      refreshBoard();
      const label = slot.kind === "image" ? "картинки" : "видео";
      toast.success(
        res.mode === "move"
          ? `${label}: перенос #${res.from_frame}.${res.from_shot} → #${res.to_frame}.${res.to_shot}`
          : `${label}: обмен #${swapPick.frameNumber}.${swapPick.shot} ↔ #${slot.frameNumber}.${slot.shot}`,
      );
    } catch (e) {
      toast.error(errorMessageFromUnknown(e));
    } finally {
      setSwapBusy(false);
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

  const pendingSlotKeys = useMemo(() => {
    const keys = new Set<string>();
    for (const op of pendingOps) {
      keys.add(slotKeyFromOp(op));
    }
    return keys;
  }, [pendingOps]);

  const failedSlotKeys = useMemo(() => new Set(failedHighlights), [failedHighlights]);
  const appliedSlotKeys = useMemo(() => new Set(highlights), [highlights]);

  const toneForSlot = useCallback(
    (key: string): SlotTone | undefined => {
      // failed > pending (доделать) > applied
      if (failedSlotKeys.has(key)) return "failed";
      if (pendingSlotKeys.has(key)) return "pending";
      if (appliedSlotKeys.has(key)) return "applied";
      return undefined;
    },
    [failedSlotKeys, pendingSlotKeys, appliedSlotKeys],
  );

  const pendingOnlyCount = useMemo(() => {
    let n = 0;
    for (const key of pendingSlotKeys) {
      if (!failedSlotKeys.has(key)) n += 1;
    }
    return n;
  }, [pendingSlotKeys, failedSlotKeys]);

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
      if (e.key !== "Escape") return;
      if (swapPick) {
        e.preventDefault();
        setSwapPick(null);
        return;
      }
      if (!preview) onClose();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open, onClose, preview, swapPick]);

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
                {swapPick
                  ? `Обмен ${swapPick.kind === "image" ? "картинок" : "видео"}: выбран #${swapPick.frameNumber}.${swapPick.shot} — нажмите ↔ на другом слоте (Esc — отмена)`
                  : "Кадры ролика — ↔ на двух слотах меняет местами картинки или видео"}
              </p>
              {(highlights.length > 0 ||
                failedHighlights.length > 0 ||
                pendingOnlyCount > 0) && (
                <p className="mt-1 flex flex-wrap items-center gap-x-3 gap-y-0.5 text-[11px] text-muted-foreground">
                  {highlights.length > 0 && (
                    <span>
                      <span className="mr-1 inline-block h-2 w-2 rounded-full bg-emerald-400/80" />
                      применено {highlights.length}
                    </span>
                  )}
                  {pendingOnlyCount > 0 && (
                    <span>
                      <span className="mr-1 inline-block h-2 w-2 rounded-full bg-amber-400/80" />
                      в очереди {pendingOnlyCount}
                    </span>
                  )}
                  {failedHighlights.length > 0 && (
                    <span>
                      <span className="mr-1 inline-block h-2 w-2 rounded-full bg-rose-500/80" />
                      ошибка {failedHighlights.length}
                    </span>
                  )}
                </p>
              )}
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
              title="Опционально: CDP-подбор готовых карточек из галереи Outsee (перегенерация через API этого не требует)"
              onClick={() => recoverOutseeMutation.mutate()}
            >
              {recoverOutseeMutation.isPending || recoverRunning ? (
                <Loader2 className="h-4 w-4 animate-spin" />
              ) : (
                <RefreshCw className="h-4 w-4" />
              )}
              {recoverRunning ? "Забираем из галереи…" : "Галерея Outsee (CDP)"}
            </Button>
            <AudioAlignPopover
              projectId={projectId}
              onFinished={() => {
                void queryClient.invalidateQueries({
                  queryKey: ["montage-board", projectId],
                });
                void board.refetch();
              }}
            />
            <Button
              type="button"
              size="sm"
              variant="outline"
              className="h-9 gap-1.5 text-xs"
              disabled={!projectId || montageMutation.isPending || montageActive}
              onClick={() => montageMutation.mutate()}
            >
              {montageMutation.isPending || montageActive ? (
                <Loader2 className="h-4 w-4 animate-spin" />
              ) : (
                <Clapperboard className="h-4 w-4" />
              )}
              Монтаж
            </Button>
            <MontageExtrasPopover projectId={projectId} />
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
                            "border-b border-white/10 px-2 py-2 text-center font-mono text-xs",
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
                              style={{
                                contentVisibility: "auto",
                                containIntrinsicSize: "240px 180px",
                              }}
                            >
                              {collapsed ? (
                                <div className="h-8 rounded-md bg-black/10" />
                              ) : row.key === "voiceover" ? (
                                <p className="whitespace-pre-wrap text-xs leading-snug text-foreground/90">
                                  {voiceoverForFrame(fr) || "—"}
                                </p>
                              ) : row.key === "characters" ? (
                                <CharactersCell fr={fr} onPreview={showPreview} />
                              ) : row.key === "timestamps" ? (
                                <TimestampCell fr={fr} />
                              ) : row.key === "image1" ? (
                                <ClickableMedia
                                  url={fr.image_shot1_url}
                                  kind="image"
                                  label={`Изображение 1 · кадр #${fr.number}`}
                                  onPreview={showPreview}
                                  scrollRootRef={tableScrollRef}
                                  imageSlot={{ frameNumber: fr.number, shot: 1 }}
                                  onImageDrop={(from) =>
                                    void handleMoveImage(from, {
                                      frameNumber: fr.number,
                                      shot: 1,
                                    })
                                  }
                                  onRegen={() =>
                                    queueOp({
                                      type: "image_regen",
                                      frame_number: fr.number,
                                      shot: 1,
                                      prompt: sourcePromptFor("image", fr.number, 1),
                                    })
                                  }
                                  onEditPrompt={() => openPromptModal("image", fr.number, 1, "prompt")}
                                  onAiChange={() =>
                                    queueOp({
                                      type: "image_ai_change",
                                      frame_number: fr.number,
                                      shot: 1,
                                    })
                                  }
                                  onRegenWithCorrection={() =>
                                    openPromptModal("image", fr.number, 1, "correction")
                                  }
                                  onDelete={() => void handleDeleteImage(fr.number, 1)}
                                  onUpload={(file) => void handleUploadImage(fr.number, 1, file)}
                                  slotTone={toneForSlot(`${fr.number}:image1`)}
                                  onSwapPick={() =>
                                    void handleSwapPick({
                                      kind: "image",
                                      frameNumber: fr.number,
                                      shot: 1,
                                    })
                                  }
                                  swapSelected={isSwapSelected("image", fr.number, 1)}
                                  swapBusy={swapBusy}
                                />
                              ) : row.key === "image2" ? (
                                <ClickableMedia
                                  url={fr.image_shot2_url}
                                  kind="image"
                                  label={`Изображение 2 · кадр #${fr.number}`}
                                  onPreview={showPreview}
                                  scrollRootRef={tableScrollRef}
                                  imageSlot={{ frameNumber: fr.number, shot: 2 }}
                                  onImageDrop={(from) =>
                                    void handleMoveImage(from, {
                                      frameNumber: fr.number,
                                      shot: 2,
                                    })
                                  }
                                  onRegen={() =>
                                    queueOp({
                                      type: "image_regen",
                                      frame_number: fr.number,
                                      shot: 2,
                                      prompt: sourcePromptFor("image", fr.number, 2),
                                    })
                                  }
                                  onEditPrompt={() => openPromptModal("image", fr.number, 2, "prompt")}
                                  onAiChange={() =>
                                    queueOp({
                                      type: "image_ai_change",
                                      frame_number: fr.number,
                                      shot: 2,
                                    })
                                  }
                                  onRegenWithCorrection={() =>
                                    openPromptModal("image", fr.number, 2, "correction")
                                  }
                                  onDelete={() => void handleDeleteImage(fr.number, 2)}
                                  onUpload={(file) => void handleUploadImage(fr.number, 2, file)}
                                  slotTone={toneForSlot(`${fr.number}:image2`)}
                                  onSwapPick={() =>
                                    void handleSwapPick({
                                      kind: "image",
                                      frameNumber: fr.number,
                                      shot: 2,
                                    })
                                  }
                                  swapSelected={isSwapSelected("image", fr.number, 2)}
                                  swapBusy={swapBusy}
                                />
                              ) : row.key === "video1" ? (
                                <VideoMediaCell
                                  fr={fr}
                                  shot={1}
                                  url={fr.video_shot1_url}
                                  onPreview={showPreview}
                                  scrollRootRef={tableScrollRef}
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
                                  onAiChange={() =>
                                    queueOp({
                                      type: "video_ai_change",
                                      frame_number: fr.number,
                                      shot: 1,
                                    })
                                  }
                                  onDelete={() => void handleDeleteVideo(fr.number, 1)}
                                  onUpload={(file) => void handleUploadVideo(fr.number, 1, file)}
                                  slotTone={toneForSlot(trimKey(fr.number, 1))}
                                  stale={isStaleVideo(fr.number, 1)}
                                  onSwapPick={() =>
                                    void handleSwapPick({
                                      kind: "video",
                                      frameNumber: fr.number,
                                      shot: 1,
                                    })
                                  }
                                  swapSelected={isSwapSelected("video", fr.number, 1)}
                                  swapBusy={swapBusy}
                                />
                              ) : (
                                <VideoMediaCell
                                  fr={fr}
                                  shot={2}
                                  url={fr.video_shot2_url}
                                  onPreview={showPreview}
                                  scrollRootRef={tableScrollRef}
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
                                  onAiChange={() =>
                                    queueOp({
                                      type: "video_ai_change",
                                      frame_number: fr.number,
                                      shot: 2,
                                    })
                                  }
                                  onDelete={() => void handleDeleteVideo(fr.number, 2)}
                                  onUpload={(file) => void handleUploadVideo(fr.number, 2, file)}
                                  slotTone={toneForSlot(trimKey(fr.number, 2))}
                                  stale={isStaleVideo(fr.number, 2)}
                                  onSwapPick={() =>
                                    void handleSwapPick({
                                      kind: "video",
                                      frameNumber: fr.number,
                                      shot: 2,
                                    })
                                  }
                                  swapSelected={isSwapSelected("video", fr.number, 2)}
                                  swapBusy={swapBusy}
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
  busy,
}: {
  onClick: () => void;
  active?: boolean;
  busy?: boolean;
}) {
  return (
    <button
      type="button"
      title={busy ? "Монтаж выполняется — панель можно открыть" : "Панель монтажа"}
      className={cn(
        "nodrag nopan nowheel absolute left-1/2 z-40 flex -translate-x-1/2 items-center gap-1 rounded-full border px-2.5 py-1 text-[10px] font-semibold shadow-md backdrop-blur transition",
        "-top-9",
        busy
          ? "border-amber-400/40 bg-amber-500/15 text-amber-200/90"
          : active
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
      {busy ? (
        <Loader2 className="h-3.5 w-3.5 animate-spin" />
      ) : (
        <Clapperboard className="h-3.5 w-3.5" />
      )}
      {busy ? "Монтаж…" : "Монтаж"}
    </button>
  );
}
