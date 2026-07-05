"use client";

import { useMemo, useState } from "react";
import { GripVertical, Plus, Trash2, ChevronDown, ChevronUp } from "lucide-react";
import { cn } from "@/lib/utils";
import { Button } from "@/components/ui/button";
import { BlockKindBadge } from "./block-kind-badge";
import { PromptPreviewPanel } from "./prompt-preview-panel";
import { MOCK_BLOCKS, MOCK_TEMPLATES, MOCK_VARS } from "@/lib/prompt-builder/mock-data";
import { composePrompt } from "@/lib/prompt-builder/compose";
import { BLOCK_KINDS } from "@/lib/prompt-builder/types";

type StackItem = {
  slotId: string;
  blockId: string;
  enabled: boolean;
};

/** Вариант B: стек блоков — порядок и вкл/выкл секций */
export function VariantStack() {
  const [templateId, setTemplateId] = useState("tpl_img_knitted");
  const template = MOCK_TEMPLATES.find((t) => t.id === templateId)!;

  const [stack, setStack] = useState<StackItem[]>(() =>
    template.slots.map((s) => ({
      slotId: s.slotId,
      blockId: s.defaultBlockId,
      enabled: true,
    })),
  );

  const resetStack = (tid: string) => {
    const t = MOCK_TEMPLATES.find((x) => x.id === tid)!;
    setTemplateId(tid);
    setStack(
      t.slots.map((s) => ({
        slotId: s.slotId,
        blockId: s.defaultBlockId,
        enabled: true,
      })),
    );
  };

  const result = useMemo(() => {
    const slots: Record<string, string> = {};
    const orderedSlots = stack
      .filter((x) => x.enabled)
      .map((item) => {
        const existing = template.slots.find((s) => s.slotId === item.slotId);
        if (existing) return existing;
        const block = MOCK_BLOCKS.find((b) => b.id === item.blockId)!;
        return {
          slotId: item.slotId,
          kind: block.kind,
          required: false,
          defaultBlockId: item.blockId,
        };
      });

    for (const item of stack.filter((x) => x.enabled)) {
      slots[item.slotId] = item.blockId;
    }

    return composePrompt(
      { ...template, slots: orderedSlots },
      MOCK_BLOCKS,
      { templateId, slots, vars: {} },
      MOCK_VARS,
    );
  }, [template, stack, templateId]);

  const move = (idx: number, dir: -1 | 1) => {
    const next = [...stack];
    const j = idx + dir;
    if (j < 0 || j >= next.length) return;
    [next[idx], next[j]] = [next[j], next[idx]];
    setStack(next);
  };

  const toggle = (slotId: string) => {
    setStack((s) => s.map((x) => (x.slotId === slotId ? { ...x, enabled: !x.enabled } : x)));
  };

  const changeBlock = (slotId: string, blockId: string) => {
    setStack((s) => s.map((x) => (x.slotId === slotId ? { ...x, blockId } : x)));
  };

  const addOptional = () => {
    const extra = MOCK_BLOCKS.find((b) => b.id === "feat_camera_slow_push");
    if (!extra) return;
    setStack((s) => [
      ...s,
      { slotId: `extra-${Date.now()}`, blockId: extra.id, enabled: true },
    ]);
  };

  return (
    <div className="grid h-full min-h-0 grid-cols-[1fr_340px] gap-0 divide-x divide-border">
      <div className="flex min-h-0 flex-col">
        <header className="flex flex-wrap items-center gap-2 border-b border-border px-4 py-3">
          <span className="text-xs text-muted-foreground">Базовый промт:</span>
          <select
            className="h-8 max-w-xs rounded-md border border-input bg-background px-2 text-xs"
            value={templateId}
            onChange={(e) => resetStack(e.target.value)}
          >
            {MOCK_TEMPLATES.map((t) => (
              <option key={t.id} value={t.id}>
                {t.label}
              </option>
            ))}
          </select>
          <Button type="button" size="sm" variant="outline" className="h-7 text-[10px]" onClick={addOptional}>
            <Plus className="h-3 w-3" />
            Добавить блок
          </Button>
        </header>

        <div className="min-h-0 flex-1 overflow-y-auto p-4">
          <p className="mb-4 text-[11px] text-muted-foreground">
            Стек: перетаскивайте порядок, отключайте лишние секции. Блок «особенности» из image-промта можно
            подставить в hero — включите и смените вариант.
          </p>

          <div className="space-y-2">
            {stack.map((item, idx) => {
              const block = MOCK_BLOCKS.find((b) => b.id === item.blockId)!;
              const tplSlot = template.slots.find((s) => s.slotId === item.slotId);
              const kind = tplSlot ? tplSlot.kind : block.kind;
              const sameKind = MOCK_BLOCKS.filter(
                (b) => b.kind === kind && b.steps.includes(template.stepCode),
              );

              return (
                <div
                  key={item.slotId}
                  className={cn(
                    "group flex gap-2 rounded-xl border p-3 transition-opacity",
                    item.enabled
                      ? "border-border bg-card/50"
                      : "border-border/40 bg-muted/20 opacity-50",
                  )}
                >
                  <div className="flex flex-col items-center gap-0.5 pt-1">
                    <GripVertical className="h-4 w-4 text-muted-foreground/50" />
                    <button
                      type="button"
                      className="rounded p-0.5 hover:bg-muted"
                      onClick={() => move(idx, -1)}
                    >
                      <ChevronUp className="h-3 w-3" />
                    </button>
                    <button
                      type="button"
                      className="rounded p-0.5 hover:bg-muted"
                      onClick={() => move(idx, 1)}
                    >
                      <ChevronDown className="h-3 w-3" />
                    </button>
                  </div>

                  <div className="min-w-0 flex-1">
                    <div className="mb-2 flex items-center gap-2">
                      <input
                        type="checkbox"
                        checked={item.enabled}
                        onChange={() => toggle(item.slotId)}
                        className="rounded"
                      />
                      <BlockKindBadge kind={kind} />
                      <select
                        className="h-7 min-w-0 flex-1 rounded border border-input bg-background px-1.5 text-[11px]"
                        value={item.blockId}
                        onChange={(e) => changeBlock(item.slotId, e.target.value)}
                        disabled={!item.enabled}
                      >
                        {sameKind.map((b) => (
                          <option key={b.id} value={b.id}>
                            {b.label}
                          </option>
                        ))}
                      </select>
                    </div>
                    <p className="text-[10px] leading-relaxed text-muted-foreground">{block.body}</p>
                  </div>

                  <Button
                    type="button"
                    size="icon"
                    variant="ghost"
                    className="h-7 w-7 shrink-0 opacity-0 group-hover:opacity-100"
                    onClick={() => setStack((s) => s.filter((x) => x.slotId !== item.slotId))}
                  >
                    <Trash2 className="h-3.5 w-3.5" />
                  </Button>
                </div>
              );
            })}
          </div>

          <div className="mt-6 rounded-lg border border-dashed border-border p-3">
            <p className="mb-2 text-[10px] font-medium uppercase text-muted-foreground">Легенда типов</p>
            <div className="flex flex-wrap gap-2">
              {BLOCK_KINDS.map((k) => (
                <div key={k.id} className="text-[10px] text-muted-foreground">
                  <BlockKindBadge kind={k.id} compact /> — {k.description}
                </div>
              ))}
            </div>
          </div>
        </div>
      </div>

      <aside className="min-h-0 bg-card/20">
        <PromptPreviewPanel result={result} className="h-full" />
      </aside>
    </div>
  );
}

export function VariantStackLegend() {
  return (
    <p className="text-xs text-muted-foreground">
      <strong className="text-foreground">Стек</strong> — промт как лента блоков: порядок, вкл/выкл, добавление
      опциональных секций. Удобно, когда блок из другого промта нужно «подсмотреть» и вставить.
    </p>
  );
}
