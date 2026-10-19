"use client";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { cn } from "@/lib/utils";
import { downloadJournalExport, fetchJournalPage, fetchJournalTotals } from "@/lib/journal-api";
import {
  EMPTY_FILTERS,
  EXPORT_MAX_ROWS,
  KIND_LABELS_RU,
  PERIOD_LABELS_RU,
  PRESETS,
  activePresetId,
  applyPreset,
  countText,
  diskText,
  spendVsCap,
  type ServerTotals,
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

export const JOURNAL_REFRESH_MS = 10_000;

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
  const [server, setServer] = useState<ServerTotals | null>(null);
  const [serverError, setServerError] = useState(false);
  const [autoRefresh, setAutoRefresh] = useState(true);
  const [refreshedAt, setRefreshedAt] = useState<Date | null>(null);
  const abortRef = useRef<AbortController | null>(null);
  const listRef = useRef<HTMLUListElement | null>(null);

  useEffect(() => {
    setFilters(EMPTY_FILTERS); setSearch(""); setItems([]); setCursor(null);
    setMissing([]); setTotal(null); setError(null); setExportError(null); setLoaded(false);
  }, [project]);

  useEffect(() => {
    const t = setTimeout(() => setFilters((f) => (f.q === search ? f : { ...f, q: search })), 400);
    return () => clearTimeout(t);
  }, [search]);

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

  useEffect(() => {
    if (projectId == null) { setServer(null); return; }
    const ctrl = new AbortController();
    setServerError(false);
    fetchJournalTotals(projectId, ctrl.signal)
      .then((t) => { if (!ctrl.signal.aborted) setServer(t); })
      .catch(() => { if (!ctrl.signal.aborted) { setServer(null); setServerError(true); } });
    return () => ctrl.abort();
  }, [projectId, reloadTick]);

  useEffect(() => {
    if (projectId == null || !autoRefresh || !loaded || loading || loadingMore || error) return;
    let stopped = false;
    let ctrl: AbortController | null = null;
    const timer = setInterval(() => {
      if (stopped || (typeof document !== "undefined" && document.visibilityState === "hidden")) return;
      ctrl?.abort();
      const mine = new AbortController();
      ctrl = mine;
      fetchJournalPage(projectId, filters, null, mine.signal)
        .then((page) => {
          if (mine.signal.aborted || stopped) return;
          setItems((old) => mergeItems(old, page.items));
          setMissing(page.sources_missing); setTotal(page.total_hint); setRefreshedAt(new Date());
        })
        .catch(() => { /* тихо: прежние строки остаются, кнопка «Обновить» работает как раньше */ });
      fetchJournalTotals(projectId, mine.signal)
        .then((t) => { if (!mine.signal.aborted && !stopped) { setServer(t); setServerError(false); } })
        .catch(() => { /* итоги останутся прежними */ });
    }, JOURNAL_REFRESH_MS);
    return () => { stopped = true; clearInterval(timer); ctrl?.abort(); };
  }, [projectId, filters, autoRefresh, loaded, loading, loadingMore, error]);

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
          <button type="button" aria-pressed={autoRefresh} className={chip(autoRefresh)} disabled={projectId == null}
            title={`Журнал сам обновляется раз в ${JOURNAL_REFRESH_MS / 1000} с, пока панель открыта`}
            onClick={() => setAutoRefresh((v) => !v)}>
            {autoRefresh ? "Автообновление: вкл" : "Автообновление: выкл"}
          </button>
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

          {server && (
            <dl data-testid="journal-server-totals" className="grid grid-cols-2 gap-x-3 gap-y-0.5 rounded border border-white/10 p-1.5 sm:grid-cols-3">
              <div><dt className="text-white/45">Потрачено сегодня</dt><dd>{spendVsCap(server.spend.daily_spent, server.spend.caps?.daily)}</dd></div>
              <div><dt className="text-white/45">Потрачено на проект</dt><dd>{spendVsCap(server.spend.project_spent, server.spend.caps?.project)}</dd></div>
              <div><dt className="text-white/45">Потрачено за запуск{server.spend.run_id ? ` ${server.spend.run_id}` : ""}</dt>
                <dd>{server.spend.run_id ? spendVsCap(server.spend.run_spent, server.spend.caps?.run) : "запусков нет"}</dd></div>
              <div><dt className="text-white/45">Ошибок за всё время</dt><dd className={server.counts.errors.count ? "text-red-300" : ""}>{countText(server.counts.errors)}</dd></div>
              <div><dt className="text-white/45">Пробелов в записи журнала</dt><dd className={server.counts.gaps.count ? "text-amber-200" : ""}>{countText(server.counts.gaps)}</dd></div>
              <div><dt className="text-white/45">Диск</dt><dd className={server.disk.level === "ok" ? "" : "text-amber-200"} title={server.disk.reason}>{diskText(server.disk)}</dd></div>
            </dl>
          )}
          {serverError && <p role="alert" className="text-amber-200">Итоги по проекту не загрузились. Нажмите «Обновить».</p>}

          {loaded && !error && (
            <p data-testid="journal-totals" className="text-white/60">
              Загружено записей: {totals.count}{total !== null ? ` из ~${total}` : ""}
              {" · "}ошибок: <span className={totals.errors ? "text-red-300" : ""}>{totals.errors}</span>
              {" · "}предупреждений: <span className={totals.warnings ? "text-amber-200" : ""}>{totals.warnings}</span>
              {" · "}потрачено в загруженных строках: {formatUsd(totals.spentUsd)}
              {cursor ? " (загружено не всё: нажмите «Показать ещё»)" : ""}
            </p>
          )}

          {autoRefresh && refreshedAt && (
            <p data-testid="journal-refreshed" className="text-white/35">Обновлено автоматически в {refreshedAt.toLocaleTimeString("ru-RU")}.</p>
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
