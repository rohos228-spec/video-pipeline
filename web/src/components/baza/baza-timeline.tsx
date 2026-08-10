"use client";

/**
 * «Хронология» — одна лента кадров, как трек в видеоредакторе.
 * Минимум элементов: горизонтальный стрип превью-тумбов 9:16 слева направо
 * (хронология), сверху тонкая линейка сцен (сегмент = сцена, цветная
 * засечка). Закадр сцены скрыт — открывается кликом по сегменту линейки
 * (1 на сцену). Данные кадра — кликом по тумбу (панель справа).
 * Ничего лишнего: ни коробок, ни карточек в карточках.
 */

import { useMemo, useState } from "react";
import { ChevronDown, ChevronRight, Clock3, ImageOff, Mic } from "lucide-react";
import { type DbFrame, type DbGraph, type DbScene } from "@/lib/api";
import { cn } from "@/lib/utils";

const STATUS_DOT: Record<string, string> = {
  planned: "bg-white/40",
  image_prompt_ready: "bg-sky-400",
  image_generated: "bg-emerald-400",
  image_approved: "bg-emerald-500",
  animation_prompt_ready: "bg-violet-400",
  video_generated: "bg-green-500",
  video_approved: "bg-green-600",
  failed: "bg-red-500",
};

const STATUS_RU: Record<string, string> = {
  planned: "запланирован",
  image_prompt_ready: "промт картинки готов",
  image_generated: "картинка готова",
  image_approved: "картинка одобрена",
  animation_prompt_ready: "промт видео готов",
  video_generated: "видео готово",
  video_approved: "видео одобрено",
  failed: "ошибка",
};

interface SceneColumn {
  key: string;
  title: string;
  place: string;
  scene: DbScene | null;
  frames: DbFrame[];
}

const SCENE_HUES = [262, 200, 150, 35, 320, 88, 0, 175];

function orderFrames(frames: DbFrame[]): DbFrame[] {
  return [...frames].sort(
    (a, b) => (a.sort_key ?? a.number * 10) - (b.sort_key ?? b.number * 10),
  );
}

function sceneVoiceover(frames: DbFrame[]): { text: string; frame: DbFrame } | null {
  for (const f of frames) {
    if (f.voiceover_text.trim()) return { text: f.voiceover_text, frame: f };
  }
  return null;
}

export function BazaTimeline({
  graph,
  frameId,
  onSelect,
}: {
  graph: DbGraph;
  frameId: number | null;
  onSelect: (id: number) => void;
  projectId: number | null;
  onChanged: () => Promise<void>;
}) {
  /** Сегменты линейки с раскрытым закадром. */
  const [voScenes, setVoScenes] = useState<Set<string>>(new Set());
  /** Сцены со свёрнутым стрипом. */
  const [collapsed, setCollapsed] = useState<Set<string>>(new Set());

  const columns = useMemo<SceneColumn[]>(() => {
    const cols: SceneColumn[] = [];
    const scenes = [...graph.scenes].sort((a, b) => a.sort_key - b.sort_key);
    for (const sc of scenes) {
      const frames = orderFrames(graph.frames.filter((f) => f.scene_id === sc.id));
      if (!frames.length) continue;
      cols.push({
        key: `sc-${sc.id}`,
        title: sc.title || `Сцена ${cols.length + 1}`,
        place: sc.place || "",
        scene: sc,
        frames,
      });
    }
    const orphan = orderFrames(graph.frames.filter((f) => f.scene_id == null));
    if (orphan.length || !cols.length) {
      cols.push({
        key: "orphan",
        title: cols.length ? "Без сцены" : "Все кадры",
        place: "",
        scene: null,
        frames: orphan.length ? orphan : orderFrames(graph.frames),
      });
    }
    const shown = new Set(cols.flatMap((c) => c.frames.map((f) => f.id)));
    const rest = orderFrames(graph.frames.filter((f) => !shown.has(f.id)));
    if (rest.length) {
      cols.push({ key: "rest", title: "Остальные", place: "", scene: null, frames: rest });
    }
    return cols;
  }, [graph]);

  const toggle = (set: Set<string>, key: string): Set<string> => {
    const next = new Set(set);
    if (next.has(key)) next.delete(key);
    else next.add(key);
    return next;
  };

  if (graph.frames.length === 0) {
    return (
      <div className="flex flex-1 items-center justify-center text-sm text-white/30">
        В базе нет кадров
      </div>
    );
  }

  return (
    <div className="min-h-0 flex-1 overflow-auto px-2 py-2">
      <div className="flex min-w-max items-start gap-5">
        {columns.map((col, ci) => {
          const hue = SCENE_HUES[ci % SCENE_HUES.length];
          const vo = sceneVoiceover(col.frames);
          const voOpen = voScenes.has(col.key);
          const isCollapsed = collapsed.has(col.key);
          const total = col.frames.reduce((a, f) => a + (f.duration_seconds ?? 0), 0);
          return (
            <div key={col.key} className="shrink-0">
              {/* Линейка сцены: цветная засечка + название, клик — закадр */}
              <div
                className="group flex cursor-pointer items-center gap-1.5 pb-1.5 pl-0.5"
                onClick={() => setVoScenes((s) => toggle(s, col.key))}
                title={
                  voOpen
                    ? "Скрыть закадр сцены"
                    : "Показать закадр сцены (1 на сцену)"
                }
              >
                <span
                  className="h-[3px] w-6 rounded-full"
                  style={{ background: `hsl(${hue} 75% 60%)` }}
                />
                <span className="max-w-[14rem] truncate text-[11px] font-medium text-white/70 transition-colors group-hover:text-white/90">
                  {col.title}
                </span>
                <span className="text-[9px] text-white/30">
                  {col.frames.length}
                  {total > 0 ? ` · ${total.toFixed(0)}с` : ""}
                </span>
                {vo && (
                  <Mic
                    className={cn(
                      "h-3 w-3 transition-colors",
                      voOpen ? "text-white/70" : "text-white/25 group-hover:text-white/50",
                    )}
                  />
                )}
                <button
                  type="button"
                  title={isCollapsed ? "Развернуть кадры" : "Свернуть кадры"}
                  onClick={(e) => {
                    e.stopPropagation();
                    setCollapsed((s) => toggle(s, col.key));
                  }}
                  className="rounded p-0.5 text-white/25 hover:bg-white/10 hover:text-white/70"
                >
                  {isCollapsed ? (
                    <ChevronRight className="h-3 w-3" />
                  ) : (
                    <ChevronDown className="h-3 w-3" />
                  )}
                </button>
              </div>

              {voOpen && (
                <div className="animate-in fade-in slide-in-from-top-1 mb-2 max-w-[24rem] duration-150">
                  {vo ? (
                    <p className="whitespace-pre-wrap border-l-2 pl-2 text-[11px] italic leading-snug text-white/65"
                      style={{ borderLeftColor: `hsl(${hue} 75% 60% / 0.6)` }}
                    >
                      {vo.text}
                    </p>
                  ) : (
                    <span className="text-[11px] text-white/25">— нет закадра —</span>
                  )}
                </div>
              )}

              {!isCollapsed && (
                <div className="flex items-start gap-1">
                  {col.frames.map((f) => (
                    <FrameThumb
                      key={f.id}
                      frame={f}
                      active={f.id === frameId}
                      onClick={() => onSelect(f.id)}
                    />
                  ))}
                </div>
              )}
            </div>
          );
        })}
      </div>
    </div>
  );
}

/** Тумб кадра: картинка 9:16, номер и статус на scrim, время пилюлей. */
function FrameThumb({
  frame: f,
  active,
  onClick,
}: {
  frame: DbFrame;
  active: boolean;
  onClick: () => void;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      title={`Кадр #${f.number} — ${STATUS_RU[f.status ?? ""] ?? "—"}`}
      className={cn(
        "group relative w-[72px] shrink-0 overflow-hidden rounded-lg border transition-all duration-150 hover:-translate-y-0.5",
        active
          ? "border-primary/60 ring-2 ring-primary/30"
          : "border-white/10 hover:border-white/30",
      )}
    >
      <div className="aspect-[9/16] w-full overflow-hidden bg-black/50">
        {f.image_url ? (
          // eslint-disable-next-line @next/next/no-img-element
          <img
            src={f.image_url}
            alt={`Кадр #${f.number}`}
            className="h-full w-full object-cover"
            loading="lazy"
          />
        ) : (
          <div className="flex h-full w-full flex-col items-center justify-center gap-1 text-white/15">
            <ImageOff className="h-4 w-4" />
            <span className="font-mono text-[10px]">#{f.number}</span>
          </div>
        )}
      </div>
      <div className="absolute inset-x-0 top-0 flex items-center gap-1 bg-gradient-to-b from-black/70 to-transparent px-1.5 pb-2.5 pt-1">
        <span
          className={cn(
            "h-1.5 w-1.5 shrink-0 rounded-full",
            STATUS_DOT[f.status ?? ""] ?? "bg-white/40",
          )}
        />
        <span className="font-mono text-[10px] font-medium text-white/90">#{f.number}</span>
      </div>
      {f.duration_seconds != null && (
        <div className="absolute bottom-1 right-1 flex items-center gap-0.5 rounded-full bg-black/55 px-1 py-0.5 text-[8.5px] text-white/75">
          <Clock3 className="h-2 w-2" />
          {f.duration_seconds}с
        </div>
      )}
    </button>
  );
}
