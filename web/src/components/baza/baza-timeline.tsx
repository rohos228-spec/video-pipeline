"use client";

/**
 * «Хронология» — лента кадров по центру + все данные внизу под своими сценами.
 *
 * Сверху (по центру): тонкая линейка сцен и стрип превью-тумбов 9:16 слева
 * направо (хронология). Снизу — блоки данных, сгруппированные по сценам:
 * закадр сцены (1 на сцену) и характеристики каждого кадра. Набор
 * характеристик настраивается: любую можно убрать/вернуть чипом над данными
 * (выбор запоминается в localStorage).
 */

import { useEffect, useMemo, useState } from "react";
import {
  ChevronDown,
  ChevronRight,
  Clock3,
  ImageOff,
  Mic,
  X,
} from "lucide-react";
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

const ATTR_CHIPS: { key: string; label: string }[] = [
  { key: "place", label: "место" },
  { key: "visual_type", label: "тип" },
  { key: "cluster", label: "кластер" },
  { key: "edit_type", label: "стык" },
  { key: "main_action", label: "действие" },
];

/** Характеристики, которые можно показывать/убирать. */
const FIELD_DEFS = [
  { key: "voiceover", label: "Закадр" },
  { key: "meaning", label: "Смысл" },
  { key: "img_prompt", label: "Промт картинки" },
  { key: "video_prompt", label: "Промт видео" },
  { key: "characters", label: "Персонажи" },
  { key: "attrs", label: "Характеристики" },
  { key: "timing", label: "Время/таймкод" },
  { key: "status", label: "Статус" },
  { key: "edges", label: "Связи" },
] as const;
type FieldKey = (typeof FIELD_DEFS)[number]["key"];

const FIELDS_STORAGE_KEY = "baza-timeline-fields-v1";

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

function loadFields(): Set<FieldKey> {
  const all = new Set<FieldKey>(FIELD_DEFS.map((f) => f.key));
  try {
    const raw = localStorage.getItem(FIELDS_STORAGE_KEY);
    if (!raw) return all;
    const arr = JSON.parse(raw) as string[];
    const valid = new Set<FieldKey>();
    for (const k of arr) {
      if (FIELD_DEFS.some((f) => f.key === k)) valid.add(k as FieldKey);
    }
    return valid;
  } catch {
    return all;
  }
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
  const [fields, setFields] = useState<Set<FieldKey>>(() => new Set(FIELD_DEFS.map((f) => f.key)));
  const [collapsed, setCollapsed] = useState<Set<string>>(new Set());

  useEffect(() => {
    setFields(loadFields());
  }, []);
  useEffect(() => {
    try {
      localStorage.setItem(FIELDS_STORAGE_KEY, JSON.stringify([...fields]));
    } catch {
      /* приватный режим — не критично */
    }
  }, [fields]);

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

  const toggleField = (key: FieldKey) =>
    setFields((prev) => {
      const next = new Set(prev);
      if (next.has(key)) next.delete(key);
      else next.add(key);
      return next;
    });

  if (graph.frames.length === 0) {
    return (
      <div className="flex flex-1 items-center justify-center text-sm text-white/30">
        В базе нет кадров
      </div>
    );
  }

  return (
    <div className="flex min-h-0 flex-1 flex-col">
      {/* Лента кадров — по центру */}
      <div className="shrink-0 overflow-x-auto px-2 pb-3 pt-2">
        <div
          className="mx-auto flex w-fit items-start gap-5"
        >
          {columns.map((col, ci) => {
            const hue = SCENE_HUES[ci % SCENE_HUES.length];
            const vo = sceneVoiceover(col.frames);
            const isCollapsed = collapsed.has(col.key);
            const total = col.frames.reduce((a, f) => a + (f.duration_seconds ?? 0), 0);
            return (
              <div key={col.key} className="shrink-0">
                <div className="flex items-center gap-1.5 pb-1.5 pl-0.5">
                  <span
                    className="h-[3px] w-6 rounded-full"
                    style={{ background: `hsl(${hue} 75% 60%)` }}
                  />
                  <span className="max-w-[14rem] truncate text-[11px] font-medium text-white/70">
                    {col.title}
                  </span>
                  <span className="text-[9px] text-white/30">
                    {col.frames.length}
                    {total > 0 ? ` · ${total.toFixed(0)}с` : ""}
                  </span>
                  {vo && <Mic className="h-3 w-3 text-white/25" />}
                  <button
                    type="button"
                    title={isCollapsed ? "Развернуть сцену" : "Свернуть сцену"}
                    onClick={() =>
                      setCollapsed((s) => {
                        const next = new Set(s);
                        if (next.has(col.key)) next.delete(col.key);
                        else next.add(col.key);
                        return next;
                      })
                    }
                    className="rounded p-0.5 text-white/25 hover:bg-white/10 hover:text-white/70"
                  >
                    {isCollapsed ? (
                      <ChevronRight className="h-3 w-3" />
                    ) : (
                      <ChevronDown className="h-3 w-3" />
                    )}
                  </button>
                </div>
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

      {/* Выбор характеристик: чип с X убирает, тусклый чип возвращает */}
      <div className="flex shrink-0 flex-wrap items-center gap-1.5 border-t border-white/[0.06] px-2 py-2">
        <span className="mr-1 text-[10px] uppercase tracking-[0.16em] text-white/30">
          Данные
        </span>
        {FIELD_DEFS.map((fd) => {
          const on = fields.has(fd.key);
          return (
            <button
              key={fd.key}
              type="button"
              onClick={() => toggleField(fd.key)}
              title={on ? `Убрать «${fd.label}»` : `Вернуть «${fd.label}»`}
              className={cn(
                "flex items-center gap-1 rounded-full border px-2 py-0.5 text-[10px] transition-colors",
                on
                  ? "border-primary/30 bg-primary/10 text-primary"
                  : "border-white/10 text-white/30 hover:border-white/25 hover:text-white/55",
              )}
            >
              {fd.label}
              {on && <X className="h-2.5 w-2.5 opacity-60" />}
            </button>
          );
        })}
      </div>

      {/* Все данные — внизу, сгруппированы по своим сценам */}
      <div className="min-h-0 flex-1 overflow-y-auto px-2 pb-3">
        <div className="mx-auto flex max-w-[1100px] flex-col gap-4">
          {columns.map((col, ci) => {
            const hue = SCENE_HUES[ci % SCENE_HUES.length];
            const vo = sceneVoiceover(col.frames);
            if (collapsed.has(col.key)) return null;
            return (
              <section
                key={col.key}
                className="rounded-xl border-l-2 pl-3"
                style={{ borderLeftColor: `hsl(${hue} 75% 60% / 0.55)` }}
              >
                <div className="mb-1.5 flex items-baseline gap-2">
                  <span className="text-xs font-semibold text-white/85">{col.title}</span>
                  {col.place && (
                    <span className="text-[10px] text-white/35">{col.place}</span>
                  )}
                </div>

                {fields.has("voiceover") && (
                  <div className="mb-2 max-w-[46rem]">
                    {vo ? (
                      <p className="whitespace-pre-wrap text-[11.5px] italic leading-snug text-white/70">
                        <Mic className="mr-1 inline h-3 w-3 text-white/30" />
                        {vo.text}
                        <span className="ml-1 not-italic text-[9px] text-white/30">
                          (кадр #{vo.frame.number})
                        </span>
                      </p>
                    ) : (
                      <span className="text-[11px] text-white/25">— нет закадра —</span>
                    )}
                  </div>
                )}

                <div className="flex flex-col gap-1.5">
                  {col.frames.map((f) => (
                    <FrameDataRow
                      key={f.id}
                      frame={f}
                      graph={graph}
                      fields={fields}
                      active={f.id === frameId}
                      onClick={() => onSelect(f.id)}
                    />
                  ))}
                </div>
              </section>
            );
          })}
        </div>
      </div>
    </div>
  );
}

/** Тумб кадра на ленте: картинка 9:16, номер/статус на scrim, время пилюлей. */
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

/** Строка данных кадра в блоке его сцены — только включённые характеристики. */
function FrameDataRow({
  frame: f,
  graph,
  fields,
  active,
  onClick,
}: {
  frame: DbFrame;
  graph: DbGraph;
  fields: Set<FieldKey>;
  active: boolean;
  onClick: () => void;
}) {
  const chips = ATTR_CHIPS.map((c) => ({ ...c, value: attrStr(f.attrs, c.key) })).filter(
    (c) => c.value,
  );
  const chars =
    attrStr(f.attrs, "characters") ||
    graph.excel_rows?.[String(f.number)]?.persons ||
    "";
  const tc = graph.excel_rows?.[String(f.number)]?.r15_timecode;
  const img = activePrompt(f, "img");
  const vid = activePrompt(f, "video");

  return (
    <div
      role="button"
      tabIndex={0}
      onClick={onClick}
      onKeyDown={(e) => {
        if (e.key === "Enter" || e.key === " ") onClick();
      }}
      className={cn(
        "flex cursor-pointer items-start gap-2.5 rounded-lg border px-2 py-1.5 transition-colors",
        active
          ? "border-primary/40 bg-primary/[0.07]"
          : "border-white/[0.06] bg-white/[0.015] hover:border-white/15",
      )}
    >
      <div className="flex w-10 shrink-0 flex-col items-center gap-1">
        <div className="w-8 overflow-hidden rounded-md border border-white/10">
          <div className="aspect-[9/16] w-full bg-black/50">
            {f.image_url ? (
              // eslint-disable-next-line @next/next/no-img-element
              <img
                src={f.image_url}
                alt={`#${f.number}`}
                className="h-full w-full object-cover"
                loading="lazy"
              />
            ) : (
              <div className="flex h-full w-full items-center justify-center text-white/15">
                <span className="font-mono text-[8px]">#{f.number}</span>
              </div>
            )}
          </div>
        </div>
        <span className="font-mono text-[9px] text-white/50">#{f.number}</span>
      </div>

      <div className="flex min-w-0 flex-1 flex-wrap items-start gap-x-4 gap-y-1.5">
        {fields.has("status") && (
          <DataBit label="Статус">
            <span className="flex items-center gap-1 text-[11px] text-white/70">
              <span
                className={cn(
                  "h-1.5 w-1.5 rounded-full",
                  STATUS_DOT[f.status ?? ""] ?? "bg-white/40",
                )}
              />
              {STATUS_RU[f.status ?? ""] ?? "—"}
            </span>
          </DataBit>
        )}
        {fields.has("timing") && (
          <DataBit label="Время">
            <span className="text-[11px] text-white/70">
              {f.duration_seconds != null ? `${f.duration_seconds}с` : "—"}
              {tc ? <span className="ml-1 font-mono text-[9px] text-white/35">{tc}</span> : null}
            </span>
          </DataBit>
        )}
        {fields.has("characters") && chars.trim() && (
          <DataBit label="Персонажи">
            <span className="font-mono text-[11px] text-white/70">{chars}</span>
          </DataBit>
        )}
        {fields.has("meaning") && f.meaning ? (
          <DataBit label="Смысл" wide>
            <span className="whitespace-pre-wrap text-[11px] text-white/70">{f.meaning}</span>
          </DataBit>
        ) : null}
        {fields.has("img_prompt") && img.trim() ? (
          <DataBit label="Промт картинки" wide>
            <span className="line-clamp-2 whitespace-pre-wrap text-[10.5px] text-white/55">
              {img}
            </span>
          </DataBit>
        ) : null}
        {fields.has("video_prompt") && vid.trim() ? (
          <DataBit label="Промт видео" wide>
            <span className="line-clamp-2 whitespace-pre-wrap text-[10.5px] text-white/55">
              {vid}
            </span>
          </DataBit>
        ) : null}
        {fields.has("attrs") && chips.length > 0 && (
          <DataBit label="Характеристики" wide>
            <span className="flex flex-wrap gap-1">
              {chips.map((c) => (
                <span
                  key={c.key}
                  title={c.value}
                  className="rounded-full border border-white/10 bg-white/[0.04] px-1.5 py-0.5 text-[9px] text-white/50"
                >
                  {c.label}: {c.value.length > 26 ? `${c.value.slice(0, 26)}…` : c.value}
                </span>
              ))}
            </span>
          </DataBit>
        )}
        {fields.has("edges") && f.edges.length > 0 && (
          <DataBit label="Связи">
            <span className="flex flex-wrap gap-1">
              {f.edges.map((e) => (
                <span
                  key={e.id}
                  className="rounded-full border border-sky-400/20 bg-sky-500/10 px-1.5 py-0.5 text-[9px] text-sky-300/80"
                >
                  → #{graph.frames.find((x) => x.id === e.to_frame_id)?.number ?? e.to_frame_id}
                </span>
              ))}
            </span>
          </DataBit>
        )}
      </div>
    </div>
  );
}

function DataBit({
  label,
  wide,
  children,
}: {
  label: string;
  wide?: boolean;
  children: React.ReactNode;
}) {
  return (
    <div className={cn("min-w-0", wide ? "w-full" : "shrink-0")}>
      <div className="text-[8.5px] font-medium uppercase tracking-[0.14em] text-white/30">
        {label}
      </div>
      <div className="mt-0.5">{children}</div>
    </div>
  );
}
