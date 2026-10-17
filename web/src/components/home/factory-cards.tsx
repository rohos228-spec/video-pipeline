"use client";
import type { ReactNode } from "react";
import { PLANS, fmtRub, fmtNum, type Plan } from "@/lib/pricing";

const T = "#5fd6bf";
const MAX = PLANS.find((p) => p.id === "conveyor")!.limits;
const TIER: Record<string, { lvl: number; tag: string; glow: string; ring: string; bg: string }> = {
  workshop: { lvl: 1, tag: "Ручная работа", glow: "0 0 0 rgba(0,0,0,0)", ring: "rgba(95,214,191,0.18)", bg: "radial-gradient(120% 80% at 0% 0%, rgba(43,184,160,0.07), transparent 60%)" },
  manufactory: { lvl: 2, tag: "Поточное производство", glow: "0 0 30px -12px rgba(43,184,160,0.35)", ring: "rgba(95,214,191,0.26)", bg: "radial-gradient(120% 80% at 100% 0%, rgba(43,184,160,0.10), transparent 60%)" },
  factory: { lvl: 3, tag: "Промышленный масштаб", glow: "0 0 44px -12px rgba(43,184,160,0.5)", ring: "rgba(95,214,191,0.36)", bg: "linear-gradient(160deg, rgba(43,184,160,0.12), rgba(255,255,255,0.015) 55%)" },
  conveyor: { lvl: 4, tag: "Флагман", glow: "0 0 70px -10px rgba(43,184,160,0.7)", ring: "rgba(95,214,191,0.6)", bg: "linear-gradient(160deg, rgba(43,184,160,0.18), rgba(10,30,28,0.6) 60%)" },
};

const S = { fill: "none", stroke: T, strokeWidth: 1.6, strokeLinecap: "round" as const, strokeLinejoin: "round" as const };
export function Emblem({ id, size = 72 }: { id: string; size?: number }) {
  const v = { width: size, height: size * 0.62, viewBox: "0 0 120 74" };
  if (id === "workshop") return (
    <svg {...v}><g {...S}><path d="M10 50h100M18 50v18M102 50v18M14 44h92" /><path d="M30 44l8-26 6 2-6 24" /><circle cx="41" cy="16" r="5" /><path d="M64 44V26M58 26h12l-2-8h-8z" /><path d="M84 44l10-22M90 20l8 4" opacity=".8" /><path d="M22 44c3-4 6-4 9 0" opacity=".5" /></g></svg>);
  if (id === "manufactory") return (
    <svg {...v}><g {...S}>{[16, 46, 76].map((x) => <g key={x}><rect x={x} y="22" width="26" height="30" rx="2" /><path d={`M${x + 4} 28v18M${x + 9} 28v18M${x + 14} 28v18M${x + 19} 28v18`} opacity=".55" /></g>)}<path d="M8 60h104" /><circle cx="104" cy="14" r="7" /><circle cx="104" cy="14" r="2.5" /><path d="M104 4v3M104 21v3M94 14h3M111 14h3" /></g></svg>);
  if (id === "factory") return (
    <svg {...v}><g {...S}><path d="M8 66V36l18 10V36l18 10V36l18 10V18h10v-8h8v8h10v48z" /><path d="M76 10V4M86 10V2" opacity=".5" /><path d="M8 66h104" /><path d="M20 56h8M36 56h8M52 56h8M88 40h12M88 50h12" opacity=".7" /></g><g fill={T} opacity=".35"><circle cx="78" cy="2" r="2" className="via-smoke" /><circle cx="88" cy="0" r="2.5" className="via-smoke via-d2" /></g></svg>);
  return (
    <svg {...v}><g {...S}><rect x="6" y="40" width="108" height="14" rx="7" /><circle cx="13" cy="47" r="4" /><circle cx="107" cy="47" r="4" /><path d="M18 60v8M60 60v8M102 60v8M12 68h96" opacity=".6" /><path d="M54 8h12v10l-6 6-6-6z" /></g>
      <g className="via-belt">{[0, 1, 2, 3, 4, 5].map((i) => <rect key={i} x={i * 24 - 20} y="28" width="12" height="11" rx="2" fill="rgba(95,214,191,0.18)" stroke={T} strokeWidth="1.2" />)}</g></svg>);
}

const PERK_ICON = (t: string): ReactNode => {
  const p = (d: string) => <svg width="14" height="14" viewBox="0 0 16 16"><path d={d} {...S} strokeWidth={1.4} /></svg>;
  const s = t.toLowerCase();
  if (s.includes("оркестр") || s.includes("агент")) return p("M8 2v3M8 11v3M2 8h3M11 8h3M8 8m-3 0a3 3 0 106 0a3 3 0 10-6 0");
  if (s.includes("шаблон")) return p("M3 3h10v10H3zM3 7h10M7 7v6");
  if (s.includes("отчёт") || s.includes("аналит")) return p("M3 13V8M7 13V4M11 13V6M2 14h12");
  if (s.includes("промт") || s.includes("стил")) return p("M8 2l1.8 3.8L14 6.4l-3 2.9.7 4.1L8 11.5l-3.7 1.9.7-4.1-3-2.9 4.2-.6z");
  if (s.includes("распис") || s.includes("очеред")) return p("M8 2a6 6 0 100 12A6 6 0 008 2zM8 5v3l2 2");
  if (s.includes("бэкап")) return p("M3 5h10v8H3zM5 5V3h6v2M6 9h4");
  if (s.includes("команд")) return p("M5 7a2 2 0 100-4 2 2 0 000 4zM11 7a2 2 0 100-4 2 2 0 000 4zM1 14c0-3 8-3 8 0M7 14c0-3 8-3 8 0");
  if (s.includes("публик") || s.includes("экспорт")) return p("M8 11V2M4 6l4-4 4 4M3 14h10");
  if (s.includes("api") || s.includes("механ")) return p("M5 4L1 8l4 4M11 4l4 4-4 4M9 3l-2 10");
  if (s.includes("поддерж")) return p("M3 9V8a5 5 0 0110 0v1M3 9h2v4H3zM11 9h2v4h-2z");
  return p("M3 8l3 3 7-7");
};


export const FactoryStyles = () => <style>{`
@keyframes via-belt { from { transform: translateX(0) } to { transform: translateX(24px) } }
.via-belt { animation: via-belt 3.2s linear infinite }
@keyframes via-smoke { 0% { transform: translateY(6px); opacity: 0 } 40% { opacity: .4 } 100% { transform: translateY(-6px); opacity: 0 } }
.via-smoke { animation: via-smoke 4s ease-in-out infinite } .via-d2 { animation-delay: 2s }
@keyframes via-shine { 0%,100% { opacity: .55 } 50% { opacity: 1 } }
.via-flag { animation: via-shine 5s ease-in-out infinite }
@media (prefers-reduced-motion: reduce) { .via-belt,.via-smoke,.via-flag { animation: none } }
`}</style>;

const pattern = (id: string) =>
  id === "factory" ? "repeating-linear-gradient(135deg, rgba(95,214,191,0.05) 0 8px, transparent 8px 16px)"
  : id === "conveyor" ? "linear-gradient(rgba(95,214,191,0.05) 1px, transparent 1px), linear-gradient(90deg, rgba(95,214,191,0.05) 1px, transparent 1px)"
  : id === "manufactory" ? "repeating-linear-gradient(90deg, rgba(95,214,191,0.035) 0 1px, transparent 1px 14px)"
  : "none";

const Gauge = ({ k, v, max, lvl }: { k: string; v: number; max: number; lvl: number }) => (
  <div>
    <div className="flex items-baseline justify-between"><span className="text-[11px] uppercase tracking-wider text-white/45">{k}</span><span className="text-lg font-semibold text-white tabular-nums">{v}</span></div>
    <div className="mt-1 h-1.5 overflow-hidden rounded-full bg-white/[0.06]"><div className="h-full rounded-full transition-all duration-700" style={{ width: `${Math.max(6, (v / max) * 100)}%`, background: `linear-gradient(90deg, rgba(43,184,160,${0.4 + lvl * 0.12}), ${T})` }} /></div>
  </div>
);

export function FactoryCard({ p }: { p: Plan }) {
  const t = TIER[p.id]; const l = p.limits; const flag = p.id === "conveyor";
  return (
    <div className="group relative flex h-full flex-col overflow-hidden rounded-[1.6rem] p-6 transition duration-500 hover:-translate-y-1"
      style={{ background: t.bg, boxShadow: `inset 0 0 0 1px ${t.ring}, ${t.glow}` }}>
      <div className="pointer-events-none absolute inset-0" style={{ backgroundImage: pattern(p.id), backgroundSize: p.id === "conveyor" ? "22px 22px" : p.id === "workshop" ? "14px 14px" : undefined }} />
      {t.lvl >= 3 && <div className="pointer-events-none absolute inset-x-0 top-0 h-1" style={{ background: "repeating-linear-gradient(135deg, rgba(95,214,191,0.5) 0 6px, transparent 6px 12px)" }} />}
      {flag && <div className="via-flag pointer-events-none absolute -inset-px rounded-[1.6rem]" style={{ boxShadow: "inset 0 0 0 1px rgba(95,214,191,0.7), inset 0 0 40px -18px rgba(95,214,191,0.6)" }} />}
      <div className="relative flex items-start justify-between gap-3">
        <div>
          <div className="text-[11px] uppercase tracking-[0.2em] text-[#5fd6bf]/70">{"▮".repeat(t.lvl)}<span className="text-white/15">{"▮".repeat(4 - t.lvl)}</span> · {t.tag}</div>
          <div className="mt-1 text-2xl font-semibold text-white">{p.name}</div>
          <div className="text-sm text-white/45">{p.note}</div>
        </div>
        {flag && <span className="rounded-full px-2.5 py-0.5 text-xs text-[#062a24]" style={{ background: "linear-gradient(90deg,#5fd6bf,#2bb8a0)" }}>Флагман</span>}
      </div>
      <div className="relative mt-3 opacity-90 transition duration-500 group-hover:opacity-100"><Emblem id={p.id} size={110} /></div>
      <div className="relative mt-2 text-3xl font-bold text-white">{fmtRub(p.price)}<span className="text-base font-normal text-white/40"> / месяц</span></div>
      <div className="relative text-sm text-white/55">{fmtNum(l.credits)} кредитов · {l.storage}</div>
      <div className="relative mt-4 grid grid-cols-2 gap-x-4 gap-y-3 rounded-2xl bg-black/20 p-3 ring-1 ring-white/[0.05]">
        <div className="col-span-2 text-[11px] uppercase tracking-[0.2em] text-[#5fd6bf]/70">Потоки и агенты</div>
        <Gauge k="Потоки LLM" v={l.llm} max={MAX.llm} lvl={t.lvl} />
        <Gauge k="Агентов" v={l.agents ?? 0} max={MAX.agents ?? 1} lvl={t.lvl} />
        <Gauge k="Потоки картинок" v={l.img} max={MAX.img} lvl={t.lvl} />
        <Gauge k="Потоки видео" v={l.vid} max={MAX.vid} lvl={t.lvl} />
      </div>
      <ul className="relative mt-4 space-y-1.5 text-sm text-white/75">
        {p.base && <li className="text-[#5fd6bf]/80">Всё из {p.base}, плюс:</li>}
        {p.perks?.map((x) => <li key={x} className="flex items-center gap-2"><span className="grid h-6 w-6 shrink-0 place-items-center rounded-lg bg-[#2bb8a0]/10 ring-1 ring-[#2bb8a0]/20">{PERK_ICON(x)}</span>{x}</li>)}
      </ul>
      <div className="relative mt-auto pt-6"><a href={`/register?plan=${p.id}`} className={`flex h-11 w-full items-center justify-center rounded-xl text-[15px] font-medium ring-1 transition duration-300 ${flag ? "bg-[#2bb8a0]/25 text-[#d5f8f0] ring-[#5fd6bf]/50 hover:bg-[#2bb8a0]/35" : "bg-white/[0.04] text-white/80 ring-[#2bb8a0]/25 hover:bg-[#2bb8a0]/15 hover:text-[#bff3e8]"}`}>Запустить {p.name.toLowerCase()}</a></div>
    </div>
  );
}

export function FactoryMini({ p, current }: { p: Plan; current?: boolean }) {
  const t = TIER[p.id]; const l = p.limits; const flag = p.id === "conveyor";
  return (
    <a href={`/register?plan=${p.id}`} className="relative block overflow-hidden rounded-[12px] p-2.5 transition duration-300 hover:-translate-y-0.5"
      style={{ background: t.bg, boxShadow: `inset 0 0 0 1px ${current ? "rgba(45,212,191,0.6)" : t.ring}, ${t.glow}` }}>
      <div className="pointer-events-none absolute inset-0" style={{ backgroundImage: pattern(p.id), backgroundSize: p.id === "conveyor" ? "22px 22px" : p.id === "workshop" ? "14px 14px" : undefined }} />
      {t.lvl >= 3 && <div className="pointer-events-none absolute inset-x-0 top-0 h-0.5" style={{ background: "repeating-linear-gradient(135deg, rgba(95,214,191,0.5) 0 5px, transparent 5px 10px)" }} />}
      <div className="relative flex items-center gap-2"><Emblem id={p.id} size={40} /><div className="min-w-0 flex-1"><div className="flex items-center gap-1.5 text-[14px] text-white">{p.name}{flag && <span className="rounded-full bg-[#2bb8a0]/25 px-1.5 text-[10px] text-[#bff3e8]">флагман</span>}</div><div className="text-[12px] text-[#5fd6bf]">{fmtRub(p.price)}<span className="text-zinc-500"> /мес</span></div></div></div>
      <div className="relative mt-1.5 text-[11px] text-zinc-400">{fmtNum(l.credits)} кр. · {l.storage}</div>
      <div className="relative mt-1 text-[10px] uppercase tracking-wider text-zinc-500">Потоки</div><div className="relative mt-0.5 space-y-1">{([["LLM", l.llm, MAX.llm], ["Агенты", l.agents ?? 0, MAX.agents ?? 1], ["Карт.", l.img, MAX.img], ["Видео", l.vid, MAX.vid]] as const).map(([k, v, m]) => (
        <div key={k} className="flex items-center gap-1.5 text-[11px]"><span className="w-11 text-zinc-500">{k}</span><div className="h-1 flex-1 rounded-full bg-white/[0.06]"><div className="h-full rounded-full" style={{ width: `${Math.max(6, (v / m) * 100)}%`, background: T }} /></div><span className="w-5 text-right text-zinc-300 tabular-nums">{v}</span></div>))}</div>
      <div className="relative mt-1.5 text-[11px] leading-snug text-zinc-500">{p.base ? `Всё из ${p.base} + ` : ""}{p.perks?.map((x) => x[0].toLowerCase() + x.slice(1)).join(", ")}</div>
      {current ? <div className="relative mt-1 text-sm text-[#5fd6bf]">Текущий</div> : null}
    </a>
  );
}

export const IndustrialDivider = () => (
  <div className="my-4 flex items-center gap-3" aria-hidden>
    <div className="h-px flex-1 bg-gradient-to-r from-transparent to-[#2bb8a0]/40" />
    <div className="h-2 w-24 rounded-sm" style={{ background: "repeating-linear-gradient(135deg, rgba(95,214,191,0.45) 0 6px, transparent 6px 12px)" }} />
    <svg width="18" height="18" viewBox="0 0 16 16"><circle cx="8" cy="8" r="5" {...S} /><circle cx="8" cy="8" r="1.6" fill={T} /></svg>
    <div className="h-2 w-24 rounded-sm" style={{ background: "repeating-linear-gradient(45deg, rgba(95,214,191,0.45) 0 6px, transparent 6px 12px)" }} />
    <div className="h-px flex-1 bg-gradient-to-l from-transparent to-[#2bb8a0]/40" />
  </div>
);
