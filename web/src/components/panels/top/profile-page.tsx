"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  Activity, ArrowLeft, Award, Bell, Bot, Camera, Check, CheckCircle2, CircleAlert, CircleDashed, Clapperboard,
  Film, Gauge, Image as ImageIcon, KeyRound, LayoutGrid, Lock, LogOut, Mic, Monitor, Pencil, Rocket, Settings2,
  ShieldCheck, Sparkles, Star, Trash2, Trophy, Upload, Workflow, X,
} from "lucide-react";
import { UserPrefsApplier, applyUserPrefs } from "@/components/panels/top/user-panel";
import { logout as authLogout, useAuthUser } from "@/components/auth/auth-pages";

const A = "#2dd4bf";
const FONT = { fontFamily: "Rubik, sans-serif" } as const;
const PREFS_KEY = "vp-user-prefs";

/* ---------- types ---------- */
type Profile = { name: string; handle: string; bio?: string; plan: string; avatar_url: string | null; created_at?: string; notifications?: Record<string, boolean> };
type Day = { date: string; images: number; videos: number; audio: number; finals: number };
type Stats = { projects: number; completed_videos: number; published: number; generations_total: number; totals_by_kind: Record<string, number>; per_project: Record<string, Record<string, number>>; daily: Day[]; status_counts: Record<string, number> };
type Key = { id: string; label: string; env: string; configured: boolean; masked: string };
type Project = { id: number; title?: string; topic?: string; slug: string; status: string; updated_at?: string; created_at?: string };
type OutseeStatus = { configured?: boolean; balance?: { balance_credits?: number } | null; default_image_model?: string; default_video_model?: string };
type TextLlm = { active_label?: string; active_provider?: string; models?: { key_configured?: boolean; active?: boolean }[] };
type LibItem = Record<string, unknown> & { id: number };

async function j<T>(url: string, init?: RequestInit): Promise<T> {
  const r = await fetch(url, init);
  if (!r.ok) {
    let msg = `HTTP ${r.status}`;
    try { const b = await r.json(); if (b?.detail) msg = String(b.detail); } catch {}
    throw new Error(msg);
  }
  return r.json() as Promise<T>;
}

/* ---------- small UI ---------- */
function Sample() {
  return <span className="ml-1.5 rounded-md bg-white/[0.07] px-1.5 py-0.5 align-middle text-[11px] font-normal text-zinc-400">пример</span>;
}
function Card({ title, icon, right, children, className = "" }: { title?: string; icon?: React.ReactNode; right?: React.ReactNode; children: React.ReactNode; className?: string }) {
  return (
    <section className={"rounded-[16px] border border-white/[0.07] bg-white/[0.03] p-4 " + className}>
      {title ? (
        <div className="mb-3 flex items-center gap-2">
          {icon ? <span style={{ color: A }}>{icon}</span> : null}
          <span className="text-[13px] font-medium uppercase tracking-[0.08em] text-zinc-400">{title}</span>
          <div className="flex-1" />
          {right}
        </div>
      ) : null}
      {children}
    </section>
  );
}
function Stat({ label, value, hint, sample }: { label: string; value: React.ReactNode; hint?: string; sample?: boolean }) {
  return (
    <div className="rounded-[12px] bg-black/25 px-4 py-3">
      <div className="text-[26px] font-medium leading-tight text-white">{value}</div>
      <div className="mt-0.5 text-[13px] text-zinc-400">{label}{sample ? <Sample /> : null}</div>
      {hint ? <div className="mt-0.5 text-[12px] text-zinc-600">{hint}</div> : null}
    </div>
  );
}
function Btn({ children, onClick, accent, danger, disabled, title, type = "button" }: { children: React.ReactNode; onClick?: () => void; accent?: boolean; danger?: boolean; disabled?: boolean; title?: string; type?: "button" | "submit" }) {
  return (
    <button type={type} onClick={onClick} disabled={disabled} title={title}
      className={"inline-flex items-center justify-center gap-2 rounded-[10px] px-3.5 py-2 text-[14px] transition-all duration-200 disabled:cursor-not-allowed disabled:opacity-45 " +
        (danger ? "bg-white/[0.04] text-zinc-300 hover:bg-red-500/12 hover:text-red-300" : accent ? "" : "bg-white/[0.05] text-zinc-200 hover:bg-white/[0.09]")}
      style={accent ? { background: A + "24", color: A } : undefined}>
      {children}
    </button>
  );
}
function Toggle({ on, onChange }: { on: boolean; onChange: (v: boolean) => void }) {
  return (
    <button type="button" role="switch" aria-checked={on} onClick={() => onChange(!on)}
      className="relative h-[24px] w-[42px] shrink-0 rounded-full transition-colors duration-200"
      style={{ background: on ? A : "rgba(255,255,255,0.12)" }}>
      <span className="absolute top-[3px] h-[18px] w-[18px] rounded-full bg-white shadow transition-all duration-200" style={{ left: on ? 21 : 3 }} />
    </button>
  );
}
function Seg<T extends string | number>({ value, options, onChange }: { value: T; options: { v: T; t: string }[]; onChange: (v: T) => void }) {
  return (
    <div className="flex gap-1 rounded-[11px] bg-black/30 p-1">
      {options.map((o) => (
        <button key={String(o.v)} type="button" onClick={() => onChange(o.v)}
          className="flex-1 rounded-[8px] px-3 py-1.5 text-[14px] transition-all duration-200"
          style={value === o.v ? { background: A + "26", color: A } : { color: "#a1a1aa" }}>{o.t}</button>
      ))}
    </div>
  );
}
function Skel({ h = 80 }: { h?: number }) {
  return <div className="animate-pulse rounded-[12px] bg-white/[0.04]" style={{ height: h }} />;
}
function Empty({ text, action }: { text: string; action?: React.ReactNode }) {
  return <div className="flex flex-col items-center gap-2 rounded-[12px] bg-black/20 px-4 py-8 text-center text-[14px] text-zinc-500">{text}{action}</div>;
}
function ErrorBox({ err, retry }: { err: unknown; retry: () => void }) {
  return <div className="flex items-center gap-3 rounded-[12px] bg-red-500/[0.07] px-4 py-3 text-[14px] text-red-300"><CircleAlert className="h-4 w-4" />{String((err as Error)?.message || err)}<div className="flex-1" /><Btn onClick={retry}>Повторить</Btn></div>;
}
function Stars({ value }: { value: number }) {
  return <span className="inline-flex gap-0.5">{[1, 2, 3, 4, 5].map((i) => <Star key={i} className="h-3.5 w-3.5" style={{ color: i <= value ? A : "#3f3f46", fill: i <= value ? A : "transparent" }} />)}</span>;
}

const STATUS_RU: Record<string, string> = { new: "Новый", published: "Опубликован", done: "Готов", failed: "Ошибка", paused: "Пауза" };
const statusLabel = (s: string) => STATUS_RU[s] ?? s.replace(/_/g, " ");
const fmtDate = (s?: string) => (s ? new Date(s).toLocaleDateString("ru-RU", { day: "numeric", month: "short" }) : "—");
const NODE_TYPES: { id: string; label: string }[] = [
  { id: "topic", label: "Тема" }, { id: "plan", label: "План" }, { id: "script", label: "Сценарий" }, { id: "hero", label: "Персонажи" },
  { id: "items", label: "Предметы" }, { id: "excel_gpt", label: "Excel GPT" }, { id: "image_prompts", label: "Промты картинок" },
  { id: "images", label: "Картинки" }, { id: "animation_prompts", label: "Промты анимации" }, { id: "videos", label: "Видео" },
  { id: "audio", label: "Озвучка" }, { id: "music", label: "Музыка" }, { id: "sfx_plan", label: "План звуков" }, { id: "sfx_gen", label: "Звуки" },
  { id: "assemble", label: "Сборка" }, { id: "publish", label: "Публикация" },
];

/* ---------- chart ---------- */
function UsageChart({ days }: { days: Day[] }) {
  const [hover, setHover] = useState<number | null>(null);
  const max = Math.max(1, ...days.map((d) => d.images + d.videos + d.audio));
  const h = 150;
  const spend = (d: Day) => d.images * 0.04 + d.videos * 0.35 + d.audio * 0.02; // образец тарифов
  const hd = hover != null ? days[hover] : null;
  return (
    <div>
      <div className="mb-2 flex flex-wrap items-center gap-4 text-[13px] text-zinc-400">
        <span className="flex items-center gap-1.5"><i className="inline-block h-2.5 w-2.5 rounded-sm" style={{ background: A }} />Картинки</span>
        <span className="flex items-center gap-1.5"><i className="inline-block h-2.5 w-2.5 rounded-sm bg-sky-400" />Видео</span>
        <span className="flex items-center gap-1.5"><i className="inline-block h-2.5 w-2.5 rounded-sm bg-violet-400" />Аудио</span>
        <div className="flex-1" />
        <span className="text-zinc-500">{hd ? `${fmtDate(hd.date)}: ${hd.images} карт. · ${hd.videos} видео · ${hd.audio} ауд. · ≈$${spend(hd).toFixed(2)}` : "наведи на столбец"}</span>
      </div>
      <div className="flex items-end gap-[3px]" style={{ height: h }} onMouseLeave={() => setHover(null)}>
        {days.map((d, i) => {
          const tot = d.images + d.videos + d.audio;
          const H = (tot / max) * (h - 8);
          return (
            <div key={d.date} onMouseEnter={() => setHover(i)} className="flex h-full flex-1 cursor-default flex-col justify-end rounded-[4px] transition-colors" style={{ background: hover === i ? "rgba(255,255,255,0.04)" : "transparent" }}>
              <div className="flex flex-col-reverse overflow-hidden rounded-[4px] transition-all duration-500" style={{ height: Math.max(tot ? 3 : 1, H), opacity: tot ? 1 : 0.25, background: tot ? undefined : "#3f3f46" }}>
                <div style={{ height: `${(d.images / (tot || 1)) * 100}%`, background: A }} />
                <div className="bg-sky-400" style={{ height: `${(d.videos / (tot || 1)) * 100}%` }} />
                <div className="bg-violet-400" style={{ height: `${(d.audio / (tot || 1)) * 100}%` }} />
              </div>
            </div>
          );
        })}
      </div>
      <div className="mt-1.5 flex justify-between text-[12px] text-zinc-600"><span>{fmtDate(days[0]?.date)}</span><span>сегодня</span></div>
    </div>
  );
}

/* ---------- sections ---------- */
const TABS = [
  { id: "overview", t: "Обзор", i: <Gauge className="h-4 w-4" /> },
  { id: "projects", t: "Проекты", i: <LayoutGrid className="h-4 w-4" /> },
  { id: "published", t: "Публикации", i: <Rocket className="h-4 w-4" /> },
  { id: "nodes", t: "Мои ноды и промты", i: <Workflow className="h-4 w-4" /> },
  { id: "keys", t: "Подключения и ключи", i: <KeyRound className="h-4 w-4" /> },
  { id: "usage", t: "Использование и лимиты", i: <Activity className="h-4 w-4" /> },
  { id: "settings", t: "Настройки", i: <Settings2 className="h-4 w-4" /> },
  { id: "security", t: "Безопасность", i: <ShieldCheck className="h-4 w-4" /> },
  { id: "activity", t: "Достижения и лента", i: <Trophy className="h-4 w-4" /> },
] as const;
type TabId = (typeof TABS)[number]["id"];

function useData() {
  const profile = useQuery({ queryKey: ["profile"], queryFn: () => j<Profile>("/api/profile") });
  const stats = useQuery({ queryKey: ["profile", "stats"], queryFn: () => j<Stats>("/api/profile/stats"), staleTime: 60_000 });
  const projects = useQuery({ queryKey: ["profile", "projects"], queryFn: () => j<Project[]>("/api/projects"), staleTime: 30_000 });
  const keys = useQuery({ queryKey: ["profile", "keys"], queryFn: () => j<Key[]>("/api/profile/keys") });
  const outsee = useQuery({ queryKey: ["profile", "outsee"], queryFn: () => j<OutseeStatus>("/api/outsee/status"), staleTime: 30_000 });
  const llm = useQuery({ queryKey: ["profile", "llm"], queryFn: () => j<TextLlm>("/api/text-llm"), staleTime: 30_000 });
  return { profile, stats, projects, keys, outsee, llm };
}
type D = ReturnType<typeof useData>;

function Overview({ d, go }: { d: D; go: (t: TabId) => void }) {
  const s = d.stats.data;
  const days = s?.daily ?? [];
  const last30 = days.reduce((a, x) => a + x.images + x.videos + x.audio, 0);
  const spend30 = days.reduce((a, x) => a + x.images * 0.04 + x.videos * 0.35 + x.audio * 0.02, 0);
  return (
    <div className="flex flex-col gap-3">
      {d.stats.isError ? <ErrorBox err={d.stats.error} retry={() => d.stats.refetch()} /> : null}
      <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
        {d.stats.isLoading ? [0, 1, 2, 3].map((i) => <Skel key={i} />) : (
          <>
            <Stat label="Проектов" value={s?.projects ?? 0} hint={`${Object.keys(s?.status_counts ?? {}).length} статусов`} />
            <Stat label="Готовых видео" value={s?.completed_videos ?? 0} hint="финальные ролики" />
            <Stat label="Генераций всего" value={(s?.generations_total ?? 0).toLocaleString("ru-RU")} hint={`за 30 дней: ${last30.toLocaleString("ru-RU")}`} />
            <Stat label="Расход за 30 дней" value={`≈$${spend30.toFixed(0)}`} hint="оценка по числу генераций" sample />
          </>
        )}
      </div>
      <Card title="Генерации и расход · 30 дней" icon={<Activity className="h-4 w-4" />} right={<span className="text-[12px] text-zinc-500">объёмы — реальные, $ — по тарифам-образцам<Sample /></span>}>
        {d.stats.isLoading ? <Skel h={170} /> : days.length ? <UsageChart days={days} /> : <Empty text="Нет данных за 30 дней" />}
      </Card>
      <div className="grid gap-3 lg:grid-cols-2">
        <Card title="Последние проекты" icon={<Clapperboard className="h-4 w-4" />} right={<button className="text-[13px] hover:underline" style={{ color: A }} onClick={() => go("projects")}>Все →</button>}>
          <ProjectList d={d} limit={4} />
        </Card>
        <Card title="Подключения" icon={<KeyRound className="h-4 w-4" />} right={<button className="text-[13px] hover:underline" style={{ color: A }} onClick={() => go("keys")}>Управлять →</button>}>
          <ConnSummary d={d} />
        </Card>
      </div>
    </div>
  );
}

function ProjectList({ d, limit }: { d: D; limit: number }) {
  const list = [...(d.projects.data ?? [])].sort((a, b) => String(b.updated_at).localeCompare(String(a.updated_at))).slice(0, limit);
  if (d.projects.isLoading) return <Skel h={150} />;
  if (!list.length) return <Empty text="Проектов пока нет" />;
  return (
    <div className="flex flex-col gap-1.5">
      {list.map((p) => (
        <a key={p.id} href="/" onClick={() => { try { localStorage.setItem("vp-studio-selected-project-id", String(p.id)); } catch {} }}
          className="flex items-center gap-3 rounded-[10px] bg-black/20 px-3 py-2 transition-colors hover:bg-white/[0.05]">
          <Cover id={p.id} className="h-10 w-16 rounded-[7px]" />
          <div className="min-w-0 flex-1"><div className="truncate text-[14px] text-white">{p.title || p.topic || p.slug}</div><div className="text-[12px] text-zinc-500">{statusLabel(p.status)} · {fmtDate(p.updated_at)}</div></div>
        </a>
      ))}
    </div>
  );
}

function Cover({ id, className = "" }: { id: number; className?: string }) {
  const [ok, setOk] = useState(true);
  return (
    <div className={"relative shrink-0 overflow-hidden bg-[radial-gradient(circle_at_30%_20%,rgba(45,212,191,0.25),transparent_60%),#121316] " + className}>
      {ok ? (
        // eslint-disable-next-line @next/next/no-img-element
        <img src={`/api/artifacts/project-cover/${id}`} alt="" loading="lazy" onError={() => setOk(false)} className="absolute inset-0 h-full w-full object-cover" />
      ) : <Film className="absolute left-1/2 top-1/2 h-5 w-5 -translate-x-1/2 -translate-y-1/2 text-zinc-700" />}
    </div>
  );
}

function Projects({ d, onlyPublished }: { d: D; onlyPublished?: boolean }) {
  const [q, setQ] = useState("");
  const [sort, setSort] = useState<"new" | "az">("new");
  const per = d.stats.data?.per_project ?? {};
  let list = d.projects.data ?? [];
  if (onlyPublished) list = list.filter((p) => p.status === "published" || (per[p.id]?.final_video ?? 0) > 0);
  if (q) list = list.filter((p) => `${p.title} ${p.topic} ${p.slug}`.toLowerCase().includes(q.toLowerCase()));
  list = [...list].sort((a, b) => (sort === "az" ? String(a.title).localeCompare(String(b.title), "ru") : String(b.updated_at).localeCompare(String(a.updated_at))));
  return (
    <div className="flex flex-col gap-3">
      <div className="flex flex-wrap items-center gap-2">
        <input value={q} onChange={(e) => setQ(e.target.value)} placeholder="Поиск по названию…" className="h-10 min-w-[240px] flex-1 rounded-[10px] border border-white/[0.08] bg-black/30 px-3.5 text-[14px] text-white outline-none transition-colors placeholder:text-zinc-600 focus:border-[#2dd4bf]/60" />
        <div className="w-[220px]"><Seg value={sort} onChange={setSort} options={[{ v: "new", t: "Новые" }, { v: "az", t: "А–Я" }]} /></div>
        <span className="text-[13px] text-zinc-500">{list.length} шт.</span>
      </div>
      {d.projects.isError ? <ErrorBox err={d.projects.error} retry={() => d.projects.refetch()} /> : null}
      {d.projects.isLoading ? <div className="grid grid-cols-2 gap-3 md:grid-cols-3 xl:grid-cols-4">{[0, 1, 2, 3, 4, 5, 6, 7].map((i) => <Skel key={i} h={170} />)}</div> : list.length === 0 ? (
        <Empty text={onlyPublished ? "Опубликованных и готовых роликов пока нет" : q ? "Ничего не найдено" : "Проектов пока нет"} action={q ? <Btn onClick={() => setQ("")}>Сбросить поиск</Btn> : undefined} />
      ) : (
        <div className="grid grid-cols-2 gap-3 md:grid-cols-3 xl:grid-cols-4">
          {list.map((p, i) => {
            const k = per[p.id] ?? {};
            return (
              <a key={p.id} href="/" onClick={() => { try { localStorage.setItem("vp-studio-selected-project-id", String(p.id)); } catch {} }}
                className="group overflow-hidden rounded-[14px] border border-white/[0.07] bg-white/[0.03] transition-all duration-300 hover:-translate-y-0.5 hover:border-[#2dd4bf]/40"
                style={{ animation: `vpFadeUp .35s ease ${Math.min(i, 12) * 25}ms both` }}>
                <Cover id={p.id} className="aspect-video w-full" />
                <div className="p-3">
                  <div className="truncate text-[15px] font-medium text-white">{p.title || p.topic || p.slug}</div>
                  <div className="mt-1 flex items-center gap-2 text-[12px] text-zinc-500">
                    <span className="rounded-md px-1.5 py-0.5" style={p.status === "published" ? { background: A + "22", color: A } : { background: "rgba(255,255,255,0.06)" }}>{statusLabel(p.status)}</span>
                    <span>{fmtDate(p.updated_at)}</span>
                  </div>
                  <div className="mt-2 flex gap-3 text-[12px] text-zinc-400">
                    <span className="flex items-center gap-1"><ImageIcon className="h-3.5 w-3.5" />{k.scene_image ?? 0}</span>
                    <span className="flex items-center gap-1"><Film className="h-3.5 w-3.5" />{k.scene_video ?? 0}</span>
                    {onlyPublished ? <span className="flex items-center gap-1" style={{ color: A }}><CheckCircle2 className="h-3.5 w-3.5" />{k.final_video ?? 0} финал</span> : null}
                  </div>
                </div>
              </a>
            );
          })}
        </div>
      )}
    </div>
  );
}

function MyNodes() {
  const ratings = useQuery({ queryKey: ["profile", "ratings-node"], queryFn: () => j<{ default: number; ratings: Record<string, number> }>("/api/ratings?kind=node") });
  const pratings = useQuery({ queryKey: ["profile", "ratings-prompt"], queryFn: () => j<{ default: number; ratings: Record<string, number> }>("/api/ratings?kind=prompt") });
  const lib = useQuery({ queryKey: ["profile", "library"], queryFn: () => j<LibItem[]>("/api/library/items") });
  const def = ratings.data?.default ?? 5;
  const items = lib.data ?? [];
  return (
    <div className="flex flex-col gap-3">
      <Card title="Ноды студии" icon={<Workflow className="h-4 w-4" />} right={<span className="text-[12px] text-zinc-500">оценки — реальные (ratings.json)</span>}>
        <div className="grid grid-cols-2 gap-2.5 md:grid-cols-4 xl:grid-cols-8">
          {NODE_TYPES.map((n) => (
            <div key={n.id} className="overflow-hidden rounded-[12px] border border-white/[0.06] bg-black/25">
              {/* eslint-disable-next-line @next/next/no-img-element */}
              <img src={`/prompt-bg/node/${n.id}.webp`} alt="" className="aspect-[16/9] w-full object-cover opacity-90" />
              <div className="px-2.5 py-2"><div className="truncate text-[13px] text-white">{n.label}</div><Stars value={ratings.data?.ratings?.[n.id] ?? def} /></div>
            </div>
          ))}
        </div>
      </Card>
      <Card title="Мои элементы библиотеки" icon={<Sparkles className="h-4 w-4" />} right={<span className="text-[12px] text-zinc-500">{items.length} шт.</span>}>
        {lib.isLoading ? <Skel h={120} /> : lib.isError ? <ErrorBox err={lib.error} retry={() => lib.refetch()} /> : items.length === 0 ? <Empty text="Сохранённых нод и промтов в библиотеке пока нет" /> : (
          <div className="grid gap-2 md:grid-cols-2 xl:grid-cols-3">
            {items.slice(0, 30).map((it) => {
              const name = String(it.name ?? it.title ?? it.slug ?? `#${it.id}`);
              const kind = String(it.kind ?? it.item_type ?? it.type ?? "");
              const r = pratings.data?.ratings?.[String(it.id)] ?? pratings.data?.default ?? 5;
              return (
                <div key={it.id} className="flex items-center gap-3 rounded-[10px] bg-black/20 px-3 py-2.5">
                  <span className="flex h-9 w-9 items-center justify-center rounded-[9px]" style={{ background: A + "18", color: A }}><Workflow className="h-4 w-4" /></span>
                  <div className="min-w-0 flex-1"><div className="truncate text-[14px] text-white">{name}</div><div className="truncate text-[12px] text-zinc-500">{kind || "элемент"}{it.updated_at ? ` · ${fmtDate(String(it.updated_at))}` : ""}</div></div>
                  <Stars value={r} />
                </div>
              );
            })}
          </div>
        )}
      </Card>
    </div>
  );
}

function ConnState({ s }: { s: "ok" | "off" | "loading" | "error" }) {
  const m = { ok: [<CheckCircle2 key="i" className="h-4 w-4" style={{ color: A }} />, "подключено"], off: [<CircleAlert key="i" className="h-4 w-4 text-amber-400" />, "нет ключа"], error: [<CircleAlert key="i" className="h-4 w-4 text-red-400" />, "ошибка"], loading: [<CircleDashed key="i" className="h-4 w-4 animate-spin text-zinc-500" />, "проверка…"] }[s];
  return <span className="flex items-center gap-1.5 whitespace-nowrap text-[13px] text-zinc-400">{m[0]}{m[1]}</span>;
}

function ConnSummary({ d }: { d: D }) {
  const keyOf = (id: string) => d.keys.data?.find((k) => k.id === id);
  const llmOk = Boolean(d.llm.data?.models?.some((m) => m.active && m.key_configured));
  const rows: { n: string; i: React.ReactNode; s: "ok" | "off" | "loading" | "error"; det: string }[] = [
    { n: "GPT / текст", i: <Bot className="h-4 w-4" />, s: d.llm.isLoading ? "loading" : d.llm.isError ? "error" : llmOk ? "ok" : "off", det: d.llm.data?.active_label ?? "модель не выбрана" },
    { n: "Kie", i: <Sparkles className="h-4 w-4" />, s: d.keys.isLoading ? "loading" : keyOf("kie")?.configured ? "ok" : "off", det: keyOf("kie")?.masked || "ключ не задан" },
    { n: "VIA Render", i: <ImageIcon className="h-4 w-4" />, s: d.outsee.isLoading ? "loading" : d.outsee.isError ? "error" : d.outsee.data?.configured ? "ok" : "off", det: d.outsee.data?.balance?.balance_credits != null ? `${d.outsee.data.balance.balance_credits.toLocaleString("ru-RU")} кредитов` : "картинки и видео" },
    { n: "ElevenLabs", i: <Mic className="h-4 w-4" />, s: d.keys.isLoading ? "loading" : keyOf("elevenlabs")?.configured ? "ok" : "off", det: keyOf("elevenlabs")?.masked || "ключ не задан" },
  ];
  return (
    <div className="flex flex-col gap-1.5">
      {rows.map((r) => (
        <div key={r.n} className="flex items-center gap-3 rounded-[10px] bg-black/20 px-3 py-2.5">
          <span className="flex h-8 w-8 items-center justify-center rounded-[8px] bg-white/[0.05] text-zinc-300">{r.i}</span>
          <div className="min-w-0 flex-1"><div className="text-[14px] text-white">{r.n}</div><div className="truncate text-[12px] text-zinc-500">{r.det}</div></div>
          <ConnState s={r.s} />
        </div>
      ))}
    </div>
  );
}

function Keys({ d }: { d: D }) {
  const qc = useQueryClient();
  const [edit, setEdit] = useState<string | null>(null);
  const [val, setVal] = useState("");
  const [err, setErr] = useState<string | null>(null);
  const save = useMutation({
    mutationFn: ({ id, value }: { id: string; value: string }) => j<Key>(`/api/profile/keys/${id}`, { method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ value }) }),
    onSuccess: () => { setEdit(null); setVal(""); setErr(null); qc.invalidateQueries({ queryKey: ["profile", "keys"] }); qc.invalidateQueries({ queryKey: ["profile", "llm"] }); },
    onError: (e) => setErr(String((e as Error).message)),
  });
  return (
    <div className="flex flex-col gap-3">
      <Card title="Статус сервисов" icon={<Activity className="h-4 w-4" />}><ConnSummary d={d} /></Card>
      <Card title="API-ключи" icon={<KeyRound className="h-4 w-4" />} right={<span className="text-[12px] text-zinc-500">хранятся в .env на этом ПК, перед записью делается бэкап</span>}>
        {d.keys.isLoading ? <Skel h={200} /> : d.keys.isError ? <ErrorBox err={d.keys.error} retry={() => d.keys.refetch()} /> : (
          <div className="flex flex-col gap-1.5">
            {(d.keys.data ?? []).map((k) => (
              <div key={k.id} className="rounded-[10px] bg-black/20 px-3 py-2.5 transition-all">
                <div className="flex items-center gap-3">
                  <Lock className="h-4 w-4 text-zinc-500" />
                  <div className="min-w-0 flex-1"><div className="text-[14px] text-white">{k.label}</div><div className="font-mono text-[12px] text-zinc-500">{k.env} · {k.masked || "не задан"}</div></div>
                  <ConnState s={k.configured ? "ok" : "off"} />
                  <Btn onClick={() => { setEdit(edit === k.id ? null : k.id); setVal(""); setErr(null); }}><Pencil className="h-3.5 w-3.5" />{k.configured ? "Изменить" : "Добавить"}</Btn>
                </div>
                {edit === k.id ? (
                  <form className="mt-2.5 flex items-center gap-2" onSubmit={(e) => { e.preventDefault(); if (val.trim()) save.mutate({ id: k.id, value: val }); }}>
                    <input autoFocus type="password" value={val} onChange={(e) => setVal(e.target.value)} placeholder={`Новый ${k.env}`} className="h-10 flex-1 rounded-[10px] border border-white/[0.08] bg-black/40 px-3 font-mono text-[13px] text-white outline-none focus:border-[#2dd4bf]/60" />
                    <Btn type="submit" accent disabled={!val.trim() || save.isPending}><Check className="h-4 w-4" />Сохранить</Btn>
                    <Btn onClick={() => setEdit(null)}><X className="h-4 w-4" /></Btn>
                  </form>
                ) : null}
                {edit === k.id && err ? <div className="mt-1.5 text-[13px] text-red-300">{err}</div> : null}
              </div>
            ))}
            <div className="pt-1 text-[12px] text-zinc-600">Новые ключи подхватываются сразу; часть сервисов применит их после перезапуска студии.</div>
          </div>
        )}
      </Card>
    </div>
  );
}

function Usage({ d }: { d: D }) {
  const credits = d.outsee.data?.balance?.balance_credits;
  const t = d.stats.data?.totals_by_kind ?? {};
  const bars = [
    { l: "Хранилище", used: 38, max: 100, u: "ГБ", sample: true },
    { l: "Генераций в месяц", used: (d.stats.data?.daily ?? []).reduce((a, x) => a + x.images + x.videos + x.audio, 0), max: 5000, u: "", sample: false },
    { l: "Параллельных потоков генерации", used: 4, max: 4, u: "", sample: false },
    { l: "Проектов", used: d.stats.data?.projects ?? 0, max: 200, u: "", sample: true },
  ];
  return (
    <div className="flex flex-col gap-3">
      <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
        <Stat label="Баланс VIA" value={d.outsee.isLoading ? "…" : credits != null ? credits.toLocaleString("ru-RU") : "—"} hint="реальный баланс" />
        <Stat label="Картинок" value={(t.scene_image ?? 0).toLocaleString("ru-RU")} hint={`+ ${((t.hero_reference ?? 0) + (t.item_reference ?? 0))} референсов`} />
        <Stat label="Видео-сцен" value={(t.scene_video ?? 0).toLocaleString("ru-RU")} />
        <Stat label="Аудио" value={((t.audio ?? 0) + (t.music ?? 0)).toLocaleString("ru-RU")} />
      </div>
      <Card title="Лимиты тарифа" icon={<Gauge className="h-4 w-4" />} right={<span className="text-[12px] text-zinc-500">лимиты — образец<Sample /></span>}>
        <div className="flex flex-col gap-3.5">
          {bars.map((b) => {
            const pct = Math.min(100, (b.used / b.max) * 100);
            return (
              <div key={b.l}>
                <div className="mb-1.5 flex justify-between text-[14px]"><span className="text-zinc-300">{b.l}{b.sample ? <Sample /> : null}</span><span className="text-zinc-400">{b.used.toLocaleString("ru-RU")} / {b.max.toLocaleString("ru-RU")} {b.u}</span></div>
                <div className="h-2 overflow-hidden rounded-full bg-white/[0.06]"><div className="h-full rounded-full transition-all duration-700" style={{ width: `${pct}%`, background: pct > 90 ? "#f87171" : A }} /></div>
              </div>
            );
          })}
        </div>
      </Card>
    </div>
  );
}

type Prefs = { lang: "ru" | "en"; fontScale: 100 | 110 | 120 };
function Settings({ d }: { d: D }) {
  const qc = useQueryClient();
  const [prefs, setPrefs] = useState<Prefs>({ lang: "ru", fontScale: 100 });
  useEffect(() => { try { const s = localStorage.getItem(PREFS_KEY); if (s) setPrefs((p) => ({ ...p, ...JSON.parse(s) })); } catch {} }, []);
  const setP = (p: Partial<Prefs>) => setPrefs((v) => { const n = { ...v, ...p }; try { localStorage.setItem(PREFS_KEY, JSON.stringify(n)); } catch {} applyUserPrefs(n); return n; });
  const notif = d.profile.data?.notifications ?? {};
  const setN = useMutation({
    mutationFn: (n: Record<string, boolean>) => j<Profile>("/api/profile", { method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ notifications: n }) }),
    onSuccess: (p) => qc.setQueryData(["profile"], p),
  });
  const N = [
    { k: "run_done", t: "Генерация завершена", s: "уведомление, когда пайплайн дошёл до конца" },
    { k: "run_failed", t: "Ошибка в ноде", s: "сразу, если шаг упал" },
    { k: "low_balance", t: "Мало кредитов", s: "когда баланс VIA подходит к концу" },
    { k: "weekly", t: "Еженедельный отчёт", s: "сводка проектов и расходов" },
  ];
  return (
    <div className="grid gap-3 lg:grid-cols-2">
      <Card title="Интерфейс" icon={<Monitor className="h-4 w-4" />}>
        <div className="flex flex-col gap-4">
          <div><div className="mb-1.5 text-[14px] text-zinc-300">Размер шрифта</div><Seg value={prefs.fontScale} onChange={(v) => setP({ fontScale: v })} options={[{ v: 100, t: "100%" }, { v: 110, t: "110%" }, { v: 120, t: "120%" }]} /><div className="mt-1.5 text-[12px] text-zinc-600">Применяется сразу ко всей студии.</div></div>
          <div><div className="mb-1.5 text-[14px] text-zinc-300">Язык <span className="text-zinc-600">(перевод интерфейса — позже)</span></div><Seg value={prefs.lang} onChange={(v) => setP({ lang: v })} options={[{ v: "ru", t: "Русский" }, { v: "en", t: "English" }]} /></div>
        </div>
      </Card>
      <Card title="Уведомления" icon={<Bell className="h-4 w-4" />} right={setN.isPending ? <CircleDashed className="h-4 w-4 animate-spin text-zinc-500" /> : null}>
        <div className="flex flex-col gap-1.5">
          {N.map((n) => (
            <div key={n.k} className="flex items-center gap-3 rounded-[10px] bg-black/20 px-3 py-2.5">
              <div className="min-w-0 flex-1"><div className="text-[14px] text-white">{n.t}</div><div className="text-[12px] text-zinc-500">{n.s}</div></div>
              <Toggle on={Boolean(notif[n.k])} onChange={(v) => setN.mutate({ [n.k]: v })} />
            </div>
          ))}
          <div className="pt-1 text-[12px] text-zinc-600">Выбор сохраняется в профиле; доставка уведомлений (Telegram) — следующий шаг.</div>
        </div>
      </Card>
    </div>
  );
}

function Security() {
  const auth = useQuery({ queryKey: ["profile", "auth"], queryFn: () => j<{ auth_required: boolean }>("/api/auth/status") });
  const req = auth.data?.auth_required ?? false;
  const ua = typeof navigator !== "undefined" ? navigator.userAgent : "";
  const browser = /Edg\//.test(ua) ? "Edge" : /Chrome\//.test(ua) ? "Chrome" : /Firefox\//.test(ua) ? "Firefox" : "Браузер";
  const sessions = [
    { d: `${browser} · Windows`, w: "этот ПК · 127.0.0.1", t: "сейчас", cur: true },
    { d: "Chrome · Android", w: "мобильный · 10.0.0.14", t: "2 дня назад", cur: false },
    { d: "Edge · Windows", w: "ноутбук · 10.0.0.22", t: "неделю назад", cur: false },
  ];
  return (
    <div className="grid gap-3 lg:grid-cols-2">
      <Card title="Доступ" icon={<ShieldCheck className="h-4 w-4" />}>
        <div className="flex flex-col gap-1.5">
          <div className="flex items-center gap-3 rounded-[10px] bg-black/20 px-3 py-2.5"><Lock className="h-4 w-4 text-zinc-500" /><div className="flex-1"><div className="text-[14px] text-white">Вход по паролю</div><div className="text-[12px] text-zinc-500">{auth.isLoading ? "проверка…" : req ? "включён на сервере" : "выключен — студия доступна локально без входа"}</div></div><ConnState s={auth.isLoading ? "loading" : req ? "ok" : "off"} /></div>
          <div className="flex items-center gap-3 rounded-[10px] bg-black/20 px-3 py-2.5"><ShieldCheck className="h-4 w-4 text-zinc-500" /><div className="flex-1"><div className="text-[14px] text-white">Двухфакторная защита<Sample /></div><div className="text-[12px] text-zinc-500">код из приложения при входе</div></div><Toggle on={false} onChange={() => {}} /></div>
          <div className="flex items-center gap-3 rounded-[10px] bg-black/20 px-3 py-2.5"><KeyRound className="h-4 w-4 text-zinc-500" /><div className="flex-1"><div className="text-[14px] text-white">Ключи API</div><div className="text-[12px] text-zinc-500">показываются только маской, полные значения не уходят в браузер</div></div><CheckCircle2 className="h-4 w-4" style={{ color: A }} /></div>
        </div>
      </Card>
      <Card title="Сессии" icon={<Monitor className="h-4 w-4" />} right={<span className="text-[12px] text-zinc-500">другие сессии<Sample /></span>}>
        <div className="flex flex-col gap-1.5">
          {sessions.map((s) => (
            <div key={s.d + s.w} className="flex items-center gap-3 rounded-[10px] bg-black/20 px-3 py-2.5">
              <Monitor className="h-4 w-4 text-zinc-500" />
              <div className="min-w-0 flex-1"><div className="text-[14px] text-white">{s.d}{s.cur ? <span className="ml-2 rounded-md px-1.5 py-0.5 text-[11px]" style={{ background: A + "22", color: A }}>текущая</span> : null}</div><div className="text-[12px] text-zinc-500">{s.w} · {s.t}</div></div>
              {!s.cur ? <Btn danger disabled title="Появится с включением входа">Завершить</Btn> : null}
            </div>
          ))}
          <AuthAccountBox />
        </div>
      </Card>
    </div>
  );
}

function ActivityTab({ d }: { d: D }) {
  const s = d.stats.data;
  const ach = [
    { t: "Первый проект", s: "создан первый ролик", ok: (s?.projects ?? 0) >= 1, i: <Clapperboard className="h-5 w-5" /> },
    { t: "Контент-завод", s: "50+ проектов", ok: (s?.projects ?? 0) >= 50, i: <Workflow className="h-5 w-5" /> },
    { t: "Тысяча кадров", s: "1000+ картинок", ok: (s?.totals_by_kind?.scene_image ?? 0) >= 1000, i: <ImageIcon className="h-5 w-5" /> },
    { t: "Режиссёр", s: "100+ видео-сцен", ok: (s?.totals_by_kind?.scene_video ?? 0) >= 100, i: <Film className="h-5 w-5" /> },
    { t: "Финал", s: "первый готовый ролик", ok: (s?.completed_videos ?? 0) >= 1, i: <Award className="h-5 w-5" /> },
    { t: "В эфире", s: "первая публикация", ok: (s?.published ?? 0) >= 1, i: <Rocket className="h-5 w-5" /> },
  ];
  const feed = [...(d.projects.data ?? [])].sort((a, b) => String(b.updated_at).localeCompare(String(a.updated_at))).slice(0, 12);
  return (
    <div className="grid gap-3 lg:grid-cols-[1fr_1.2fr]">
      <Card title="Достижения" icon={<Trophy className="h-4 w-4" />} right={<span className="text-[12px] text-zinc-500">{ach.filter((a) => a.ok).length} / {ach.length}</span>}>
        <div className="grid grid-cols-2 gap-2">
          {ach.map((a) => (
            <div key={a.t} className="flex items-center gap-3 rounded-[12px] px-3 py-3 transition-all" style={{ background: a.ok ? A + "12" : "rgba(0,0,0,0.2)", opacity: a.ok ? 1 : 0.55 }}>
              <span className="flex h-10 w-10 items-center justify-center rounded-full" style={{ background: a.ok ? A + "26" : "rgba(255,255,255,0.05)", color: a.ok ? A : "#71717a" }}>{a.i}</span>
              <div className="min-w-0"><div className="text-[14px] text-white">{a.t}</div><div className="text-[12px] text-zinc-500">{a.s}</div></div>
            </div>
          ))}
        </div>
      </Card>
      <Card title="Лента активности" icon={<Activity className="h-4 w-4" />} right={<span className="text-[12px] text-zinc-500">по обновлениям проектов</span>}>
        {d.projects.isLoading ? <Skel h={240} /> : feed.length === 0 ? <Empty text="Пока нет активности" /> : (
          <div className="relative flex flex-col gap-0.5 pl-4 before:absolute before:bottom-2 before:left-[5px] before:top-2 before:w-px before:bg-white/[0.08]">
            {feed.map((p) => (
              <div key={p.id} className="relative flex items-center gap-3 rounded-[10px] px-2 py-2 hover:bg-white/[0.03]">
                <span className="absolute -left-[14px] h-2 w-2 rounded-full" style={{ background: p.status === "published" ? A : "#52525b" }} />
                <div className="min-w-0 flex-1"><div className="truncate text-[14px] text-white">{p.title || p.topic || p.slug}</div><div className="text-[12px] text-zinc-500">статус: {statusLabel(p.status)}</div></div>
                <span className="text-[12px] text-zinc-500">{p.updated_at ? new Date(p.updated_at).toLocaleString("ru-RU", { day: "numeric", month: "short", hour: "2-digit", minute: "2-digit" }) : ""}</span>
              </div>
            ))}
          </div>
        )}
      </Card>
    </div>
  );
}

/* ---------- header ---------- */
function Header({ d }: { d: D }) {
  const qc = useQueryClient();
  const p = d.profile.data;
  const [editing, setEditing] = useState(false);
  const [name, setName] = useState("");
  const [handle, setHandle] = useState("");
  const [err, setErr] = useState<string | null>(null);
  const fileRef = useRef<HTMLInputElement>(null);
  const save = useMutation({
    mutationFn: (b: Partial<Profile>) => j<Profile>("/api/profile", { method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify(b) }),
    onSuccess: (np) => { qc.setQueryData(["profile"], np); setEditing(false); setErr(null); },
    onError: (e) => setErr(String((e as Error).message)),
  });
  const upload = useMutation({
    mutationFn: async (f: File) => { const fd = new FormData(); fd.append("file", f); return j<Profile>("/api/profile/avatar", { method: "POST", body: fd }); },
    onSuccess: (np) => qc.setQueryData(["profile"], np),
    onError: (e) => setErr(String((e as Error).message)),
  });
  const delAv = useMutation({ mutationFn: () => j<Profile>("/api/profile/avatar", { method: "DELETE" }), onSuccess: (np) => qc.setQueryData(["profile"], np) });
  const initial = (p?.name || "A").trim().charAt(0).toUpperCase();
  return (
    <div className="relative overflow-hidden rounded-[18px] border border-white/[0.07]">
      {/* eslint-disable-next-line @next/next/no-img-element */}
      <img src="/prompt-bg/node/assemble.webp" alt="" className="absolute inset-0 h-full w-full object-cover opacity-70" />
      <div className="absolute inset-0 bg-gradient-to-t from-[#141518] via-[#141518]/70 to-transparent" />
      <div className="relative flex flex-wrap items-end gap-5 px-6 pb-5 pt-24">
        <div className="group relative">
          <div className="h-[104px] w-[104px] overflow-hidden rounded-full border-4 border-[#141518] text-[40px] font-medium text-black shadow-xl" style={{ background: A }}>
            {p?.avatar_url ? (
              // eslint-disable-next-line @next/next/no-img-element
              <img src={p.avatar_url} alt="" className="h-full w-full object-cover" />
            ) : <span className="flex h-full w-full items-center justify-center">{initial}</span>}
          </div>
          <button type="button" onClick={() => fileRef.current?.click()} title="Загрузить аватар" className="absolute inset-1 flex items-center justify-center rounded-full bg-black/55 text-white opacity-0 transition-opacity duration-200 group-hover:opacity-100">
            {upload.isPending ? <CircleDashed className="h-6 w-6 animate-spin" /> : <Camera className="h-6 w-6" />}
          </button>
          <input ref={fileRef} type="file" accept="image/*" className="hidden" onChange={(e) => { const f = e.target.files?.[0]; if (f) upload.mutate(f); e.target.value = ""; }} />
        </div>
        <div className="min-w-0 flex-1 pb-1">
          {editing ? (
            <form className="flex flex-wrap items-center gap-2" onSubmit={(e) => { e.preventDefault(); save.mutate({ name, handle }); }}>
              <input autoFocus value={name} onChange={(e) => setName(e.target.value)} placeholder="Имя" className="h-11 w-[240px] rounded-[10px] border border-white/10 bg-black/50 px-3 text-[20px] text-white outline-none focus:border-[#2dd4bf]/60" />
              <div className="flex h-11 items-center rounded-[10px] border border-white/10 bg-black/50 px-3 text-[15px] text-zinc-400 focus-within:border-[#2dd4bf]/60">@<input value={handle} onChange={(e) => setHandle(e.target.value)} className="w-[150px] bg-transparent text-white outline-none" /></div>
              <Btn type="submit" accent disabled={save.isPending || !name.trim()}><Check className="h-4 w-4" />Сохранить</Btn>
              <Btn onClick={() => setEditing(false)}>Отмена</Btn>
            </form>
          ) : (
            <>
              <div className="flex flex-wrap items-center gap-3">
                <h1 className="text-[30px] font-medium leading-tight text-white">{d.profile.isLoading ? "…" : p?.name}</h1>
                <span className="rounded-[8px] px-2.5 py-1 text-[13px] font-medium" style={{ background: A + "26", color: A }}>{p?.plan ?? "Pro"}<Sample /></span>
              </div>
              <div className="mt-1 text-[15px] text-zinc-400">@{p?.handle} · {p?.bio} · с {p?.created_at ? new Date(p.created_at).toLocaleDateString("ru-RU", { month: "long", year: "numeric" }) : "—"}</div>
            </>
          )}
          {err ? <div className="mt-1.5 text-[13px] text-red-300">{err}</div> : null}
        </div>
        {!editing ? (
          <div className="flex gap-2 pb-1">
            <Btn onClick={() => { setName(p?.name ?? ""); setHandle(p?.handle ?? ""); setEditing(true); }}><Pencil className="h-4 w-4" />Редактировать</Btn>
            <Btn onClick={() => fileRef.current?.click()}><Upload className="h-4 w-4" />Аватар</Btn>
            {p?.avatar_url ? <Btn danger onClick={() => delAv.mutate()} title="Удалить аватар"><Trash2 className="h-4 w-4" /></Btn> : null}
          </div>
        ) : null}
      </div>
    </div>
  );
}

/* ---------- page ---------- */
export function ProfilePage() {
  const d = useData();
  const [tab, setTab] = useState<TabId>("overview");
  useEffect(() => {
    const h = location.hash.replace("#", "") as TabId;
    if (TABS.some((t) => t.id === h)) setTab(h);
  }, []);
  const go = (t: TabId) => { setTab(t); history.replaceState(null, "", `#${t}`); };
  const body = useMemo(() => {
    switch (tab) {
      case "overview": return <Overview d={d} go={go} />;
      case "projects": return <Projects d={d} />;
      case "published": return <Projects d={d} onlyPublished />;
      case "nodes": return <MyNodes />;
      case "keys": return <Keys d={d} />;
      case "usage": return <Usage d={d} />;
      case "settings": return <Settings d={d} />;
      case "security": return <Security />;
      case "activity": return <ActivityTab d={d} />;
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [tab, d.profile.data, d.stats.data, d.projects.data, d.keys.data, d.outsee.data, d.llm.data, d.stats.isLoading, d.projects.isLoading, d.keys.isLoading]);
  return (
    <div data-profile-page className="min-h-screen bg-[#141518] text-zinc-200" style={FONT}>
      <UserPrefsApplier />
      <style>{`@keyframes vpFadeUp{from{opacity:0;transform:translateY(8px)}to{opacity:1;transform:none}}`}</style>
      <div className="mx-auto flex max-w-[1400px] flex-col gap-4 px-5 py-4">
        <div className="flex items-center gap-3">
          <a href="/" className="inline-flex items-center gap-2 rounded-[10px] bg-white/[0.05] px-3.5 py-2 text-[14px] text-zinc-200 transition-colors hover:bg-white/[0.09]"><ArrowLeft className="h-4 w-4" />В студию</a>
          {/* eslint-disable-next-line @next/next/no-img-element */}
          <img src="/via-logo.png" alt="VIA" className="h-6 opacity-80" />
          <div className="flex-1" />
          <span className="text-[13px] text-zinc-500">Профиль</span>
        </div>
        <Header d={d} />
        <div className="grid gap-4 lg:grid-cols-[250px_1fr]">
          <nav className="flex flex-row gap-1 overflow-x-auto lg:sticky lg:top-4 lg:flex-col lg:self-start">
            {TABS.map((t) => (
              <button key={t.id} type="button" data-profile-tab={t.id} onClick={() => go(t.id)}
                className="flex shrink-0 items-center gap-2.5 rounded-[11px] px-3.5 py-2.5 text-left text-[15px] transition-all duration-200"
                style={tab === t.id ? { background: A + "1f", color: A } : { color: "#a1a1aa" }}>
                {t.i}{t.t}
              </button>
            ))}
          </nav>
          <div key={tab} className="min-w-0" style={{ animation: "vpFadeUp .3s ease both" }}>{body}</div>
        </div>
      </div>
    </div>
  );
}

function AuthAccountBox() {
  const { user, loading } = useAuthUser();
  const [busy, setBusy] = useState(false);
  if (loading) return <div className="mt-1 text-[13px] text-zinc-500">Проверка входа…</div>;
  if (!user) return (
    <div className="mt-1 flex flex-wrap items-center gap-2">
      <span className="text-[13px] text-zinc-500">Вы не вошли в аккаунт</span>
      <a href="/login" className="rounded-[10px] border border-white/10 px-3 py-1.5 text-[13px] text-zinc-200 hover:bg-white/5">Войти</a>
      <a href="/register" className="rounded-[10px] border border-white/10 px-3 py-1.5 text-[13px] text-zinc-400 hover:bg-white/5">Регистрация</a>
    </div>
  );
  return (
    <div className="mt-1 flex flex-wrap items-center gap-3 rounded-[10px] bg-black/20 px-3 py-2.5" data-testid="auth-user">
      <div className="min-w-0 flex-1"><div className="text-[14px] text-white">{user.name || user.username} <span className="text-zinc-500">@{user.username}</span></div><div className="text-[12px] text-zinc-500">{user.email}{user.email_verified ? " · подтверждён" : " · не подтверждён"}{(user as any).plan ? ` · тариф ${(user as any).plan}` : ""}</div></div>
      <Btn danger disabled={busy} onClick={async () => { setBusy(true); await authLogout(); location.href = "/login"; }}><LogOut className="h-4 w-4" />Выйти</Btn>
    </div>
  );
}
