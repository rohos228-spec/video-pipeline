"use client";

import { useEffect, useRef, useState } from "react";
import { cn } from "@/lib/utils";

/** Нормальная сетка Excel: буквы колонок, номера строк, оба скроллбара. */
export function StudioExcelGrid({
  rows,
  startRow = 1,
  colLetters,
  className,
  /** Как в Excel: ячейки в одну строку, без переноса текста. */
  nowrap = false,
  onCellClick,
  editable = false,
  onCellCommit,
}: {
  rows: string[][];
  startRow?: number;
  colLetters?: string[];
  className?: string;
  nowrap?: boolean;
  onCellClick?: (rowIndex: number, colIndex: number) => void;
  editable?: boolean;
  onCellCommit?: (
    rowIndex: number,
    colIndex: number,
    value: string,
  ) => void | Promise<void>;
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

  const [edit, setEdit] = useState<{ ri: number; ci: number } | null>(null);
  const [draft, setDraft] = useState("");
  const [saving, setSaving] = useState(false);
  const inputRef = useRef<HTMLInputElement | null>(null);
  const textAreaRef = useRef<HTMLTextAreaElement | null>(null);

  useEffect(() => {
    const el = inputRef.current ?? textAreaRef.current;
    if (edit && el) {
      el.focus();
      el.select();
    }
  }, [edit]);

  const beginEdit = (ri: number, ci: number) => {
    if (!editable || saving) return;
    setEdit({ ri, ci });
    setDraft(rows[ri]?.[ci] ?? "");
    onCellClick?.(ri, ci);
  };

  const cancelEdit = () => {
    setEdit(null);
    setDraft("");
  };

  const commitEdit = async () => {
    if (!edit || !onCellCommit) {
      cancelEdit();
      return;
    }
    const { ri, ci } = edit;
    const prev = rows[ri]?.[ci] ?? "";
    const next = draft;
    if (next === prev) {
      cancelEdit();
      return;
    }
    setSaving(true);
    try {
      await onCellCommit(ri, ci, next);
      setEdit(null);
      setDraft("");
    } catch {
      // parent shows toast; keep editor open
    } finally {
      setSaving(false);
    }
  };

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
        saving && "opacity-80",
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
                const isEditing = edit?.ri === ri && edit?.ci === ci;
                return (
                  <td
                    key={ci}
                    title={isEditing ? undefined : cell || undefined}
                    onClick={
                      editable
                        ? () => beginEdit(ri, ci)
                        : onCellClick
                          ? () => onCellClick(ri, ci)
                          : undefined
                    }
                    className={cn(
                      "border-r border-white/5 px-2 py-1.5 align-top text-foreground/90",
                      nowrap
                        ? "whitespace-nowrap max-w-none"
                        : "whitespace-pre-wrap",
                      !nowrap && (isFirst ? "min-w-[160px] max-w-[640px]" : "min-w-[72px] max-w-[320px]"),
                      nowrap && (isFirst ? "min-w-[160px]" : "min-w-[120px]"),
                      (editable || onCellClick) && "cursor-pointer",
                      isEditing && "p-0",
                    )}
                  >
                    {isEditing ? (
                      nowrap ? (
                        <input
                          ref={inputRef}
                          value={draft}
                          disabled={saving}
                          onChange={(e) => setDraft(e.target.value)}
                          onBlur={() => void commitEdit()}
                          onKeyDown={(e) => {
                            if (e.key === "Enter") {
                              e.preventDefault();
                              void commitEdit();
                            } else if (e.key === "Escape") {
                              e.preventDefault();
                              cancelEdit();
                            }
                          }}
                          className="box-border w-full min-w-[120px] border-0 bg-primary/15 px-2 py-1.5 text-xs text-foreground outline-none ring-1 ring-primary"
                        />
                      ) : (
                        <textarea
                          ref={textAreaRef}
                          value={draft}
                          disabled={saving}
                          rows={3}
                          onChange={(e) => setDraft(e.target.value)}
                          onBlur={() => void commitEdit()}
                          onKeyDown={(e) => {
                            if (e.key === "Enter" && !e.shiftKey) {
                              e.preventDefault();
                              void commitEdit();
                            } else if (e.key === "Escape") {
                              e.preventDefault();
                              cancelEdit();
                            }
                          }}
                          className="box-border w-full min-w-[160px] resize-y border-0 bg-primary/15 px-2 py-1.5 text-xs text-foreground outline-none ring-1 ring-primary"
                        />
                      )
                    ) : (
                      cell || "\u00a0"
                    )}
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
