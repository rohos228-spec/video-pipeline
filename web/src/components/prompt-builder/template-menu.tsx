"use client";

import { useEffect, useRef, useState } from "react";
import { LayoutTemplate } from "lucide-react";
import { cn } from "@/lib/utils";
import type { PromptTemplate } from "@/lib/prompt-builder/types";

export function TemplateMenu({
  templates,
  activeId,
  onPick,
}: {
  templates: PromptTemplate[];
  activeId: string;
  onPick: (id: string) => void;
}) {
  const [open, setOpen] = useState(false);
  const rootRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!open) return;
    const onDoc = (e: MouseEvent) => {
      if (!rootRef.current?.contains(e.target as Node)) setOpen(false);
    };
    document.addEventListener("mousedown", onDoc);
    return () => document.removeEventListener("mousedown", onDoc);
  }, [open]);

  const active = templates.find((t) => t.id === activeId);

  return (
    <div ref={rootRef} className="relative flex h-full flex-col items-center py-2">
      <button
        type="button"
        title={active?.label ?? "Шаблоны"}
        aria-label="Выбрать шаблон"
        aria-expanded={open}
        onClick={() => setOpen((v) => !v)}
        className={cn(
          "pb-template-rail-btn flex h-8 w-8 items-center justify-center rounded-md",
          open && "pb-template-rail-btn-open",
        )}
      >
        <LayoutTemplate className="h-4 w-4" strokeWidth={1.75} />
      </button>

      {open && (
        <div className="pb-template-dropdown absolute left-[calc(100%+6px)] top-2 z-50 min-w-[200px]">
          <p className="px-2.5 py-1.5 text-[8px] font-bold uppercase tracking-widest pb-text-dim">
            Шаблоны
          </p>
          <ul className="max-h-[min(420px,70vh)] overflow-y-auto pb-1">
            {templates.map((t) => (
              <li key={t.id}>
                <button
                  type="button"
                  onClick={() => {
                    onPick(t.id);
                    setOpen(false);
                  }}
                  className={cn(
                    "pb-template-dropdown-item w-full text-left",
                    t.id === activeId && "pb-template-dropdown-item-active",
                  )}
                >
                  <span className="block truncate text-[11px] font-medium">{t.label}</span>
                  <span className="block truncate text-[9px] pb-text-dim">{t.stepCode}</span>
                </button>
              </li>
            ))}
          </ul>
        </div>
      )}
    </div>
  );
}
