"use client";

import { useEffect, useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  Loader2,
  Users,
  ChevronDown,
  ChevronRight,
  Save,
  FileSpreadsheet,
  RefreshCw,
} from "lucide-react";
import { toast } from "sonner";
import { errorMessageFromUnknown } from "@/lib/error-message";
import { api } from "@/lib/api";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";

/**
 * Inline-редактор данных героев прямо в ноде «Hero».
 *
 * Два режима, шаг hero выбирает между ними сам:
 *
 * 1. **Excel-режим** — данные тянутся с листа «Персонажи» в `project.xlsx`
 *    (имя/внешность/одежда/характер/правила, ref-вариации по правилам).
 *    Активируется когда `project.meta.excel_hero.characters` непуст;
 *    тогда `generate_hero` идёт через `_run_excel` и считает hero_count /
 *    hero_descriptions ненужными.
 *
 * 2. **Ручной режим** — `hero_count` + `hero_descriptions[]` +
 *    `hero_variations[]` на самом проекте. Используется когда excel-листа
 *    нет (или его явно сбросили).
 *
 * Панель приоритетно показывает Excel-источник: если лист «Персонажи»
 * заполнен — просто один клик «Загрузить из Excel». Ручной ввод остаётся
 * под кнопкой «Ввести руками» как fallback.
 */
export function HeroConfigPanel({
  projectId,
}: {
  projectId: number;
}) {
  const qc = useQueryClient();
  const [open, setOpen] = useState(false);
  const [showManual, setShowManual] = useState(false);

  const projectQ = useQuery({
    queryKey: ["project", projectId],
    queryFn: () => api.getProject(projectId),
    enabled: open,
  });

  const excelQ = useQuery({
    queryKey: ["excel-hero", projectId],
    queryFn: () => api.getExcelHero(projectId),
    enabled: open,
  });

  // ── Excel-режим ───────────────────────────────────────────────────
  const loadExcel = useMutation({
    mutationFn: () => api.loadExcelHero(projectId),
    onSuccess: (r) => {
      qc.invalidateQueries({ queryKey: ["excel-hero", projectId] });
      qc.invalidateQueries({ queryKey: ["project", projectId] });
      toast.success(`Загружено персонажей: ${r.count}`);
    },
    onError: (e) => toast.error(errorMessageFromUnknown(e)),
  });

  const clearExcel = useMutation({
    mutationFn: () => api.clearExcelHero(projectId),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["excel-hero", projectId] });
      qc.invalidateQueries({ queryKey: ["project", projectId] });
      toast.success("Excel-персонажи отвязаны");
    },
    onError: (e) => toast.error(errorMessageFromUnknown(e)),
  });

  // ── Ручной режим ──────────────────────────────────────────────────
  const [count, setCount] = useState<number>(1);
  const [descriptions, setDescriptions] = useState<string[]>([""]);
  const [variations, setVariations] = useState<number[]>([1]);

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

  const saveManual = useMutation({
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
      toast.success("Описания героев сохранены");
    },
    onError: (e) => toast.error(errorMessageFromUnknown(e)),
  });

  const canSaveManual =
    count >= 1 && descriptions.every((d) => d.trim().length >= 5);

  const excelChars = excelQ.data?.characters ?? [];
  const excelLoaded = excelQ.data?.loaded ?? false;

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
          {excelLoaded ? (
            <span className="ml-1 text-emerald-300">
              · Excel ({excelChars.length})
            </span>
          ) : projectQ.data?.hero_count ? (
            <span className="ml-1 text-muted-foreground">
              ({projectQ.data.hero_count})
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
          {/* ── Excel-блок (приоритет) ──────────────────────────── */}
          <div className="flex flex-col gap-1.5 rounded-md border border-emerald-400/20 bg-emerald-500/[0.05] p-2">
            <div className="flex items-center justify-between gap-2">
              <span className="flex items-center gap-1 text-[10px] text-emerald-300">
                <FileSpreadsheet className="h-3 w-3" />
                Лист «Персонажи» в project.xlsx
              </span>
              {excelLoaded && (
                <span className="text-[9px] text-emerald-300/90">
                  ✓ {excelChars.length}
                </span>
              )}
            </div>

            {excelLoaded && excelChars.length > 0 && (
              <ul className="max-h-[64px] overflow-y-auto text-[10px] leading-snug text-muted-foreground">
                {excelChars.slice(0, 6).map((c) => (
                  <li key={c.id} className="truncate">
                    <span className="text-emerald-300/90">{c.id}</span>
                    {c.name ? ` · ${c.name}` : ""}
                    {c.ref_ids.length > 0
                      ? ` · ref ${c.ref_ids.join(",")}`
                      : ""}
                  </li>
                ))}
                {excelChars.length > 6 && (
                  <li className="text-muted-foreground/70">
                    …и ещё {excelChars.length - 6}
                  </li>
                )}
              </ul>
            )}

            <div className="flex gap-1.5">
              <Button
                type="button"
                size="sm"
                variant="outline"
                className="h-6 flex-1 text-[10px]"
                disabled={loadExcel.isPending}
                onClick={() => loadExcel.mutate()}
              >
                {loadExcel.isPending ? (
                  <Loader2 className="mr-1 h-3 w-3 animate-spin" />
                ) : (
                  <RefreshCw className="mr-1 h-3 w-3" />
                )}
                {excelLoaded ? "Перечитать" : "Загрузить из Excel"}
              </Button>
              {excelLoaded && (
                <Button
                  type="button"
                  size="sm"
                  variant="ghost"
                  className="h-6 text-[10px] text-muted-foreground hover:text-destructive"
                  disabled={clearExcel.isPending}
                  onClick={() => clearExcel.mutate()}
                  title="Отвязать excel и пользоваться ручным описанием"
                >
                  Сброс
                </Button>
              )}
            </div>
          </div>

          {/* ── Ручной блок (свернут, если есть Excel) ──────────── */}
          <button
            type="button"
            className="flex items-center gap-1 text-[10px] text-muted-foreground hover:text-amber-300"
            onClick={() => setShowManual((v) => !v)}
          >
            {showManual ? (
              <ChevronDown className="h-3 w-3" />
            ) : (
              <ChevronRight className="h-3 w-3" />
            )}
            Ввести руками
            {!excelLoaded && projectQ.data?.hero_count ? (
              <span className="text-amber-300/90">· {projectQ.data.hero_count}</span>
            ) : null}
          </button>

          {showManual && (
            <div className="flex flex-col gap-2 rounded-md border border-amber-400/15 bg-amber-500/[0.03] p-2">
              <label className="flex items-center justify-between gap-2 text-[10px] text-muted-foreground">
                <span>Сколько героев</span>
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
                disabled={!canSaveManual || saveManual.isPending}
                onClick={() => saveManual.mutate()}
              >
                {saveManual.isPending ? (
                  <Loader2 className="mr-1 h-3 w-3 animate-spin" />
                ) : (
                  <Save className="mr-1 h-3 w-3" />
                )}
                Сохранить руками
              </Button>

              {!canSaveManual && (
                <p className="text-[9px] text-amber-400/80">
                  у каждого героя описание ≥ 5 символов
                </p>
              )}
            </div>
          )}
        </div>
      )}
    </div>
  );
}
