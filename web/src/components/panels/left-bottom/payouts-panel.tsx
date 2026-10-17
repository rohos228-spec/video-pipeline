"use client";
import { useState } from "react";
import { PanelShell, Section, Btn, Chips, StatusPill, Empty, card, ACCENT } from "./ui";

type Row = { d: string; a: number; m: string; s: "успешно" | "ожидает" | "ошибка" };
const HISTORY: Row[] = [
  { d: "12.10", a: 4200, m: "Карта", s: "ожидает" },
  { d: "28.09", a: 7800, m: "СБП", s: "успешно" },
  { d: "10.09", a: 3100, m: "USDT", s: "успешно" },
  { d: "02.09", a: 1500, m: "Карта", s: "ошибка" },
];

export function PayoutsPanel({ onClose }: { onClose: () => void }) {
  const [f, setF] = useState<"Все" | "успешно" | "ожидает" | "ошибка">("Все");
  const [method, setMethod] = useState<"Карта" | "СБП" | "USDT">("Карта");
  const rows = HISTORY.filter((r) => f === "Все" || r.s === f);
  const stats = [["Доступно", "6 350 ₽", true], ["В холде", "4 200 ₽", false], ["Выплачено", "10 900 ₽", false]] as const;
  return (
    <PanelShell title="Выплаты" onClose={onClose}>
      <div className="grid grid-cols-3 gap-2">
        {stats.map(([l, v, a]) => (
          <div key={l} className={card + " p-2.5"}><div className="text-sm text-zinc-500">{l}</div><div className="mt-0.5 text-[16px] font-medium" style={{ color: a ? ACCENT : "#fff" }}>{v}</div></div>
        ))}
      </div>
      <div className="flex items-center justify-between"><span className="text-sm text-zinc-500">Минимум к выводу: 1 000 ₽</span><Btn primary disabled title="скоро">Запросить выплату</Btn></div>
      <Section title="Реквизиты" right={<Chips value={method} options={["Карта", "СБП", "USDT"]} onChange={setMethod} />}>
        <div className={card + " flex items-center justify-between px-3 py-2.5"}>
          <span className="text-[14px] text-zinc-300">{method === "Карта" ? "•••• 8812" : method === "СБП" ? "+7 ••• ••• 41 07" : "TQ3x…9fA2"}</span>
          <span className="text-sm" style={{ color: ACCENT }}>верифицировано</span>
        </div>
      </Section>
      <Section title="Источники дохода">
        <div className="space-y-1">
          {[["Продажи нод в каталоге", "12 продаж", "5 400 ₽"], ["Продажи промтов", "31 продажа", "3 850 ₽"], ["Рефералы (10%)", "7 человек", "1 650 ₽"]].map(([a, b, c]) => (
            <div key={a} className="flex items-center gap-2 rounded-[10px] px-2 py-2 hover:bg-white/[0.04]"><span className="flex-1 text-sm text-zinc-300">{a}</span><span className="text-sm text-zinc-500">{b}</span><span className="w-16 text-right text-sm text-white">{c}</span></div>
          ))}
        </div>
      </Section>
      <Section title="История выплат" right={<Chips value={f} options={["Все", "успешно", "ожидает", "ошибка"]} onChange={setF} />}>
        {rows.length === 0 ? <Empty text="Выплат ещё не было" /> : (
          <div className="space-y-1">
            {rows.map((r, i) => (
              <div key={i} className="flex items-center gap-2 rounded-[10px] px-2 py-2 hover:bg-white/[0.04]">
                <span className="w-10 text-sm text-zinc-500">{r.d}</span><span className="flex-1 text-sm text-zinc-300">{r.m}</span>
                <span className="text-sm text-white">{r.a.toLocaleString("ru-RU")} ₽</span><StatusPill s={r.s} />
              </div>
            ))}
          </div>
        )}
      </Section>
    </PanelShell>
  );
}
