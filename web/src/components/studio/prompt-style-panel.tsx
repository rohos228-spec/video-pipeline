"use client";

import { useMemo, useState } from "react";
import { Download, Plus, Trash2 } from "lucide-react";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { cn } from "@/lib/utils";
import { humanizeSlug, formatStylePresetLabel } from "@/lib/format-labels";
import type { NodePromptSlot } from "@/lib/node-prompts";
import {
  type CustomStylePreset,
  type PromptStyleConfig,
  slotSupportsStyles,
} from "@/lib/prompt-styles";

export function PromptStylePanel({
  slot,
  config,
  blockCategories,
  catalogPresets,
  onChange,
}: {
  slot: NodePromptSlot;
  config: PromptStyleConfig;
  blockCategories: Record<string, string[]>;
  catalogPresets: { id: string; label: string; description?: string; blocks?: Record<string, string> }[];
  onChange: (next: PromptStyleConfig) => void;
}) {
  const [newStyleName, setNewStyleName] = useState("");
  const [showAdd, setShowAdd] = useState(false);

  const blocks = config.blocks ?? {};
  const customStyles = config.custom_styles ?? [];
  const activePreset = config.style_preset ?? "";

  const allPresets = useMemo(() => {
    const builtIn = catalogPresets.map((p) => ({
      id: p.id,
      label: formatStylePresetLabel(p),
      description: p.description,
      source: "builtin" as const,
      blocks: p.blocks,
    }));
    const custom = customStyles.map((c) => ({
      id: c.id,
      label: c.label,
      source: "custom" as const,
      blocks: c.blocks,
    }));
    return [...builtIn, ...custom];
  }, [catalogPresets, customStyles]);

  if (!slotSupportsStyles(slot)) {
    return (
      <p className="text-xs text-muted-foreground">Для этого промта стили не используются.</p>
    );
  }

  const applyPreset = (id: string, presetBlocks?: Record<string, string>) => {
    onChange({
      ...config,
      style_preset: id,
      blocks: presetBlocks ? { ...presetBlocks } : { ...blocks },
    });
  };

  const addCustomStyle = () => {
    const name = newStyleName.trim();
    if (!name) {
      toast.error("Введите название стиля");
      return;
    }
    const id = `custom_${Date.now()}`;
    onChange({
      ...config,
      style_preset: id,
      custom_styles: [...customStyles, { id, label: name, blocks: { ...blocks } }],
    });
    setNewStyleName("");
    setShowAdd(false);
    toast.success(`Стиль «${name}» добавлен`);
  };

  const deleteCustomStyle = (id: string) => {
    onChange({
      ...config,
      custom_styles: customStyles.filter((c) => c.id !== id),
      style_preset: activePreset === id ? (catalogPresets[0]?.id ?? "") : activePreset,
    });
    toast.message("Стиль удалён");
  };

  const downloadStyle = (id: string, label: string) => {
    const preset = allPresets.find((p) => p.id === id);
    const blob = new Blob(
      [JSON.stringify({ id, label, blocks: preset?.blocks ?? blocks }, null, 2)],
      { type: "application/json" },
    );
    const a = document.createElement("a");
    a.href = URL.createObjectURL(blob);
    a.download = `${label.replace(/\s+/g, "-")}.json`;
    a.click();
    toast.success("Стиль скачан");
  };

  return (
    <div className="flex flex-col gap-4 rounded-xl border border-white/10 bg-white/[0.03] p-4">
      <div className="flex items-start justify-between gap-2">
        <div>
          <h3 className="text-xs font-medium uppercase tracking-wider text-amber-400/90">
            Стили промта «{slot.title}»
          </h3>
          <p className="mt-1 text-[10px] text-muted-foreground">Только для этой ноды и этого промта</p>
        </div>
        <Button type="button" size="sm" variant="outline" className="h-7 text-[10px]" onClick={() => setShowAdd((v) => !v)}>
          <Plus className="h-3 w-3" />
          Добавить
        </Button>
      </div>
      {showAdd && (
        <div className="flex gap-2">
          <Input value={newStyleName} onChange={(e) => setNewStyleName(e.target.value)} placeholder="Название стиля" className="h-8 text-xs" />
          <Button type="button" size="sm" className="h-8 text-xs" onClick={addCustomStyle}>
            Сохранить
          </Button>
        </div>
      )}
      <div className="grid gap-2 sm:grid-cols-2">
        {allPresets.map((p) => (
          <div
            key={p.id}
            className={cn(
              "rounded-xl border px-3 py-2 text-xs",
              activePreset === p.id ? "border-amber-400/50 bg-amber-400/10" : "border-white/10",
            )}
          >
            <button type="button" className="w-full text-left font-medium" onClick={() => applyPreset(p.id, p.blocks)}>
              {p.label}
            </button>
            <div className="mt-2 flex gap-1">
              <Button type="button" size="sm" variant="ghost" className="h-6 px-1.5 text-[9px]" onClick={() => downloadStyle(p.id, p.label)}>
                <Download className="h-3 w-3" />
                Скачать
              </Button>
              {p.source === "custom" && (
                <Button type="button" size="sm" variant="ghost" className="h-6 px-1.5 text-[9px] text-destructive" onClick={() => deleteCustomStyle(p.id)}>
                  <Trash2 className="h-3 w-3" />
                  Удалить
                </Button>
              )}
            </div>
          </div>
        ))}
      </div>
      {(slot.kind === "blocks" || slot.kind === "gpt") && (
        <section>
          <h4 className="text-[10px] uppercase text-muted-foreground">Блоки для «{slot.title}»</h4>
          <div className="mt-2 flex flex-col gap-2">
            {Object.entries(blockCategories).map(([cat, names]) => (
              <div key={cat}>
                <label className="text-[10px] text-muted-foreground">{humanizeSlug(cat)}</label>
                <select
                  className="mt-1 h-8 w-full rounded-md border border-input bg-background px-2 text-xs"
                  value={blocks[cat] ?? ""}
                  onChange={(e) => onChange({ ...config, blocks: { ...blocks, [cat]: e.target.value } })}
                >
                  <option value="">— по умолчанию —</option>
                  {names.map((n) => (
                    <option key={n} value={n}>
                      {humanizeSlug(n)}
                    </option>
                  ))}
                </select>
              </div>
            ))}
          </div>
        </section>
      )}
    </div>
  );
}
