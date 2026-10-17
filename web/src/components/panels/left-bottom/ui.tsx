"use client";
import { useEffect, useState } from "react";

export const ACCENT = "#2dd4bf";

export function PanelShell({ title, onClose, children }: { title: string; onClose: () => void; children: React.ReactNode }) {
  const [shown, setShown] = useState(false);
  useEffect(() => { const t = requestAnimationFrame(() => setShown(true)); return () => cancelAnimationFrame(t); }, []);
  return (
    <div data-rail-panel={title} className="fixed bottom-3 left-[72px] z-50 flex max-h-[calc(100vh-24px)] w-[380px] flex-col rounded-[14px] border border-white/[0.08] bg-[#141417] shadow-2xl transition-all duration-200 ease-out" style={{ fontFamily: "Rubik, sans-serif", opacity: shown ? 1 : 0, transform: shown ? "translateX(0)" : "translateX(-8px)" }}>
      <div className="flex items-center justify-between border-b border-white/[0.06] px-4 py-3">
        <div className="flex items-center gap-2">
          <span className="text-[17px] font-medium text-white">{title}</span>
          <span className="rounded-md px-1.5 py-0.5 text-sm" style={{ color: ACCENT, background: "rgba(45,212,191,0.12)" }}>демо-данные</span>
        </div>
        <button type="button" onClick={onClose} aria-label="Закрыть" className="flex h-8 w-8 items-center justify-center rounded-lg text-[18px] text-zinc-400 transition-colors hover:bg-white/[0.06] hover:text-white">×</button>
      </div>
      <div className="min-h-0 flex-1 space-y-4 overflow-y-auto px-4 py-4">{children}</div>
    </div>
  );
}

export function Section({ title, children, right }: { title: string; children: React.ReactNode; right?: React.ReactNode }) {
  return (
    <div>
      <div className="mb-2 flex items-center justify-between"><span className="text-sm uppercase tracking-[0.1em] text-zinc-500">{title}</span>{right}</div>
      {children}
    </div>
  );
}

export function Btn({ children, primary, disabled, title }: { children: React.ReactNode; primary?: boolean; disabled?: boolean; title?: string }) {
  return (
    <button type="button" disabled={disabled} title={title} className="h-9 rounded-[10px] px-3 text-[14px] transition-colors disabled:cursor-not-allowed disabled:opacity-60" style={primary ? { background: "rgba(45,212,191,0.16)", color: ACCENT } : { background: "rgba(255,255,255,0.05)", color: "#d4d4d8" }}>
      {children}
    </button>
  );
}

export function Chips<T extends string>({ value, options, onChange }: { value: T; options: T[]; onChange: (v: T) => void }) {
  return (
    <div className="flex gap-1">
      {options.map((o) => (
        <button key={o} type="button" onClick={() => onChange(o)} className="rounded-lg px-2 py-1 text-sm transition-colors" style={o === value ? { background: "rgba(45,212,191,0.16)", color: ACCENT } : { background: "rgba(255,255,255,0.04)", color: "#a1a1aa" }}>{o}</button>
      ))}
    </div>
  );
}

export function StatusPill({ s }: { s: "успешно" | "ожидает" | "ошибка" }) {
  const c = s === "успешно" ? ACCENT : s === "ожидает" ? "#fbbf24" : "#f87171";
  return <span className="rounded-md px-1.5 py-0.5 text-sm" style={{ color: c, background: c + "1f" }}>{s}</span>;
}

export function Empty({ text }: { text: string }) {
  return <div className="rounded-[10px] bg-white/[0.03] px-3 py-6 text-center text-sm text-zinc-500">{text}</div>;
}

export const card = "rounded-[12px] border border-white/[0.06] bg-white/[0.03]";
