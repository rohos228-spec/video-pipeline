"use client";
import { useCallback, useEffect, useMemo, useState } from "react";
import { Button } from "@/components/ui/button";
import { usePersistedState } from "@/hooks/use-persisted-state";
import { ProposalCard } from "./proposal-card";
import { ProposeForm } from "./propose-form";
import {
  describeChangeError, getAllowList, listProposals, type AllowList, type Proposal,
} from "@/lib/change-control";
import { cn } from "@/lib/utils";

type Filter = "open" | "applied" | "closed" | "all";

const FILTERS: { id: Filter; label: string; match: (p: Proposal) => boolean }[] = [
  { id: "open", label: "Открытые", match: (p) => p.status === "proposed" || p.status === "approved" || p.status === "developer_review" },
  { id: "applied", label: "Применённые", match: (p) => p.status === "applied" },
  { id: "closed", label: "Закрытые", match: (p) => p.status === "rolled_back" || p.status === "failed" || p.status === "rejected" },
  { id: "all", label: "Все", match: () => true },
];

/**
 * Контролируемые изменения: предложения по проекту, просмотр diff, применение и откат через слой действий.
 * Применение и откат выполняются только по клику оператора в окне подтверждения; планировщик и модель только предлагают.
 */
export function ChangeControlSection({ projectId }: { projectId: number | null }) {
  const [open, setOpen] = usePersistedState("vp-change-control-open", false);
  const [filter, setFilter] = useState<Filter>("open");
  const [items, setItems] = useState<Proposal[] | null>(null);
  const [error, setError] = useState("");
  const [allow, setAllow] = useState<AllowList | null>(null);
  const [allowError, setAllowError] = useState("");
  const [formOpen, setFormOpen] = useState(false);
  const [notice, setNotice] = useState("");
  // Карточки с открытым потоком применения/отката не пропадают из списка, пока оператор не закроет результат.
  const [pinned, setPinned] = useState<Record<string, boolean>>({});

  const refresh = useCallback(async () => {
    if (projectId == null) return;
    try {
      const r = await listProposals(projectId);
      setItems(r.items);
      setError("");
    } catch (e) {
      setError(describeChangeError(e));
    }
  }, [projectId]);

  useEffect(() => {
    setItems(null); setFormOpen(false); setNotice("");
    if (open) void refresh();
  }, [projectId, open, refresh]);

  useEffect(() => {
    if (!open || allow) return;
    let cancelled = false;
    void (async () => {
      try {
        const a = await getAllowList();
        if (!cancelled) { setAllow(a); setAllowError(""); }
      } catch (e) {
        if (!cancelled) setAllowError(describeChangeError(e));
      }
    })();
    return () => { cancelled = true; };
  }, [open, allow]);

  const counts = useMemo(() => {
    const c: Record<Filter, number> = { open: 0, applied: 0, closed: 0, all: 0 };
    for (const p of items ?? []) for (const f of FILTERS) if (f.match(p)) c[f.id] += 1;
    return c;
  }, [items]);
  const shown = useMemo(() => {
    const match = FILTERS.find((f) => f.id === filter)!.match;
    return (items ?? []).filter((p) => match(p) || pinned[p.id]);
  }, [items, filter, pinned]);

  return (
    <section data-testid="change-control-panel" aria-label="Контролируемые изменения" className="mt-3 space-y-2 rounded border border-white/10 p-2 text-xs text-white/80">
      <h4>
        <button type="button" aria-expanded={open} data-testid="change-control-toggle" onClick={() => setOpen(!open)} className="flex w-full items-center gap-2 text-left">
          <span aria-hidden className="w-3 text-white/50">{open ? "−" : "+"}</span>
          <span>Контролируемые изменения{items && open ? ` · открытых: ${counts.open}` : ""}</span>
        </button>
      </h4>

      {open && (
        <>
          <p className="text-white/60">
            Параметры и тексты промптов меняются только через предложение. Применить или откатить может только оператор: после предпросмотра
            и вашего подтверждения. Планировщик и модель могут лишь предлагать. Изменения кода и общих файлов никогда не применяются автоматически.
          </p>

          {projectId == null ? (
            <p className="text-white/50">Откройте проект, чтобы увидеть предложения.</p>
          ) : (
            <>
              <div className="flex flex-wrap items-center gap-1.5">
                {FILTERS.map((f) => (
                  <button key={f.id} type="button" aria-pressed={filter === f.id} onClick={() => { setFilter(f.id); setNotice(""); }}
                    className={cn("rounded border px-2 py-0.5", filter === f.id ? "border-white/50 text-white" : "border-white/15 text-white/60")}>
                    {f.label}{items ? ` · ${counts[f.id]}` : ""}
                  </button>
                ))}
                <span className="flex-1" />
                <Button size="sm" variant="ghost" className="h-7 text-xs" onClick={() => void refresh()}>Обновить</Button>
                <Button size="sm" variant="outline" className="h-7 text-xs" aria-expanded={formOpen} onClick={() => { setFormOpen((v) => !v); setNotice(""); }}>
                  {formOpen ? "Скрыть форму" : "Новое предложение…"}
                </Button>
              </div>

              {formOpen && (
                <ProposeForm projectId={projectId} allow={allow} allowError={allowError} onCancel={() => setFormOpen(false)}
                  onCreated={(p) => {
                    setNotice(p.deduplicated
                      ? `Такое предложение уже открыто (${p.id.slice(0, 8)}): новое не создавалось.`
                      : `Предложение ${p.id.slice(0, 8)} записано. Оно ещё не применено.`);
                    setFormOpen(false);
                    setFilter("open");
                    void refresh();
                  }} />
              )}
              {notice && <p role="status" data-testid="change-notice" className="text-emerald-200">{notice}</p>}

              {error && <p role="alert" data-testid="change-list-error" className="text-red-300">Список предложений недоступен: {error}</p>}
              {!error && items === null && <p className="text-white/50">Загружаю предложения…</p>}
              {items && shown.length === 0 && (
                <p className="text-white/55" data-testid="change-empty">
                  {filter === "open" ? "Открытых предложений нет." : "Предложений в этой группе нет."}
                </p>
              )}
              <div className="space-y-2" data-testid="change-list">
                {shown.map((p) => <ProposalCard key={p.id} proposal={p} onChanged={() => void refresh()}
                  onPin={(on) => setPinned((m) => ({ ...m, [p.id]: on }))} />)}
              </div>
            </>
          )}
        </>
      )}
    </section>
  );
}
