"use client";

import { useEffect, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { CheckCircle2, CircleAlert, CircleDashed, LogOut, X, Sparkles, Image as ImageIcon, Mic, Bot } from "lucide-react";

const ACCENT = "#2dd4bf";
const PREFS_KEY = "vp-user-prefs";

type Prefs = { theme: "dark" | "dim"; lang: "ru" | "en"; fontScale: 100 | 110 | 120 };
const DEFAULT_PREFS: Prefs = { theme: "dark", lang: "ru", fontScale: 100 };

async function getJson<T>(url: string): Promise<T> {
  const r = await fetch(url);
  if (!r.ok) throw new Error(`HTTP ${r.status}`);
  return r.json() as Promise<T>;
}

type OutseeStatus = { configured?: boolean; balance?: { balance_credits?: number } | null; default_image_model?: string; default_video_model?: string };
type TextLlm = { active_label?: string; models?: { key_configured?: boolean; active?: boolean }[] };
type Project = { id: number; status: string; updated_at?: string };

function Sample() {
  return <span className="ml-1.5 rounded-md bg-white/[0.06] px-1.5 py-0.5 text-sm font-normal text-zinc-400">пример</span>;
}

function Card({ title, children, right }: { title: string; children: React.ReactNode; right?: React.ReactNode }) {
  return (
    <section className="rounded-[14px] border border-white/[0.07] bg-white/[0.03] p-3.5">
      <div className="mb-2.5 flex items-center justify-between">
        <span className="text-sm font-medium uppercase tracking-[0.08em] text-zinc-400">{title}</span>
        {right}
      </div>
      {children}
    </section>
  );
}

function Seg<T extends string | number>({ value, options, onChange }: { value: T; options: { v: T; t: string }[]; onChange: (v: T) => void }) {
  return (
    <div className="flex gap-1 rounded-[10px] bg-black/25 p-1">
      {options.map((o) => (
        <button key={String(o.v)} type="button" onClick={() => onChange(o.v)}
          className="flex-1 rounded-[8px] px-2.5 py-1.5 text-sm transition-all duration-200"
          style={value === o.v ? { background: ACCENT + "26", color: ACCENT } : { color: "#a1a1aa" }}>
          {o.t}
        </button>
      ))}
    </div>
  );
}

function ConnRow({ icon, name, state, detail }: { icon: React.ReactNode; name: string; state: "ok" | "off" | "loading" | "error"; detail: string }) {
  const st = {
    ok: { i: <CheckCircle2 className="h-4 w-4" style={{ color: ACCENT }} />, t: "подключено" },
    off: { i: <CircleAlert className="h-4 w-4 text-amber-400" />, t: "нет ключа" },
    error: { i: <CircleAlert className="h-4 w-4 text-red-400" />, t: "ошибка" },
    loading: { i: <CircleDashed className="h-4 w-4 animate-spin text-zinc-500" />, t: "проверка…" },
  }[state];
  return (
    <div className="flex items-center gap-3 rounded-[10px] bg-black/20 px-3 py-2.5">
      <span className="flex h-8 w-8 items-center justify-center rounded-[8px] bg-white/[0.05] text-zinc-300">{icon}</span>
      <div className="min-w-0 flex-1">
        <div className="text-[14px] text-white">{name}</div>
        <div className="truncate text-sm text-zinc-500">{detail}</div>
      </div>
      <span className="flex items-center gap-1.5 text-sm text-zinc-400">{st.i}{st.t}</span>
    </div>
  );
}

export function applyUserPrefs(p: Partial<Prefs>) {
  if (typeof document === "undefined") return;
  const scale = p.fontScale ?? 100;
  const root = document.documentElement;
  root.style.setProperty("--vp-font-scale", String(scale / 100));
  root.style.fontSize = scale === 100 ? "" : `${scale}%`;
  // большинство размеров в px — масштабируем через zoom (Chromium)
  (root.style as CSSStyleDeclaration & { zoom: string }).zoom = scale === 100 ? "" : String(scale / 100);
  root.lang = p.lang === "en" ? "en" : "ru";
}

/** Применяет сохранённые настройки при загрузке приложения (монтируется в рейле). */
export function UserPrefsApplier() {
  useEffect(() => {
    try { const s = localStorage.getItem(PREFS_KEY); if (s) applyUserPrefs(JSON.parse(s)); } catch {}
  }, []);
  return null;
}

export function UserPanel({ onClose }: { onClose: () => void }) {
  const [shown, setShown] = useState(false);
  const [prefs, setPrefs] = useState<Prefs>(DEFAULT_PREFS);
  useEffect(() => {
    requestAnimationFrame(() => setShown(true));
    try { const s = localStorage.getItem(PREFS_KEY); if (s) setPrefs({ ...DEFAULT_PREFS, ...JSON.parse(s) }); } catch {}
    const onKey = (e: KeyboardEvent) => { if (e.key === "Escape") onClose(); };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);
  const setP = (p: Partial<Prefs>) => setPrefs((v) => { const n = { ...v, ...p }; try { localStorage.setItem(PREFS_KEY, JSON.stringify(n)); } catch {} applyUserPrefs(n); return n; });

  const outsee = useQuery({ queryKey: ["user-panel", "outsee"], queryFn: () => getJson<OutseeStatus>("/api/outsee/status"), staleTime: 30_000 });
  const llm = useQuery({ queryKey: ["user-panel", "llm"], queryFn: () => getJson<TextLlm>("/api/text-llm"), staleTime: 30_000 });
  const auth = useQuery({ queryKey: ["user-panel", "auth"], queryFn: () => getJson<{ auth_required: boolean }>("/api/auth/status"), staleTime: 60_000 });
  const projects = useQuery({ queryKey: ["user-panel", "projects"], queryFn: () => getJson<Project[]>("/api/projects"), staleTime: 30_000 });

  const credits = outsee.data?.balance?.balance_credits;
  const llmOk = Boolean(llm.data?.models?.some((m) => m.active && m.key_configured));
  const plist = projects.data ?? [];
  const weekAgo = Date.now() - 7 * 864e5;
  const activeWeek = plist.filter((p) => p.updated_at && Date.parse(p.updated_at) > weekAgo).length;
  const done = plist.filter((p) => /done|publish|ready_final|complete/i.test(p.status)).length;
  const authRequired = auth.data?.auth_required ?? false;

  const logout = () => {
    try { localStorage.removeItem("vp-auth-token"); sessionStorage.clear(); } catch {}
    window.location.reload();
  };

  return (
    <div data-user-panel
      className="fixed bottom-3 left-[72px] z-50 flex max-h-[calc(100vh-24px)] w-[420px] flex-col overflow-hidden rounded-[16px] border border-white/10 bg-[#16171a] shadow-2xl transition-all duration-200 ease-out"
      style={{ fontFamily: "Rubik, sans-serif", opacity: shown ? 1 : 0, transform: shown ? "translateY(0)" : "translateY(8px)" }}>
      <div className="flex items-center gap-3 border-b border-white/[0.06] px-4 py-3.5">
        <div className="flex h-12 w-12 items-center justify-center rounded-full text-[18px] font-medium text-black" style={{ background: ACCENT }}>A</div>
        <div className="min-w-0 flex-1">
          <div className="text-[17px] font-medium text-white">Admin</div>
          <div className="text-sm text-zinc-500">Локальная студия · housepc</div>
        </div>
        <span className="rounded-[8px] px-2.5 py-1 text-sm font-medium" style={{ background: ACCENT + "1f", color: ACCENT }}>Pro<Sample /></span>
        <button type="button" onClick={onClose} aria-label="Закрыть" className="flex h-8 w-8 items-center justify-center rounded-[8px] text-zinc-500 transition-colors hover:bg-white/[0.06] hover:text-white"><X className="h-4 w-4" /></button>
      </div>

      <div className="flex flex-col gap-2.5 overflow-y-auto p-3">
        <Card title="Баланс">
          <div className="grid grid-cols-2 gap-2">
            <div className="rounded-[10px] bg-black/20 px-3 py-2.5">
              <div className="text-sm text-zinc-500">Баланс VIA, кредиты</div>
              <div className="text-[22px] font-medium text-white">{outsee.isLoading ? "…" : credits != null ? credits.toLocaleString("ru-RU") : "—"}</div>
            </div>
            <div className="rounded-[10px] bg-black/20 px-3 py-2.5">
              <div className="text-sm text-zinc-500">Расход за месяц<Sample /></div>
              <div className="text-[22px] font-medium text-white">$42</div>
            </div>
          </div>
        </Card>

        <Card title="Ключи и подключения">
          <div className="flex flex-col gap-1.5">
            <ConnRow icon={<Bot className="h-4 w-4" />} name="GPT (текст)" state={llm.isLoading ? "loading" : llm.isError ? "error" : llmOk ? "ok" : "off"} detail={llm.data?.active_label ?? "активная модель не выбрана"} />
            <ConnRow icon={<ImageIcon className="h-4 w-4" />} name="VIA Render" state={outsee.isLoading ? "loading" : outsee.isError ? "error" : outsee.data?.configured ? "ok" : "off"} detail={[outsee.data?.default_image_model, outsee.data?.default_video_model].filter(Boolean).join(" · ") || "картинки и видео"} />
            <ConnRow icon={<Mic className="h-4 w-4" />} name="ElevenLabs" state="off" detail="статус не отдаётся бэкендом (пример)" />
          </div>
        </Card>

        <Card title="Статистика">
          <div className="grid grid-cols-3 gap-2">
            {[
              { l: "Проектов", v: projects.isLoading ? "…" : String(plist.length) },
              { l: "За неделю", v: projects.isLoading ? "…" : String(activeWeek) },
              { l: "Готово", v: projects.isLoading ? "…" : String(done) },
            ].map((s) => (
              <div key={s.l} className="rounded-[10px] bg-black/20 px-3 py-2.5">
                <div className="text-[20px] font-medium text-white">{s.v}</div>
                <div className="text-sm text-zinc-500">{s.l}</div>
              </div>
            ))}
          </div>
          <div className="mt-2 flex items-center gap-2 text-sm text-zinc-500"><Sparkles className="h-3.5 w-3.5" style={{ color: ACCENT }} />Генераций за месяц: 1 284<Sample /></div>
        </Card>

        <Card title="Настройки">
          <div className="flex flex-col gap-2.5">
            <div><div className="mb-1.5 text-sm text-zinc-400">Язык <span className="text-zinc-600">(перевод интерфейса — позже)</span></div><Seg value={prefs.lang} onChange={(v) => setP({ lang: v })} options={[{ v: "ru", t: "Русский" }, { v: "en", t: "English" }]} /></div>
            <div><div className="mb-1.5 text-sm text-zinc-400">Размер шрифта</div><Seg value={prefs.fontScale} onChange={(v) => setP({ fontScale: v })} options={[{ v: 100, t: "100%" }, { v: 110, t: "110%" }, { v: 120, t: "120%" }]} /></div>
            <div className="text-sm text-zinc-600">Размер шрифта применяется сразу ко всему интерфейсу и сохраняется.</div>
          </div>
        </Card>

        <button type="button" onClick={logout} disabled={!authRequired}
          title={authRequired ? "Выйти из аккаунта" : "Вход не требуется: авторизация на сервере выключена"}
          className="flex items-center justify-center gap-2 rounded-[12px] bg-white/[0.04] px-4 py-3 text-[14px] text-zinc-300 transition-colors hover:bg-red-500/10 hover:text-red-300 disabled:cursor-not-allowed disabled:opacity-50 disabled:hover:bg-white/[0.04] disabled:hover:text-zinc-300">
          <LogOut className="h-4 w-4" />Выйти{!authRequired ? " (вход не требуется)" : ""}
        </button>
      </div>
    </div>
  );
}
