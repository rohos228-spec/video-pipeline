"use client";

/**
 * «Библиотека» в дизайне студии. Первый уровень — категории крупными
 * прямоугольными ячейками, клик открывает ноды категории. Клик по ноде
 * открывает развёрнутое окно в стиле меню ноды на канвасе: шапка 60px
 * с картинкой, название и ×, вкладки.
 */

import { useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import { StarRating } from "@/components/ui/star-rating";
import { errorMessageFromUnknown } from "@/lib/error-message";
import { Plus } from "lucide-react";
import type { NodeGroupSummary } from "@/lib/types";
import {
  getNodeSpec,
  NODE_CATEGORY_LABELS,
  NODE_CATEGORY_ORDER,
  type NodeCategory,
} from "@/lib/node-catalog";
import { defaultPromptSlots, nodeTypeRequiresExcel } from "@/lib/node-prompts";
import { stepCodeForNodeType, stepHasPromptVariants } from "@/lib/node-step-map";
import { nodeBgFor, promptBgFor } from "@/components/prompts/prompt-card-catalog";
import { cn } from "@/lib/utils";

export const RUBIK = "Rubik, sans-serif";

export type LibrarySource = "builtin" | "custom" | "mine" | "groups";

export const LIBRARY_SOURCES: [LibrarySource, string][] = [
  ["builtin", "Встроенные"],
  ["custom", "Пользовательские"],
  ["mine", "Мои"],
  ["groups", "Группы"],
];

/** Переключатель верхнего уровня — как вкладки меню ноды. */
export function LibrarySwitch({
  value,
  onChange,
}: {
  value: LibrarySource;
  onChange: (v: LibrarySource) => void;
}) {
  return (
    <div className="grid grid-cols-4 border border-white/10" style={{ fontFamily: RUBIK }}>
      {LIBRARY_SOURCES.map(([id, label]) => (
        <button
          key={id}
          type="button"
          onClick={() => onChange(id)}
          className={cn(
            "h-9 rounded-none px-4 text-[12px] font-medium uppercase tracking-[0.08em] transition-colors",
            value === id ? "bg-[#2bb8a0]/15 text-[#bff3e8]" : "text-white/60 hover:bg-white/[0.05] hover:text-white",
          )}
        >
          {label}
        </button>
      ))}
    </div>
  );
}

export function LibraryBrowser({
  source,
  addable,
  matchNode,
  query,
  myGroups,
  matchGroup,
  onAddNode,
  onOpenGroup,
}: {
  source: Exclude<LibrarySource, "groups">;
  addable: string[];
  matchNode: (t: string) => boolean;
  query: string;
  myGroups: NodeGroupSummary[];
  matchGroup: (g: NodeGroupSummary) => boolean;
  onAddNode: (t: string) => void;
  onOpenGroup: (id: string) => void;
}) {
  const list = addable.filter(matchNode);
  const [selected, setSelected] = useState<string | null>(null);
  const openType = selected && list.includes(selected) ? selected : (list[0] ?? null);
  const byCategory = useMemo(() => {
    const map = new Map<NodeCategory, string[]>();
    for (const t of list) {
      const cat = getNodeSpec(t).category;
      map.set(cat, [...(map.get(cat) ?? []), t]);
    }
    return NODE_CATEGORY_ORDER.filter((c) => map.has(c)).map((c) => [c, map.get(c)!] as const);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [list.join("|")]);

  if (source === "custom") return <EmptyPane text="Пользовательских нод пока нет." />;
  if (source === "mine") {
    const mine = myGroups.filter(matchGroup);
    if (!mine.length) return <EmptyPane text="Своих пока нет — сохрани выделение на канвасе во вкладке «Группы»." />;
    return (
      <div className="min-h-0 flex-1 overflow-y-auto p-2" style={{ fontFamily: RUBIK }}>
        <div className="flex w-[300px] flex-col gap-1.5">
          {mine.map((g) => (
            <button key={g.id} type="button" onClick={() => onOpenGroup(g.id)} className="rounded-xl border border-white/10 bg-white/[0.02] px-2.5 py-2 text-left transition-colors hover:border-teal-400/40">
              <div className="truncate text-xs font-semibold">{g.title}</div>
              <div className="mt-0.5 text-[10px] text-muted-foreground">{g.description || `${g.node_count} нод`}</div>
            </button>
          ))}
        </div>
      </div>
    );
  }

  return (
    <div className="flex min-h-0 flex-1" style={{ fontFamily: RUBIK }} data-library-browser>
      <div className="flex w-[300px] shrink-0 flex-col border-r border-white/10">
        <div className="min-h-0 flex-1 overflow-y-auto">
          <div className="flex flex-col gap-1.5 p-2">
            {!list.length && <div className="px-2 py-6 text-center text-xs text-muted-foreground">{query ? "Ничего не найдено." : "Нод нет."}</div>}
            {byCategory.map(([cat, types]) => (
              <div key={cat} className="flex flex-col gap-1.5">
                <div className="px-1 pt-2 text-[10px] font-semibold uppercase tracking-wider text-muted-foreground">
                  {NODE_CATEGORY_LABELS[cat]} · {types.length}
                </div>
                {types.map((t) => (
                  <NodeCard key={t} type={t} active={t === openType} onClick={() => setSelected(t)} onDoubleClick={() => onAddNode(t)} />
                ))}
              </div>
            ))}
          </div>
        </div>
      </div>
      <div className="min-h-0 flex-1">
        {openType ? (
          <NodeDetailPane key={openType} type={openType} onAdd={() => onAddNode(openType)} />
        ) : (
          <div className="flex h-full items-center justify-center text-center text-[11px] text-muted-foreground">Кликни по ноде слева, чтобы посмотреть её данные</div>
        )}
      </div>
    </div>
  );
}

/** Current node header picture — the same one the node uses on the pipeline canvas. */
export function nodeTypeBg(type: string): string {
  const s = getNodeSpec(type);
  return nodeBgFor(`n_${type}`, promptBgFor(type, s.label)) ?? promptBgFor(type, s.label);
}

function EmptyPane({ text }: { text: string }) {
  return (
    <div className="flex min-h-0 flex-1 items-center justify-center p-6 text-center text-[12px] text-muted-foreground" style={{ fontFamily: RUBIK }}>
      {text}
    </div>
  );
}

function NodeCard({ type, active, onClick, onDoubleClick }: { type: string; active: boolean; onClick: () => void; onDoubleClick: () => void }) {
  const s = getNodeSpec(type);
  return (
    <div
      role="button"
      tabIndex={0}
      data-node-card={type}
      title="Клик — данные ноды; двойной клик — сразу на канвас"
      onClick={onClick}
      onDoubleClick={onDoubleClick}
      onKeyDown={(e) => e.key === "Enter" && onClick()}
      className={cn(
        "cursor-pointer overflow-hidden rounded-xl border text-left transition-colors",
        active ? "border-teal-400/50 bg-teal-400/10" : "border-white/10 bg-white/[0.02] hover:border-white/25",
      )}
    >
      <div className="relative h-[64px] bg-cover bg-center" style={{ backgroundImage: `url(${nodeTypeBg(type)})` }}>
        <div className="pointer-events-none absolute inset-0 bg-gradient-to-t from-black/60 to-transparent" />
        <StarRating kind="node" id={type} size={11} className="absolute bottom-1.5 left-1.5" />
      </div>
      <div className="px-2.5 py-2">
        <div className="truncate text-xs font-semibold">{s.label}</div>
        <div className="mt-0.5 line-clamp-2 text-[10px] text-muted-foreground">{s.description}</div>
      </div>
    </div>
  );
}

type Review = { author: string; rating: number; text: string; ts?: number; placeholder?: boolean };

function NodeDetailPane({ type, onAdd }: { type: string; onAdd: () => void }) {
  const qc = useQueryClient();
  const spec = getNodeSpec(type);
  const step = stepCodeForNodeType(type);
  const slots = defaultPromptSlots(type);
  const reviews = useQuery({
    queryKey: ["node-reviews", type],
    queryFn: async () => ((await (await fetch(`/api/node-reviews/${type}`)).json()) as { reviews: Review[] }).reviews,
  });
  const [text, setText] = useState("");
  const [author, setAuthor] = useState("");
  const [stars, setStars] = useState(5);
  const add = useMutation({
    mutationFn: async () => {
      const r = await fetch(`/api/node-reviews/${type}`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ text, author, rating: stars }) });
      if (!r.ok) throw new Error(await r.text());
    },
    onSuccess: () => {
      setText("");
      toast.success("Отзыв добавлен");
      void qc.invalidateQueries({ queryKey: ["node-reviews", type] });
    },
    onError: (e) => toast.error(errorMessageFromUnknown(e)),
  });
  const H = ({ children }: { children: React.ReactNode }) => (
    <div className="mb-1.5 text-[10px] font-semibold uppercase tracking-wider text-muted-foreground">{children}</div>
  );
  return (
    <div className="h-full overflow-y-auto" data-node-detail={type}>
      <div className="flex flex-col gap-4 p-4">
        <div className="relative h-[150px] overflow-hidden rounded-xl border border-white/10 bg-cover bg-center" style={{ backgroundImage: `url(${nodeTypeBg(type)})` }}>
          <div className="pointer-events-none absolute inset-0 bg-gradient-to-t from-black/70 via-black/10 to-transparent" />
          <StarRating kind="node" id={type} size={14} className="absolute bottom-2.5 left-2.5" />
        </div>
        <div className="flex items-start justify-between gap-3">
          <div className="min-w-0">
            <div className="flex flex-wrap items-center gap-1.5">
              <span className="inline-block h-2.5 w-2.5 shrink-0 rounded-full bg-teal-300" />
              <span className="truncate text-sm font-semibold">{spec.label}</span>
              <span className="rounded-full border border-teal-400/30 bg-teal-500/10 px-1.5 py-0.5 text-[9px] font-medium text-teal-300">встроенная</span>
            </div>
            <div className="mt-1 text-[10px] text-muted-foreground">
              {NODE_CATEGORY_LABELS[spec.category]} · <code>{type}</code> · автор: VIA Studio
            </div>
          </div>
          <button type="button" onClick={onAdd} data-node-add className="flex h-8 shrink-0 items-center gap-1.5 rounded-lg bg-[#2bb8a0]/20 px-3 text-[12px] font-medium text-[#bff3e8] ring-1 ring-[#2bb8a0]/35 hover:bg-[#2bb8a0]/30">
            <Plus className="h-3.5 w-3.5" /> На канвас
          </button>
        </div>
        <div>
          <H>Что делает</H>
          <p className="text-xs leading-relaxed text-white/80">{spec.description || "Без описания."}</p>
          <div className="mt-2 text-[11px] leading-relaxed text-muted-foreground">
            Шаг пайплайна: {step ?? "—"} · варианты промтов: {stepHasPromptVariants(step) ? "есть" : "нет"} · нужна Excel-таблица: {nodeTypeRequiresExcel(type) ? "да" : "нет"}
          </div>
          {slots.length ? (
            <div className="mt-2 flex flex-wrap gap-1">
              {slots.map((sl) => (
                <span key={sl.id} title={sl.description} className="rounded-full border border-white/10 bg-white/[0.03] px-2 py-0.5 text-[10px] text-white/75">{sl.title}</span>
              ))}
            </div>
          ) : null}
        </div>
        <div>
          <H>Автор</H>
          <div className="text-xs text-white/80">VIA Studio · встроенная нода</div>
        </div>
        <div>
          <H>Отзывы ({reviews.data?.length ?? 0})</H>
          <div className="flex flex-col gap-1.5">
            {(reviews.data ?? []).map((r, i) => (
              <div key={i} className="rounded-xl border border-white/10 bg-white/[0.02] px-2.5 py-2" data-review>
                <div className="flex items-center gap-2">
                  <span className="text-xs font-semibold">{r.author}</span>
                  <span className="text-[10px] text-teal-300">{"★".repeat(r.rating)}<span className="text-white/25">{"★".repeat(5 - r.rating)}</span></span>
                  {r.placeholder ? <span className="text-[9px] text-muted-foreground">пример</span> : null}
                </div>
                <div className="mt-0.5 text-[11px] leading-relaxed text-white/75">{r.text}</div>
              </div>
            ))}
          </div>
          <div className="mt-2 flex flex-col gap-1.5 rounded-xl border border-dashed border-teal-400/40 bg-teal-400/5 p-2.5">
            <div className="flex items-center gap-2">
              <input value={author} onChange={(e) => setAuthor(e.target.value)} placeholder="Имя" className="h-7 w-[140px] rounded-md border border-white/10 bg-black/30 px-2 text-[12px] text-white" />
              <span className="flex items-center">
                {[1, 2, 3, 4, 5].map((n) => (
                  <button key={n} type="button" onClick={() => setStars(n)} className={cn("px-[1px] text-[14px]", n <= stars ? "text-teal-300" : "text-white/25")}>★</button>
                ))}
              </span>
            </div>
            <textarea value={text} onChange={(e) => setText(e.target.value)} placeholder="Ваш отзыв о ноде…" data-review-text className="min-h-[56px] rounded-md border border-white/10 bg-black/30 p-2 text-[12px] text-white" />
            <button type="button" disabled={!text.trim() || add.isPending} onClick={() => add.mutate()} data-review-add className="h-7 w-fit rounded-md border border-teal-400/50 px-3 text-[12px] text-teal-300 hover:bg-teal-400/10 disabled:opacity-40">
              Добавить отзыв
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}
