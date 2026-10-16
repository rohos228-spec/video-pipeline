"use client";
import { parseParamDiff, parseUnifiedDiff, type DiffLineKind } from "@/lib/change-control";
import { cn } from "@/lib/utils";

const LINE_CLASS: Record<DiffLineKind, string> = {
  add: "bg-emerald-500/10 text-emerald-100",
  del: "bg-red-500/10 text-red-200",
  ctx: "text-white/55",
  hunk: "text-sky-200/60",
  meta: "text-white/35",
};
const GUTTER: Record<DiffLineKind, string> = { add: "+", del: "−", ctx: " ", hunk: "@", meta: " " };

export function TextDiff({ diff, label = "Изменение текста" }: { diff: string; label?: string }) {
  if (!diff.trim()) return <p className="text-white/50">Различий в тексте нет или diff не получен.</p>;
  const { lines, added, removed, truncated } = parseUnifiedDiff(diff);
  const shown = lines.filter((l) => l.kind !== "meta");
  return (
    <div className="space-y-1" data-testid="change-text-diff">
      <p className="text-white/60">
        {label}: <span className="text-emerald-200">+{added} добавлено</span> · <span className="text-red-300">−{removed} удалено</span>
        <span className="text-white/40"> (показаны изменённые строки и по 3 строки вокруг)</span>
      </p>
      <div role="region" aria-label={label} tabIndex={0}
        className="max-h-72 overflow-auto rounded border border-white/10 bg-black/30 py-1 font-mono text-[11px] leading-snug">
        {shown.map((l, i) => (
          <div key={i} className={cn("flex gap-2 px-2", LINE_CLASS[l.kind])}>
            <span aria-hidden className="w-3 shrink-0 select-none text-center text-white/40">{GUTTER[l.kind]}</span>
            <span className="min-w-0 flex-1 whitespace-pre-wrap break-words">{l.text === "" ? " " : l.text}</span>
          </div>
        ))}
      </div>
      {truncated && <p className="text-amber-200">Diff обрезан сервером: показано не всё изменение.</p>}
    </div>
  );
}

export function ParamTable({ diff }: { diff: string }) {
  if (!diff.trim()) return <p className="text-white/50">Описание изменения не получено.</p>;
  const rows = parseParamDiff(diff);
  if (!rows) return <pre className="whitespace-pre-wrap break-words rounded border border-white/10 bg-black/30 p-1.5 font-mono text-[11px] text-white/75">{diff}</pre>;
  return (
    <div className="overflow-x-auto" data-testid="change-param-table">
      <table className="w-full max-w-xl text-left" aria-label="Что изменится: было и станет">
        <thead>
          <tr className="text-white/50">
            <th className="pr-3 font-normal">Параметр</th>
            <th className="pr-3 font-normal whitespace-nowrap">Было</th>
            <th className="font-normal">Станет</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((r) => (
            <tr key={r.name} className="border-t border-white/10">
              <td className="py-0.5 pr-3">{r.name}</td>
              <td className="pr-3 text-red-200 whitespace-nowrap">{r.before}</td>
              <td className="text-emerald-200 whitespace-nowrap">{r.after}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

export function CodeDiff({ diff }: { diff: string }) {
  const lines = diff.split("\n");
  return (
    <div role="region" aria-label="Diff для разработчика" tabIndex={0}
      className="max-h-72 overflow-auto rounded border border-white/10 bg-black/30 py-1 font-mono text-[11px] leading-snug">
      {lines.map((raw, i) => {
        const kind: DiffLineKind = raw.startsWith("@@") ? "hunk" : /^(\+\+\+|---) /.test(raw) ? "meta"
          : raw.startsWith("+") ? "add" : raw.startsWith("-") ? "del" : "ctx";
        return (
          <div key={i} className={cn("whitespace-pre-wrap break-words px-2", LINE_CLASS[kind])}>{raw === "" ? " " : raw}</div>
        );
      })}
    </div>
  );
}
