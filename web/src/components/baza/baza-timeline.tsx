"use client";

/**
 * «Хронология» — таблица в стиле панели монтажа: кадры — колонки слева
 * направо (хронология), данные — строки. Сцены — объединённые полосы
 * поверх своих кадров (colspan), закадровый текст — одна объединённая
 * ячейка на сцену (VO живёт на родительском кадре, шоты не дублируют).
 * Каждую строку данных можно свернуть/развернуть шевроном в левой
 * sticky-колонке. Клик по номеру кадра — детали справа.
 */

import { useMemo, useState } from "react";
import {
  ChevronDown,
  ChevronRight,
  Clock3,
  Layers,
  Mic,
  Plus,
} from "lucide-react";
import { toast } from "sonner";
import { api, type DbFrame, type DbGraph, type DbScene } from "@/lib/api";
import { cn } from "@/lib/utils";

const LABEL_W = "w-[11rem] min-w-[11rem]";
const COL_W = "w-[13rem] min-w-[13rem]";

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

type RowKey =
  | "voiceover"
  | "meaning"
  | "img_prompt"
  | "video_prompt"
  | "characters"
  | "timing"
  | "status"
  | "edges";

const ROWS: { key: RowKey; label: string }[] = [
  { key: "voiceover", label: "Закадр (1 на сцену)" },
  { key: "meaning", label: "Смысл кадра" },
  { key: "img_prompt", label: "Промт картинки" },
  { key: "video_prompt", label: "Промт видео" },
  { key: "characters", label: "Персонажи" },
  { key: "timing", label: "Время / таймкод" },
  { key: "status", label: "Статус" },
  { key: "edges", label: "Связи" },
];

interface SceneColumn {
  key: string;
  title: string;
  place: string;
  scene: DbScene | null;
  frames: DbFrame[];
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

/** Цвет полосы сцены — стабильный по индексу. */
const SCENE_HUES = [262, 200, 150, 35, 320, 88, 0, 175];

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
  const [collapsedRows, setCollapsedRows] = useState<Set<RowKey>>(new Set());

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

  const frames = useMemo(() => columns.flatMap((c) => c.frames), [columns]);
  /** scene key по frame id — для границы колонки (первая колонка сцены). */
  const sceneStartByFrame = useMemo(() => {
    const m = new Map<number, SceneColumn>();
    for (const c of columns) {
      if (c.frames[0]) m.set(c.frames[0].id, c);
    }
    return m;
  }, [columns]);

  const toggleRow = (key: RowKey) =>
    setCollapsedRows((prev) => {
      const next = new Set(prev);
      if (next.has(key)) next.delete(key);
      else next.add(key);
      return next;
    });

  if (frames.length === 0) {
    return (
      <div className="flex flex-1 items-center justify-center text-sm text-white/30">
        В базе нет кадров
      </div>
    );
  }

  const tableWidth = 11 * 16 + frames.length * 13 * 16;

  return (
    <div className="min-h-0 flex-1 overflow-auto pr-1">
      <table
        className="border-collapse text-[13px]"
        style={{ width: tableWidth, tableLayout: "fixed" }}
      >
        <thead>
          {/* Полоса сцен: одна объединённая ячейка на сцену поверх её кадров */}
          <tr>
            <th
              className={cn(
                "sticky left-0 z-10 border-b border-r border-white/10 bg-[#0a0a0a] px-3 py-2 text-left text-xs font-medium text-white/40",
                LABEL_W,
              )}
            >
              <span className="flex items-center gap-1.5">
                <Layers className="h-3.5 w-3.5" />
                Сцены →
              </span>
            </th>
            {columns.map((col, ci) => {
              const hue = SCENE_HUES[ci % SCENE_HUES.length];
              const total = col.frames.reduce((a, f) => a + (f.duration_seconds ?? 0), 0);
              return (
                <th
                  key={col.key}
                  colSpan={col.frames.length}
                  className="border-b border-l-2 border-white/10 px-2 py-1.5 text-left"
                  style={{
                    borderLeftColor: `hsl(${hue} 70% 55% / 0.8)`,
                    background: `hsl(${hue} 60% 50% / 0.08)`,
                  }}
                  title={col.place || col.title}
                >
                  <div className="flex items-center gap-1.5 text-xs font-semibold text-white/85">
                    {ci > 0 && <span className="text-white/30">→</span>}
                    <span className="truncate">{col.title}</span>
                  </div>
                  <div className="mt-0.5 text-[10px] font-normal text-white/40">
                    {col.frames.length} кадр.
                    {total > 0 ? ` · Σ ${total.toFixed(1)}с` : ""}
                    {col.place ? ` · ${col.place}` : ""}
                  </div>
                </th>
              );
            })}
          </tr>
          {/* Номера кадров */}
          <tr>
            <th
              className={cn(
                "sticky left-0 z-10 border-b border-r border-white/10 bg-[#0a0a0a] px-3 py-2 text-left text-xs font-medium text-white/40",
                LABEL_W,
              )}
            >
              Кадр
            </th>
            {frames.map((f) => {
              const sceneStart = sceneStartByFrame.get(f.id);
              const hue = sceneStart
                ? SCENE_HUES[columns.indexOf(sceneStart) % SCENE_HUES.length]
                : null;
              return (
                <th
                  key={f.id}
                  className={cn(
                    "cursor-pointer border-b border-white/10 px-2 py-2 text-center font-mono text-xs transition-colors hover:bg-white/[0.06]",
                    COL_W,
                    f.id === frameId && "bg-primary/15 text-primary",
                  )}
                  style={hue ? { borderLeft: `2px solid hsl(${hue} 70% 55% / 0.8)` } : undefined}
                  onClick={() => onSelect(f.id)}
                  title={`Кадр #${f.number} — открыть детали справа`}
                >
                  #{f.number}
                </th>
              );
            })}
          </tr>
        </thead>
        <tbody>
          {ROWS.map((row) => {
            const collapsed = collapsedRows.has(row.key);
            return (
              <tr key={row.key} className="border-b border-white/5">
                <td
                  className={cn(
                    "sticky left-0 z-10 border-r border-white/10 bg-[#0a0a0a]/95 px-2 py-2 align-top",
                    LABEL_W,
                  )}
                >
                  <button
                    type="button"
                    className="flex w-full items-center gap-1.5 rounded-md px-2 py-1.5 text-left text-xs font-medium text-white/70 transition hover:bg-white/5"
                    onClick={() => toggleRow(row.key)}
                    title={collapsed ? "Показать строку" : "Скрыть строку"}
                  >
                    {collapsed ? (
                      <ChevronRight className="h-4 w-4 shrink-0" />
                    ) : (
                      <ChevronDown className="h-4 w-4 shrink-0" />
                    )}
                    <span>{row.label}</span>
                  </button>
                </td>
                {row.key === "voiceover"
                  ? /* Закадр — ОДНА объединённая ячейка на сцену. */
                    columns.map((col, ci) => {
                      const hue = SCENE_HUES[ci % SCENE_HUES.length];
                      const vo = sceneVoiceover(col.frames);
                      const extra = col.frames.filter(
                        (f) => f.voiceover_text.trim() && vo && f.id !== vo.frame.id,
                      );
                      return (
                        <td
                          key={col.key}
                          colSpan={col.frames.length}
                          className="border-l-2 px-3 py-2 align-top"
                          style={{
                            borderLeftColor: `hsl(${hue} 70% 55% / 0.8)`,
                            background: `hsl(${hue} 60% 50% / 0.05)`,
                          }}
                        >
                          {collapsed ? (
                            <div className="h-8 rounded-md bg-black/10" />
                          ) : vo ? (
                            <div>
                              <div className="mb-1 flex items-center gap-1 text-[9px] uppercase tracking-[0.16em] text-white/35">
                                <Mic className="h-3 w-3" />
                                кадр #{vo.frame.number}
                              </div>
                              <p className="whitespace-pre-wrap text-xs leading-snug text-white/85">
                                {vo.text}
                              </p>
                              {extra.length > 0 && (
                                <div className="mt-1 text-[9px] text-amber-300/70">
                                  + ещё закадр на кадрах:{" "}
                                  {extra.map((f) => `#${f.number}`).join(", ")}
                                </div>
                              )}
                            </div>
                          ) : (
                            <span className="text-xs text-white/25">— нет закадра —</span>
                          )}
                        </td>
                      );
                    })
                  : frames.map((f) => {
                      const sceneStart = sceneStartByFrame.get(f.id);
                      const hue = sceneStart
                        ? SCENE_HUES[columns.indexOf(sceneStart) % SCENE_HUES.length]
                        : null;
                      return (
                        <td
                          key={`${f.id}-${row.key}`}
                          className={cn(
                            "px-3 py-2 align-top",
                            f.id === frameId && "bg-primary/[0.07]",
                          )}
                          style={
                            hue
                              ? { borderLeft: `2px solid hsl(${hue} 70% 55% / 0.35)` }
                              : undefined
                          }
                        >
                          {collapsed ? (
                            <div className="h-8 rounded-md bg-black/10" />
                          ) : (
                            <FrameCell
                              row={row.key}
                              frame={f}
                              graph={graph}
                              projectId={projectId}
                              onChanged={onChanged}
                            />
                          )}
                        </td>
                      );
                    })}
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

function FrameCell({
  row,
  frame: f,
  graph,
  projectId,
  onChanged,
}: {
  row: RowKey;
  frame: DbFrame;
  graph: DbGraph;
  projectId: number | null;
  onChanged: () => Promise<void>;
}) {
  if (row === "meaning") {
    return (
      <p className="whitespace-pre-wrap text-xs leading-snug text-white/75">
        {f.meaning || "—"}
      </p>
    );
  }
  if (row === "img_prompt" || row === "video_prompt") {
    const text = activePrompt(f, row === "img_prompt" ? "img" : "video");
    return text.trim() ? (
      <p className="line-clamp-5 whitespace-pre-wrap text-[11px] leading-snug text-white/60">
        {text}
      </p>
    ) : (
      <span className="text-xs text-white/25">—</span>
    );
  }
  if (row === "characters") {
    const chars =
      attrStr(f.attrs, "characters") ||
      graph.excel_rows?.[String(f.number)]?.persons ||
      "";
    return chars.trim() ? (
      <div className="flex flex-wrap gap-1">
        {chars
          .split(/[,\s]+/)
          .filter(Boolean)
          .map((c) => (
            <span
              key={c}
              className="rounded-full border border-white/10 bg-black/30 px-1.5 py-0.5 font-mono text-[10px] text-white/60"
            >
              {c}
            </span>
          ))}
      </div>
    ) : (
      <span className="text-xs text-white/25">—</span>
    );
  }
  if (row === "timing") {
    const tc = graph.excel_rows?.[String(f.number)]?.r15_timecode;
    return (
      <div className="flex flex-col gap-1 text-[11px] text-white/60">
        <span className="flex items-center gap-1">
          <Clock3 className="h-3 w-3 text-white/35" />
          {f.duration_seconds != null ? `${f.duration_seconds}с` : "—"}
        </span>
        {tc ? <span className="font-mono text-[10px] text-white/40">{tc}</span> : null}
      </div>
    );
  }
  if (row === "status") {
    const hasImg = !!activePrompt(f, "img").trim();
    const hasVid = !!activePrompt(f, "video").trim();
    return (
      <div className="flex flex-col gap-1">
        <span className="flex items-center gap-1.5 text-[11px] text-white/70">
          <span
            className={cn(
              "h-2 w-2 shrink-0 rounded-full",
              STATUS_DOT[f.status ?? ""] ?? "bg-white/30",
            )}
          />
          {STATUS_RU[f.status ?? ""] ?? f.status ?? "—"}
        </span>
        <span className="flex gap-1.5 text-[9px]">
          <span className={hasImg ? "text-emerald-400" : "text-white/25"}>
            img {hasImg ? "✓" : "—"}
          </span>
          <span className={hasVid ? "text-emerald-400" : "text-white/25"}>
            vid {hasVid ? "✓" : "—"}
          </span>
        </span>
      </div>
    );
  }
  // edges
  return (
    <div className="flex flex-col gap-1">
      {f.edges.length > 0 ? (
        f.edges.map((e) => (
          <span
            key={e.id}
            className="w-fit rounded-full border border-sky-400/20 bg-sky-500/10 px-1.5 py-0.5 text-[9px] text-sky-300/80"
          >
            → кадр {graph.frames.find((x) => x.id === e.to_frame_id)?.number ?? e.to_frame_id}
          </span>
        ))
      ) : (
        <span className="text-xs text-white/25">—</span>
      )}
      {projectId != null && (
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
          className="mt-0.5 flex w-fit items-center gap-1 rounded px-1.5 py-0.5 text-[9px] text-white/35 hover:bg-white/10 hover:text-white"
        >
          <Plus className="h-3 w-3" />
          кадр после
        </button>
      )}
    </div>
  );
}
