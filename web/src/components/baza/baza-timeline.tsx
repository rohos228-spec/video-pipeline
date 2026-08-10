"use client";

/**
 * «Хронология» — горизонтальный таймлайн базы проекта: сцены идут слева
 * направо по порядку (sort_key), кадры внутри сцены соединены цепочкой
 * сверху вниз. Закадровый текст показывается один раз на сцену (VO-ячейка
 * живёт на родительском кадре — шоты SET её не дублируют). Данные кадра
 * раскрываются/скрываются: и по одному (шеврон), и слоями глобально
 * (закадр / промты / время).
 */

import { useMemo, useState } from "react";
import {
  ChevronDown,
  ChevronRight,
  ChevronUp,
  Clock3,
  Layers,
  Mic,
  MoveRight,
  Plus,
} from "lucide-react";
import { toast } from "sonner";
import { api, type DbFrame, type DbGraph, type DbScene } from "@/lib/api";
import { cn } from "@/lib/utils";

const STATUS_DOT: Record<string, string> = {
  planned: "bg-white/30",
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

const ATTR_CHIPS: { key: string; label: string }[] = [
  { key: "place", label: "место" },
  { key: "visual_type", label: "тип" },
  { key: "cluster", label: "кластер" },
  { key: "edit_type", label: "стык" },
  { key: "main_action", label: "действие" },
];

export interface TimelineLayers {
  voiceover: boolean;
  prompts: boolean;
  timing: boolean;
}

function attrStr(attrs: Record<string, unknown> | null | undefined, key: string): string {
  const v = attrs?.[key];
  if (v == null) return "";
  return typeof v === "string" ? v : String(v);
}

function activePrompt(f: DbFrame, kind: "img" | "video"): string {
  const pv = f.prompts.find((p) => p.kind === kind && p.is_active && p.text.trim());
  if (pv) return pv.text;
  return (kind === "img" ? f.image_prompt : f.animation_prompt) ?? "";
}

function frameHasImg(f: DbFrame): boolean {
  return !!activePrompt(f, "img").trim();
}

function frameHasVideo(f: DbFrame): boolean {
  return !!activePrompt(f, "video").trim();
}

/** Закадр сцены — текст VO-родителя (первый кадр с непустым закадром). */
function sceneVoiceover(frames: DbFrame[]): { text: string; frame: DbFrame } | null {
  for (const f of frames) {
    if (f.voiceover_text.trim()) return { text: f.voiceover_text, frame: f };
  }
  return null;
}

function sceneDuration(frames: DbFrame[]): number {
  return frames.reduce((acc, f) => acc + (f.duration_seconds ?? 0), 0);
}

function orderFrames(frames: DbFrame[]): DbFrame[] {
  return [...frames].sort(
    (a, b) => (a.sort_key ?? a.number * 10) - (b.sort_key ?? b.number * 10),
  );
}

export function BazaTimeline({
  graph,
  frameId,
  onSelect,
  projectId,
  onChanged,
}: {
  graph: DbGraph;
  frameId: number | null;
  onSelect: (id: number) => void;
  projectId: number | null;
  onChanged: () => Promise<void>;
}) {
  const [layers, setLayers] = useState<TimelineLayers>({
    voiceover: true,
    prompts: true,
    timing: true,
  });
  const [expanded, setExpanded] = useState<Set<number>>(new Set());
  const [collapsedScenes, setCollapsedScenes] = useState<Set<string>>(new Set());

  const columns = useMemo(() => {
    const cols: {
      key: string;
      title: string;
      place: string;
      scene: DbScene | null;
      frames: DbFrame[];
    }[] = [];
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
    // Кадры со scene_id на несуществующую сцену — не терять.
    const shown = new Set(cols.flatMap((c) => c.frames.map((f) => f.id)));
    const rest = orderFrames(graph.frames.filter((f) => !shown.has(f.id)));
    if (rest.length) {
      cols.push({ key: "rest", title: "Остальные", place: "", scene: null, frames: rest });
    }
    return cols;
  }, [graph]);

  const toggleFrame = (id: number) =>
    setExpanded((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });

  const toggleScene = (key: string) =>
    setCollapsedScenes((prev) => {
      const next = new Set(prev);
      if (next.has(key)) next.delete(key);
      else next.add(key);
      return next;
    });

  const layerToggle = (key: keyof TimelineLayers, label: string) => (
    <button
      type="button"
      onClick={() => setLayers((prev) => ({ ...prev, [key]: !prev[key] }))}
      className={cn(
        "shrink-0 rounded-md px-2.5 py-1 text-xs transition-colors",
        layers[key]
          ? "bg-primary/20 text-primary"
          : "bg-white/[0.04] text-white/40 hover:bg-white/[0.08]",
      )}
      title={`${layers[key] ? "Скрыть" : "Показать"}: ${label}`}
    >
      {label}
    </button>
  );

  if (graph.frames.length === 0) {
    return (
      <div className="flex flex-1 items-center justify-center text-sm text-white/30">
        В базе нет кадров
      </div>
    );
  }

  return (
    <div className="flex min-h-0 flex-1 flex-col gap-2">
      <div className="flex shrink-0 flex-wrap items-center gap-1.5">
        <span className="mr-1 shrink-0 text-[10px] uppercase tracking-[0.18em] text-white/35">
          Данные
        </span>
        {layerToggle("voiceover", "Закадр")}
        {layerToggle("prompts", "Промты")}
        {layerToggle("timing", "Время")}
        <span className="ml-2 text-[10px] text-white/25">
          сцены идут слева направо · кадры соединены цепочкой · шеврон на кадре —
          подробности
        </span>
      </div>

      <div className="min-h-0 flex-1 overflow-x-auto overflow-y-hidden pb-2">
        <div className="flex h-full items-stretch gap-0">
          {columns.map((col, ci) => {
            const vo = sceneVoiceover(col.frames);
            const extraVo = col.frames.filter(
              (f) => f.voiceover_text.trim() && vo && f.id !== vo.frame.id,
            );
            const collapsed = collapsedScenes.has(col.key);
            const total = sceneDuration(col.frames);
            return (
              <div key={col.key} className="flex items-stretch">
                {ci > 0 && (
                  <div className="flex w-8 shrink-0 flex-col items-center justify-center">
                    <MoveRight className="h-4 w-4 text-white/25" />
                  </div>
                )}
                <section className="flex w-[320px] shrink-0 flex-col overflow-hidden rounded-xl border border-white/[0.08] bg-white/[0.02]">
                  <header className="flex shrink-0 items-center gap-2 border-b border-white/[0.07] bg-white/[0.03] px-3 py-2">
                    <Layers className="h-3.5 w-3.5 shrink-0 text-primary/70" />
                    <div className="min-w-0 flex-1">
                      <div className="truncate text-xs font-semibold">{col.title}</div>
                      <div className="text-[10px] text-white/40">
                        {col.frames.length} кадр.
                        {layers.timing && total > 0 ? ` · Σ ${total.toFixed(1)}с` : ""}
                        {col.place ? ` · ${col.place}` : ""}
                      </div>
                    </div>
                    <button
                      type="button"
                      title={collapsed ? "Развернуть сцену" : "Свернуть сцену"}
                      onClick={() => toggleScene(col.key)}
                      className="rounded p-1 text-white/40 hover:bg-white/10 hover:text-white"
                    >
                      {collapsed ? (
                        <ChevronRight className="h-3.5 w-3.5" />
                      ) : (
                        <ChevronDown className="h-3.5 w-3.5" />
                      )}
                    </button>
                  </header>

                  {!collapsed && layers.voiceover && (
                    <div className="shrink-0 border-b border-white/[0.07] bg-black/25 px-3 py-2">
                      <div className="mb-1 flex items-center gap-1.5 text-[9px] uppercase tracking-[0.16em] text-white/35">
                        <Mic className="h-3 w-3" />
                        Закадр сцены
                        {vo ? (
                          <span className="text-white/25">· кадр #{vo.frame.number}</span>
                        ) : null}
                      </div>
                      {vo ? (
                        <div className="whitespace-pre-wrap text-[11px] leading-snug text-white/80">
                          {vo.text}
                        </div>
                      ) : (
                        <div className="text-[11px] text-white/25">— нет закадра —</div>
                      )}
                      {extraVo.length > 0 && (
                        <div className="mt-1 text-[9px] text-amber-300/70">
                          + ещё закадр на кадрах:{" "}
                          {extraVo.map((f) => `#${f.number}`).join(", ")}
                        </div>
                      )}
                    </div>
                  )}

                  {!collapsed && (
                    <div className="min-h-0 flex-1 overflow-y-auto px-2 py-2">
                      {col.frames.map((f, fi) => (
                        <div key={f.id}>
                          {fi > 0 && (
                            <div className="flex justify-center py-0.5">
                              <div className="h-3 w-px bg-white/15" />
                            </div>
                          )}
                          <TimelineFrameCard
                            frame={f}
                            active={f.id === frameId}
                            expanded={expanded.has(f.id)}
                            layers={layers}
                            allFrames={graph.frames}
                            onToggle={() => toggleFrame(f.id)}
                            onSelect={() => onSelect(f.id)}
                            onInsertAfter={() => {
                              if (projectId == null) return;
                              void (async () => {
                                await api.dbInsertFrame(projectId, f.id, f.scene_id);
                                toast.success("Кадр добавлен");
                                void onChanged();
                              })();
                            }}
                          />
                        </div>
                      ))}
                    </div>
                  )}
                </section>
              </div>
            );
          })}
        </div>
      </div>
    </div>
  );
}

function TimelineFrameCard({
  frame: f,
  active,
  expanded,
  layers,
  allFrames,
  onToggle,
  onSelect,
  onInsertAfter,
}: {
  frame: DbFrame;
  active: boolean;
  expanded: boolean;
  layers: TimelineLayers;
  allFrames: DbFrame[];
  onToggle: () => void;
  onSelect: () => void;
  onInsertAfter: () => void;
}) {
  const place = attrStr(f.attrs, "place");
  const sense = attrStr(f.attrs, "scene_sense");
  const ownVo = f.voiceover_text.trim();
  const img = activePrompt(f, "img");
  const vid = activePrompt(f, "video");
  const chips = ATTR_CHIPS.map((c) => ({ ...c, value: attrStr(f.attrs, c.key) })).filter(
    (c) => c.value,
  );

  return (
    <div
      role="button"
      tabIndex={0}
      onClick={onSelect}
      onKeyDown={(e) => {
        if (e.key === "Enter" || e.key === " ") onSelect();
      }}
      className={cn(
        "cursor-pointer rounded-lg border p-2 text-left transition",
        active
          ? "border-primary/50 bg-primary/10"
          : "border-white/[0.08] bg-white/[0.02] hover:border-white/20 hover:bg-white/[0.04]",
      )}
    >
      <div className="flex items-center gap-1.5">
        <span
          className={cn("h-2 w-2 shrink-0 rounded-full", STATUS_DOT[f.status ?? ""] ?? "bg-white/30")}
          title={STATUS_RU[f.status ?? ""] ?? f.status ?? "—"}
        />
        <span className="font-mono text-[11px] text-white/75">#{f.number}</span>
        <span className="truncate text-[10px] text-white/35">
          {STATUS_RU[f.status ?? ""] ?? "—"}
        </span>
        {layers.timing && f.duration_seconds != null && (
          <span className="flex shrink-0 items-center gap-0.5 rounded bg-black/40 px-1 py-0.5 text-[9px] text-white/50">
            <Clock3 className="h-2.5 w-2.5" />
            {f.duration_seconds}с
          </span>
        )}
        <span className="ml-auto flex shrink-0 gap-1 text-[9px]">
          <span className={frameHasImg(f) ? "text-emerald-400" : "text-white/25"}>
            img {frameHasImg(f) ? "✓" : "—"}
          </span>
          <span className={frameHasVideo(f) ? "text-emerald-400" : "text-white/25"}>
            vid {frameHasVideo(f) ? "✓" : "—"}
          </span>
        </span>
        <button
          type="button"
          title={expanded ? "Скрыть данные" : "Показать данные"}
          onClick={(e) => {
            e.stopPropagation();
            onToggle();
          }}
          className="shrink-0 rounded p-0.5 text-white/35 hover:bg-white/10 hover:text-white"
        >
          {expanded ? (
            <ChevronUp className="h-3.5 w-3.5" />
          ) : (
            <ChevronDown className="h-3.5 w-3.5" />
          )}
        </button>
      </div>

      <div className="mt-1 line-clamp-2 text-[11px] text-white/80">
        {place || f.meaning || ownVo || "—"}
      </div>

      {expanded && (
        <div className="mt-2 flex flex-col gap-1.5 border-t border-white/[0.07] pt-2">
          {ownVo && layers.voiceover ? (
            <div className="rounded bg-black/30 p-1.5">
              <div className="text-[9px] uppercase tracking-wide text-white/35">
                Закадр кадра (VO-родитель)
              </div>
              <div className="whitespace-pre-wrap text-[10.5px] text-white/75">{ownVo}</div>
            </div>
          ) : null}
          {f.meaning ? (
            <div className="rounded bg-black/30 p-1.5">
              <div className="text-[9px] uppercase tracking-wide text-white/35">Смысл</div>
              <div className="whitespace-pre-wrap text-[10.5px] text-white/70">{f.meaning}</div>
            </div>
          ) : null}
          {sense ? (
            <div className="rounded bg-black/30 p-1.5">
              <div className="text-[9px] uppercase tracking-wide text-white/35">Смысл сцены</div>
              <div className="whitespace-pre-wrap text-[10.5px] text-white/60">{sense}</div>
            </div>
          ) : null}
          {layers.prompts && img ? (
            <div className="rounded bg-black/30 p-1.5">
              <div className="text-[9px] uppercase tracking-wide text-white/35">
                Промт картинки
              </div>
              <div className="line-clamp-3 whitespace-pre-wrap text-[10.5px] text-white/60">
                {img}
              </div>
            </div>
          ) : null}
          {layers.prompts && vid ? (
            <div className="rounded bg-black/30 p-1.5">
              <div className="text-[9px] uppercase tracking-wide text-white/35">Промт видео</div>
              <div className="line-clamp-3 whitespace-pre-wrap text-[10.5px] text-white/60">
                {vid}
              </div>
            </div>
          ) : null}
          {chips.length > 0 && (
            <div className="flex flex-wrap gap-1">
              {chips.map((c) => (
                <span
                  key={c.key}
                  title={c.value}
                  className="rounded-full border border-white/10 bg-black/30 px-1.5 py-0.5 text-[9px] text-white/50"
                >
                  {c.label}: {c.value.length > 24 ? `${c.value.slice(0, 24)}…` : c.value}
                </span>
              ))}
            </div>
          )}
          {f.edges.length > 0 && (
            <div className="flex flex-wrap gap-1">
              {f.edges.map((e) => (
                <span
                  key={e.id}
                  className="rounded-full border border-sky-400/20 bg-sky-500/10 px-1.5 py-0.5 text-[9px] text-sky-300/80"
                >
                  → кадр {allFrames.find((x) => x.id === e.to_frame_id)?.number ?? e.to_frame_id}
                </span>
              ))}
            </div>
          )}
          <div className="flex justify-end">
            <button
              type="button"
              title="Вставить кадр после"
              onClick={(e) => {
                e.stopPropagation();
                onInsertAfter();
              }}
              className="flex items-center gap-1 rounded px-1.5 py-0.5 text-[9px] text-white/35 hover:bg-white/10 hover:text-white"
            >
              <Plus className="h-3 w-3" />
              кадр после
            </button>
          </div>
        </div>
      )}
    </div>
  );
}
