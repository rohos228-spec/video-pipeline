"use client";

/**
 * «Хронология» — фильм-стрип проекта: сцены и кадры на одной горизонтальной
 * оси слева направо. Кадры — превью-тумбы 9:16 с реальными картинками на
 * общем рельсе-линии; сцены — стеклянные группы с цветовым акцентом.
 * Никакого всегда видимого текста: закадр сцены — за тумблером в шапке
 * (1 на сцену), данные кадра — по клику на тумб (карточка внутри группы).
 * Всё сворачивается, раскрытие — с плавной анимацией.
 */

import { useMemo, useState } from "react";
import {
  ChevronDown,
  ChevronRight,
  Clock3,
  ImageOff,
  Mic,
  MoveRight,
  Plus,
  X,
} from "lucide-react";
import { toast } from "sonner";
import { api, type DbFrame, type DbGraph, type DbScene } from "@/lib/api";
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

const ATTR_CHIPS: { key: string; label: string }[] = [
  { key: "place", label: "место" },
  { key: "visual_type", label: "тип" },
  { key: "cluster", label: "кластер" },
  { key: "edit_type", label: "стык" },
  { key: "main_action", label: "действие" },
];

interface SceneColumn {
  key: string;
  title: string;
  place: string;
  scene: DbScene | null;
  frames: DbFrame[];
}

const SCENE_HUES = [262, 200, 150, 35, 320, 88, 0, 175];

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

function sceneProgress(frames: DbFrame[]): number {
  if (!frames.length) return 0;
  const done = frames.filter((f) =>
    ["video_generated", "video_approved"].includes(f.status ?? ""),
  ).length;
  return done / frames.length;
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
  const [openFrame, setOpenFrame] = useState<number | null>(null);
  const [voScenes, setVoScenes] = useState<Set<string>>(new Set());
  const [collapsedScenes, setCollapsedScenes] = useState<Set<string>>(new Set());

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

  const toggleSet = (set: Set<string>, key: string): Set<string> => {
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
    <div className="min-h-0 flex-1 overflow-auto px-1 py-2">
      <div className="flex min-w-max items-start gap-2">
        {columns.map((col, ci) => {
          const hue = SCENE_HUES[ci % SCENE_HUES.length];
          const vo = sceneVoiceover(col.frames);
          const voOpen = voScenes.has(col.key);
          const collapsed = collapsedScenes.has(col.key);
          const total = col.frames.reduce((a, f) => a + (f.duration_seconds ?? 0), 0);
          const progress = sceneProgress(col.frames);
          const openInScene =
            openFrame != null && col.frames.some((f) => f.id === openFrame)
              ? openFrame
              : null;
          return (
            <div key={col.key} className="flex items-start">
              {ci > 0 && (
                <div className="flex h-[120px] w-7 shrink-0 items-center justify-center">
                  <div className="flex items-center">
                    <div className="h-px w-3 bg-gradient-to-r from-white/5 to-white/25" />
                    <MoveRight className="h-3.5 w-3.5 text-white/25" />
                  </div>
                </div>
              )}
              <section
                className="shrink-0 rounded-2xl border border-white/[0.07] bg-white/[0.02] shadow-[0_12px_40px_rgba(0,0,0,0.35)] backdrop-blur-sm"
                style={{ boxShadow: `0 12px 40px rgba(0,0,0,0.35), 0 0 0 1px hsl(${hue} 60% 55% / 0.06), 0 0 32px hsl(${hue} 60% 50% / 0.05)` }}
              >
                {/* Цветовая кромка сцены */}
                <div
                  className="h-[3px] rounded-t-2xl"
                  style={{
                    background: `linear-gradient(90deg, hsl(${hue} 75% 60% / 0.9), hsl(${hue} 75% 60% / 0.15))`,
                  }}
                />
                <header className="flex items-center gap-2 px-3 pb-1.5 pt-2.5">
                  <span
                    className="flex h-6 w-6 shrink-0 items-center justify-center rounded-lg text-[10px] font-bold text-white/90"
                    style={{
                      background: `linear-gradient(145deg, hsl(${hue} 70% 55% / 0.9), hsl(${hue} 70% 40% / 0.7))`,
                    }}
                  >
                    {ci + 1}
                  </span>
                  <div className="min-w-0">
                    <div className="max-w-[15rem] truncate text-xs font-semibold tracking-tight text-white/90">
                      {col.title}
                    </div>
                    <div className="mt-px text-[10px] text-white/35">
                      {col.frames.length} кадр.
                      {total > 0 ? ` · Σ ${total.toFixed(1)}с` : ""}
                      {col.place ? ` · ${col.place}` : ""}
                    </div>
                  </div>
                  <div className="ml-auto flex shrink-0 items-center gap-1">
                    <button
                      type="button"
                      title={voOpen ? "Скрыть закадр сцены" : "Показать закадр сцены"}
                      onClick={() => setVoScenes((s) => toggleSet(s, col.key))}
                      className={cn(
                        "flex items-center gap-1 rounded-full border px-2 py-0.5 text-[9px] font-medium transition-all",
                        voOpen
                          ? "border-white/20 bg-white/10 text-white/80"
                          : "border-white/10 text-white/35 hover:border-white/20 hover:text-white/60",
                      )}
                    >
                      <Mic className="h-3 w-3" />
                      закадр
                      {vo && !voOpen && (
                        <span
                          className="h-1 w-1 rounded-full"
                          style={{ background: `hsl(${hue} 80% 65%)` }}
                        />
                      )}
                    </button>
                    <button
                      type="button"
                      title={collapsed ? "Развернуть кадры" : "Свернуть кадры"}
                      onClick={() => setCollapsedScenes((s) => toggleSet(s, col.key))}
                      className="rounded-full p-1 text-white/35 transition-colors hover:bg-white/10 hover:text-white"
                    >
                      {collapsed ? (
                        <ChevronRight className="h-3.5 w-3.5" />
                      ) : (
                        <ChevronDown className="h-3.5 w-3.5" />
                      )}
                    </button>
                  </div>
                </header>

                {/* Прогресс сцены: доля кадров с готовым видео */}
                {!collapsed && col.frames.length > 1 && (
                  <div className="mx-3 mb-1 h-[2px] overflow-hidden rounded-full bg-white/[0.06]">
                    <div
                      className="h-full rounded-full transition-all duration-500"
                      style={{
                        width: `${Math.round(progress * 100)}%`,
                        background: `hsl(${hue} 75% 58% / 0.8)`,
                      }}
                    />
                  </div>
                )}

                {voOpen && (
                  <div className="animate-in fade-in slide-in-from-top-1 mx-3 mb-2 duration-200">
                    <div
                      className="max-w-[26rem] rounded-xl border-l-2 bg-black/30 px-3 py-2"
                      style={{ borderLeftColor: `hsl(${hue} 75% 60% / 0.7)` }}
                    >
                      {vo ? (
                        <>
                          <div className="mb-0.5 flex items-center gap-1 text-[9px] uppercase tracking-[0.16em] text-white/35">
                            <Mic className="h-3 w-3" />
                            Закадр сцены · кадр #{vo.frame.number}
                          </div>
                          <p className="whitespace-pre-wrap text-[11px] italic leading-snug text-white/80">
                            {vo.text}
                          </p>
                        </>
                      ) : (
                        <span className="text-[11px] text-white/25">— нет закадра —</span>
                      )}
                    </div>
                  </div>
                )}

                {!collapsed && (
                  <div className="relative px-3 pb-3">
                    {/* Рельс хронологии за тумбами */}
                    <div className="pointer-events-none absolute inset-x-3 top-[60px] h-px bg-gradient-to-r from-white/[0.04] via-white/[0.14] to-white/[0.04]" />
                    <div className="relative flex items-center gap-1.5">
                      {col.frames.map((f) => (
                        <FrameBead
                          key={f.id}
                          frame={f}
                          active={f.id === frameId}
                          open={openFrame === f.id}
                          onClick={() => {
                            setOpenFrame((cur) => (cur === f.id ? null : f.id));
                            onSelect(f.id);
                          }}
                        />
                      ))}
                    </div>
                  </div>
                )}

                {!collapsed && openInScene != null && (
                  <FrameInlineCard
                    frame={col.frames.find((f) => f.id === openInScene)!}
                    graph={graph}
                    projectId={projectId}
                    onClose={() => setOpenFrame(null)}
                    onChanged={onChanged}
                  />
                )}
              </section>
            </div>
          );
        })}
      </div>
    </div>
  );
}

/** Тумб кадра на рельсе: картинка 9:16, scrim-оверлеи, hover-подъём. */
function FrameBead({
  frame: f,
  active,
  open,
  onClick,
}: {
  frame: DbFrame;
  active: boolean;
  open: boolean;
  onClick: () => void;
}) {
  const hasImg = !!activePrompt(f, "img").trim();
  const hasVid = !!activePrompt(f, "video").trim();
  return (
    <button
      type="button"
      onClick={onClick}
      title={`Кадр #${f.number} — ${STATUS_RU[f.status ?? ""] ?? "—"}. Клик — данные кадра`}
      className={cn(
        "group relative w-[76px] shrink-0 overflow-hidden rounded-xl border text-left shadow-lg shadow-black/40 transition-all duration-200 hover:-translate-y-1 hover:shadow-xl",
        open
          ? "border-primary/70 ring-2 ring-primary/40"
          : active
            ? "border-primary/50 ring-1 ring-primary/30"
            : "border-white/10 hover:border-white/30",
      )}
    >
      <div className="aspect-[9/16] w-full overflow-hidden bg-black/50">
        {f.image_url ? (
          // eslint-disable-next-line @next/next/no-img-element
          <img
            src={f.image_url}
            alt={`Кадр #${f.number}`}
            className="h-full w-full object-cover transition-transform duration-300 group-hover:scale-[1.06]"
            loading="lazy"
          />
        ) : (
          <div className="flex h-full w-full flex-col items-center justify-center gap-1 text-white/15">
            <ImageOff className="h-4 w-4" />
            <span className="font-mono text-[10px]">#{f.number}</span>
          </div>
        )}
      </div>
      <div className="absolute inset-x-0 top-0 flex items-center gap-1 bg-gradient-to-b from-black/75 via-black/30 to-transparent px-1.5 pb-3 pt-1">
        <span
          className={cn(
            "h-1.5 w-1.5 shrink-0 rounded-full ring-1 ring-black/40",
            STATUS_DOT[f.status ?? ""] ?? "bg-white/40",
          )}
        />
        <span className="font-mono text-[10px] font-semibold text-white/95 drop-shadow">
          #{f.number}
        </span>
        <span className="ml-auto flex gap-1">
          <span
            className={cn("h-1 w-1 rounded-full", hasImg ? "bg-emerald-400" : "bg-white/25")}
            title={hasImg ? "промт картинки есть" : "нет промта картинки"}
          />
          <span
            className={cn("h-1 w-1 rounded-full", hasVid ? "bg-violet-400" : "bg-white/25")}
            title={hasVid ? "промт видео есть" : "нет промта видео"}
          />
        </span>
      </div>
      {f.duration_seconds != null && (
        <div className="absolute bottom-1 right-1 flex items-center gap-0.5 rounded-full bg-black/55 px-1.5 py-0.5 text-[9px] font-medium text-white/80 backdrop-blur-sm">
          <Clock3 className="h-2.5 w-2.5" />
          {f.duration_seconds}с
        </div>
      )}
    </button>
  );
}

/** Раскрытые данные кадра — стеклянная карточка внутри группы сцены. */
function FrameInlineCard({
  frame: f,
  graph,
  projectId,
  onClose,
  onChanged,
}: {
  frame: DbFrame;
  graph: DbGraph;
  projectId: number | null;
  onClose: () => void;
  onChanged: () => Promise<void>;
}) {
  const ownVo = f.voiceover_text.trim();
  const img = activePrompt(f, "img");
  const vid = activePrompt(f, "video");
  const chips = ATTR_CHIPS.map((c) => ({ ...c, value: attrStr(f.attrs, c.key) })).filter(
    (c) => c.value,
  );
  const tc = graph.excel_rows?.[String(f.number)]?.r15_timecode;

  return (
    <div className="animate-in fade-in slide-in-from-top-1 mx-3 mb-3 mt-1 w-[26rem] max-w-[80vw] rounded-2xl border border-white/10 bg-black/40 p-3 shadow-xl shadow-black/40 backdrop-blur-md duration-200">
      <div className="mb-2 flex items-center gap-2">
        <span className="font-mono text-xs font-semibold text-white/85">#{f.number}</span>
        <span className="flex items-center gap-1 rounded-full bg-white/[0.06] px-2 py-0.5 text-[9px] text-white/55">
          <span
            className={cn(
              "h-1.5 w-1.5 rounded-full",
              STATUS_DOT[f.status ?? ""] ?? "bg-white/40",
            )}
          />
          {STATUS_RU[f.status ?? ""] ?? "—"}
        </span>
        {tc ? (
          <span className="font-mono text-[9px] text-white/30">{tc}</span>
        ) : null}
        <button
          type="button"
          title="Свернуть"
          onClick={onClose}
          className="ml-auto rounded-full p-1 text-white/35 transition-colors hover:bg-white/10 hover:text-white"
        >
          <X className="h-3.5 w-3.5" />
        </button>
      </div>

      <div className="flex max-h-64 flex-col gap-1.5 overflow-y-auto pr-0.5">
        {ownVo && (
          <Section label="Закадр кадра (VO-родитель)">
            <p className="whitespace-pre-wrap text-[11px] italic leading-snug text-white/80">
              {ownVo}
            </p>
          </Section>
        )}
        {f.meaning ? (
          <Section label="Смысл">
            <p className="whitespace-pre-wrap text-[11px] leading-snug text-white/70">
              {f.meaning}
            </p>
          </Section>
        ) : null}
        {img.trim() ? (
          <Section label="Промт картинки">
            <p className="line-clamp-4 whitespace-pre-wrap text-[10.5px] leading-snug text-white/60">
              {img}
            </p>
          </Section>
        ) : null}
        {vid.trim() ? (
          <Section label="Промт видео">
            <p className="line-clamp-4 whitespace-pre-wrap text-[10.5px] leading-snug text-white/60">
              {vid}
            </p>
          </Section>
        ) : null}
        {chips.length > 0 && (
          <div className="flex flex-wrap gap-1">
            {chips.map((c) => (
              <span
                key={c.key}
                title={c.value}
                className="rounded-full border border-white/10 bg-white/[0.04] px-2 py-0.5 text-[9px] text-white/50"
              >
                {c.label}: {c.value.length > 26 ? `${c.value.slice(0, 26)}…` : c.value}
              </span>
            ))}
          </div>
        )}
        {f.edges.length > 0 && (
          <div className="flex flex-wrap gap-1">
            {f.edges.map((e) => (
              <span
                key={e.id}
                className="rounded-full border border-sky-400/20 bg-sky-500/10 px-2 py-0.5 text-[9px] text-sky-300/80"
              >
                → кадр {graph.frames.find((x) => x.id === e.to_frame_id)?.number ?? e.to_frame_id}
              </span>
            ))}
          </div>
        )}
      </div>

      {projectId != null && (
        <div className="mt-2 flex justify-end border-t border-white/[0.06] pt-2">
          <button
            type="button"
            title="Вставить кадр после этого"
            onClick={() => {
              void (async () => {
                await api.dbInsertFrame(projectId, f.id, f.scene_id);
                toast.success("Кадр добавлен");
                void onChanged();
              })();
            }}
            className="flex items-center gap-1 rounded-full border border-white/10 px-2.5 py-1 text-[9px] text-white/45 transition-colors hover:border-white/25 hover:text-white"
          >
            <Plus className="h-3 w-3" />
            кадр после
          </button>
        </div>
      )}
    </div>
  );
}

function Section({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="rounded-xl bg-white/[0.03] px-2.5 py-1.5">
      <div className="mb-0.5 text-[9px] font-medium uppercase tracking-[0.14em] text-white/35">
        {label}
      </div>
      {children}
    </div>
  );
}
