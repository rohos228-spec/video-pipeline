"use client";

/**
 * «Хронология» — лёгкая структура проекта: сцены и кадры на одной
 * горизонтальной оси слева направо. Кадры — компактные бусины, соединённые
 * линией цепочки; сцены — лёгкие тонированные группы со стрелкой между ними.
 * Никакого всегда видимого текста: закадр сцены открывается шевроном в
 * шапке сцены (1 на сцену), данные кадра — кликом по бусине (раскрытие
 * внутри группы). Всё сворачивается.
 */

import { useMemo, useState } from "react";
import {
  ChevronDown,
  ChevronRight,
  Clock3,
  Mic,
  MoveRight,
  Plus,
  X,
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
  /** Раскрытый кадр (данные внутри группы) — один глобально. */
  const [openFrame, setOpenFrame] = useState<number | null>(null);
  /** Сцены с раскрытым закадром. */
  const [voScenes, setVoScenes] = useState<Set<string>>(new Set());
  /** Сцены со скрытой цепочкой кадров. */
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
    <div className="min-h-0 flex-1 overflow-x-auto overflow-y-auto pb-2">
      <div className="flex min-w-max items-start gap-1 px-1 py-1">
        {columns.map((col, ci) => {
          const hue = SCENE_HUES[ci % SCENE_HUES.length];
          const vo = sceneVoiceover(col.frames);
          const voOpen = voScenes.has(col.key);
          const collapsed = collapsedScenes.has(col.key);
          const total = col.frames.reduce((a, f) => a + (f.duration_seconds ?? 0), 0);
          const openInScene =
            openFrame != null && col.frames.some((f) => f.id === openFrame)
              ? openFrame
              : null;
          return (
            <div key={col.key} className="flex items-start">
              {ci > 0 && (
                <div className="flex h-[52px] w-6 shrink-0 items-center justify-center">
                  <MoveRight className="h-3.5 w-3.5 text-white/20" />
                </div>
              )}
              <section
                className="shrink-0 rounded-2xl border p-2"
                style={{
                  borderColor: `hsl(${hue} 60% 55% / 0.25)`,
                  background: `hsl(${hue} 60% 50% / 0.045)`,
                }}
              >
                {/* Шапка сцены: название + мета, закадр скрыт за шевроном */}
                <header className="flex items-center gap-1.5 px-1 pb-1.5">
                  <span
                    className="h-2 w-2 shrink-0 rounded-full"
                    style={{ background: `hsl(${hue} 75% 60%)` }}
                  />
                  <span className="max-w-[16rem] truncate text-xs font-semibold text-white/85">
                    {col.title}
                  </span>
                  <span className="shrink-0 text-[10px] text-white/35">
                    {col.frames.length} кадр.{total > 0 ? ` · Σ ${total.toFixed(1)}с` : ""}
                  </span>
                  <button
                    type="button"
                    title={voOpen ? "Скрыть закадр сцены" : "Показать закадр сцены"}
                    onClick={() => setVoScenes((s) => toggleSet(s, col.key))}
                    className={cn(
                      "ml-auto flex shrink-0 items-center gap-1 rounded-md px-1.5 py-0.5 text-[9px] transition-colors",
                      voOpen
                        ? "bg-white/10 text-white/70"
                        : "text-white/35 hover:bg-white/5 hover:text-white/60",
                    )}
                  >
                    <Mic className="h-3 w-3" />
                    {voOpen ? "скрыть" : "закадр"}
                  </button>
                  <button
                    type="button"
                    title={collapsed ? "Развернуть кадры" : "Свернуть кадры"}
                    onClick={() => setCollapsedScenes((s) => toggleSet(s, col.key))}
                    className="shrink-0 rounded p-0.5 text-white/35 hover:bg-white/10 hover:text-white"
                  >
                    {collapsed ? (
                      <ChevronRight className="h-3.5 w-3.5" />
                    ) : (
                      <ChevronDown className="h-3.5 w-3.5" />
                    )}
                  </button>
                </header>

                {voOpen && (
                  <div className="mb-2 max-w-[26rem] rounded-lg bg-black/25 px-2.5 py-2">
                    {vo ? (
                      <>
                        <div className="mb-0.5 text-[9px] uppercase tracking-[0.16em] text-white/35">
                          Закадр сцены · кадр #{vo.frame.number}
                        </div>
                        <p className="whitespace-pre-wrap text-[11px] leading-snug text-white/80">
                          {vo.text}
                        </p>
                      </>
                    ) : (
                      <span className="text-[11px] text-white/25">— нет закадра —</span>
                    )}
                  </div>
                )}

                {!collapsed && (
                  <div className="flex items-center">
                    {col.frames.map((f, fi) => (
                      <div key={f.id} className="flex items-center">
                        {fi > 0 && <div className="h-px w-3 shrink-0 bg-white/15" />}
                        <FrameBead
                          frame={f}
                          active={f.id === frameId}
                          open={openFrame === f.id}
                          onClick={() => {
                            setOpenFrame((cur) => (cur === f.id ? null : f.id));
                            onSelect(f.id);
                          }}
                        />
                      </div>
                    ))}
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

/** Бусина кадра — фильм-стрип: реальная картинка кадра 9:16 + оверлеи. */
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
        "group relative w-[76px] shrink-0 overflow-hidden rounded-xl border text-left transition-all",
        open
          ? "border-primary/70 ring-2 ring-primary/40"
          : active
            ? "border-primary/50 ring-1 ring-primary/30"
            : "border-white/10 hover:border-white/30",
      )}
    >
      <div className="aspect-[9/16] w-full bg-black/40">
        {f.image_url ? (
          // eslint-disable-next-line @next/next/no-img-element
          <img
            src={f.image_url}
            alt={`Кадр #${f.number}`}
            className="h-full w-full object-cover"
            loading="lazy"
          />
        ) : (
          <div className="flex h-full w-full items-center justify-center text-white/15">
            <span className="font-mono text-sm">#{f.number}</span>
          </div>
        )}
      </div>
      {/* Оверлеи: номер+статус сверху, время и маркеры снизу */}
      <div className="absolute inset-x-0 top-0 flex items-center gap-1 bg-gradient-to-b from-black/70 to-transparent px-1.5 pb-2.5 pt-1">
        <span
          className={cn(
            "h-1.5 w-1.5 shrink-0 rounded-full",
            STATUS_DOT[f.status ?? ""] ?? "bg-white/30",
          )}
        />
        <span className="font-mono text-[10px] font-medium text-white/90">#{f.number}</span>
        <span className="ml-auto flex gap-1">
          <span
            className={cn("h-1 w-1 rounded-full", hasImg ? "bg-emerald-400" : "bg-white/20")}
            title={hasImg ? "промт картинки есть" : "нет промта картинки"}
          />
          <span
            className={cn("h-1 w-1 rounded-full", hasVid ? "bg-violet-400" : "bg-white/20")}
            title={hasVid ? "промт видео есть" : "нет промта видео"}
          />
        </span>
      </div>
      {f.duration_seconds != null && (
        <div className="absolute inset-x-0 bottom-0 flex items-center gap-0.5 bg-gradient-to-t from-black/70 to-transparent px-1.5 pb-1 pt-2.5 text-[9px] text-white/70">
          <Clock3 className="h-2.5 w-2.5" />
          {f.duration_seconds}с
        </div>
      )}
    </button>
  );
}

/** Раскрытые данные кадра — внутри группы сцены, под цепочкой. */
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
    <div className="mt-2 w-[26rem] max-w-[80vw] rounded-xl border border-white/10 bg-black/30 p-2.5">
      <div className="mb-1.5 flex items-center gap-1.5">
        <span className="font-mono text-[11px] text-white/70">#{f.number}</span>
        <span className="text-[10px] text-white/40">
          {STATUS_RU[f.status ?? ""] ?? "—"}
        </span>
        {tc ? <span className="font-mono text-[9px] text-white/30">{tc}</span> : null}
        <button
          type="button"
          title="Свернуть"
          onClick={onClose}
          className="ml-auto rounded p-0.5 text-white/35 hover:bg-white/10 hover:text-white"
        >
          <X className="h-3.5 w-3.5" />
        </button>
      </div>

      <div className="flex max-h-64 flex-col gap-1.5 overflow-y-auto pr-0.5">
        {ownVo && (
          <Section label="Закадр кадра (VO-родитель)">
            <p className="whitespace-pre-wrap text-[11px] text-white/80">{ownVo}</p>
          </Section>
        )}
        {f.meaning ? (
          <Section label="Смысл">
            <p className="whitespace-pre-wrap text-[11px] text-white/70">{f.meaning}</p>
          </Section>
        ) : null}
        {img.trim() ? (
          <Section label="Промт картинки">
            <p className="line-clamp-4 whitespace-pre-wrap text-[10.5px] text-white/60">{img}</p>
          </Section>
        ) : null}
        {vid.trim() ? (
          <Section label="Промт видео">
            <p className="line-clamp-4 whitespace-pre-wrap text-[10.5px] text-white/60">{vid}</p>
          </Section>
        ) : null}
        {chips.length > 0 && (
          <div className="flex flex-wrap gap-1">
            {chips.map((c) => (
              <span
                key={c.key}
                title={c.value}
                className="rounded-full border border-white/10 bg-black/30 px-1.5 py-0.5 text-[9px] text-white/50"
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
                className="rounded-full border border-sky-400/20 bg-sky-500/10 px-1.5 py-0.5 text-[9px] text-sky-300/80"
              >
                → кадр {graph.frames.find((x) => x.id === e.to_frame_id)?.number ?? e.to_frame_id}
              </span>
            ))}
          </div>
        )}
      </div>

      {projectId != null && (
        <div className="mt-1.5 flex justify-end border-t border-white/[0.06] pt-1.5">
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
            className="flex items-center gap-1 rounded px-1.5 py-0.5 text-[9px] text-white/35 hover:bg-white/10 hover:text-white"
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
    <div className="rounded-lg bg-white/[0.03] p-1.5">
      <div className="mb-0.5 text-[9px] uppercase tracking-wide text-white/35">{label}</div>
      {children}
    </div>
  );
}
