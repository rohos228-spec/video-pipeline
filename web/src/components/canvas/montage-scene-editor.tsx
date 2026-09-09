"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import { createPortal } from "react-dom";
import { useQuery } from "@tanstack/react-query";
import { Loader2, Plus, Sparkles, Trash2, X } from "lucide-react";
import { toast } from "sonner";
import {
  api,
  type MontagePendingOp,
  type SceneAnchorRow,
  type SceneEditorState,
  type SceneVariant,
  type SceneVariantKind,
} from "@/lib/api";
import { errorMessageFromUnknown } from "@/lib/error-message";
import { cn } from "@/lib/utils";
import { Button } from "@/components/ui/button";

export type SceneEditorTarget = { frameId: number; frameNumber: number } | null;

/** Что оператор поменял в панели — очередь собираем только из этого. */
type Draft = {
  kind: "parent" | "child" | "";
  parentNumber: number | null;
  template: string;
  plan: string;
  action: string;
  anchors: SceneAnchorRow[];
};

const SECTION = "border-t border-white/10 px-4 py-3.5 first:border-t-0";
const LABEL = "text-[10px] font-semibold uppercase tracking-wide text-white/40";
const HINT = "text-[11px] leading-snug text-muted-foreground";
const FIELD =
  "w-full rounded-md border border-white/12 bg-black/40 px-2 py-1.5 text-[12px] text-foreground outline-none focus:border-white/30";

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
        "rounded-md border px-2 py-1 text-[11px] leading-none transition disabled:opacity-40",
        active
          ? "border-transparent bg-[rgba(209,254,23,1)] font-semibold text-black"
          : "border-white/12 text-white/70 hover:border-white/30 hover:text-white",
      )}
    >
      {children}
    </button>
  );
}

function Section({
  label,
  aside,
  children,
}: {
  label: string;
  aside?: React.ReactNode;
  children: React.ReactNode;
}) {
  return (
    <section className={SECTION}>
      <div className="mb-2 flex items-center justify-between gap-2">
        <span className={LABEL}>{label}</span>
        {aside}
      </div>
      {children}
    </section>
  );
}

/** Текст ячейки с подсветкой найденных якорей — видно, где режется закадр. */
function AnchoredText({ text, anchors }: { text: string; anchors: SceneAnchorRow[] }) {
  const marks = useMemo(() => {
    const out: Array<{ start: number; end: number; index: number }> = [];
    const lower = text.toLowerCase();
    let cursor = 0;
    anchors.forEach((row, i) => {
      const needle = (row["якорь"] || "").trim();
      if (!needle) return;
      let at = text.indexOf(needle, cursor);
      if (at < 0) at = lower.indexOf(needle.toLowerCase(), cursor);
      if (at < 0) return;
      out.push({ start: at, end: at + needle.length, index: i + 1 });
      cursor = at + Math.max(needle.length, 1);
    });
    return out;
  }, [text, anchors]);

  if (!text) return <p className={HINT}>Ячейка без закадрового текста.</p>;
  const parts: React.ReactNode[] = [];
  let pos = 0;
  marks.forEach((m) => {
    if (m.start > pos) parts.push(text.slice(pos, m.start));
    parts.push(
      <mark
        key={`${m.index}-${m.start}`}
        className="rounded bg-[rgba(209,254,23,0.22)] px-0.5 text-[rgba(209,254,23,1)]"
      >
        {text.slice(m.start, m.end)}
      </mark>,
    );
    pos = m.end;
  });
  if (pos < text.length) parts.push(text.slice(pos));
  return (
    <p className="max-h-28 overflow-y-auto rounded-md border border-white/10 bg-black/25 p-2 text-[11px] leading-relaxed text-white/75">
      {parts}
    </p>
  );
}

function VariantList({
  kind,
  variants,
  busy,
  onTake,
}: {
  kind: SceneVariantKind;
  variants: SceneVariant[];
  busy: boolean;
  onTake: (variant: SceneVariant) => void;
}) {
  if (busy) {
    return (
      <p className={cn(HINT, "mt-2 flex items-center gap-1.5")}>
        <Loader2 className="h-3 w-3 animate-spin" /> Ноды сцен подбирают варианты…
      </p>
    );
  }
  if (!variants.length) return null;
  return (
    <ul className="mt-2 space-y-1.5">
      {variants.map((v, i) => {
        const head =
          kind === "action"
            ? v["действие"] || ""
            : kind === "template"
              ? `${v["шаблон"]} · ${v["лестница"] || ""}`
              : (v["биты"] || []).map((b) => `«${b["якорь"]}»`).join(" · ");
        return (
          <li
            key={i}
            className="rounded-md border border-white/10 bg-black/25 p-2 text-[11px] leading-snug"
          >
            <p className="text-white/85">{head}</p>
            {v["план"] ? (
              <p className="mt-1 text-[10px] uppercase tracking-wide text-white/40">
                план: {v["план"]}
              </p>
            ) : null}
            {v["почему"] ? <p className={cn(HINT, "mt-1")}>{v["почему"]}</p> : null}
            {kind === "anchors" && v.dropped ? (
              <p className="mt-1 text-[10px] text-amber-200">
                {v.dropped} якорь не нашёлся в тексте — отброшен
              </p>
            ) : null}
            <Button
              type="button"
              size="sm"
              variant="outline"
              className="mt-1.5 h-6 px-2 text-[11px]"
              onClick={() => onTake(v)}
            >
              Взять
            </Button>
          </li>
        );
      })}
    </ul>
  );
}

function draftFromState(state: SceneEditorState): Draft {
  return {
    kind: state.frame.role,
    parentNumber: state.frame.role === "child" ? state.parent.number : null,
    template: state.template.current,
    plan: state.plan.current,
    action: state.action.current,
    anchors: (state.anchors.bits || []).map((b) => ({
      "якорь": b["якорь"],
      "изменение": b["изменение"] || "",
      "главный": Boolean(b["главный"]),
    })),
  };
}

function anchorsEqual(a: SceneAnchorRow[], b: SceneAnchorRow[]): boolean {
  if (a.length !== b.length) return false;
  return a.every(
    (row, i) =>
      (row["якорь"] || "").trim() === (b[i]["якорь"] || "").trim() &&
      (row["изменение"] || "").trim() === (b[i]["изменение"] || "").trim() &&
      Boolean(row["главный"]) === Boolean(b[i]["главный"]),
  );
}

export function MontageSceneEditor({
  target,
  projectId,
  disabled,
  onClose,
  onQueue,
  onDeleteChild,
}: {
  target: SceneEditorTarget;
  projectId: number | null;
  disabled?: boolean;
  onClose: () => void;
  onQueue: (ops: MontagePendingOp[]) => void;
  onDeleteChild: (frameNumber: number) => void;
}) {
  const open = Boolean(target && projectId);
  const editor = useQuery({
    queryKey: ["scene-editor", projectId, target?.frameId],
    queryFn: () => api.getSceneEditor(projectId as number, target!.frameId),
    enabled: open,
    staleTime: 0,
  });

  const state = editor.data;
  const [draft, setDraft] = useState<Draft | null>(null);
  const [desc, setDesc] = useState("");
  const [variantKind, setVariantKind] = useState<SceneVariantKind | null>(null);
  const [variants, setVariants] = useState<Record<string, SceneVariant[]>>({});
  const [templateOpen, setTemplateOpen] = useState(false);

  useEffect(() => {
    if (state) setDraft(draftFromState(state));
  }, [state]);
  useEffect(() => {
    if (!open) {
      setDraft(null);
      setDesc("");
      setVariants({});
      setTemplateOpen(false);
    }
  }, [open]);
  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open, onClose]);

  const askVariants = useCallback(
    async (kind: SceneVariantKind) => {
      if (!projectId || !target) return;
      setVariantKind(kind);
      try {
        const res = await api.getSceneVariants(projectId, target.frameId, {
          kind,
          desc,
          count: 3,
        });
        setVariants((prev) => ({ ...prev, [kind]: res.variants || [] }));
        if (!res.variants?.length) toast.error("Модель не дала пригодных вариантов");
      } catch (e) {
        toast.error(errorMessageFromUnknown(e));
      } finally {
        setVariantKind(null);
      }
    },
    [projectId, target, desc],
  );

  const ops = useMemo<MontagePendingOp[]>(() => {
    if (!state || !draft) return [];
    const number = state.frame.number;
    const out: MontagePendingOp[] = [];
    const base = { frame_number: number, shot: 1 as const };
    if (
      draft.kind !== state.frame.role ||
      (draft.kind === "child" && draft.parentNumber !== state.parent.number)
    ) {
      if (draft.kind === "child" && draft.parentNumber) {
        out.push({
          ...base,
          type: "coverage_kind",
          kind: "child",
          parent_number: draft.parentNumber,
        });
      } else if (draft.kind === "parent") {
        out.push({ ...base, type: "coverage_kind", kind: "parent" });
      }
    }
    if (draft.template && draft.template !== state.template.current) {
      out.push({ ...base, type: "coverage_template", template: draft.template });
    }
    if (draft.plan && draft.plan !== state.plan.current) {
      out.push({ ...base, type: "coverage_plan", plan: draft.plan });
    }
    const action = draft.action.trim();
    if (action && action !== state.action.current.trim()) {
      out.push({ ...base, type: "coverage_action", action });
    }
    const anchors = draft.anchors.filter((a) => (a["якорь"] || "").trim());
    if (anchors.length && !anchorsEqual(anchors, draftFromState(state).anchors)) {
      out.push({ ...base, type: "coverage_anchors", anchors });
    }
    return out;
  }, [state, draft]);

  if (!open) return null;

  const patch = (next: Partial<Draft>) =>
    setDraft((prev) => (prev ? { ...prev, ...next } : prev));

  const currentTemplate = state?.template.choices.find(
    (c) => c.id === (draft?.template || ""),
  );

  return createPortal(
    <div className="fixed inset-0 z-[10120] flex justify-end bg-black/50" onMouseDown={onClose}>
      <aside
        className="flex h-full w-full max-w-[30rem] flex-col border-l border-white/12 bg-card shadow-2xl"
        onMouseDown={(e) => e.stopPropagation()}
      >
        <header className="flex items-center justify-between gap-2 border-b border-white/10 px-4 py-3">
          <div className="min-w-0">
            <h2 className="truncate text-sm font-semibold">
              Кадр #{target?.frameNumber} · сцена
            </h2>
            <p className={cn(HINT, "mt-0.5")}>
              {state
                ? `${state.frame.role === "child" ? `дочерний шот родителя #${state.parent.number}` : "VO-родитель ячейки"} · кадров в ячейке: ${state.template.group_len}`
                : "загрузка…"}
            </p>
          </div>
          <button
            type="button"
            onClick={onClose}
            className="rounded-md p-1 text-white/50 transition hover:bg-white/10 hover:text-white"
            title="Закрыть (Esc)"
          >
            <X className="h-4 w-4" />
          </button>
        </header>

        <div className="min-h-0 flex-1 overflow-y-auto">
          {editor.isLoading ? (
            <p className={cn(HINT, "flex items-center gap-1.5 px-4 py-6")}>
              <Loader2 className="h-3.5 w-3.5 animate-spin" /> Читаю карточку кадра…
            </p>
          ) : editor.isError ? (
            <p className="px-4 py-6 text-[12px] text-rose-300">
              {errorMessageFromUnknown(editor.error)}
            </p>
          ) : !state || !draft ? null : (
            <>
              <Section label="Что видно в кадре (для подбора)">
                <textarea
                  className={cn(FIELD, "min-h-[3.5rem] resize-y leading-snug")}
                  value={desc}
                  disabled={disabled}
                  placeholder="Коротко: руки крупно на тесьме папки, лицо в профиль…"
                  onChange={(e) => setDesc(e.target.value)}
                />
                <p className={cn(HINT, "mt-1.5")}>
                  Это описание уходит нодам сцен во всех подборах ниже.
                </p>
              </Section>

              <Section label="Роль в покрытии">
                <div className="flex flex-wrap items-center gap-1.5">
                  <Chip
                    active={draft.kind === "parent"}
                    disabled={disabled}
                    onClick={() => patch({ kind: "parent", parentNumber: null })}
                  >
                    Родитель
                  </Chip>
                  <Chip
                    active={draft.kind === "child"}
                    disabled={disabled}
                    onClick={() =>
                      patch({
                        kind: "child",
                        parentNumber:
                          draft.parentNumber ??
                          state.parent_choices.find((p) => p.role === "parent")?.number ??
                          state.parent_choices[0]?.number ??
                          null,
                      })
                    }
                  >
                    Дочерний
                  </Chip>
                  {draft.kind === "child" ? (
                    <select
                      className={cn(FIELD, "w-auto min-w-[9rem] py-1")}
                      disabled={disabled}
                      value={draft.parentNumber == null ? "" : String(draft.parentNumber)}
                      onChange={(e) =>
                        patch({ parentNumber: Number(e.target.value) || null })
                      }
                    >
                      <option value="">родитель…</option>
                      {state.parent_choices.map((p) => (
                        <option key={p.number} value={p.number}>
                          #{p.number}
                          {p.role === "parent" ? " · род." : ""}
                        </option>
                      ))}
                    </select>
                  ) : null}
                  {state.frame.role === "child" ? (
                    <button
                      type="button"
                      disabled={disabled}
                      onClick={() => onDeleteChild(state.frame.number)}
                      className="ml-auto inline-flex items-center gap-1 rounded-md border border-rose-400/40 px-2 py-1 text-[11px] text-rose-200 transition hover:bg-rose-500/15 disabled:opacity-40"
                    >
                      <Trash2 className="h-3 w-3" /> Удалить кадр
                    </button>
                  ) : null}
                </div>
              </Section>

              <Section
                label="Формат сцены"
                aside={
                  <button
                    type="button"
                    disabled={disabled}
                    onClick={() => void askVariants("template")}
                    className="inline-flex items-center gap-1 text-[11px] text-white/55 transition hover:text-white disabled:opacity-40"
                  >
                    <Sparkles className="h-3 w-3" /> подобрать
                  </button>
                }
              >
                <div className="flex flex-wrap gap-1.5">
                  {state.template.choices.map((c) => (
                    <Chip
                      key={c.id}
                      active={draft.template === c.id}
                      disabled={disabled}
                      title={`${c.name} — ${c.when}`}
                      onClick={() => {
                        patch({ template: c.id });
                        setTemplateOpen(true);
                      }}
                    >
                      {c.id}
                    </Chip>
                  ))}
                </div>
                {state.template.auto && state.template.auto !== draft.template ? (
                  <p className={cn(HINT, "mt-2")}>
                    Дерево «Выбор» предлагает{" "}
                    <button
                      type="button"
                      className="underline decoration-dotted hover:text-white"
                      onClick={() => patch({ template: state.template.auto })}
                    >
                      {state.template.auto}
                    </button>
                    .
                  </p>
                ) : null}
                {currentTemplate ? (
                  <div className="mt-2 rounded-md border border-white/10 bg-black/25 p-2">
                    <p className="text-[11px] font-semibold text-white/85">
                      {currentTemplate.id} · {currentTemplate.name}
                    </p>
                    <p className={cn(HINT, "mt-1")}>{currentTemplate.when}</p>
                    <button
                      type="button"
                      className="mt-1.5 text-[10px] uppercase tracking-wide text-white/40 hover:text-white/70"
                      onClick={() => setTemplateOpen((v) => !v)}
                    >
                      {templateOpen ? "скрыть лестницу" : "лестница кадров"}
                    </button>
                    {templateOpen ? (
                      <ol className="mt-1.5 space-y-1">
                        {currentTemplate.plans.map((plan, i) => (
                          <li
                            key={i}
                            className={cn(
                              "flex items-baseline gap-1.5 text-[11px]",
                              i < state.template.group_len
                                ? "text-white/75"
                                : "text-amber-200/80",
                            )}
                          >
                            <span className="text-white/35">K{i + 1}</span>
                            <span>{plan}</span>
                            <span className="text-white/35">
                              {currentTemplate.roles[i] || ""}
                            </span>
                            {i >= state.template.group_len ? (
                              <span className="ml-auto text-[10px]">нет кадра</span>
                            ) : null}
                          </li>
                        ))}
                      </ol>
                    ) : null}
                  </div>
                ) : null}
                <VariantList
                  kind="template"
                  variants={variants.template || []}
                  busy={variantKind === "template"}
                  onTake={(v) => patch({ template: v["шаблон"] || draft.template })}
                />
              </Section>

              <Section label="Крупность">
                <div className="flex flex-wrap gap-1.5">
                  {state.plan.choices.map((p) => (
                    <Chip
                      key={p}
                      active={draft.plan === p}
                      disabled={disabled}
                      onClick={() => patch({ plan: p })}
                    >
                      {p}
                    </Chip>
                  ))}
                </div>
              </Section>

              <Section
                label="Действие кадра"
                aside={
                  <button
                    type="button"
                    disabled={disabled}
                    onClick={() => void askVariants("action")}
                    className="inline-flex items-center gap-1 text-[11px] text-white/55 transition hover:text-white disabled:opacity-40"
                  >
                    <Sparkles className="h-3 w-3" /> подобрать
                  </button>
                }
              >
                <textarea
                  className={cn(FIELD, "min-h-[6rem] resize-y leading-snug")}
                  value={draft.action}
                  disabled={disabled}
                  placeholder="Помещение, кто в кадре, одежда, видимый поступок…"
                  onChange={(e) => patch({ action: e.target.value })}
                />
                <p className={cn(HINT, "mt-1.5")}>
                  Шаг развития, а не «взял / посмотрел / переложил»: покажите, что
                  поменялось после кадра.
                </p>
                <VariantList
                  kind="action"
                  variants={variants.action || []}
                  busy={variantKind === "action"}
                  onTake={(v) =>
                    patch({
                      action: v["действие"] || draft.action,
                      plan: v["план"] || draft.plan,
                    })
                  }
                />
              </Section>

              <Section
                label="Якоря закадра"
                aside={
                  <button
                    type="button"
                    disabled={disabled}
                    onClick={() => void askVariants("anchors")}
                    className="inline-flex items-center gap-1 text-[11px] text-white/55 transition hover:text-white disabled:opacity-40"
                  >
                    <Sparkles className="h-3 w-3" /> подобрать
                  </button>
                }
              >
                <AnchoredText text={state.anchors.text} anchors={draft.anchors} />
                <ul className="mt-2 space-y-1.5">
                  {draft.anchors.map((row, i) => {
                    const found =
                      !!(row["якорь"] || "").trim() &&
                      state.anchors.text
                        .toLowerCase()
                        .includes((row["якорь"] || "").trim().toLowerCase());
                    return (
                      <li key={i} className="flex items-start gap-1.5">
                        <button
                          type="button"
                          title="Главный бит ячейки"
                          disabled={disabled}
                          onClick={() =>
                            patch({
                              anchors: draft.anchors.map((r, j) => ({
                                ...r,
                                "главный": j === i,
                              })),
                            })
                          }
                          className={cn(
                            "mt-1.5 h-3 w-3 shrink-0 rounded-full border transition",
                            row["главный"]
                              ? "border-transparent bg-[rgba(209,254,23,1)]"
                              : "border-white/25 hover:border-white/60",
                          )}
                        />
                        <div className="min-w-0 flex-1">
                          <input
                            className={cn(
                              FIELD,
                              "py-1",
                              !found && "border-rose-400/50 text-rose-200",
                            )}
                            value={row["якорь"]}
                            disabled={disabled}
                            placeholder="дословный кусок текста ячейки"
                            onChange={(e) =>
                              patch({
                                anchors: draft.anchors.map((r, j) =>
                                  j === i ? { ...r, "якорь": e.target.value } : r,
                                ),
                              })
                            }
                          />
                          <input
                            className={cn(FIELD, "mt-1 py-1 text-[11px]")}
                            value={row["изменение"] || ""}
                            disabled={disabled}
                            placeholder="было → стало"
                            onChange={(e) =>
                              patch({
                                anchors: draft.anchors.map((r, j) =>
                                  j === i ? { ...r, "изменение": e.target.value } : r,
                                ),
                              })
                            }
                          />
                        </div>
                        <button
                          type="button"
                          disabled={disabled}
                          title="Убрать якорь"
                          onClick={() =>
                            patch({ anchors: draft.anchors.filter((_, j) => j !== i) })
                          }
                          className="mt-1 rounded-md p-1 text-white/35 transition hover:bg-white/10 hover:text-rose-200"
                        >
                          <Trash2 className="h-3 w-3" />
                        </button>
                      </li>
                    );
                  })}
                </ul>
                <button
                  type="button"
                  disabled={disabled}
                  onClick={() =>
                    patch({
                      anchors: [
                        ...draft.anchors,
                        { "якорь": "", "изменение": "", "главный": false },
                      ],
                    })
                  }
                  className="mt-2 inline-flex items-center gap-1 rounded-md border border-dashed border-white/20 px-2 py-1 text-[11px] text-white/55 transition hover:border-white/40 hover:text-white disabled:opacity-40"
                >
                  <Plus className="h-3 w-3" /> Якорь
                </button>
                <p className={cn(HINT, "mt-2")}>
                  Сколько якорей — столько визуальных кадров у ячейки. Красный якорь не
                  найден в тексте и будет отброшен.
                </p>
                <VariantList
                  kind="anchors"
                  variants={variants.anchors || []}
                  busy={variantKind === "anchors"}
                  onTake={(v) => patch({ anchors: v["биты"] || draft.anchors })}
                />
              </Section>

              {state.scene_action.chain.length ? (
                <Section label="Цепь сцен ячейки">
                  <ol className="space-y-1">
                    {state.scene_action.chain.map((row) => (
                      <li key={row.n} className="text-[11px] leading-snug text-white/70">
                        <span className="text-white/35">{row.n}.</span>{" "}
                        {row.place ? <span className="text-white/85">{row.place}</span> : null}
                        {row.place && row.action ? " — " : ""}
                        {row.action}
                      </li>
                    ))}
                  </ol>
                </Section>
              ) : null}
            </>
          )}
        </div>

        <footer className="flex items-center justify-between gap-2 border-t border-white/10 px-4 py-3">
          <p className={HINT}>
            {ops.length
              ? `${ops.length} правк${ops.length === 1 ? "а" : "и"} · применятся кнопкой «Применить правки»`
              : "Правок нет"}
          </p>
          <div className="flex items-center gap-2">
            <Button type="button" variant="outline" size="sm" onClick={onClose}>
              Закрыть
            </Button>
            <Button
              type="button"
              size="sm"
              disabled={disabled || !ops.length}
              onClick={() => {
                onQueue(ops);
                onClose();
              }}
            >
              В очередь
            </Button>
          </div>
        </footer>
      </aside>
    </div>,
    document.body,
  );
}

/** Компактная сводка сцены в клетке доски (одна строка вместо трёх). */
export function SceneSummaryCell({
  kindLabel,
  template,
  plan,
  action,
  anchors,
  pending,
  disabled,
  onOpen,
}: {
  kindLabel: string;
  template: string;
  plan: string;
  action: string;
  anchors: number;
  pending: boolean;
  disabled?: boolean;
  onOpen: () => void;
}) {
  const badge = "rounded border border-white/12 px-1.5 py-0.5 text-[10px] leading-none";
  return (
    <button
      type="button"
      disabled={disabled}
      onClick={onOpen}
      title="Открыть редактор сцены"
      className={cn(
        "w-full rounded-md border p-2 text-left transition disabled:opacity-40",
        pending
          ? "border-amber-400/60 bg-amber-500/10"
          : "border-white/10 bg-black/20 hover:border-white/25 hover:bg-black/30",
      )}
    >
      <div className="flex flex-wrap items-center gap-1">
        <span className={cn(badge, "text-white/70")}>{kindLabel}</span>
        {template ? (
          <span className={cn(badge, "text-[rgba(209,254,23,0.9)]")}>{template}</span>
        ) : null}
        {plan ? <span className={cn(badge, "text-white/70")}>{plan}</span> : null}
        {anchors ? (
          <span className={cn(badge, "text-white/45")}>{anchors} якор.</span>
        ) : null}
      </div>
      <p className="mt-1.5 line-clamp-3 text-[11px] leading-snug text-white/75">
        {action || <span className="text-white/35">действие не задано</span>}
      </p>
    </button>
  );
}
