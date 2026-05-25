"use client";

import { useEffect, useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Loader2, Users, ChevronDown, ChevronRight, Save } from "lucide-react";
import { toast } from "sonner";
import { api } from "@/lib/api";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";

/**
 * Inline-редактор данных героев прямо в ноде «Hero».
 *
 * Раньше hero_count / hero_descriptions / hero_variations заполнялись
 * либо ботом-сценаристом (шаги plan/script), либо через Excel-лист
 * «Персонажи». Если оператор прыгает на шаг hero без этих предшественников,
 * хочется задать значения руками — а не дёргать PATCH через PowerShell.
 *
 * Хранение в БД остаётся прежним: PATCH /api/projects/{id} с полями
 * hero_count, hero_descriptions, hero_variations.
 */
export function HeroConfigPanel({
  projectId,
}: {
  projectId: number;
}) {
  const qc = useQueryClient();
  const [open, setOpen] = useState(false);
  const projectQ = useQuery({
    queryKey: ["project", projectId],
    queryFn: () => api.getProject(projectId),
    enabled: open, // не дёргаем API пока панель закрыта
  });

  const [count, setCount] = useState<number>(1);
  const [descriptions, setDescriptions] = useState<string[]>([""]);
  const [variations, setVariations] = useState<number[]>([1]);

  // Подтянуть актуальные значения из проекта при открытии / обновлении кэша.
  const projectHash = useMemo(() => {
    if (!projectQ.data) return "";
    return JSON.stringify({
      c: projectQ.data.hero_count,
      d: projectQ.data.hero_descriptions,
      v: projectQ.data.hero_variations,
    });
  }, [projectQ.data]);

  useEffect(() => {
    if (!projectQ.data) return;
    const apiCount = projectQ.data.hero_count ?? 0;
    const apiDescs = projectQ.data.hero_descriptions ?? [];
    const apiVars = projectQ.data.hero_variations ?? [];
    const n = Math.max(1, Math.min(5, apiCount || apiDescs.length || 1));
    setCount(n);
    setDescriptions(
      Array.from({ length: n }, (_, i) => apiDescs[i] ?? "")
    );
    setVariations(
      Array.from({ length: n }, (_, i) => apiVars[i] ?? 1)
    );
    // projectHash — стабильный «отпечаток» полей, чтобы не перетирать
    // локальные правки на каждый ререндер.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [projectHash]);

  const setN = (n: number) => {
    const clamped = Math.max(1, Math.min(5, Math.floor(n) || 1));
    setCount(clamped);
    setDescriptions((prev) =>
      Array.from({ length: clamped }, (_, i) => prev[i] ?? "")
    );
    setVariations((prev) =>
      Array.from({ length: clamped }, (_, i) => prev[i] ?? 1)
    );
  };

  const save = useMutation({
    mutationFn: () =>
      api.patchProject(projectId, {
        hero_count: count,
        hero_descriptions: descriptions.map((s) => s.trim()),
        hero_variations: variations.map((v) =>
          Math.max(1, Math.min(5, Math.floor(v) || 1))
        ),
      }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["project", projectId] });
      toast.success("Персонажи сохранены");
    },
    onError: (e) => toast.error(String(e)),
  });

  const canSave =
    count >= 1 && descriptions.every((d) => d.trim().length >= 5);

  return (
    <div
      className="nodrag nopan nowheel border-t border-amber-400/20 bg-amber-500/[0.04]"
      onMouseDown={(e) => e.stopPropagation()}
      onWheel={(e) => e.stopPropagation()}
    >
      <button
        type="button"
        className="flex w-full items-center justify-between gap-2 px-3 py-1.5 text-[10px] text-amber-200/90 transition hover:bg-amber-500/[0.08]"
        onClick={() => setOpen((v) => !v)}
      >
        <span className="flex items-center gap-1.5">
          <Users className="h-3 w-3" />
          Персонажи
          {projectQ.data ? (
            <span className="ml-1 text-muted-foreground">
              ({projectQ.data.hero_count ?? 0})
            </span>
          ) : null}
        </span>
        {open ? (
          <ChevronDown className="h-3 w-3" />
        ) : (
          <ChevronRight className="h-3 w-3" />
        )}
      </button>

      {open && (
        <div className="flex flex-col gap-2 px-3 pb-2.5">
          <label className="flex items-center justify-between gap-2 text-[10px] text-muted-foreground">
            <span>Сколько</span>
            <Input
              type="number"
              min={1}
              max={5}
              value={count}
              onChange={(e) => setN(Number(e.target.value))}
              className="h-6 w-14 text-[11px]"
            />
          </label>

          {descriptions.map((desc, i) => (
            <div
              key={i}
              className="flex flex-col gap-1 rounded-md border border-white/10 bg-black/20 p-1.5"
            >
              <div className="flex items-center justify-between gap-2">
                <span className="text-[10px] uppercase tracking-wider text-amber-300/80">
                  Герой {i + 1}
                </span>
                <label className="flex items-center gap-1 text-[9px] text-muted-foreground">
                  вариаций
                  <Input
                    type="number"
                    min={1}
                    max={5}
                    value={variations[i] ?? 1}
                    onChange={(e) => {
                      const v = Math.max(
                        1,
                        Math.min(5, Number(e.target.value) || 1)
                      );
                      setVariations((prev) => {
                        const next = [...prev];
                        next[i] = v;
                        return next;
                      });
                    }}
                    className="h-5 w-10 text-[10px]"
                  />
                </label>
              </div>
              <Textarea
                value={desc}
                placeholder="Опиши героя: возраст, одежда, лицо, эпоха…"
                onChange={(e) =>
                  setDescriptions((prev) => {
                    const next = [...prev];
                    next[i] = e.target.value;
                    return next;
                  })
                }
                className="min-h-[42px] resize-y text-[10.5px] leading-snug"
                rows={2}
              />
            </div>
          ))}

          <Button
            type="button"
            size="sm"
            variant="outline"
            className="h-6 text-[10px]"
            disabled={!canSave || save.isPending}
            onClick={() => save.mutate()}
          >
            {save.isPending ? (
              <Loader2 className="mr-1 h-3 w-3 animate-spin" />
            ) : (
              <Save className="mr-1 h-3 w-3" />
            )}
            Сохранить
          </Button>

          {!canSave && (
            <p className="text-[9px] text-amber-400/80">
              у каждого героя описание ≥ 5 символов
            </p>
          )}
        </div>
      )}
    </div>
  );
}
