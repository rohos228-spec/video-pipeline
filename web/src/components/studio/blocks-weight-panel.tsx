"use client";

import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import { api } from "@/lib/api";
import { humanizeSlug } from "@/lib/format-labels";
import {
  type BlockSelection,
  blockIsCustomText,
  blockName,
  blockText,
  blockWeight,
  makeBlockSelection,
} from "@/lib/prompt-styles";

/**
 * Редактор категорий `{{BLOCK:cat}}` для мастер-промта одного шага (steps/v2).
 * Хранит значения прямо в `project.prompt_overrides.blocks` — том же поле,
 * которое реально читает `prompt_composer.compose_step()`. Показывается
 * только для категорий, которые реально встречаются в шаблоне этого шага
 * (`catalog.step_block_categories`), чтобы нельзя было настроить то, что
 * ни на что не влияет.
 */
export function BlocksWeightPanel({
  projectId,
  stepId,
  promptOverrides,
}: {
  projectId: number;
  stepId: string;
  promptOverrides: Record<string, unknown>;
}) {
  const qc = useQueryClient();
  const catalog = useQuery({
    queryKey: ["prompt-studio-catalog"],
    queryFn: () => api.promptStudioCatalog(),
    staleTime: 5 * 60_000,
  });

  const categories = catalog.data?.step_block_categories?.[stepId] ?? [];
  const blockCategories = catalog.data?.block_categories ?? {};
  const currentBlocks = (promptOverrides.blocks || {}) as Record<string, BlockSelection>;

  const [draft, setDraft] = useState<Record<string, BlockSelection> | null>(null);
  const blocks = draft ?? currentBlocks;

  const save = useMutation({
    mutationFn: (next: Record<string, BlockSelection>) =>
      api.patchProjectPromptConfig(projectId, { blocks: next }),
    onSuccess: () => {
      toast.success("Блоки промта обновлены");
      setDraft(null);
      qc.invalidateQueries({ queryKey: ["project", projectId] });
    },
    onError: (e) => toast.error(String(e)),
  });

  const updateCategory = (
    cat: string,
    patch: Partial<{ name: string; text: string; weight: number; customText: boolean }>,
  ) => {
    const cur = blocks[cat];
    const useCustom = patch.customText ?? blockIsCustomText(cur);
    const next: Record<string, BlockSelection> = {
      ...blocks,
      [cat]: makeBlockSelection({
        name: useCustom ? undefined : (patch.name ?? blockName(cur)),
        text: useCustom ? (patch.text ?? blockText(cur)) : undefined,
        weight: patch.weight ?? blockWeight(cur),
      }),
    };
    setDraft(next);
  };

  if (!categories.length) {
    return null;
  }

  return (
    <div className="flex flex-col gap-3 rounded-xl border border-white/10 bg-white/[0.03] p-4">
      <div className="flex items-center justify-between gap-2">
        <h4 className="text-xs font-medium uppercase tracking-wider text-amber-400/90">
          Блоки промта — вес и содержимое
        </h4>
        {draft && (
          <button
            type="button"
            className="rounded-md bg-amber-500/90 px-2 py-1 text-[10px] font-medium text-black hover:bg-amber-400 disabled:opacity-50"
            onClick={() => draft && save.mutate(draft)}
            disabled={save.isPending}
          >
            {save.isPending ? "Сохраняю…" : "Сохранить"}
          </button>
        )}
      </div>
      <p className="text-[10px] text-muted-foreground">
        Для каждой категории — готовый блок из библиотеки или свой текст (можно
        использовать <span className="font-mono">{"{{VAR:ИМЯ}}"}</span>), плюс вес
        приоритета (1 = по умолчанию, без пометки). Сохраняется в
        <span className="font-mono"> prompt_overrides.blocks</span> — том самом поле,
        которое использует сборка промта.
      </p>
      <div className="flex flex-col gap-3">
        {categories.map((cat) => {
          const sel = blocks[cat];
          const names = blockCategories[cat] ?? [];
          const isCustom = blockIsCustomText(sel);
          const weight = blockWeight(sel);
          return (
            <div key={cat} className="rounded-lg border border-white/5 p-2">
              <div className="flex items-center justify-between gap-2">
                <label className="text-[10px] font-medium text-foreground/90">
                  {humanizeSlug(cat)}
                </label>
                <label className="flex items-center gap-1 text-[9px] text-muted-foreground">
                  <input
                    type="checkbox"
                    checked={isCustom}
                    onChange={(e) => updateCategory(cat, { customText: e.target.checked })}
                  />
                  свой текст
                </label>
              </div>
              {isCustom ? (
                <textarea
                  className="mt-1 h-16 w-full rounded-md border border-input bg-background px-2 py-1 text-xs"
                  value={blockText(sel)}
                  placeholder="Свой текст блока, можно использовать {{VAR:ИМЯ}}"
                  onChange={(e) => updateCategory(cat, { text: e.target.value, customText: true })}
                />
              ) : (
                <select
                  className="mt-1 h-8 w-full rounded-md border border-input bg-background px-2 text-xs"
                  value={blockName(sel)}
                  onChange={(e) => updateCategory(cat, { name: e.target.value, customText: false })}
                >
                  <option value="">— по умолчанию —</option>
                  {names.map((n) => (
                    <option key={n} value={n}>
                      {humanizeSlug(n)}
                    </option>
                  ))}
                </select>
              )}
              <div className="mt-1 flex items-center gap-2">
                <input
                  type="range"
                  min={0}
                  max={1}
                  step={0.05}
                  value={weight}
                  className="h-1.5 flex-1 accent-amber-400"
                  onChange={(e) => updateCategory(cat, { weight: Number(e.target.value) })}
                />
                <span className="w-9 text-right text-[9px] text-muted-foreground">
                  {weight.toFixed(2)}
                </span>
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
}
