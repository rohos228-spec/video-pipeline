"use client";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { cn } from "@/lib/utils";
import { downloadJournalExport, fetchJournalPage } from "@/lib/journal-api";
import {
  EMPTY_FILTERS,
  EXPORT_MAX_ROWS,
  KIND_LABELS_RU,
  PERIOD_LABELS_RU,
  PRESETS,
  activePresetId,
  applyPreset,
  formatTs,
  formatUsd,
  journalErrorText,
  kindLabel,
  mergeItems,
  missingSourcesText,
  severityClass,
  severityLabel,
  sourceLabel,
  summarize,
  toggleKind,
  type JournalFilters,
  type JournalItem,
  type JournalPeriod,
} from "@/lib/journal-view";

function statusOf(e: unknown): number | null {
  const s = (e as { status?: unknown } | null)?.status;
  return typeof s === "number" ? s : null;
}

function Row({ it }: { it: JournalItem }) {
  const sev = severityClass(it.severity);
  const meta: string[] = [];
  if (it.run_id) meta.push(`запуск ${it.run_id}`);
  if (it.request_id) meta.push(`запрос ${it.request_id}`);
  if (it.ref) meta.push(`ссылка ${it.ref}`);
  meta.push(`источник: ${sourceLabel(it.source)}`);
  return (
    <li data-testid="journal-row" data-severity={it.severity} className={cn("rounded border p-1.5 break-words [contain-intrinsic-size:auto_72px] [content-visibility:auto]", sev.row)}>
      <div className="flex flex-wrap items-center gap-x-2 gap-y-0.5">
        <span aria-hidden className={cn("inline-block h-2 w-2 shrink-0 rounded-full", sev.dot)} />
        <span className={cn("shrink-0", sev.text)}>{severityLabel(it.severity)}</span>
        <span className="text-white/45">{formatTs(it.ts)}</span>
        <span className="rounded bg-white/10 px-1 text-white/70">{kindLabel(it.kind, it.source)}</span>
        {it.amount_usd !== null && <span className="text-white/90">{formatUsd(it.amount_usd)}</span>}
      </div>
      <p className="mt-0.5 text-white/90">{it.title_ru || "Событие без описания"}</p>
      {it.detail_ru && <p className="text-white/55">{it.detail_ru}</p>}
      <p className="text-white/35">{meta.join(" · ")}</p>
    </li>
  );
}

export function JournalSection({ projectId }: { projectId: number | null }) {
  const project = projectId ?? 0;
  const [filters, setFilters] = useState<JournalFilters>(EMPTY_FILTERS);
  const [search, setSearch] = useState("");
  const [items, setItems] = useState<JournalItem[]>([]);
  const [cursor, setCursor] = useState<string | null>(null);
  const [missing, setMissing] = useState<string[]>([]);
  const [total, setTotal] = useState<number | null>(null);
  const [loading, setLoading] = useState(false);
  const [loadingMore, setLoadingMore] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [exportError, setExportError] = useState<string | null>(null);
  const [exporting, setExporting] = useState<"" | "csv" | "json">("");
  const [loaded, setLoaded] = useState(false);
  const [reloadTick, setReloadTick] = useState(0);
  const abortRef = useRef<AbortController | null>(null);
  const listRef = useRef<HTMLUListElement | null>(null);

  // Смена проекта: сбросить всё.
  useEffect(() => {
    setFilters(EMPTY_FILTERS); setSearch(""); setItems([]); setCursor(null);
    setMissing([]); setTotal(null); setError(null); setExportError(null); setLoaded(false);
  }, [project]);

  // Поиск применяется с задержкой, чтобы не слать запрос на каждую букву.
  useEffect(() => {
    const t = setTimeout(() => setFilters((f) => (f.q === search ? f : { ...f, q: search })), 400);
    return () => clearTimeout(t);
  }, [search]);

  // Первая страница при смене проекта, фильтров или по кнопке «Обновить».
  useEffect(() => {
    if (projectId == null) return;
    abortRef.current?.abort();
    const ctrl = new AbortController();
    abortRef.current = ctrl;
    setLoading(true); setError(null);
    fetchJournalPage(projectId, filters, null, ctrl.signal)
      .then((page) => {
        if (ctrl.signal.aborted) return;
        setItems(mergeItems([], page.items)); setCursor(page.next_cursor);
        if (listRef.current) listRef.current.scrollTop = 0;
        setMissing(page.sources_missing); setTotal(page.total_hint); setLoaded(true);
      })
      .catch((e) => {
        if (ctrl.signal.aborted) return;
        setItems([]); setCursor(null); setLoaded(true);
        setError(journalErrorText(statusOf(e)));
      })
      .finally(() => { if (!ctrl.signal.aborted) setLoading(false); });
    return () => ctrl.abort();
  }, [projectId, filters, reloadTick]);

  const loadMore = useCallback(async () => {
    if (projectId == null || !cursor || loadingMore) return;
    const ctrl = abortRef.current;
    setLoadingMore(true); setError(null);
    try {
      const page = await fetchJournalPage(projectId, filters, cursor);
      if (ctrl?.signal.aborted) return;
      setItems((old) => mergeItems(old, page.items)); setCursor(page.next_cursor);
      setMissing(page.sources_missing);
    } catch (e) {
      setError(journalErrorText(statusOf(e)));
    } finally {
      setLoadingMore(false);
    }
  }, [projectId, filters, cursor, loadingMore]);

  const doExport = useCallback(async (format: "csv" | "json") => {
    if (projectId == null || exporting) return;
    setExporting(format); setExportError(null);
    try {
      await downloadJournalExport(projectId, filters, format);
    } catch (e) {
      setExportError(`Не удалось скачать файл. ${journalErrorText(statusOf(e))}`);
    } finally {
      setExporting("");
    }
  }, [projectId, filters, exporting]);

  const totals = useMemo(() => summarize(items), [items]);
  const preset = activePresetId(filters);
  const hasFilters = filters.kinds.length > 0 || filters.severity !== "" || filters.period !== "all" || filters.q !== "";
  const chip = (on: boolean) => cn("rounded border px-1.5 py-0.5", on ? "border-sky-300/60 bg-sky-300/15 text-white" : "border-white/15 text-white/65 hover:bg-white/5");

  return (
    <section data-testid="journal-panel" className="mt-3 rounded border border-white/10 p-2 space-y-2 text-xs text-white/80">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h4>Журнал проекта</h4>
        <div className="flex flex-wrap gap-1.5">
          <button type="button" className={chip(false)} disabled={loading || projectId == null} onClick={() => setReloadTick((n) => n + 1)}>Обновить</button>
          <button type="button" className={chip(false)} disabled={!!exporting || projectId == null} onClick={() => void doExport("csv")}
            title={`Таблица для Excel (до ${EXPORT_MAX_ROWS} строк, с учётом фильтров)`}>
            {exporting === "csv" ? "Готовлю CSV…" : "Скачать CSV"}
          </button>
          <button type="button" className={chip(false)} disabled={!!exporting || projectId == null} onClick={() => void doExport("json")}
            title={`Файл для разработчика (до ${EXPORT_MAX_ROWS} строк, с учётом фильтров)`}>
            {exporting === "json" ? "Готовлю JSON…" : "Скачать для разработчика"}
          </button>
        </div>
      </div>

      {projectId == null && <p className="text-white/45">Выберите проект, чтобы увидеть его журнал.</p>}

      {projectId != null && (
        <>
          <div className="flex flex-wrap gap-1.5" role="group" aria-label="Быстрые фильтры">
            {PRESETS.map((p) => (
              <button key={p.id} type="button" aria-pressed={preset === p.id} className={chip(preset === p.id)}
                onClick={() => setFilters((f) => applyPreset(f, p.id))}>{p.label}</button>
            ))}
          </div>

          <div className="flex flex-wrap gap-1.5" role="group" aria-label="Фильтр по виду записи">
            {Object.entries(KIND_LABELS_RU).map(([k, label]) => (
              <button key={k} type="button" aria-pressed={filters.kinds.includes(k)} className={chip(filters.kinds.includes(k))}
                onClick={() => setFilters((f) => toggleKind(f, k))}>{label}</button>
            ))}
          </div>

          <div className="flex flex-wrap items-center gap-2">
            <label className="flex items-center gap-1 text-white/60">Важность
              <select aria-label="Важность" value={filters.severity}
                onChange={(e) => setFilters((f) => ({ ...f, severity: e.target.value as JournalFilters["severity"] }))}
                className="rounded border border-white/15 bg-[#161616] p-1">
                <option value="">любая</option>
                <option value="error">ошибки</option>
                <option value="warn">внимание</option>
                <option value="info">обычные</option>
              </select>
            </label>
            <label className="flex items-center gap-1 text-white/60">Период
              <select aria-label="Период" value={filters.period}
                onChange={(e) => setFilters((f) => ({ ...f, period: e.target.value as JournalPeriod }))}
                className="rounded border border-white/15 bg-[#161616] p-1">
                {Object.entries(PERIOD_LABELS_RU).map(([v, l]) => <option key={v} value={v}>{l}</option>)}
              </select>
            </label>
            <input type="search" aria-label="Поиск по журналу" placeholder="Поиск по тексту" value={search}
              onChange={(e) => setSearch(e.target.value)}
              className="min-w-0 flex-1 basis-40 rounded border border-white/15 bg-[#161616] p-1 text-white/90 placeholder:text-white/30" />
            {hasFilters && (
              <button type="button" className={chip(false)} onClick={() => { setSearch(""); setFilters(EMPTY_FILTERS); }}>Сбросить фильтры</button>
            )}
          </div>

          {loaded && !error && (
            <p data-testid="journal-totals" className="text-white/60">
              Загружено записей: {totals.count}{total !== null ? ` из ~${total}` : ""}
              {" · "}ошибок: <span className={totals.errors ? "text-red-300" : ""}>{totals.errors}</span>
              {" · "}предупреждений: <span className={totals.warnings ? "text-amber-200" : ""}>{totals.warnings}</span>
              {" · "}потрачено в загруженных строках: {formatUsd(totals.spentUsd)}
              {cursor ? " (загружено не всё: нажмите «Показать ещё»)" : ""}
            </p>
          )}

          {missingSourcesText(missing) && <p className="text-white/45">{missingSourcesText(missing)}</p>}
          {exportError && <p role="alert" className="text-amber-200">{exportError}</p>}
          {error && (
            <p role="alert" className="text-red-300">
              {error}{" "}
              <button type="button" className="underline" onClick={() => (items.length && cursor ? void loadMore() : setReloadTick((n) => n + 1))}>Повторить</button>
            </p>
          )}
          {loading && <p className="text-white/45">Загружаю журнал…</p>}

          {loaded && !loading && !error && items.length === 0 && (
            <p data-testid="journal-empty" className="text-white/45">
              {hasFilters ? "По этим фильтрам записей нет. Попробуйте сбросить фильтры или выбрать период «За всё время»." : "В журнале этого проекта пока нет записей."}
            </p>
          )}

          {items.length > 0 && (
            // Длинный журнал листается внутри своего окна, а не растягивает всю панель на тысячи пикселей.
            <ul ref={listRef} data-testid="journal-list" tabIndex={0} className="max-h-[60vh] space-y-1 overflow-y-auto overscroll-contain pr-1" aria-label="Записи журнала">
              {items.map((it) => <Row key={it.id} it={it} />)}
            </ul>
          )}

          {cursor && (
            <button type="button" className={chip(false)} disabled={loadingMore || loading} onClick={() => void loadMore()}>
              {loadingMore ? "Загружаю…" : "Показать ещё"}
            </button>
          )}
          {loaded && !cursor && items.length > 0 && !error && <p className="text-white/35">Это все записи по выбранным фильтрам.</p>}
        </>
      )}
    </section>
  );
}
