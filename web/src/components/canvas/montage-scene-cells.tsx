"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { ChevronDown, Loader2, Plus, Sparkles, Trash2 } from "lucide-react";
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

/**
 * Метка «в очереди» — точка поверх клетки, а не строка текста: строка добавляла
 * клетке высоту в тот момент, когда правка уходила в очередь, и кнопка под
 * курсором уезжала из-под mouseup. Счётчик очереди есть в шапке доски.
 */
function PendingMark({ show }: { show: boolean }) {
  if (!show) return null;
  return (
    <span className="pointer-events-none absolute right-0.5 top-0.5 h-1.5 w-1.5 rounded-full bg-amber-300/80" />
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
    <div className={cn("relative rounded-md p-0.5", pending && "bg-amber-500/10")}>
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

export type CoverageMenuGroup = {
  key: string;
  label: string;
  value: string;
  choices: Array<string | ChipChoice> | undefined;
  note?: string;
  pending?: boolean;
  onPick: (id: string) => void;
};

const MENU_W = 420;
const MENU_H = 190;

/**
 * Покрытие кадра под его картинкой: в доске видны только текущие значения,
 * варианты живут во всплывающем меню и показываются по одной группе — той,
 * на которую наведён курсор.
 *
 * Слева список групп (его высота не меняется), справа чипы выбранной группы:
 * в раскрывающемся аккордеоне пункты уезжали из-под курсора.
 */
export function CoverageMenu({
  title,
  groups,
  disabled,
}: {
  title: string;
  groups: CoverageMenuGroup[];
  disabled?: boolean;
}) {
  const hostRef = useRef<HTMLDivElement>(null);
  const hideRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const [box, setBox] = useState<{ left: number; top: number } | null>(null);
  const [hot, setHot] = useState(0);

  const place = useCallback(() => {
    const el = hostRef.current;
    if (!el) return;
    const r = el.getBoundingClientRect();
    const fit = (x: number) =>
      Math.round(Math.min(Math.max(8, x), Math.max(8, window.innerWidth - MENU_W - 8)));
    if (r.bottom + MENU_H + 8 <= window.innerHeight) {
      setBox({ left: fit(r.left - 6), top: Math.round(r.bottom + 6) });
      return;
    }
    // Снизу не хватает места: уводим меню в сторону, а не наверх — иначе оно
    // закрывает сам кадр, для которого его и открыли.
    const toRight = r.right + 8 + MENU_W + 8 <= window.innerWidth;
    setBox({
      left: fit(toRight ? r.right + 8 : r.left - 8 - MENU_W),
      top: Math.round(Math.min(Math.max(8, r.top - 8), Math.max(8, window.innerHeight - MENU_H - 8))),
    });
  }, []);

  const show = useCallback(() => {
    if (hideRef.current) clearTimeout(hideRef.current);
    place();
  }, [place]);

  const hide = useCallback(() => {
    if (hideRef.current) clearTimeout(hideRef.current);
    // Пауза, чтобы курсор успел дойти от строки значений до самого меню.
    hideRef.current = setTimeout(() => setBox(null), 140);
  }, []);

  useEffect(() => () => (hideRef.current ? clearTimeout(hideRef.current) : undefined), []);

  const open = box !== null;
  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") setBox(null);
    };
    const onScroll = () => place();
    window.addEventListener("keydown", onKey);
    window.addEventListener("scroll", onScroll, true);
    window.addEventListener("resize", onScroll);
    return () => {
      window.removeEventListener("keydown", onKey);
      window.removeEventListener("scroll", onScroll, true);
      window.removeEventListener("resize", onScroll);
    };
  }, [open, place]);

  const active = groups[Math.min(hot, groups.length - 1)];
  const anyPending = groups.some((g) => g.pending);

  return (
    <div ref={hostRef} onMouseEnter={show} onMouseLeave={hide}>
      <button
        type="button"
        onFocus={show}
        onBlur={hide}
        onClick={() => (open ? setBox(null) : show())}
        title={`Наведи или нажми, чтобы менять: ${groups
          .map((g) => g.label.toLowerCase())
          .join(", ")}`}
        className={cn(
          "group w-full rounded-md border px-1.5 py-1 text-left transition",
          open
            ? "border-white/30 bg-white/[0.08]"
            : "border-white/10 bg-white/[0.03] hover:border-white/25 hover:bg-white/[0.07]",
        )}
      >
        <span className="flex items-center gap-1 text-[9px] uppercase tracking-wide text-white/35">
          {title}
          <ChevronDown className="h-2.5 w-2.5" />
          {anyPending ? (
            <span className="h-1.5 w-1.5 rounded-full bg-amber-300/80" title="есть правка в очереди" />
          ) : null}
          {/* Подсказка держит своё место всегда — иначе строка дёргалась. */}
          <span
            className={cn(
              "ml-auto normal-case tracking-normal transition-colors",
              open ? "text-white/45" : "text-transparent group-hover:text-white/40",
            )}
          >
            изменить
          </span>
        </span>
        <span
          className={cn(
            "mt-0.5 grid gap-x-2 text-[10px] leading-snug",
            groups.length > 1 ? "grid-cols-2" : "grid-cols-1",
          )}
        >
          {groups.map((g) => (
            <span key={g.key} className="flex min-w-0 items-baseline gap-1">
              {/* У меню на одну группу заголовок уже назвал её — не дублируем. */}
              {groups.length > 1 ? (
                <span className="shrink-0 text-white/30">{g.label.toLowerCase()}</span>
              ) : null}
              <span
                className={cn(
                  "truncate",
                  g.pending ? "text-amber-200/90" : g.value ? "text-white/80" : "text-white/25",
                )}
              >
                {g.value || "—"}
              </span>
            </span>
          ))}
        </span>
      </button>
      {open && typeof document !== "undefined"
        ? createPortal(
            <div
              onMouseEnter={show}
              onMouseLeave={hide}
              style={{ left: box.left, top: box.top, width: MENU_W }}
              className="fixed z-[10060] rounded-xl border border-white/12 bg-[#0b0b0b]/98 p-2 shadow-2xl backdrop-blur"
            >
              <p className="flex items-baseline gap-2 px-1 pb-1.5">
                <span className="text-[9px] uppercase tracking-wide text-white/35">{title}</span>
                {groups.length > 1 ? (
                  <span className="text-[10px] text-white/25">
                    наведи строку слева — справа её варианты
                  </span>
                ) : null}
              </p>
              <div className="flex gap-2">
                <ul className="w-[9.5rem] shrink-0 space-y-0.5">
                  {groups.map((g, i) => (
                    <li key={g.key}>
                      <button
                        type="button"
                        onMouseEnter={() => setHot(i)}
                        onFocus={() => setHot(i)}
                        className={cn(
                          "flex w-full items-center gap-1 rounded-md px-1.5 py-1 text-left text-[11px] transition",
                          i === hot ? "bg-white/10 text-white" : "text-white/55 hover:text-white",
                        )}
                      >
                        <span className="shrink-0">{g.label}</span>
                        <span
                          className="ml-auto min-w-0 truncate text-[10px]"
                          style={{ color: g.value ? ACCENT : "rgba(255,255,255,0.25)" }}
                        >
                          {g.value || "—"}
                        </span>
                        {g.pending ? (
                          <span className="h-1.5 w-1.5 shrink-0 rounded-full bg-amber-300/80" />
                        ) : null}
                      </button>
                    </li>
                  ))}
                </ul>
                <div className="min-h-[6.5rem] flex-1 rounded-lg border border-white/10 bg-black/40 p-1.5">
                  {active ? (
                    <ChipsCell
                      value={active.value}
                      choices={active.choices}
                      pending={active.pending}
                      disabled={disabled}
                      note={active.note}
                      onPick={active.onPick}
                    />
                  ) : null}
                </div>
              </div>
            </div>,
            document.body,
          )
        : null}
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
    <div className={cn("relative rounded-md p-0.5", pending && "bg-amber-500/10")}>
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
    <div className={cn("relative rounded-md p-0.5", pending && "bg-amber-500/10")}>
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
  cellText,
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
  cellText: string;
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

  // Сравнение по значению, а не по ссылке: `rows` — новый массив на каждый
  // ре-рендер доски, и по ссылке дописанная строка якоря тут же стиралась.
  // Пустую строку очередь вернуть не может — её и держим сами.
  const rowsKey = JSON.stringify(rows);
  useEffect(() => {
    const next = JSON.parse(rowsKey) as MontageAnchorRow[];
    setDraft((prev) => {
      const typing = prev.filter((r) => !(r["якорь"] || "").trim());
      return typing.length ? [...next, ...typing] : next;
    });
    savedRef.current = rowsKey;
  }, [rowsKey, frameId]);

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

  // Дописанный якорь режет ячейку, а не текущий кадр: красным — только то,
  // чего нет в закадре ЯЧЕЙКИ (apply на таком якоре и падает).
  const cellLower = (cellText || frameText || "").toLowerCase();
  const frameLower = (frameText || "").toLowerCase();

  return (
    <div className={cn("relative rounded-md p-0.5", pending && "bg-amber-500/10")}>
      {draft.length === 0 ? (
        <p className={HINT}>якоря нет — ячейка идёт одним куском</p>
      ) : null}
      <ul className="space-y-1">
        {draft.map((row, i) => {
          const anchor = (row["якорь"] || "").trim();
          const found = !anchor || cellLower.includes(anchor.toLowerCase());
          const ownFrame = !anchor || frameLower.includes(anchor.toLowerCase());
          return (
            <li key={i} className="flex items-start gap-1">
              <span
                title={
                  row["главный"]
                    ? "Главный бит ячейки"
                    : ownFrame
                      ? "Бит кадра"
                      : "Режет ячейку — станет отдельным шотом"
                }
                className={cn(
                  "mt-1.5 h-2.5 w-2.5 shrink-0 rounded-full",
                  row["главный"] ? "" : "border border-white/25",
                )}
                style={row["главный"] ? { backgroundColor: ACCENT } : undefined}
              />
              <div className="min-w-0 flex-1">
                <input
                  className={cn(
                    FIELD,
                    !found && "border-rose-400/60 text-rose-200",
                    found && !ownFrame && "border-dashed border-white/25",
                  )}
                  value={row["якорь"] || ""}
                  disabled={disabled}
                  title={
                    found ? undefined : "Такого куска нет в закадре ячейки — правка не применится"
                  }
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
          aria-label="Дописать якорь"
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

/**
 * Формат сцены (шаблон T0…T10 / X1 / X2) — выбор варианта для всей VO-ячейки.
 * При выбранном варианте сразу показываем, для чего он и какая
 * последовательность кадров из него выйдет: `K1 ОБЩИЙ → кадр #1`. Кадров у
 * ячейки может быть меньше, чем шотов у шаблона — тогда шаг подписан
 * «нет кадра», и его добавляют якорем.
 */
export function TemplateCell({
  projectId,
  frameId,
  value,
  auto,
  choices,
  frameNumbers,
  pending,
  disabled,
  onPick,
}: {
  projectId: number | null;
  frameId: number;
  value: string;
  auto: string;
  choices: MontageTemplateChoice[] | undefined;
  frameNumbers: number[];
  pending?: boolean;
  disabled?: boolean;
  onPick: (template: string) => void;
}) {
  const [detailsOpen, setDetailsOpen] = useState(true);
  const { ask, busy, clear, kind, variants } = useSceneVariants(projectId, frameId);
  const items = choices || [];
  const current = items.find((c) => c.id === value);
  const groupLen = frameNumbers.length;

  return (
    <div className={cn("relative rounded-md p-0.5", pending && "bg-amber-500/10")}>
      <div className="flex flex-wrap items-center gap-1">
        <span className="mr-0.5 text-[9px] uppercase tracking-wide text-white/35">
          формат сцены
        </span>
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
      </div>
      {current ? (
        <div className="mt-1 rounded-lg border border-white/10 bg-black/30 p-1.5">
          <div className="flex items-baseline gap-2">
            <span className="text-[11px] font-semibold" style={{ color: ACCENT }}>
              {current.id} · {current.name}
            </span>
            <span className={HINT}>
              {current.shots ? `${current.shots} шота в шаблоне · ` : ""}
              кадров в ячейке {groupLen}
            </span>
            <button
              type="button"
              onClick={() => setDetailsOpen((v) => !v)}
              className="ml-auto text-[10px] uppercase tracking-wide text-white/35 transition hover:text-white/70"
            >
              {detailsOpen ? "скрыть" : "что это даёт"}
            </button>
          </div>
          {detailsOpen ? (
            <>
              {current.when ? (
                <p className="mt-1 text-[10px] leading-relaxed text-white/55">
                  <span className="text-white/35">для чего: </span>
                  {current.when}
                </p>
              ) : null}
              <p className="mt-1.5 text-[9px] uppercase tracking-wide text-white/35">
                последовательность кадров
              </p>
              <ol className="mt-0.5 space-y-0.5">
                {current.plans.map((plan, i) => {
                  const frameNo = frameNumbers[i];
                  return (
                    <li
                      key={i}
                      className={cn(
                        "flex items-baseline gap-1.5 text-[10px]",
                        frameNo ? "text-white/70" : "text-amber-200/70",
                      )}
                    >
                      <span className="w-6 shrink-0 text-white/30">K{i + 1}</span>
                      <span className="shrink-0 font-medium">{plan}</span>
                      <span className="min-w-0 truncate text-white/35">
                        {current.roles[i] || ""}
                      </span>
                      <span className="ml-auto shrink-0">
                        {frameNo ? `кадр #${frameNo}` : "нет кадра — дописать якорь"}
                      </span>
                    </li>
                  );
                })}
                {/* Кадров в сцене может быть больше, чем шотов у шаблона —
                    такие показываем отдельно, а не прячем. */}
                {frameNumbers.slice(current.plans.length).map((n) => (
                  <li
                    key={`extra-${n}`}
                    className="flex items-baseline gap-1.5 text-[10px] text-white/45"
                  >
                    <span className="w-6 shrink-0 text-white/25">+</span>
                    <span className="shrink-0 font-medium">{`кадр #${n}`}</span>
                    <span className="ml-auto shrink-0">сверх шаблона</span>
                  </li>
                ))}
              </ol>
              {current.example ? (
                <p className="mt-1 text-[10px] leading-relaxed text-white/35">
                  <span className="text-white/25">пример: </span>
                  {current.example}
                </p>
              ) : null}
            </>
          ) : null}
        </div>
      ) : (
        <p className={cn(HINT, "mt-1")}>
          формат не выбран — нажми T-вариант, чтобы увидеть, для чего он и какие
          кадры даёт
        </p>
      )}
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

/**
 * Сцена = одна ячейка закадра. Здесь всё, что общее для сцены: её номер и
 * кадры, место, персонажи, набор, свет, формат с описанием и полный текст
 * закадра, разложенный по кадрам. Данные отдельного кадра (роль, действие,
 * якорь, крупность…) живут в его колонке ниже — понятия не смешиваем.
 */
export function SceneCell({
  head,
  sceneNo,
  frames,
  setValue,
  setPending,
  disabled,
  template,
  light,
  onSet,
}: {
  head: MontageBoardFrame;
  /** Номер сцены по порядку на доске: ячейки закадра идут подряд. */
  sceneNo: number;
  frames: MontageBoardFrame[];
  setValue: string;
  setPending?: boolean;
  disabled?: boolean;
  /** Формат сцены (`TemplateCell`) — общий для всей ячейки. */
  template?: React.ReactNode;
  /** Свет сцены — тоже на всю ячейку, а не на отдельный кадр. */
  light?: React.ReactNode;
  onSet: (value: string) => void;
}) {
  const frameNumbers = frames.map((f) => f.number);
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

  const parts = frames.filter((f) => (f.voiceover_text || "").trim());

  return (
    <div className="space-y-1.5">
      <div className="flex flex-wrap items-baseline gap-x-3 gap-y-1">
        <span className="text-[11px] font-semibold text-white/85">
          Сцена {sceneNo}
        </span>
        <span className="text-[10px] text-white/45">
          {frames.length === 1
            ? `один кадр #${frameNumbers[0]}`
            : `${frames.length} кадра: ${frameNumbers.map((n) => `#${n}`).join(" · ")}`}
        </span>
        <MetaChip label="место" value={head.scene_place} />
        <MetaChip label="персонажи" value={head.scene_characters} />
        <MetaChip label="в плане" value={head.scene_no || head.scene_id} />
      </div>
      <div className="flex flex-wrap items-start gap-2">
        <div
          className={cn(
            "relative flex min-w-[12rem] flex-1 items-center gap-2",
            setPending && "rounded-md bg-amber-500/10 p-0.5",
          )}
        >
          <span className="shrink-0 text-[10px] uppercase tracking-wide text-white/35">
            набор
          </span>
          <input
            className={cn(FIELD, "max-w-[28rem]")}
            value={setText}
            disabled={disabled}
            placeholder="SET / декорация сцены"
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
        {light ? <div className="w-[13rem] shrink-0">{light}</div> : null}
      </div>
      {template}
      <div className="flex flex-wrap gap-x-3 gap-y-0.5">
        <MetaChip label="смысл" value={head.scene_sense} />
        <MetaChip label="тип" value={head.scene_visual_type} />
        <MetaChip label="предметы" value={head.scene_props} />
        <MetaChip label="фон" value={head.scene_bg} />
        <MetaChip label="акцент" value={head.scene_accent} />
        <MetaChip label="особенность" value={head.scene_feature} />
      </div>
      {parts.length || head.vo_cell_full ? (
        <div className="rounded-md border border-white/10 bg-black/25 p-1.5">
          <p className="text-[9px] uppercase tracking-wide text-white/30">
            закадр сцены по кадрам — так его режут якоря
          </p>
          {parts.length ? (
            <p className="mt-0.5 max-h-16 overflow-y-auto text-[10px] leading-relaxed text-white/60">
              {parts.map((f) => (
                <span key={f.frame_id}>
                  <span className="font-mono text-[9px] text-white/30">
                    #{f.number}{" "}
                  </span>
                  <span>{f.voiceover_text.trim()} </span>
                </span>
              ))}
            </p>
          ) : (
            <p className="mt-0.5 max-h-16 overflow-y-auto text-[10px] leading-relaxed text-white/60">
              {head.vo_cell_full}
            </p>
          )}
        </div>
      ) : null}
    </div>
  );
}
