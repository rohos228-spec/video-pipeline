"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Loader2, Plus, Sparkles, Trash2 } from "lucide-react";
import { toast } from "sonner";
import {
  api,
  type MontagePendingOp,
  type SceneAnchorRow,
  type SceneVariant,
  type SceneVariantKind,
} from "@/lib/api";
import { errorMessageFromUnknown } from "@/lib/error-message";
import type { MontageAnchorRow, MontageBoardFrame, MontageTemplateChoice } from "@/lib/types";
import { cn } from "@/lib/utils";

/** Строки сцены живут прямо в клетках доски — никаких всплывающих окон. */
const ACCENT = "rgba(209,254,23,1)";
const HINT = "text-[10px] leading-snug text-white/40";
const FIELD =
  "w-full rounded-md border border-white/12 bg-black/40 px-2 py-1 text-[11px] leading-snug text-foreground outline-none focus:border-white/30 disabled:opacity-40";

export type ChipChoice = { id: string; label: string };

export function toChipChoices(
  raw: Array<string | ChipChoice> | undefined,
  current: string,
): ChipChoice[] {
  const out: ChipChoice[] = (raw || []).map((c) =>
    typeof c === "string" ? { id: c, label: c } : c,
  );
  const text = (current || "").trim();
  if (text && !out.some((c) => c.id === text || c.label === text)) {
    out.push({ id: text, label: text });
  }
  return out;
}

function Chip({
  active,
  children,
  title,
  disabled,
  onClick,
}: {
  active: boolean;
  children: React.ReactNode;
  title?: string;
  disabled?: boolean;
  onClick: () => void;
}) {
  return (
    <button
      type="button"
      title={title}
      disabled={disabled}
      onClick={onClick}
      className={cn(
        "rounded-md border px-1.5 py-0.5 text-[10px] leading-none transition disabled:opacity-40",
        active
          ? "border-transparent font-semibold text-black"
          : "border-white/12 text-white/65 hover:border-white/30 hover:text-white",
      )}
      style={active ? { backgroundColor: ACCENT } : undefined}
    >
      {children}
    </button>
  );
}

function PendingMark({ show }: { show: boolean }) {
  if (!show) return null;
  return (
    <span
      className="mt-1 block text-[9px] uppercase tracking-wide text-amber-300/80"
      title="Правка в очереди — примените кнопкой «Применить правки»"
    >
      в очереди
    </span>
  );
}

/** Ячейка-набор чипов: крупность / ракурс / движение / стык / свет. */
export function ChipsCell({
  value,
  choices,
  pending,
  disabled,
  note,
  onPick,
}: {
  value: string;
  choices: Array<string | ChipChoice> | undefined;
  pending?: boolean;
  disabled?: boolean;
  note?: string;
  onPick: (id: string) => void;
}) {
  const items = useMemo(() => toChipChoices(choices, value), [choices, value]);
  const current = (value || "").trim();
  return (
    <div className={cn("rounded-md p-0.5", pending && "bg-amber-500/10")}>
      <div className="flex flex-wrap gap-1">
        {items.map((c) => (
          <Chip
            key={c.id}
            active={current === c.id || current === c.label}
            disabled={disabled}
            onClick={() => {
              if (current === c.id || current === c.label) return;
              onPick(c.id);
            }}
          >
            {c.label}
          </Chip>
        ))}
      </div>
      {note ? <p className={cn(HINT, "mt-1")}>{note}</p> : null}
      <PendingMark show={Boolean(pending)} />
    </div>
  );
}

/** Роль в покрытии: родитель / дочерний + выбор родителя + удаление шота. */
export function RoleCell({
  kind,
  parentNumber,
  frameNumber,
  parentChoices,
  pending,
  disabled,
  onKind,
  onDeleteChild,
}: {
  kind: "parent" | "child" | "";
  parentNumber: number | null;
  frameNumber: number;
  parentChoices: Array<{ number: number; kind: string; vo: string }>;
  pending?: boolean;
  disabled?: boolean;
  onKind: (kind: "parent" | "child", parentNumber: number | null) => void;
  onDeleteChild: () => void;
}) {
  const fallbackParent =
    parentChoices.find((p) => p.kind === "parent")?.number ??
    parentChoices[0]?.number ??
    null;
  return (
    <div className={cn("rounded-md p-0.5", pending && "bg-amber-500/10")}>
      <div className="flex flex-wrap items-center gap-1">
        <Chip
          active={kind === "parent"}
          disabled={disabled}
          onClick={() => onKind("parent", null)}
        >
          Родитель
        </Chip>
        <Chip
          active={kind === "child"}
          disabled={disabled}
          onClick={() => onKind("child", parentNumber ?? fallbackParent)}
        >
          Дочерний
        </Chip>
        {kind === "child" ? (
          <button
            type="button"
            title="Удалить дочерний кадр"
            disabled={disabled}
            onClick={onDeleteChild}
            className="rounded-md p-1 text-white/30 transition hover:bg-rose-500/15 hover:text-rose-200 disabled:opacity-40"
          >
            <Trash2 className="h-3 w-3" />
          </button>
        ) : null}
      </div>
      {kind === "child" ? (
        <select
          className={cn(FIELD, "mt-1")}
          disabled={disabled}
          value={parentNumber == null ? "" : String(parentNumber)}
          onChange={(e) => onKind("child", Number(e.target.value) || null)}
        >
          <option value="">родитель…</option>
          {parentChoices.map((p) => (
            <option key={p.number} value={p.number}>
              #{p.number}
              {p.kind === "parent" ? " · род." : ""}
            </option>
          ))}
        </select>
      ) : (
        <p className={cn(HINT, "mt-1")}>ячейка закадра #{frameNumber}</p>
      )}
      <PendingMark show={Boolean(pending)} />
    </div>
  );
}

/** Подбор вариантов нодами сцен — рисуем внутри клетки, без модалок. */
function useSceneVariants(projectId: number | null, frameId: number) {
  const [busy, setBusy] = useState<SceneVariantKind | null>(null);
  const [variants, setVariants] = useState<SceneVariant[]>([]);
  const [kind, setKind] = useState<SceneVariantKind | null>(null);

  const ask = useCallback(
    async (want: SceneVariantKind, desc: string) => {
      if (projectId == null) return;
      setBusy(want);
      setKind(want);
      try {
        const res = await api.getSceneVariants(projectId, frameId, {
          kind: want,
          desc,
          count: 3,
        });
        setVariants(res.variants || []);
        if (!res.variants?.length) toast.error("Модель не дала пригодных вариантов");
      } catch (e) {
        toast.error(errorMessageFromUnknown(e));
      } finally {
        setBusy(null);
      }
    },
    [projectId, frameId],
  );

  const clear = useCallback(() => {
    setVariants([]);
    setKind(null);
  }, []);

  return { ask, busy, clear, kind, variants };
}

function AskButton({
  busy,
  disabled,
  onClick,
}: {
  busy: boolean;
  disabled?: boolean;
  onClick: () => void;
}) {
  return (
    <button
      type="button"
      disabled={disabled || busy}
      onClick={onClick}
      title="Подобрать вариантами (ноды сцен)"
      className="inline-flex items-center gap-1 text-[10px] text-white/45 transition hover:text-white disabled:opacity-40"
    >
      {busy ? (
        <Loader2 className="h-3 w-3 animate-spin" />
      ) : (
        <Sparkles className="h-3 w-3" />
      )}
      подобрать
    </button>
  );
}

function VariantBox({
  items,
  render,
  onTake,
  onClose,
}: {
  items: SceneVariant[];
  render: (v: SceneVariant) => string;
  onTake: (v: SceneVariant) => void;
  onClose: () => void;
}) {
  if (!items.length) return null;
  return (
    <ul className="mt-1 space-y-1">
      {items.map((v, i) => (
        <li
          key={i}
          className="rounded-md border border-white/10 bg-black/30 p-1.5 text-[10px] leading-snug"
        >
          <p className="text-white/80">{render(v)}</p>
          {v["почему"] ? <p className={cn(HINT, "mt-0.5")}>{v["почему"]}</p> : null}
          <div className="mt-1 flex gap-1.5">
            <button
              type="button"
              onClick={() => {
                onTake(v);
                onClose();
              }}
              className="rounded border border-white/20 px-1.5 py-0.5 text-[10px] text-white/75 transition hover:border-white/40 hover:text-white"
            >
              взять
            </button>
            <button
              type="button"
              onClick={onClose}
              className="text-[10px] text-white/35 transition hover:text-white/70"
            >
              скрыть
            </button>
          </div>
        </li>
      ))}
    </ul>
  );
}

/** Действие кадра — своя строка доски: правка текстом + подбор вариантов. */
export function ActionCell({
  projectId,
  frameId,
  value,
  plan,
  pending,
  disabled,
  onCommit,
}: {
  projectId: number | null;
  frameId: number;
  value: string;
  plan: string;
  pending?: boolean;
  disabled?: boolean;
  onCommit: (action: string, plan?: string) => void;
}) {
  const [text, setText] = useState(value);
  const { ask, busy, clear, kind, variants } = useSceneVariants(projectId, frameId);
  useEffect(() => {
    setText(value);
  }, [value, frameId]);

  const commit = () => {
    const next = text.trim();
    if (!next || next === value.trim()) return;
    onCommit(next);
  };

  return (
    <div className={cn("rounded-md p-0.5", pending && "bg-amber-500/10")}>
      <textarea
        className={cn(FIELD, "min-h-[4.5rem] resize-y")}
        value={text}
        disabled={disabled}
        placeholder="Помещение, кто в кадре, одежда, видимый поступок…"
        onChange={(e) => setText(e.target.value)}
        onBlur={commit}
        onKeyDown={(e) => {
          if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) {
            e.preventDefault();
            commit();
          }
          if (e.key === "Escape") setText(value);
        }}
      />
      <div className="mt-1 flex items-center justify-between gap-2">
        <span className={HINT}>шаг развития, не «взял / посмотрел»</span>
        <AskButton
          busy={busy === "action"}
          disabled={disabled}
          onClick={() => void ask("action", text.trim())}
        />
      </div>
      {kind === "action" ? (
        <VariantBox
          items={variants}
          render={(v) => v["действие"] || ""}
          onClose={clear}
          onTake={(v) => {
            const action = (v["действие"] || "").trim();
            if (!action) return;
            setText(action);
            const nextPlan = (v["план"] || "").trim();
            onCommit(action, nextPlan && nextPlan !== plan ? nextPlan : undefined);
          }}
        />
      ) : null}
      <PendingMark show={Boolean(pending)} />
    </div>
  );
}

function anchorRowsToOps(rows: MontageAnchorRow[]): SceneAnchorRow[] {
  return rows
    .filter((r) => (r["якорь"] || "").trim())
    .map((r) => ({
      "якорь": (r["якорь"] || "").trim(),
      "изменение": (r["изменение"] || "").trim(),
      "главный": Boolean(r["главный"]),
      cell_index: r.cell_index ?? undefined,
      frame_number: r.frame_number ?? undefined,
    }));
}

/**
 * Якоря ЭТОГО кадра. Правка одного шота не должна стирать биты соседей:
 * в мультишоте кладём строку кадра обратно в список ячейки по `cell_index`,
 * а дописанный якорь встаёт сразу за якорями кадра — он режет именно его.
 */
export function mergeAnchorRows(
  frameRows: MontageAnchorRow[],
  cellRows: MontageAnchorRow[],
  canAdd: boolean,
  frameNumber: number,
): SceneAnchorRow[] {
  if (canAdd || cellRows.length <= 1) return anchorRowsToOps(frameRows);
  const next = cellRows.map((r) => ({ ...r }));
  let at = -1;
  next.forEach((row, i) => {
    const fn = row.frame_number;
    if (typeof fn === "number" && fn <= frameNumber) at = i;
  });
  const fresh: MontageAnchorRow[] = [];
  for (const row of frameRows) {
    const idx = typeof row.cell_index === "number" ? row.cell_index : -1;
    if (idx >= 0 && idx < next.length) {
      next[idx] = {
        ...next[idx],
        "якорь": row["якорь"],
        "изменение": row["изменение"] || "",
      };
      at = Math.max(at, idx);
    } else if ((row["якорь"] || "").trim()) {
      fresh.push(row);
    }
  }
  next.splice(at >= 0 ? at + 1 : next.length, 0, ...fresh);
  return anchorRowsToOps(next);
}

/** Якорь кадра — своя строка: где этот кадр режет закадр ячейки. */
export function AnchorCell({
  projectId,
  frameId,
  frameNumber,
  frameText,
  rows,
  cellRows,
  canAdd,
  pending,
  disabled,
  onCommit,
}: {
  projectId: number | null;
  frameId: number;
  frameNumber: number;
  frameText: string;
  rows: MontageAnchorRow[];
  cellRows: MontageAnchorRow[];
  canAdd: boolean;
  pending?: boolean;
  disabled?: boolean;
  onCommit: (anchors: SceneAnchorRow[]) => void;
}) {
  const [draft, setDraft] = useState<MontageAnchorRow[]>(rows);
  const { ask, busy, clear, kind, variants } = useSceneVariants(projectId, frameId);
  const savedRef = useRef(JSON.stringify(rows));

  useEffect(() => {
    setDraft(rows);
    savedRef.current = JSON.stringify(rows);
  }, [rows, frameId]);

  const commit = (next: MontageAnchorRow[]) => {
    const key = JSON.stringify(next);
    if (key === savedRef.current) return;
    const ops = mergeAnchorRows(next, cellRows, canAdd, frameNumber);
    if (!ops.length) return;
    savedRef.current = key;
    onCommit(ops);
  };

  const patch = (i: number, field: "якорь" | "изменение", val: string) =>
    setDraft((prev) => prev.map((r, j) => (j === i ? { ...r, [field]: val } : r)));

  const lower = (frameText || "").toLowerCase();

  return (
    <div className={cn("rounded-md p-0.5", pending && "bg-amber-500/10")}>
      {draft.length === 0 ? (
        <p className={HINT}>якоря нет — ячейка идёт одним куском</p>
      ) : null}
      <ul className="space-y-1">
        {draft.map((row, i) => {
          const anchor = (row["якорь"] || "").trim();
          const found = !anchor || lower.includes(anchor.toLowerCase());
          return (
            <li key={i} className="flex items-start gap-1">
              <span
                title={row["главный"] ? "Главный бит ячейки" : "Бит кадра"}
                className={cn(
                  "mt-1.5 h-2.5 w-2.5 shrink-0 rounded-full",
                  row["главный"] ? "" : "border border-white/25",
                )}
                style={row["главный"] ? { backgroundColor: ACCENT } : undefined}
              />
              <div className="min-w-0 flex-1">
                <input
                  className={cn(FIELD, !found && "border-rose-400/60 text-rose-200")}
                  value={row["якорь"] || ""}
                  disabled={disabled}
                  placeholder="дословный кусок закадра кадра"
                  onChange={(e) => patch(i, "якорь", e.target.value)}
                  onBlur={() => commit(draft)}
                />
                <input
                  className={cn(FIELD, "mt-1 text-[10px]")}
                  value={row["изменение"] || ""}
                  disabled={disabled}
                  placeholder="было → стало"
                  onChange={(e) => patch(i, "изменение", e.target.value)}
                  onBlur={() => commit(draft)}
                />
              </div>
              {canAdd || draft.length > 1 ? (
                <button
                  type="button"
                  title="Убрать якорь"
                  disabled={disabled}
                  onClick={() => {
                    const next = draft.filter((_, j) => j !== i);
                    setDraft(next);
                    commit(next);
                  }}
                  className="mt-1 rounded-md p-1 text-white/30 transition hover:bg-white/10 hover:text-rose-200 disabled:opacity-40"
                >
                  <Trash2 className="h-3 w-3" />
                </button>
              ) : null}
            </li>
          );
        })}
      </ul>
      {draft.length > rows.length ? (
        <p className={cn(HINT, "mt-1")} style={{ color: ACCENT }}>
          +{draft.length - rows.length} шот в ячейке после «Применить правки»
        </p>
      ) : null}
      <div className="mt-1 flex items-center justify-between gap-2">
        <button
          type="button"
          disabled={disabled}
          title="Дописать якорь — по нему ячейка режется на ещё один шот"
          onClick={() =>
            setDraft((prev) => [
              ...prev,
              {
                "якорь": "",
                "изменение": "",
                "главный": prev.length === 0,
                cell_index: null,
                frame_number: frameNumber,
              },
            ])
          }
          className="inline-flex items-center gap-1 rounded-md border border-dashed border-white/20 px-1.5 py-0.5 text-[10px] text-white/50 transition hover:border-white/40 hover:text-white disabled:opacity-40"
        >
          <Plus className="h-3 w-3" /> якорь
        </button>
        <AskButton
          busy={busy === "anchors"}
          disabled={disabled}
          onClick={() => void ask("anchors", "")}
        />
      </div>
      {kind === "anchors" ? (
        <VariantBox
          items={variants}
          render={(v) => (v["биты"] || []).map((b) => `«${b["якорь"]}»`).join(" · ")}
          onClose={clear}
          onTake={(v) => {
            const bits = v["биты"] || [];
            if (!bits.length) return;
            // Биты сверх якорей кадра — не выбрасываем: станут шотами ячейки.
            const next: MontageAnchorRow[] = bits.map((b, i) => ({
              ...draft[i],
              "якорь": b["якорь"],
              "изменение": b["изменение"] || "",
              "главный": canAdd
                ? Boolean(b["главный"])
                : Boolean(draft[i]?.["главный"]),
            }));
            setDraft(next);
            commit(next);
          }}
        />
      ) : null}
      <PendingMark show={Boolean(pending)} />
    </div>
  );
}

/** Формат сцены (шаблон T0…T10 / X1 / X2) — на всю VO-ячейку одной строкой. */
export function TemplateCell({
  projectId,
  frameId,
  value,
  auto,
  choices,
  groupLen,
  pending,
  disabled,
  onPick,
}: {
  projectId: number | null;
  frameId: number;
  value: string;
  auto: string;
  choices: MontageTemplateChoice[] | undefined;
  groupLen: number;
  pending?: boolean;
  disabled?: boolean;
  onPick: (template: string) => void;
}) {
  const [ladderOpen, setLadderOpen] = useState(false);
  const { ask, busy, clear, kind, variants } = useSceneVariants(projectId, frameId);
  const items = choices || [];
  const current = items.find((c) => c.id === value);

  return (
    <div className={cn("rounded-md p-0.5", pending && "bg-amber-500/10")}>
      <div className="flex flex-wrap items-center gap-1">
        {items.map((c) => (
          <Chip
            key={c.id}
            active={value === c.id}
            disabled={disabled}
            title={`${c.name} — ${c.when}`}
            onClick={() => onPick(c.id)}
          >
            {c.id}
          </Chip>
        ))}
        <AskButton
          busy={busy === "template"}
          disabled={disabled}
          onClick={() => void ask("template", "")}
        />
      </div>
      <div className="mt-1 flex flex-wrap items-baseline gap-x-2 gap-y-1">
        {current ? (
          <span className="text-[10px] text-white/70">
            {current.id} · {current.name}
          </span>
        ) : (
          <span className={HINT}>формат не выбран</span>
        )}
        {auto && auto !== value ? (
          <button
            type="button"
            disabled={disabled}
            onClick={() => onPick(auto)}
            className="text-[10px] text-white/45 underline decoration-dotted transition hover:text-white disabled:opacity-40"
          >
            дерево «Выбор» предлагает {auto}
          </button>
        ) : null}
        {current ? (
          <button
            type="button"
            onClick={() => setLadderOpen((v) => !v)}
            className="text-[10px] uppercase tracking-wide text-white/35 transition hover:text-white/70"
          >
            {ladderOpen ? "скрыть лестницу" : "лестница кадров"}
          </button>
        ) : null}
      </div>
      {current && ladderOpen ? (
        <ol className="mt-1 space-y-0.5">
          {current.plans.map((plan, i) => (
            <li
              key={i}
              className={cn(
                "flex items-baseline gap-1.5 text-[10px]",
                i < groupLen ? "text-white/70" : "text-amber-200/70",
              )}
            >
              <span className="text-white/30">K{i + 1}</span>
              <span>{plan}</span>
              <span className="text-white/30">{current.roles[i] || ""}</span>
              {i >= groupLen ? <span className="ml-auto">нет кадра</span> : null}
            </li>
          ))}
        </ol>
      ) : null}
      {kind === "template" ? (
        <VariantBox
          items={variants}
          render={(v) => `${v["шаблон"]} · ${v["лестница"] || ""}`}
          onClose={clear}
          onTake={(v) => {
            const tid = (v["шаблон"] || "").trim();
            if (tid) onPick(tid);
          }}
        />
      ) : null}
      <PendingMark show={Boolean(pending)} />
    </div>
  );
}

function MetaChip({ label, value }: { label: string; value?: string | null }) {
  const text = (value || "").trim();
  if (!text) return null;
  return (
    <span className="text-[10px] leading-snug text-white/60">
      <span className="text-white/35">{label}: </span>
      {text}
    </span>
  );
}

/** Общее по VO-ячейке: набор / место / персонажи / смысл — одной строкой. */
export function SceneInfoCell({
  head,
  frameNumbers,
  setValue,
  setPending,
  disabled,
  onSet,
}: {
  head: MontageBoardFrame;
  frameNumbers: number[];
  setValue: string;
  setPending?: boolean;
  disabled?: boolean;
  onSet: (value: string) => void;
}) {
  const source = (setValue || "").trim();
  const [setText, setSetText] = useState(source);
  useEffect(() => {
    setSetText(source);
  }, [source, head.frame_id]);

  const commit = () => {
    const next = setText.trim();
    if (!next || next === source) return;
    onSet(next);
  };

  return (
    <div className="space-y-1">
      <div className="flex flex-wrap items-center gap-x-3 gap-y-1">
        <span className="text-[10px] font-semibold text-white/75">
          кадры {frameNumbers.map((n) => `#${n}`).join(" · ")}
        </span>
        <MetaChip label="сцена" value={head.scene_no || head.scene_id} />
        <MetaChip label="место" value={head.scene_place} />
        <MetaChip label="персонажи" value={head.scene_characters} />
      </div>
      <div className={cn("flex items-center gap-2", setPending && "rounded-md bg-amber-500/10 p-0.5")}>
        <span className="shrink-0 text-[10px] uppercase tracking-wide text-white/35">
          набор
        </span>
        <input
          className={cn(FIELD, "max-w-[28rem]")}
          value={setText}
          disabled={disabled}
          placeholder="SET / декорация ячейки"
          onChange={(e) => setSetText(e.target.value)}
          onBlur={commit}
          onKeyDown={(e) => {
            if (e.key === "Enter") {
              e.preventDefault();
              commit();
            }
            if (e.key === "Escape") setSetText(source);
          }}
        />
        <PendingMark show={Boolean(setPending)} />
      </div>
      <div className="flex flex-wrap gap-x-3 gap-y-0.5">
        <MetaChip label="смысл" value={head.scene_sense} />
        <MetaChip label="тип" value={head.scene_visual_type} />
        <MetaChip label="предметы" value={head.scene_props} />
        <MetaChip label="фон" value={head.scene_bg} />
        <MetaChip label="акцент" value={head.scene_accent} />
        <MetaChip label="особенность" value={head.scene_feature} />
      </div>
      {head.vo_cell_full ? (
        <p className="max-h-16 overflow-y-auto rounded-md border border-white/10 bg-black/25 p-1.5 text-[10px] leading-relaxed text-white/60">
          {head.vo_cell_full}
        </p>
      ) : null}
    </div>
  );
}
