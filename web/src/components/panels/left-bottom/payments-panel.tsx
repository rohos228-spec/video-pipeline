"use client";
import { useEffect, useState } from "react";
import { PLANS, PLAN_GROUPS, fmtRub, limitRows } from "@/lib/pricing";
import { FactoryMini, FactoryStyles } from "@/components/home/factory-cards";
import { PanelShell, Section, Btn, Chips, StatusPill, Empty, card, ACCENT } from "./ui";

type Row = { d: string; t: string; a: number; s: "успешно" | "ожидает" | "ошибка" };
const HISTORY: Row[] = [
  { d: "14.10", t: "Пополнение 2000 кр.", a: 2000, s: "успешно" },
  { d: "13.10", t: "Генерация: картинки", a: -340, s: "успешно" },
  { d: "11.10", t: "Генерация: озвучка", a: -120, s: "успешно" },
  { d: "09.10", t: "Пополнение 500 кр.", a: 500, s: "ожидает" },
  { d: "05.10", t: "Пополнение 500 кр.", a: 500, s: "ошибка" },
];
const CURRENT_PLAN = "start";

export function PaymentsPanel({ onClose }: { onClose: () => void }) {
  const [f, setF] = useState<"Все" | "Пополнения" | "Списания">("Все");
  const [projects, setProjects] = useState<{ id: number; title: string; status: string }[]>([]);
  useEffect(() => { fetch("/api/projects").then((r) => r.json()).then((j) => Array.isArray(j) && setProjects(j.slice(0, 5))).catch(() => {}); }, []);
  const rows = HISTORY.filter((r) => f === "Все" || (f === "Пополнения" ? r.a > 0 : r.a < 0));
  return (
    <PanelShell title="Оплата" onClose={onClose}>
      <div className={card + " p-4"}>
        <div className="text-sm text-zinc-500">Баланс</div>
        <div className="mt-1 text-[28px] font-medium text-white">2 540 <span className="text-[15px] text-zinc-500">кредитов</span></div>
        <div className="mt-3 h-1.5 overflow-hidden rounded-full bg-white/[0.06]"><div className="h-full rounded-full" style={{ width: "38%", background: ACCENT }} /></div>
        <div className="mt-1.5 text-sm text-zinc-500">Израсходовано за месяц: 1 560 из 4 100</div>
        <div className="mt-3 flex gap-2"><Btn primary disabled title="скоро">Пополнить</Btn>{["500", "2000", "10000"].map((v) => <Btn key={v} disabled title="скоро">+{v}</Btn>)}</div>
      </div>
      <Section title="Тариф">
        <FactoryStyles />
        {(["trial", "factory"] as const).map((g) => (
          <div key={g} className="mb-3">
            <div className="mb-1.5 text-sm text-zinc-400">{PLAN_GROUPS[g].title} <span className="text-zinc-600">· {g === "trial" ? "разово" : "в месяц"}</span></div>
            <div className="grid grid-cols-2 gap-2">
              {PLANS.filter((p) => p.group === g).map((p) => {
                if (g === "factory") return <FactoryMini key={p.id} p={p} current={p.id === CURRENT_PLAN} />;
                const cur = p.id === CURRENT_PLAN;
                return (
                  <a key={p.id} href={`/register?plan=${p.id}`} className={card + " block p-2.5 transition-colors duration-200 hover:bg-white/[0.05]"} style={cur ? { borderColor: "rgba(45,212,191,0.5)" } : undefined}>
                    <div className="flex items-baseline justify-between"><span className="text-[14px] text-white">{p.name}</span><span className="text-sm" style={{ color: cur ? ACCENT : "#a1a1aa" }}>{fmtRub(p.price)}</span></div>
                    <ul className="mt-1.5 space-y-0.5 text-[12px] text-zinc-500">{limitRows(p.limits).map(([k, v]) => <li key={k} className="flex justify-between gap-2"><span>{k}</span><span className="text-zinc-300">{v}</span></li>)}</ul>
                    {p.perks ? <div className="mt-1.5 text-[12px] text-zinc-500">{p.base ? `Всё из ${p.base} + ` : ""}{p.perks.map((x) => x[0].toLowerCase() + x.slice(1)).join(", ")}</div> : null}
                    {cur ? <div className="mt-1.5 text-sm" style={{ color: ACCENT }}>Текущий</div> : null}
                  </a>
                );
              })}
            </div>
          </div>
        ))}
      </Section>
      <Section title="Способ оплаты">
        <div className={card + " flex items-center justify-between px-3 py-2.5"}><span className="text-[14px] text-zinc-300">Карта •••• 4242</span><Btn disabled title="скоро">Изменить</Btn></div>
      </Section>
      <Section title="История" right={<Chips value={f} options={["Все", "Пополнения", "Списания"]} onChange={setF} />}>
        {rows.length === 0 ? <Empty text="Платежей пока нет" /> : (
          <div className="space-y-1">
            {rows.map((r, i) => (
              <div key={i} className="flex items-center gap-2 rounded-[10px] px-2 py-2 transition-colors hover:bg-white/[0.04]">
                <span className="w-10 text-sm text-zinc-500">{r.d}</span>
                <span className="flex-1 truncate text-sm text-zinc-300">{r.t}</span>
                <span className="text-sm" style={{ color: r.a > 0 ? ACCENT : "#e4e4e7" }}>{r.a > 0 ? "+" : "−"}{Math.abs(r.a)}</span>
                <StatusPill s={r.s} />
              </div>
            ))}
          </div>
        )}
      </Section>
      <Section title="Расход по проектам">
        {projects.length === 0 ? <Empty text="Проектов нет" /> : (
          <div className="space-y-1">
            {projects.map((p, i) => (
              <div key={p.id} className="flex items-center gap-2 px-2 py-1.5 text-sm">
                <span className="flex-1 truncate text-zinc-300">{p.title}</span>
                <span className="text-sm text-zinc-500">{p.status}</span>
                <span className="w-14 text-right text-zinc-400">~{(i + 2) * 110} кр.</span>
              </div>
            ))}
            <div className="px-2 text-sm text-zinc-600">Проекты реальные, суммы — пример.</div>
          </div>
        )}
      </Section>
    </PanelShell>
  );
}
