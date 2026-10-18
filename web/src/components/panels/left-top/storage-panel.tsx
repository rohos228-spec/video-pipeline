"use client";

import { useEffect, useMemo, useState } from "react";
import { HardDrive, Search, X, RefreshCw, Film, Image as ImageIcon, Music, FileText, Trash2, Archive, ExternalLink, Check, Download } from "lucide-react";

const ACC = "#2dd4bf";

type Project = { id: number; title: string; status: string; updated_at: string };

const TYPES = [
  { key: "video", label: "Видео", color: "#2dd4bf", icon: Film, share: 0 },
  { key: "image", label: "Картинки", color: "#22a3a0", icon: ImageIcon, share: 0 },
  { key: "audio", label: "Аудио", color: "#5eead4", icon: Music, share: 0 },
  { key: "prompts", label: "Промты и тексты", color: "#99f6e4", icon: FileText, share: 0 },
  { key: "other", label: "Прочее", color: "#52525b", icon: FileText, share: 0 },
];

type Usage = {
  total_bytes: number;
  files: number;
  by_type: Record<string, number>;
  projects: { id: number; bytes: number; files: number }[];
  disk: { free: number; total: number; used?: number; drive: string };
  limit_bytes: number;
};
const fmt = (b: number) => (b >= 1e9 ? (b / 1e9).toFixed(1) + " ГБ" : b >= 1e6 ? (b / 1e6).toFixed(0) + " МБ" : (b / 1e3).toFixed(0) + " КБ");

const FILTERS = [
  { k: "all", l: "Все" },
  { k: "active", l: "Активные" },
  { k: "done", l: "Готовые" },
  { k: "paused", l: "На паузе" },
] as const;
type FilterK = (typeof FILTERS)[number]["k"];

function statusKind(s: string): Exclude<FilterK, "all"> {
  if (/paus/i.test(s)) return "paused";
  if (/done|publish|final|complete/i.test(s)) return "done";
  return "active";
}
const KIND_LABEL = { active: "в работе", done: "готов", paused: "пауза" } as const;

export function StoragePanel({ onClose }: { onClose: () => void }) {
  const [items, setItems] = useState<Project[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [q, setQ] = useState("");
  const [filter, setFilter] = useState<FilterK>("all");
  const [sort, setSort] = useState<"new" | "big" | "az">("new");
  const [shown, setShown] = useState(false);
  const [usage, setUsage] = useState<Usage | null>(null);
  const [sel, setSel] = useState<number | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [exp, setExp] = useState<{ path: string; url: string; bytes: number } | null>(null);
  const [cacheAsk, setCacheAsk] = useState<{ total_bytes: number; dirs: { path: string; bytes: number }[] } | null>(null);
  const [note, setNote] = useState<string | null>(null);
  const loadUsage = (refresh = false) =>
    fetch("/api/storage/usage" + (refresh ? "?refresh=1" : ""))
      .then((r) => (r.ok ? r.json() : Promise.reject(new Error("HTTP " + r.status))))
      .then(setUsage)
      .catch((e) => setNote("Размеры: " + String(e?.message ?? e)));
  const sizeOf = (id: number) => usage?.projects.find((x) => x.id === id)?.bytes ?? 0;
  const doExport = async () => {
    if (sel == null) return;
    setBusy("export"); setExp(null); setNote(null);
    try {
      const r = await fetch(`/api/storage/export/${sel}`, { method: "POST" });
      if (!r.ok) throw new Error((await r.text()) || "HTTP " + r.status);
      setExp(await r.json());
    } catch (e) { setNote("Экспорт: " + String((e as Error).message)); } finally { setBusy(null); }
  };
  const askCache = async () => {
    setBusy("cache");
    try { setCacheAsk(await (await fetch("/api/storage/cache")).json()); } catch { setNote("Не удалось оценить кэш"); } finally { setBusy(null); }
  };
  const clearCache = async () => {
    setBusy("cache");
    try {
      const d = await (await fetch("/api/storage/cache/clear", { method: "POST" })).json();
      setNote(`Кэш очищен: освобождено ${fmt(d.freed_bytes ?? 0)}`); setCacheAsk(null); loadUsage(true);
    } catch { setNote("Очистка не удалась"); } finally { setBusy(null); }
  };

  const load = () => {
    setError(null);
    setItems(null);
    fetch("/api/projects")
      .then((r) => (r.ok ? r.json() : Promise.reject(new Error("HTTP " + r.status))))
      .then((d: Project[]) => setItems(Array.isArray(d) ? d : []))
      .catch((e) => setError(String(e?.message ?? e)));
  };
  useEffect(() => {
    load();
    loadUsage();
    const t = requestAnimationFrame(() => setShown(true));
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", onKey);
    return () => {
      cancelAnimationFrame(t);
      window.removeEventListener("keydown", onKey);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const total = usage?.total_bytes ?? 0;
  // limit_bytes в API — свободное место, не потолок. Полоса считается от объёма диска.
  const limit = usage?.disk.total || (usage ? usage.total_bytes + usage.limit_bytes : 0);
  const list = useMemo(() => {
    let l = (items ?? []).filter((p) => (filter === "all" || statusKind(p.status) === filter) && (p.title ?? "").toLowerCase().includes(q.toLowerCase()));
    l = [...l].sort((a, b) =>
      sort === "big" ? sizeOf(b.id) - sizeOf(a.id) : sort === "az" ? (a.title ?? "").localeCompare(b.title ?? "", "ru") : (b.updated_at ?? "").localeCompare(a.updated_at ?? ""),
    );
    return l;
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [items, filter, q, sort, usage]);
  const pct = limit ? Math.min(100, (total / limit) * 100) : 0;

  const chip = (on: boolean) =>
    "h-8 rounded-[10px] px-3 text-sm transition-colors " + (on ? "bg-[rgba(45,212,191,0.16)] text-[#5eead4]" : "bg-white/[0.04] text-zinc-400 hover:bg-white/[0.08] hover:text-white");

  return (
    <div
      data-rail-panel="Хранилище"
      className={"fixed left-[72px] top-3 bottom-3 z-50 flex w-[420px] flex-col rounded-[16px] border border-white/[0.08] bg-[#141417] shadow-2xl transition-all duration-200 ease-out " + (shown ? "translate-x-0 opacity-100" : "-translate-x-2 opacity-0")}
      style={{ fontFamily: "Rubik, sans-serif" }}
    >
      <div className="flex items-center justify-between border-b border-white/[0.06] px-4 py-3">
        <div className="flex items-center gap-2">
          <HardDrive className="h-5 w-5" style={{ color: ACC }} />
          <span className="text-[17px] font-medium text-white">Хранилище</span>
          <button type="button" onClick={() => { setUsage(null); loadUsage(true); }} title="Пересчитать" className="flex h-7 w-7 items-center justify-center rounded-[8px] text-zinc-500 hover:bg-white/[0.06] hover:text-white"><RefreshCw className="h-3.5 w-3.5" /></button>
        </div>
        <button type="button" onClick={onClose} aria-label="Закрыть" className="flex h-8 w-8 items-center justify-center rounded-[10px] text-zinc-500 transition-colors hover:bg-white/[0.06] hover:text-white">
          <X className="h-4 w-4" />
        </button>
      </div>

      <div className="min-h-0 flex-1 space-y-4 overflow-y-auto px-4 py-4">
        <section className="rounded-[12px] bg-white/[0.03] p-3">
          <div className="mb-2 flex items-baseline justify-between">
            <span className="text-[14px] text-zinc-300">Проекты занимают</span>
            <span className="text-[15px] text-white">
              {usage ? fmt(total) : "…"} <span className="text-zinc-500">из доступных {usage ? fmt(limit) : "…"}</span>
            </span>
          </div>
          <div className="flex h-2.5 overflow-hidden rounded-full bg-white/[0.06]">
            {TYPES.map((t) => (
              <div key={t.key} className="h-full transition-all duration-500" style={{ width: `${total ? (pct * (usage?.by_type[t.key] ?? 0)) / total : 0}%`, background: t.color }} />
            ))}
          </div>
          <div className="mt-3 grid grid-cols-3 gap-2">
            {[
              ["Проектов", items ? String(items.length) : "—"],
              ["Файлов", usage ? usage.files.toLocaleString("ru-RU") : "…"],
              [`Свободно ${usage?.disk.drive ?? ""}`, usage ? fmt(usage.disk.free) : "…"],
            ].map(([l, v]) => (
              <div key={l} className="rounded-[10px] bg-white/[0.03] px-2.5 py-2">
                <div className="text-sm text-zinc-500">{l}</div>
                <div className="text-[16px] text-white">{v}</div>
              </div>
            ))}
          </div>
        </section>

        <section className="space-y-1.5">
          <div className="text-sm uppercase tracking-[0.1em] text-zinc-500">По типам</div>
          {TYPES.map((t) => {
            const I = t.icon;
            return (
              <div key={t.key} className="flex items-center gap-2.5 rounded-[10px] px-2 py-1.5 transition-colors hover:bg-white/[0.03]">
                <I className="h-4 w-4" style={{ color: t.color }} />
                <span className="flex-1 text-[14px] text-zinc-300">{t.label}</span>
                <span className="text-[14px] text-white">{usage ? fmt(usage.by_type[t.key] ?? 0) : "…"}</span>
              </div>
            );
          })}
        </section>

        <section className="space-y-2">
          <div className="text-sm uppercase tracking-[0.1em] text-zinc-500">Проекты</div>
          <div className="flex items-center gap-2 rounded-[10px] bg-white/[0.04] px-3">
            <Search className="h-4 w-4 text-zinc-500" />
            <input value={q} onChange={(e) => setQ(e.target.value)} placeholder="Поиск проекта" className="h-9 flex-1 bg-transparent text-[14px] text-white outline-none placeholder:text-zinc-600" />
          </div>
          <div className="flex flex-wrap gap-1.5">
            {FILTERS.map((f) => (
              <button key={f.k} type="button" className={chip(filter === f.k)} onClick={() => setFilter(f.k)}>
                {f.l}
              </button>
            ))}
            <select value={sort} onChange={(e) => setSort(e.target.value as typeof sort)} className="ml-auto h-8 rounded-[10px] bg-white/[0.04] px-2 text-sm text-zinc-300 outline-none">
              <option value="new">Новые</option>
              <option value="big">Крупные</option>
              <option value="az">А–Я</option>
            </select>
          </div>

          {error ? (
            <div className="rounded-[12px] bg-rose-500/[0.06] px-3 py-4 text-center text-[14px] text-rose-200">
              Не удалось загрузить: {error}
              <button type="button" onClick={load} className="mx-auto mt-2 flex items-center gap-1.5 rounded-[10px] bg-white/[0.06] px-3 py-1.5 text-sm text-white hover:bg-white/[0.1]">
                <RefreshCw className="h-3.5 w-3.5" /> Повторить
              </button>
            </div>
          ) : items == null ? (
            <div className="space-y-1.5">
              {[0, 1, 2, 3].map((i) => (
                <div key={i} className="h-[52px] animate-pulse rounded-[12px] bg-white/[0.04]" />
              ))}
            </div>
          ) : items.length === 0 ? (
            <div className="rounded-[12px] bg-white/[0.03] px-3 py-6 text-center text-[14px] text-zinc-500">Проектов пока нет</div>
          ) : list.length === 0 ? (
            <div className="rounded-[12px] bg-white/[0.03] px-3 py-6 text-center text-[14px] text-zinc-500">
              Ничего не найдено
              <button type="button" onClick={() => { setQ(""); setFilter("all"); }} className="mx-auto mt-2 block rounded-[10px] bg-white/[0.06] px-3 py-1.5 text-sm text-white hover:bg-white/[0.1]">
                Сбросить фильтры
              </button>
            </div>
          ) : (
            <div className="space-y-1">
              {list.map((p) => {
                const k = statusKind(p.status);
                return (
                  <div
                    key={p.id}
                    data-project-row={p.id}
                    onClick={() => setSel((v) => (v === p.id ? null : p.id))}
                    className={"flex w-full cursor-pointer items-center gap-3 rounded-[12px] px-3 py-2.5 text-left transition-colors " + (sel === p.id ? "bg-[rgba(45,212,191,0.12)] ring-1 ring-[rgba(45,212,191,0.4)]" : "bg-white/[0.02] hover:bg-[rgba(45,212,191,0.06)]")}
                  >
                    {sel === p.id ? <Check className="h-4 w-4 shrink-0" style={{ color: ACC }} /> : null}
                    <div className="min-w-0 flex-1">
                      <div className="truncate text-[14px] text-white">{p.title || `Проект ${p.id}`}</div>
                      <div className="text-sm text-zinc-500">{p.updated_at ? new Date(p.updated_at).toLocaleDateString("ru-RU") : ""}</div>
                    </div>
                    <span className={"rounded-md px-1.5 py-0.5 text-sm " + (k === "done" ? "bg-[rgba(45,212,191,0.16)] text-[#5eead4]" : k === "paused" ? "bg-amber-400/10 text-amber-200" : "bg-white/[0.06] text-zinc-300")}>{KIND_LABEL[k]}</span>
                    <span className="w-[64px] text-right text-sm text-zinc-300">{usage ? fmt(sizeOf(p.id)) : "…"}</span>
                    <button type="button" title="Открыть проект" onClick={(e) => { e.stopPropagation(); window.dispatchEvent(new CustomEvent("studio-select-project", { detail: { projectId: p.id } })); onClose(); }} className="flex h-7 w-7 items-center justify-center rounded-[8px] text-zinc-500 hover:bg-white/[0.08] hover:text-white"><ExternalLink className="h-3.5 w-3.5" /></button>
                  </div>
                );
              })}
            </div>
          )}
        </section>
      </div>

      <div className="space-y-2 border-t border-white/[0.06] px-4 py-3">
        {cacheAsk ? (
          <div data-cache-confirm className="rounded-[12px] bg-white/[0.04] p-3 text-sm text-zinc-300">
            Удалить временные файлы на <b className="text-white">{fmt(cacheAsk.total_bytes)}</b>? Результаты проектов не трогаются.
            <div className="mt-1 max-h-[72px] overflow-y-auto text-sm text-zinc-500">{cacheAsk.dirs.map((d) => <div key={d.path} className="truncate">{d.path} — {fmt(d.bytes)}</div>)}</div>
            <div className="mt-2 flex gap-2">
              <button type="button" disabled={busy === "cache" || cacheAsk.total_bytes === 0} onClick={clearCache} className="h-8 flex-1 rounded-[10px] bg-[rgba(45,212,191,0.16)] text-sm text-[#5eead4] hover:bg-[rgba(45,212,191,0.24)] disabled:opacity-40">Очистить</button>
              <button type="button" onClick={() => setCacheAsk(null)} className="h-8 flex-1 rounded-[10px] bg-white/[0.05] text-sm text-zinc-300 hover:bg-white/[0.09]">Отмена</button>
            </div>
          </div>
        ) : null}
        {exp ? (
          <div data-export-result className="rounded-[12px] bg-[rgba(45,212,191,0.08)] p-3 text-sm text-zinc-300">
            Архив готов ({fmt(exp.bytes)}): <span className="break-all text-white">{exp.path}</span>
            <a href={exp.url} download className="mt-2 flex h-8 items-center justify-center gap-1.5 rounded-[10px] bg-white/[0.06] text-sm text-white hover:bg-white/[0.1]"><Download className="h-4 w-4" /> Скачать</a>
          </div>
        ) : null}
        {note ? <div className="text-sm text-zinc-400">{note}</div> : null}
        <div className="flex gap-2">
          <button type="button" data-act="cache" disabled={busy != null} onClick={askCache} className="flex h-9 flex-1 items-center justify-center gap-1.5 rounded-[10px] bg-white/[0.05] text-sm text-zinc-300 transition-colors hover:bg-white/[0.09] hover:text-white disabled:opacity-40">
            <Trash2 className="h-4 w-4" /> Очистить кэш
          </button>
          <button type="button" data-act="export" disabled={sel == null || busy != null} title={sel == null ? "Выберите проект в списке" : ""} onClick={doExport} className="flex h-9 flex-1 items-center justify-center gap-1.5 rounded-[10px] bg-white/[0.05] text-sm text-zinc-300 transition-colors hover:bg-white/[0.09] hover:text-white disabled:opacity-40">
            <Archive className="h-4 w-4" /> {busy === "export" ? "Архивирую…" : "Экспорт архива"}
          </button>
        </div>
      </div>
    </div>
  );
}
