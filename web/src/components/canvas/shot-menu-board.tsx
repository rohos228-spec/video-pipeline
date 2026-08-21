"use client";

import { useEffect, useMemo, useState } from "react";
import { createPortal } from "react-dom";
import { useQuery } from "@tanstack/react-query";
import { Loader2, Plus, X } from "lucide-react";
import { api } from "@/lib/api";
import { cn } from "@/lib/utils";
import { Button } from "@/components/ui/button";
import {
  loadShotMenuTracks,
  saveShotMenuTracks,
  type ShotMenuCell,
  type ShotMenuTrackDef,
  type ShotMenuTrackKey,
} from "@/lib/shot-menu";

const PPS = 28;
const MIN_SHOT_PX = 96;
const LABEL_PX = 148;
const CELL_HUES = [200, 262, 150, 35, 320, 88, 0, 175];

function shotWidth(sec: number): number {
  return Math.max(MIN_SHOT_PX, Math.round(Math.max(sec, 0.6) * PPS));
}

function cellWidth(cell: ShotMenuCell): number {
  return cell.shots.reduce((sum, s) => sum + shotWidth(s.duration_sec), 0);
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
  const q = useQuery({
    queryKey: ["shot-menu", projectId],
    queryFn: () => api.shotMenu(projectId!),
    enabled: open && projectId != null,
    staleTime: 4000,
  });
  const tracks = q.data?.tracks ?? [];
  const defaultKey = (q.data?.default_tracks ?? ["vo", "action", "cam", "set"]).join("|");
  const defaults = defaultKey.split("|") as ShotMenuTrackKey[];
  const [active, setActive] = useState<ShotMenuTrackKey[]>(defaults);

  useEffect(() => {
    if (!open) return;
    setActive(loadShotMenuTracks(defaults));
  }, [open, defaultKey]);

  const setTracks = (next: ShotMenuTrackKey[] | ((prev: ShotMenuTrackKey[]) => ShotMenuTrackKey[])) => {
    setActive((prev) => {
      const val = typeof next === "function" ? next(prev) : next;
      saveShotMenuTracks(val);
      return val;
    });
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

  const visible = useMemo(() => {
    const byKey = new Map(tracks.map((t) => [t.key, t]));
    return active
      .map((k) => byKey.get(k))
      .filter((t): t is ShotMenuTrackDef => !!t);
  }, [active, tracks]);

  const addable = tracks.filter((t) => !t.pinned && !active.includes(t.key));
  const cells = q.data?.cells ?? [];
  const summary = q.data?.summary;

  if (!open || typeof document === "undefined") return null;

  return createPortal(
    <div className="fixed inset-0 z-[10050] flex flex-col bg-card">
      <header className="flex shrink-0 items-center gap-3 border-b border-white/10 px-4 py-2.5">
        <div className="min-w-0 flex-1">
          <h2 className="text-sm font-semibold tracking-tight">Меню съёмки</h2>
          <p className="text-[11px] text-muted-foreground">
            {summary
              ? `${summary.vo_cells} ячеек закадра · ${summary.shots} шотов · ${summary.duration_clock} · ${summary.vo_chars} зн.`
              : "Лента из БД"}
            {" · "}
            закадр всегда виден · строки добавляются из полей кадра
          </p>
        </div>
        <Button type="button" variant="ghost" size="icon" className="h-8 w-8" onClick={onClose}>
          <X className="h-4 w-4" />
        </Button>
      </header>

      <div className="flex shrink-0 flex-wrap items-center gap-1.5 border-b border-white/10 px-4 py-2">
        {visible.map((t) => (
          <button
            key={t.key}
            type="button"
            disabled={t.pinned}
            title={t.pinned ? "Закадр нельзя убрать" : "Убрать строку"}
            className={cn(
              "rounded-full border px-2 py-0.5 text-[10px]",
              t.pinned
                ? "border-sky-400/40 bg-sky-500/15 text-sky-100"
                : "border-white/15 bg-white/5 text-foreground/85 hover:border-destructive/40",
            )}
            onClick={() => {
              if (t.pinned) return;
              setTracks((prev) => prev.filter((k) => k !== t.key));
            }}
          >
            {t.label}
            {t.pinned ? "" : " ×"}
          </button>
        ))}
        {addable.length > 0 ? (
          <label className="ml-1 inline-flex items-center gap-1 text-[10px] text-muted-foreground">
            <Plus className="h-3 w-3" />
            <select
              className="rounded border border-white/15 bg-black/40 px-1.5 py-0.5 text-[10px] text-foreground"
              value=""
              onChange={(e) => {
                const key = e.target.value as ShotMenuTrackKey;
                if (!key) return;
                setTracks((prev) => (prev.includes(key) ? prev : [...prev, key]));
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
        ) : null}
      </div>

      <div className="min-h-0 flex-1 overflow-hidden">
        {q.isLoading ? (
          <div className="flex h-full items-center justify-center text-muted-foreground">
            <Loader2 className="mr-2 h-5 w-5 animate-spin" />
            Загружаю кадры из БД…
          </div>
        ) : cells.length === 0 ? (
          <p className="p-6 text-sm text-muted-foreground">
            В базе ещё нет кадров. Сначала сценарист / разбивка / кадры.
          </p>
        ) : (
          <div className="flex h-full min-h-0">
            <div
              className="shrink-0 border-r border-white/10 bg-black/30"
              style={{ width: LABEL_PX }}
            >
              <div className="h-[72px] border-b border-white/10 px-3 py-2 text-[10px] uppercase tracking-wide text-muted-foreground">
                Ячейка
              </div>
              {visible.map((t) => (
                <div
                  key={t.key}
                  className={cn(
                    "flex items-center border-b border-white/5 px-3 text-[11px] font-medium",
                    t.key === "vo" ? "h-[88px] text-sky-100" : "h-[64px] text-foreground/80",
                  )}
                >
                  {t.label}
                </div>
              ))}
            </div>
            <div className="min-w-0 flex-1 overflow-auto">
              <div className="flex min-w-max">
                {cells.map((cell, i) => (
                  <CellColumn
                    key={cell.parent_uuid || cell.index}
                    cell={cell}
                    hue={CELL_HUES[i % CELL_HUES.length]}
                    tracks={visible}
                  />
                ))}
              </div>
            </div>
          </div>
        )}
      </div>
    </div>,
    document.body,
  );
}

function CellColumn({
  cell,
  hue,
  tracks,
}: {
  cell: ShotMenuCell;
  hue: number;
  tracks: ShotMenuTrackDef[];
}) {
  const width = cellWidth(cell);
  return (
    <div
      id={`shot-menu-cell-${cell.index}`}
      className="shrink-0 border-r border-white/10"
      style={{ width, minWidth: width }}
    >
      <div
        className="h-[72px] border-b border-white/10 px-2 py-1.5"
        style={{ background: `hsl(${hue} 40% 18% / 0.55)` }}
      >
        <div className="truncate text-[11px] font-semibold">
          Сцена {cell.index}
          <span className="ml-1 font-normal text-foreground/70">
            · {cell.title} (~{cell.duration_sec.toFixed(1)} сек)
          </span>
        </div>
        <div className="mt-0.5 line-clamp-2 text-[10px] text-foreground/65">{cell.layer}</div>
      </div>
      {tracks.map((t) => {
        if (t.key === "vo") {
          return (
            <div
              key={t.key}
              className="h-[88px] overflow-hidden border-b border-white/5 bg-sky-500/10 px-2 py-1.5 text-[12px] leading-snug"
            >
              <p className="h-full overflow-auto whitespace-pre-wrap text-sky-50">
                {cell.voiceover || "—"}
              </p>
            </div>
          );
        }
        return (
          <div key={t.key} className="flex h-[64px] border-b border-white/5">
            {cell.shots.map((shot) => (
              <div
                key={`${shot.uuid || shot.id}-${t.key}`}
                className="overflow-hidden border-r border-white/5 px-1.5 py-1 text-[10px] leading-snug text-foreground/85 last:border-r-0"
                style={{ width: shotWidth(shot.duration_sec) }}
                title={shot.fields[t.key] || ""}
              >
                <div className="mb-0.5 font-mono text-[8px] text-muted-foreground">
                  {shot.label}
                  {t.key === "frame_no" ? "" : ` · ${shot.duration_sec.toFixed(1)}с`}
                </div>
                <div className="line-clamp-3">
                  {t.key === "frame_no"
                    ? shot.fields.frame_no || "—"
                    : shot.fields[t.key] || "—"}
                </div>
              </div>
            ))}
          </div>
        );
      })}
    </div>
  );
}
