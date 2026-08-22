"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Loader2, Pencil, Plus, X } from "lucide-react";
import { toast } from "sonner";
import { api } from "@/lib/api";
import { errorMessageFromUnknown } from "@/lib/error-message";
import { cn } from "@/lib/utils";
import {
  loadShotMenuTracks,
  saveShotMenuTracks,
  type ShotMenuCell,
  type ShotMenuTrackDef,
} from "@/lib/shot-menu";

const PPS = 28;
const MIN_SHOT_PX = 96;
const LABEL_PX = 160;
const CELL_HUES = [200, 262, 150, 35, 320, 88, 0, 175];
const ACCENT = "#d1fe17";

function shotWidth(sec: number): number {
  return Math.max(MIN_SHOT_PX, Math.round(Math.max(sec, 0.6) * PPS));
}

function cellWidth(cell: ShotMenuCell): number {
  return cell.shots.reduce((sum, s) => sum + shotWidth(s.duration_sec), 0);
}

/** Кликабельный текст с inline-редактированием (Enter/blur — сохранить). */
function EditableText({
  value,
  placeholder,
  multiline,
  className,
  onSave,
}: {
  value: string;
  placeholder?: string;
  multiline?: boolean;
  className?: string;
  onSave: (next: string) => Promise<void>;
}) {
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState(value);
  const [busy, setBusy] = useState(false);

  useEffect(() => setDraft(value), [value]);

  const commit = async () => {
    if (!editing) return;
    setEditing(false);
    if (draft.trim() === value.trim()) return;
    setBusy(true);
    try {
      await onSave(draft.trim());
    } catch (e) {
      toast.error(errorMessageFromUnknown(e));
    } finally {
      setBusy(false);
    }
  };

  if (editing) {
    const shared = {
      value: draft,
      onChange: (
        e: React.ChangeEvent<HTMLTextAreaElement | HTMLInputElement>,
      ) => setDraft(e.target.value),
      onBlur: () => void commit(),
      onKeyDown: (
        e: React.KeyboardEvent<HTMLTextAreaElement | HTMLInputElement>,
      ) => {
        if (e.key === "Escape") {
          setDraft(value);
          setEditing(false);
        }
        if (e.key === "Enter" && (!multiline || e.ctrlKey || e.metaKey)) {
          e.preventDefault();
          void commit();
        }
      },
      autoFocus: true,
      className:
        "w-full rounded-md border border-white/20 bg-black/50 px-1.5 py-1 text-inherit outline-none focus:border-white/40",
      style: { font: "inherit" },
    } as const;
    return multiline ? (
      <textarea {...shared} rows={4} />
    ) : (
      <input {...shared} />
    );
  }

  return (
    <span
      role="button"
      tabIndex={0}
      title="Клик — редактировать"
      className={cn(
        "group/ed relative block cursor-text rounded px-0.5 transition hover:bg-white/[0.06]",
        className,
      )}
      onClick={() => setEditing(true)}
      onKeyDown={(e) => e.key === "Enter" && setEditing(true)}
    >
      {busy ? (
        <Loader2 className="inline h-3 w-3 animate-spin text-white/50" />
      ) : (
        value || <span className="text-white/25">{placeholder || "—"}</span>
      )}
      <Pencil className="absolute -right-3.5 top-0 hidden h-2.5 w-2.5 text-white/30 group-hover/ed:inline" />
    </span>
  );
}

export function ShotMenuBoard({
  open,
  projectId,
  focusCell,
  onClose,
}: {
  open: boolean;
  projectId: number | null;
  focusCell?: number | null;
  onClose: () => void;
}) {
  const qc = useQueryClient();
  const q = useQuery({
    queryKey: ["shot-menu", projectId],
    queryFn: () => api.shotMenu(projectId!),
    enabled: open && projectId != null,
    staleTime: 4000,
  });
  const tracks = q.data?.tracks ?? [];
  const defaultKey = (q.data?.default_tracks ?? ["vo", "action", "cam", "set"]).join("|");
  const defaults = defaultKey.split("|");
  const [active, setActive] = useState<ShotMenuTrackDef[]>([]);
  const [customOpen, setCustomOpen] = useState(false);
  const [customKey, setCustomKey] = useState("");
  const [customLabel, setCustomLabel] = useState("");
  const [addingAt, setAddingAt] = useState<number | "end" | null>(null);
  const [addText, setAddText] = useState("");

  useEffect(() => {
    if (!open) return;
    const stored = loadShotMenuTracks(defaults);
    setActive(
      stored.map((s) => {
        const known = tracks.find((t) => t.key === s.key);
        return {
          key: s.key,
          label: s.label || known?.label || s.key,
          pinned: known?.pinned ?? s.key === "vo",
          custom: s.custom ?? !known,
        };
      }),
    );
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open, defaultKey, q.data]);

  const setTracks = (next: ShotMenuTrackDef[]) => {
    setActive(next);
    saveShotMenuTracks(
      next.map((t) => ({ key: t.key, label: t.label, custom: t.custom })),
    );
  };

  useEffect(() => {
    if (!open || focusCell == null) return;
    const id = window.setTimeout(() => {
      document.getElementById(`shot-menu-cell-${focusCell}`)?.scrollIntoView({
        inline: "start",
        block: "nearest",
        behavior: "smooth",
      });
    }, 80);
    return () => window.clearTimeout(id);
  }, [open, focusCell, q.data?.cells.length]);

  const visible = active;
  const addable = tracks.filter((t) => !active.some((a) => a.key === t.key));
  const cells = q.data?.cells ?? [];
  const summary = q.data?.summary;

  const refresh = () => qc.invalidateQueries({ queryKey: ["shot-menu", projectId] });

  const saveCellVo = async (cell: ShotMenuCell, text: string) => {
    if (!projectId || !cell.parent_uuid) return;
    await api.shotMenuEditCell(projectId, cell.parent_uuid, text);
    refresh();
  };

  const saveField = async (uuid: string | null, field: string, value: string) => {
    if (!projectId || !uuid) return;
    await api.shotMenuEditField(projectId, uuid, field, value);
    refresh();
  };

  const addCell = async (beforeIndex: number | null) => {
    if (!projectId) return;
    try {
      await api.shotMenuAddCell(projectId, beforeIndex, addText.trim());
      setAddingAt(null);
      setAddText("");
      refresh();
      toast.success("Ячейка добавлена");
    } catch (e) {
      toast.error(errorMessageFromUnknown(e));
    }
  };

  const addCustomTrack = () => {
    const key = customKey.trim();
    if (!key) return;
    if (active.some((t) => t.key === key)) {
      toast.error("Такая строка уже есть");
      return;
    }
    setTracks([
      ...active,
      { key, label: customLabel.trim() || key, pinned: false, custom: true },
    ]);
    setCustomOpen(false);
    setCustomKey("");
    setCustomLabel("");
  };

  if (!open || typeof document === "undefined") return null;

  return createPortal(
    <div className="fixed inset-0 z-[10050] flex flex-col bg-[#0a0a0a] text-white">
      <header className="flex h-[52px] shrink-0 items-center gap-3 border-b border-white/[0.06] bg-[#0f0f0f] px-4">
        <div className="min-w-0 flex-1 leading-tight">
          <h2 className="text-sm font-semibold tracking-tight">Меню съёмки</h2>
          <p className="text-[10px] text-white/40">
            {q.isError
              ? "Лента не загрузилась — обновите страницу"
              : summary
                ? `${summary.vo_cells} ячеек закадра · ${summary.shots} шотов · ${summary.duration_clock} · ${summary.vo_chars} зн.`
                : "Лента из БД"}
            {" · "}клик по тексту — править · «+» между ячейками — новая
          </p>
        </div>
        <button
          type="button"
          onClick={onClose}
          className="inline-flex h-8 w-8 items-center justify-center rounded-lg border border-white/10 bg-white/[0.03] text-white/70 hover:bg-white/[0.08]"
        >
          <X className="h-4 w-4" />
        </button>
      </header>

      {/* строки: активные + добавить (предустановленные или своя) */}
      <div className="flex shrink-0 flex-wrap items-center gap-1.5 border-b border-white/[0.06] bg-[#0c0c0c] px-4 py-2">
        {visible.map((t) => (
          <button
            key={t.key}
            type="button"
            disabled={t.pinned}
            title={
              t.pinned
                ? "Закадр нельзя убрать"
                : t.custom
                  ? `Своя строка: поле «${t.key}» — убрать`
                  : "Убрать строку"
            }
            className={cn(
              "rounded-full border px-2 py-0.5 text-[10px] transition",
              t.pinned
                ? "border-[rgba(209,254,23,0.4)] bg-[rgba(209,254,23,0.12)] text-[rgba(209,254,23,1)]"
                : t.custom
                  ? "border-violet-400/30 bg-violet-500/10 text-violet-200 hover:border-red-400/40"
                  : "border-white/15 bg-white/5 text-white/85 hover:border-red-400/40",
            )}
            onClick={() => {
              if (t.pinned) return;
              setTracks(active.filter((k) => k.key !== t.key));
            }}
          >
            {t.label}
            {t.pinned ? "" : " ×"}
          </button>
        ))}
        {addable.length > 0 && (
          <label className="ml-1 inline-flex items-center gap-1 text-[10px] text-white/40">
            <Plus className="h-3 w-3" />
            <select
              className="rounded-lg border border-white/15 bg-black/40 px-1.5 py-0.5 text-[10px] text-white/85 outline-none"
              value=""
              onChange={(e) => {
                const key = e.target.value;
                if (!key) return;
                const def = tracks.find((t) => t.key === key);
                if (def && !active.some((a) => a.key === key)) {
                  setTracks([...active, def]);
                }
                e.target.value = "";
              }}
            >
              <option value="">Добавить строку…</option>
              {addable.map((t) => (
                <option key={t.key} value={t.key}>
                  {t.label}
                </option>
              ))}
            </select>
          </label>
        )}
        <button
          type="button"
          onClick={() => setCustomOpen((v) => !v)}
          className="inline-flex items-center gap-1 rounded-lg border border-dashed border-violet-400/40 bg-violet-500/5 px-2 py-0.5 text-[10px] text-violet-200 hover:bg-violet-500/15"
          title="Своя строка: любое поле кадра (attrs/колонка)"
        >
          <Plus className="h-3 w-3" /> Своя строка
        </button>
        {customOpen && (
          <span className="inline-flex items-center gap-1">
            <input
              value={customLabel}
              onChange={(e) => setCustomLabel(e.target.value)}
              placeholder="Название строки"
              className="h-7 w-28 rounded-lg border border-white/15 bg-black/40 px-2 text-[10px] text-white/85 outline-none"
            />
            <input
              value={customKey}
              onChange={(e) => setCustomKey(e.target.value)}
              placeholder="ключ поля (напр. shot01_bg)"
              onKeyDown={(e) => e.key === "Enter" && addCustomTrack()}
              className="h-7 w-44 rounded-lg border border-white/15 bg-black/40 px-2 font-mono text-[10px] text-white/85 outline-none"
            />
            <button
              type="button"
              onClick={addCustomTrack}
              className="h-7 rounded-lg border border-white/15 px-2 text-[10px] text-white/70 hover:text-white"
            >
              OK
            </button>
          </span>
        )}
      </div>

      <div className="min-h-0 flex-1 overflow-hidden">
        {q.isLoading ? (
          <div className="flex h-full items-center justify-center text-white/40">
            <Loader2 className="mr-2 h-5 w-5 animate-spin" />
            Загружаю кадры из БД…
          </div>
        ) : cells.length === 0 ? (
          <div className="flex h-full flex-col items-center justify-center gap-3">
            <p className="text-sm text-white/40">
              В базе ещё нет кадров. Создай первую ячейку:
            </p>
            <AddCellButton onClick={() => setAddingAt("end")} label="Первая ячейка" />
          </div>
        ) : (
          <div className="flex h-full min-h-0">
            {/* левая колонка — названия строк */}
            <div
              className="shrink-0 border-r border-white/[0.06] bg-[#0c0c0c]"
              style={{ width: LABEL_PX }}
            >
              <div className="h-[72px] border-b border-white/[0.06] px-3 py-2 text-[10px] font-semibold uppercase tracking-[0.14em] text-white/35">
                Ячейка
              </div>
              {visible.map((t) => (
                <div
                  key={t.key}
                  className={cn(
                    "flex items-center border-b border-white/5 px-3 text-[11px] font-medium",
                    t.key === "vo" ? "h-[88px]" : "h-[64px]",
                  )}
                  style={t.key === "vo" ? { color: ACCENT } : undefined}
                >
                  {t.label}
                  {t.custom && (
                    <span className="ml-1 font-mono text-[8px] text-violet-300/60">
                      ·{t.key}
                    </span>
                  )}
                </div>
              ))}
            </div>
            {/* лента ячеек */}
            <div className="min-w-0 flex-1 overflow-auto">
              <div className="flex min-w-max">
                <AddCellButton
                  onClick={() => setAddingAt(1)}
                  vertical
                  label="Ячейка в начало"
                />
                {cells.map((cell, i) => (
                  <div key={cell.parent_uuid || cell.index} className="flex">
                    <CellColumn
                      cell={cell}
                      hue={CELL_HUES[i % CELL_HUES.length]}
                      tracks={visible}
                      onSaveVo={(text) => saveCellVo(cell, text)}
                      onSaveField={saveField}
                    />
                    <AddCellButton
                      onClick={() => setAddingAt(cell.index + 1)}
                      vertical
                      label={`Ячейка после ${cell.index}`}
                    />
                  </div>
                ))}
              </div>
            </div>
          </div>
        )}
      </div>

      {/* попап новой ячейки */}
      {addingAt !== null && (
        <div className="fixed inset-0 z-[10060] flex items-center justify-center bg-black/70 p-4">
          <div className="w-full max-w-md rounded-2xl border border-white/10 bg-[#141414] p-4 shadow-2xl">
            <h3 className="text-sm font-semibold">
              Новая ячейка{" "}
              {addingAt === "end" ? "в конец ленты" : `перед ячейкой ${addingAt}`}
            </h3>
            <p className="mt-1 text-[11px] text-white/40">
              Пустая ячейка встаёт между соседями (дробный порядок), закадр можно
              написать сразу или потом кликом по тексту.
            </p>
            <textarea
              value={addText}
              onChange={(e) => setAddText(e.target.value)}
              rows={3}
              autoFocus
              placeholder="Закадр новой ячейки (можно пусто)"
              className="mt-3 w-full resize-none rounded-xl border border-white/10 bg-black/40 px-3 py-2 text-[13px] text-white/90 outline-none focus:border-white/30"
            />
            <div className="mt-3 flex justify-end gap-2">
              <button
                type="button"
                onClick={() => {
                  setAddingAt(null);
                  setAddText("");
                }}
                className="rounded-xl border border-white/10 px-3 py-1.5 text-[12px] text-white/60 hover:text-white"
              >
                Отмена
              </button>
              <button
                type="button"
                onClick={() => void addCell(addingAt === "end" ? null : addingAt)}
                className="rounded-xl px-4 py-1.5 text-[12px] font-semibold text-black"
                style={{ backgroundColor: ACCENT }}
              >
                Добавить
              </button>
            </div>
          </div>
        </div>
      )}
    </div>,
    document.body,
  );
}

function AddCellButton({
  onClick,
  label,
  vertical,
}: {
  onClick: () => void;
  label: string;
  vertical?: boolean;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      title={label}
      className={cn(
        "group/add flex shrink-0 items-center justify-center text-white/20 transition hover:text-black",
        vertical ? "w-5 self-stretch" : "h-5",
      )}
      style={{}}
    >
      <span
        className={cn(
          "flex items-center justify-center rounded-full border border-dashed border-white/20 bg-white/[0.03] transition group-hover/add:border-transparent group-hover/add:bg-[rgba(209,254,23,1)]",
          vertical ? "h-8 w-4" : "h-4 w-8",
        )}
      >
        <Plus className="h-3 w-3" />
      </span>
    </button>
  );
}

function CellColumn({
  cell,
  hue,
  tracks,
  onSaveVo,
  onSaveField,
}: {
  cell: ShotMenuCell;
  hue: number;
  tracks: ShotMenuTrackDef[];
  onSaveVo: (text: string) => Promise<void>;
  onSaveField: (uuid: string | null, field: string, value: string) => Promise<void>;
}) {
  const width = cellWidth(cell);
  return (
    <div
      id={`shot-menu-cell-${cell.index}`}
      className="shrink-0 border-r border-white/[0.06]"
      style={{ width, minWidth: width }}
    >
      <div
        className="h-[72px] border-b border-white/[0.06] px-2 py-1.5"
        style={{ background: `hsl(${hue} 45% 14% / 0.9)` }}
      >
        <div className="truncate text-[11px] font-semibold text-white/90">
          Сцена {cell.index}
          <span className="ml-1 font-normal text-white/55">
            · {cell.title} (~{cell.duration_sec.toFixed(1)} сек)
          </span>
        </div>
        <div className="mt-0.5 line-clamp-2 text-[10px] text-white/50">{cell.layer}</div>
      </div>
      {tracks.map((t) => {
        if (t.key === "vo") {
          return (
            <div
              key={t.key}
              className="h-[88px] overflow-hidden border-b border-white/5 px-2 py-1.5 text-[12px] leading-snug"
              style={{ background: "rgba(209,254,23,0.05)" }}
            >
              <div className="h-full overflow-auto whitespace-pre-wrap text-white/90">
                <EditableText
                  value={cell.voiceover}
                  placeholder="+ написать закадр ячейки"
                  multiline
                  onSave={onSaveVo}
                />
              </div>
            </div>
          );
        }
        return (
          <div key={t.key} className="flex h-[64px] border-b border-white/5">
            {cell.shots.map((shot) => {
              const val = t.custom
                ? (shot.all?.[t.key] ?? shot.fields[t.key] ?? "")
                : shot.fields[t.key] || "";
              const editable =
                !t.custom &&
                ["action", "characters", "stitch", "scene", "img_prompt", "video_prompt"].includes(
                  t.key,
                );
              return (
                <div
                  key={`${shot.uuid || shot.id}-${t.key}`}
                  className="overflow-hidden border-r border-white/5 px-1.5 py-1 text-[10px] leading-snug text-white/85 last:border-r-0"
                  style={{ width: shotWidth(shot.duration_sec) }}
                  title={val}
                >
                  <div className="mb-0.5 font-mono text-[8px] text-white/35">
                    {shot.label}
                    {t.key === "frame_no" ? "" : ` · ${shot.duration_sec.toFixed(1)}с`}
                  </div>
                  {editable ? (
                    <EditableText
                      value={val}
                      placeholder="—"
                      multiline={t.key === "img_prompt" || t.key === "video_prompt"}
                      className="line-clamp-3"
                      onSave={(next) => onSaveField(shot.uuid, t.key, next)}
                    />
                  ) : (
                    <div className="line-clamp-3">{val || "—"}</div>
                  )}
                </div>
              );
            })}
          </div>
        );
      })}
    </div>
  );
}
