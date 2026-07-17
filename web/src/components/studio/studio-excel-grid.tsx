"use client";

import { cn } from "@/lib/utils";

/** Нормальная сетка Excel: буквы колонок, номера строк, оба скроллбара. */
export function StudioExcelGrid({
  rows,
  startRow = 1,
  colLetters,
  className,
}: {
  rows: string[][];
  startRow?: number;
  colLetters?: string[];
  className?: string;
}) {
  const width = Math.max(0, ...rows.map((r) => r.length), colLetters?.length ?? 0);
  const letters =
    colLetters && colLetters.length >= width
      ? colLetters.slice(0, width)
      : Array.from({ length: width }, (_, i) => {
          let n = i + 1;
          let s = "";
          while (n) {
            const rem = (n - 1) % 26;
            s = String.fromCharCode(65 + rem) + s;
            n = Math.floor((n - 1) / 26);
          }
          return s;
        });

  if (!rows.length) {
    return (
      <p className="p-4 text-xs text-muted-foreground">Лист пуст или ещё не заполнен.</p>
    );
  }

  return (
    <div
      className={cn(
        "min-h-0 overflow-auto overscroll-contain rounded-xl border border-white/10 bg-black/20",
        "max-h-[min(70vh,720px)]",
        // Явные полосы прокрутки (Win/Chrome/WebKit + Firefox).
        "[scrollbar-gutter:stable] [scrollbar-width:thin] [scrollbar-color:hsl(0_0%_55%_/_0.55)_transparent]",
        "[&::-webkit-scrollbar]:h-2.5 [&::-webkit-scrollbar]:w-2.5",
        "[&::-webkit-scrollbar-thumb]:rounded-full [&::-webkit-scrollbar-thumb]:bg-white/25",
        "[&::-webkit-scrollbar-thumb:hover]:bg-white/40",
        "[&::-webkit-scrollbar-track]:bg-transparent",
        className,
      )}
    >
      <table className="min-w-max border-collapse text-left text-xs">
        <thead className="sticky top-0 z-20">
          <tr className="bg-card/95">
            <th className="sticky left-0 z-30 border-b border-r border-white/10 bg-card px-2 py-1.5 text-[10px] font-medium text-muted-foreground">
              #
            </th>
            {letters.map((letter) => (
              <th
                key={letter}
                className="border-b border-r border-white/10 px-2 py-1.5 text-center text-[10px] font-medium text-muted-foreground"
              >
                {letter}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map((row, ri) => (
            <tr key={ri} className="border-b border-white/5 hover:bg-white/[0.03]">
              <td className="sticky left-0 z-10 border-r border-white/10 bg-card/95 px-2 py-1.5 text-[10px] text-muted-foreground">
                {startRow + ri}
              </td>
              {Array.from({ length: width }, (_, ci) => {
                const cell = row[ci] ?? "";
                const isFirst = ci === 0;
                return (
                  <td
                    key={ci}
                    className={cn(
                      "whitespace-pre-wrap border-r border-white/5 px-2 py-1.5 align-top text-foreground/90",
                      isFirst ? "min-w-[160px] max-w-[640px]" : "min-w-[72px] max-w-[320px]",
                    )}
                  >
                    {cell || "\u00a0"}
                  </td>
                );
              })}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
