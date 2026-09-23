"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { ChevronDown, Images, Loader2, Plus, Sparkles, Trash2 } from "lucide-react";
import { toast } from "sonner";
import {
  api,
  type MontagePendingOp,
  type SceneAnchorRow,
  type SceneVariant,
  type SceneVariantKind,
} from "@/lib/api";
import { errorMessageFromUnknown } from "@/lib/error-message";
import type {
  MontageAnchorRow,
  MontageBoardFrame,
  MontageImproveReport,
  MontageSceneChainRow,
} from "@/lib/types";
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
const MENU_H = 230;

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

  useEffect(() => {
    if (!disabled) return;
    if (hideRef.current) clearTimeout(hideRef.current);
    setBox(null);
  }, [disabled]);

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
              className="pointer-events-auto fixed z-[10060] rounded-xl border border-white/12 bg-[#0b0b0b]/98 p-2 shadow-2xl backdrop-blur"
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
  fallbackParentNumber,
  pending,
  disabled,
  compact,
  onKind,
  onDeleteChild,
}: {
  kind: "parent" | "child" | "";
  parentNumber: number | null;
  frameNumber: number;
  parentChoices: Array<{ number: number; kind: string; vo: string }>;
  /** Still-родитель по умолчанию: голова этой же VO-сцены, не кадр #1 проекта. */
  fallbackParentNumber?: number | null;
  pending?: boolean;
  disabled?: boolean;
  /** Компактная полоска поверх картинки кадра. */
  compact?: boolean;
  onKind: (kind: "parent" | "child", parentNumber: number | null) => void;
  onDeleteChild: () => void;
}) {
  const fallbackParent =
    fallbackParentNumber ??
    parentChoices.find((p) => p.kind === "parent")?.number ??
    parentChoices[0]?.number ??
    null;
  return (
    <div
      className={cn(
        "relative rounded-md",
        compact
          ? "bg-black/75 p-1 shadow-lg backdrop-blur-sm"
          : "p-0.5",
        pending && "bg-amber-500/10",
      )}
    >
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
      ) : compact ? null : (
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

/** Якоря VO-ячейки: где сцена режется на кадры. */
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
  heading,
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
  heading?: string;
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
      {heading ? (
        <span className="mb-1 flex items-center gap-1.5 text-[10px] font-semibold uppercase tracking-wide text-white/55">
          {heading}
          {pending ? <span className="h-1.5 w-1.5 rounded-full bg-amber-300/80" /> : null}
        </span>
      ) : null}
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

export type SceneDataField =
  | "sense"
  | "visual_type"
  | "place"
  | "characters"
  | "props"
  | "bg"
  | "accent"
  | "feature"
  | "set"
  | "light";

export type SceneDataValues = Record<SceneDataField, string>;

export const SCENE_DATA_OPS: Record<SceneDataField, MontagePendingOp["type"]> = {
  sense: "coverage_sense",
  visual_type: "coverage_visual_type",
  place: "coverage_place",
  characters: "coverage_characters",
  props: "coverage_props",
  bg: "coverage_bg",
  accent: "coverage_accent",
  feature: "coverage_feature",
  set: "coverage_set",
  light: "coverage_light",
};

const SCENE_DATA_FIELDS: {
  key: SceneDataField;
  label: string;
  placeholder: string;
  multiline?: boolean;
}[] = [
  { key: "sense", label: "смысл", placeholder: "что происходит в ячейке", multiline: true },
  { key: "visual_type", label: "тип", placeholder: "стиль картинки" },
  { key: "place", label: "место", placeholder: "где стоит камера" },
  { key: "set", label: "набор", placeholder: "декорация / обстановка" },
  { key: "characters", label: "персонажи", placeholder: "c01, c02" },
  { key: "light", label: "свет", placeholder: "дневной / ночной" },
  { key: "props", label: "предметы", placeholder: "что видно в кадре" },
  { key: "bg", label: "фон", placeholder: "задний план" },
  { key: "accent", label: "акцент", placeholder: "на чём глаз" },
  { key: "feature", label: "особенность", placeholder: "чем сцена отличается" },
];

function SceneDataFieldInput({
  field,
  value,
  pending,
  disabled,
  visualTypeChoices,
  lightChoices,
  onCommit,
}: {
  field: (typeof SCENE_DATA_FIELDS)[number];
  value: string;
  pending?: boolean;
  disabled?: boolean;
  visualTypeChoices?: string[];
  lightChoices?: string[];
  onCommit: (key: SceneDataField, next: string) => void;
}) {
  const [text, setText] = useState(value);
  useEffect(() => {
    setText(value);
  }, [value]);

  const commit = () => {
    const next = text.trim();
    if (!next || next === value.trim()) return;
    onCommit(field.key, next);
  };

  if (field.key === "visual_type") {
    return (
      <ChipsCell
        value={value}
        choices={visualTypeChoices}
        pending={pending}
        disabled={disabled}
        onPick={(id) => {
          if (id === value.trim()) return;
          onCommit("visual_type", id);
        }}
      />
    );
  }

  if (field.key === "light") {
    return (
      <ChipsCell
        value={value}
        choices={lightChoices}
        pending={pending}
        disabled={disabled}
        onPick={(id) => {
          if (id === value.trim()) return;
          onCommit("light", id);
        }}
      />
    );
  }

  const shared = {
    className: cn(FIELD, field.multiline && "min-h-[3.25rem] resize-y"),
    value: text,
    disabled,
    placeholder: field.placeholder,
    onChange: (e: { target: { value: string } }) => setText(e.target.value),
    onBlur: commit,
    onKeyDown: (e: { key: string; metaKey: boolean; ctrlKey: boolean; preventDefault: () => void }) => {
      if (e.key === "Enter" && (e.metaKey || e.ctrlKey || !field.multiline)) {
        e.preventDefault();
        commit();
      }
      if (e.key === "Escape") setText(value);
    },
  };

  return field.multiline ? <textarea {...shared} /> : <input {...shared} />;
}

/**
 * Кнопка в блоке сцены: по клику в клетке появляются поля ячейки.
 * Правка кладётся в очередь; «Применить правки» пишет те же attrs, что читает доска.
 */
export function SceneDataCell({
  values,
  pending,
  visualTypeChoices,
  lightChoices,
  disabled,
  onCommit,
}: {
  values: SceneDataValues;
  pending: Partial<Record<SceneDataField, boolean>>;
  visualTypeChoices?: string[];
  lightChoices?: string[];
  disabled?: boolean;
  onCommit: (field: SceneDataField, value: string) => void;
}) {
  const [open, setOpen] = useState(false);
  const anyPending = SCENE_DATA_FIELDS.some((f) => pending[f.key]);
  const summary = [
    values.sense,
    values.place,
    values.characters,
    values.light,
    values.props,
  ].filter((x) => x.trim());

  return (
    <div className={cn("relative rounded-md", anyPending && "bg-amber-500/10")}>
      <button
        type="button"
        disabled={disabled}
        onClick={() => setOpen((v) => !v)}
        title="Паспорт сцены: место, персонажи, свет — генерация сцен берёт эти поля"
        className={cn(
          "group flex w-full items-center gap-1.5 rounded-md border px-1.5 py-1 text-left transition disabled:opacity-40",
          open
            ? "border-white/30 bg-white/[0.08]"
            : "border-white/10 bg-white/[0.03] hover:border-white/25 hover:bg-white/[0.07]",
        )}
      >
        <span className="text-[9px] uppercase tracking-wide text-white/35">паспорт сцены</span>
        <ChevronDown
          className={cn(
            "h-2.5 w-2.5 text-white/35 transition",
            open && "rotate-180",
          )}
        />
        {anyPending ? (
          <span className="h-1.5 w-1.5 rounded-full bg-amber-300/80" title="есть правка в очереди" />
        ) : null}
        <span
          className={cn(
            "ml-auto text-[10px] normal-case tracking-normal transition-colors",
            open ? "text-white/45" : "text-transparent group-hover:text-white/40",
          )}
        >
          {open ? "свернуть" : "изменить"}
        </span>
      </button>
      {!open ? (
        <p className={cn(HINT, "mt-1 truncate")}>
          {summary.length ? summary.join(" · ") : "смысл, место, персонажи — нажми, чтобы править"}
        </p>
      ) : (
        <div className="mt-1.5 space-y-1.5 rounded-lg border border-white/10 bg-black/30 p-1.5">
          {SCENE_DATA_FIELDS.map((field) => (
            <label key={field.key} className="block">
              <span className="mb-0.5 flex items-center gap-1 text-[9px] uppercase tracking-wide text-white/35">
                {field.label}
                {pending[field.key] ? (
                  <span className="h-1.5 w-1.5 rounded-full bg-amber-300/80" />
                ) : null}
              </span>
              <SceneDataFieldInput
                field={field}
                value={values[field.key]}
                pending={pending[field.key]}
                disabled={disabled}
                visualTypeChoices={visualTypeChoices}
                lightChoices={lightChoices}
                onCommit={onCommit}
              />
            </label>
          ))}
          <p className={HINT}>
            в очередь сразу; генерация сцен забирает эти поля в промт action
          </p>
        </div>
      )}
      <PendingMark show={anyPending && !open} />
    </div>
  );
}

/**
 * Промт оператора + генерация сцен ячейки / пересборка кусков ``N.``.
 * GPT берёт промт action группы, не гоняет всю ноду fw_action.
 */
export function SceneGenerateBlock({
  projectId,
  frameId,
  chain,
  passport,
  disabled,
  onDone,
  onImprove,
}: {
  projectId: number;
  frameId: number;
  chain: MontageSceneChainRow[];
  passport: Record<string, string>;
  disabled?: boolean;
  onDone?: () => void;
  onImprove?: (prompt: string) => Promise<MontageImproveReport | undefined | void> | void;
}) {
  const [prompt, setPrompt] = useState("");
  const [selected, setSelected] = useState<number[]>([]);
  const [busy, setBusy] = useState<"all" | "pieces" | "improve" | null>(null);
  const [report, setReport] = useState<MontageImproveReport | null>(null);

  useEffect(() => {
    const allowed = new Set(chain.map((row) => row.n));
    setSelected((prev) => prev.filter((n) => allowed.has(n)));
  }, [chain]);

  const toggle = (n: number) => {
    setSelected((prev) => (prev.includes(n) ? prev.filter((x) => x !== n) : [...prev, n].sort((a, b) => a - b)));
  };

  const run = async (replaceNs: number[]) => {
    if (busy) return;
    setBusy(replaceNs.length ? "pieces" : "all");
    try {
      const result = await api.generateSceneAction(projectId, frameId, {
        prompt: prompt.trim(),
        replace_ns: replaceNs,
        passport,
      });
      const skipped = Number(result.skipped_shots || 0);
      toast.success(
        replaceNs.length
          ? `Куски ${replaceNs.join(", ")} пересобраны`
          : `Сцены ячейки записаны${skipped ? ` · ${skipped} шагов без новых кадров` : ""}`,
      );
      onDone?.();
    } catch (err) {
      toast.error(errorMessageFromUnknown(err));
    } finally {
      setBusy(null);
    }
  };

  const improve = async () => {
    if (busy || !onImprove) return;
    setBusy("improve");
    try {
      const next = await onImprove(prompt.trim());
      if (next) setReport(next);
    } finally {
      setBusy(null);
    }
  };

  return (
    <div className="rounded-lg border border-white/12 bg-black/25 p-2">
      <span className="mb-1 flex items-center gap-1.5 text-[10px] font-semibold uppercase tracking-wide text-white/70">
        <Sparkles className="h-3 w-3" />
        сцены по промту
      </span>
      <textarea
        className={cn(FIELD, "min-h-[3.5rem] resize-y")}
        value={prompt}
        disabled={disabled || Boolean(busy)}
        placeholder="что сделать со сценами этой ячейки закадра"
        onChange={(e) => setPrompt(e.target.value)}
      />
      {chain.length ? (
        <div className="mt-1.5 flex flex-wrap items-center gap-1">
          <span className="text-[9px] uppercase tracking-wide text-white/35">куски</span>
          {chain.map((row) => (
            <Chip
              key={row.n}
              active={selected.includes(row.n)}
              disabled={disabled || Boolean(busy)}
              title={[row.place, row.action].filter(Boolean).join(" — ")}
              onClick={() => toggle(row.n)}
            >
              {row.n}.
            </Chip>
          ))}
        </div>
      ) : (
        <p className={cn(HINT, "mt-1.5")}>кусков пока нет — сначала сгенерируйте сцены</p>
      )}
      <div className="mt-2 grid grid-cols-2 gap-1.5">
        <button
          type="button"
          disabled={disabled || Boolean(busy)}
          onClick={() => void run([])}
          className={cn(
            "flex h-10 items-center justify-center gap-1.5 rounded-md text-[11px] font-semibold transition disabled:opacity-40",
            "bg-[rgba(209,254,23,0.95)] text-black hover:bg-[rgba(209,254,23,1)]",
          )}
        >
          {busy === "all" ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : null}
          Сгенерировать сцены
        </button>
        <button
          type="button"
          disabled={disabled || Boolean(busy) || selected.length === 0}
          onClick={() => void run(selected)}
          className={cn(
            "flex h-10 items-center justify-center gap-1.5 rounded-md border border-white/15 text-[11px] font-semibold text-white/80 transition disabled:opacity-40 hover:border-white/30",
          )}
        >
          {busy === "pieces" ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : null}
          Пересобрать куски
        </button>
      </div>
      {onImprove ? (
        <button
          type="button"
          title="Ячейка идёт через 6 нод группы: биты → проверка → действие → кадры → QC → отчёт. Дописывает вход, мосты, реакцию и следствие, ставит покрытие кадров и паспорт, затем PNG."
          disabled={disabled || Boolean(busy)}
          onClick={() => void improve()}
          className={cn(
            "mt-1.5 flex h-10 w-full items-center justify-center gap-1.5 rounded-md border text-[11px] font-semibold transition disabled:opacity-40",
            "border-[rgba(209,254,23,0.45)] bg-black/35 text-[rgba(209,254,23,0.95)] hover:bg-[rgba(209,254,23,0.12)]",
          )}
        >
          {busy === "improve" ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Sparkles className="h-3.5 w-3.5" />}
          Улучшить сцену
        </button>
      ) : null}
      {busy ? (
        <p className="mt-1.5 text-[11px] font-medium text-[rgba(209,254,23,0.95)]">
          {busy === "improve"
            ? "6 нод: биты → проверка → действие → кадры → QC → отчёт…"
            : "GPT пишет сцены этой ячейки…"}
        </p>
      ) : (
        <p className={cn(HINT, "mt-1.5")}>
          сгенерировать — цепь на существующие кадры. улучшить — по правилам
          монтажа: вход в место, мосты, реакция, следствие; покрытие кадров и
          паспорт сцены; недостающие кадры вставляются и получают PNG.
        </p>
      )}
      {report ? <ImproveReportView report={report} onClose={() => setReport(null)} /> : null}
    </div>
  );
}

const IMPROVE_STATUS_TONE: Record<string, string> = {
  ok: "text-[rgba(209,254,23,0.9)]",
  reused: "text-white/60",
  fixed: "text-sky-300",
  fallback: "text-amber-300",
  warn: "text-amber-300",
};

function ImproveReportView({
  report,
  onClose,
}: {
  report: MontageImproveReport;
  onClose: () => void;
}) {
  return (
    <div className="mt-2 rounded-md border border-white/10 bg-black/30 p-2 text-[10px] leading-snug text-white/75">
      <div className="mb-1 flex items-center justify-between">
        <span className="font-semibold uppercase tracking-wide text-white/60">отчёт улучшения</span>
        <button type="button" className="text-white/40 hover:text-white/80" onClick={onClose}>
          скрыть
        </button>
      </div>
      <ol className="space-y-0.5">
        {report.nodes.map((n) => (
          <li key={n.node} className="flex gap-1.5">
            <span className={cn("shrink-0 font-semibold", IMPROVE_STATUS_TONE[n.status] ?? "text-white/60")}>
              {n.label}
            </span>
            <span className="text-white/55">{n.note}</span>
          </li>
        ))}
      </ol>
      {report.shots.length ? (
        <ol className="mt-1.5 space-y-0.5 border-t border-white/10 pt-1.5">
          {report.shots.map((s, i) => (
            <li key={`${s["порядок"] ?? i}`}>
              <span className="text-white/45">{s["порядок"] ?? i + 1}.</span>{" "}
              <span className="text-white/50">
                [{[s["роль"], s["план"], s["ракурс"], s["движение"], s["стык"]].filter(Boolean).join(" · ")}]
              </span>{" "}
              {s["действие"]}
            </li>
          ))}
        </ol>
      ) : null}
      {report.warnings?.length ? (
        <ul className="mt-1.5 space-y-0.5 text-amber-300/80">
          {report.warnings.map((w) => (
            <li key={w}>· {w}</li>
          ))}
        </ul>
      ) : null}
    </div>
  );
}

/** Цепь «A → B» или нумерованные строки → шаги для картинок кадров. */
export function splitSceneActionBeats(text: string): string[] {
  const raw = (text || "").trim();
  if (!raw) return [];
  const arrows = raw
    .split(/\s*→\s*/)
    .map((s) => s.trim())
    .filter(Boolean);
  if (arrows.length >= 2) return arrows;
  const numbered: string[] = [];
  const re = /^\s*\d+[.)]\s+(.+)$/gm;
  let m: RegExpExecArray | null = re.exec(raw);
  while (m) {
    const line = m[1].replace(/\s+/g, " ").trim();
    if (line) numbered.push(line);
    m = re.exec(raw);
  }
  if (numbered.length >= 2) return numbered;
  return [raw];
}

export function sceneImageInstruction(opts: {
  beat?: string;
  place?: string;
  set?: string;
  light?: string;
  characters?: string;
  sense?: string;
  bg?: string;
}): string {
  const parts: string[] = [];
  const place = (opts.place || opts.set || "").trim();
  if (place) parts.push(`Место только: ${place}. Не выдумывай другое место.`);
  if (opts.light?.trim()) parts.push(`Свет: ${opts.light.trim()}.`);
  if (opts.characters?.trim()) parts.push(`Персонажи: ${opts.characters.trim()}.`);
  if (opts.bg?.trim()) parts.push(`Фон: ${opts.bg.trim()}.`);
  if (opts.sense?.trim()) parts.push(`Смысл: ${opts.sense.trim()}.`);
  if (opts.beat?.trim()) parts.push(`Действие этого кадра: ${opts.beat.trim()}.`);
  parts.push("Один кадр, не коллаж.");
  return parts.join(" ");
}

/**
 * Последовательность кадров сцены. Большая кнопка кладёт разбор
 * в очередь и сразу запускает «Применить правки».
 * «Генерация с картинками» — тот же разбор + ИИзменение PNG на каждый кадр.
 */
export function SceneActionBlock({
  value,
  pending,
  disabled,
  applyBusy,
  onQueue,
  onApply,
  onApplyWithImages,
}: {
  value: string;
  pending?: boolean;
  disabled?: boolean;
  applyBusy?: boolean;
  onQueue: (action: string) => void;
  onApply: (action: string) => void;
  onApplyWithImages: (action: string) => void;
}) {
  const [text, setText] = useState(value);
  useEffect(() => {
    setText(value);
  }, [value]);

  const commitQueue = () => {
    const next = text.trim();
    if (!next || next === value.trim()) return;
    onQueue(next);
  };

  const runApply = () => {
    const next = text.trim();
    if (!next) return;
    onApply(next);
  };

  const runApplyWithImages = () => {
    const next = text.trim();
    if (!next) return;
    onApplyWithImages(next);
  };

  return (
    <div
      className={cn(
        "rounded-lg border p-2",
        pending
          ? "border-amber-400/50 bg-amber-500/10"
          : "border-[rgba(209,254,23,0.35)] bg-[rgba(209,254,23,0.06)]",
      )}
    >
      <span className="mb-1 flex items-center gap-1.5 text-[10px] font-semibold uppercase tracking-wide text-[rgba(209,254,23,0.9)]">
        последовательность кадров
        {pending ? <span className="h-1.5 w-1.5 rounded-full bg-amber-300/80" /> : null}
      </span>
      <textarea
        className={cn(FIELD, "min-h-[7rem] resize-y text-[12px]")}
        value={text}
        disabled={disabled}
        placeholder={
          "действие кадра 1 → действие кадра 2 → действие кадра 3\nдевушка подходит к двери → девушка открывает дверь → мужчина сидит за столом"
        }
        onChange={(e) => setText(e.target.value)}
        onBlur={commitQueue}
        onKeyDown={(e) => {
          if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) {
            e.preventDefault();
            runApply();
          }
          if (e.key === "Escape") setText(value);
        }}
      />
      <button
        type="button"
        disabled={disabled || applyBusy || !text.trim()}
        onClick={runApply}
        className={cn(
          "mt-2 flex h-12 w-full items-center justify-center gap-2 rounded-md text-sm font-semibold transition disabled:opacity-40",
          "bg-[rgba(209,254,23,0.95)] text-black hover:bg-[rgba(209,254,23,1)]",
        )}
      >
        {applyBusy ? <Loader2 className="h-4 w-4 animate-spin" /> : null}
        Разобрать на кадры
      </button>
      <button
        type="button"
        title="Разбор цепи + новый промт и PNG на каждый видимый кадр сцены"
        disabled={disabled || applyBusy || !text.trim()}
        onClick={runApplyWithImages}
        className={cn(
          "mt-1.5 flex h-12 w-full items-center justify-center gap-2 rounded-md border text-sm font-semibold transition disabled:opacity-40",
          "border-[rgba(209,254,23,0.45)] bg-black/35 text-[rgba(209,254,23,0.95)] hover:bg-[rgba(209,254,23,0.12)]",
        )}
      >
        {applyBusy ? <Loader2 className="h-4 w-4 animate-spin" /> : <Images className="h-4 w-4" />}
        Генерация с картинками
      </button>
      <p className={cn(HINT, "mt-1.5")}>
        разобрать — только действия кадров. генерация с картинками — GPT
        пишет сцены ячейки, затем ИИзменение и PNG на каждый видимый кадр.
      </p>
    </div>
  );
}

/**
 * Сцена = одна ячейка закадра. В полосе «Сцены» — последовательность кадров,
 * якоря ячейки и данные ячейки.
 */
export function SceneCell({
  action,
  anchors,
  data,
  generate,
}: {
  action?: React.ReactNode;
  anchors?: React.ReactNode;
  data?: React.ReactNode;
  generate?: React.ReactNode;
}) {
  if (!action && !anchors && !data && !generate) return null;
  return (
    <div className="min-w-0 space-y-2">
      {generate}
      {action}
      {data}
      {anchors}
    </div>
  );
}
