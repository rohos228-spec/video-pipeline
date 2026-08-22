"use client";

import { useEffect, useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  Loader2,
  Package,
  ChevronDown,
  ChevronRight,
  Save,
} from "lucide-react";
import { toast } from "sonner";
import { errorMessageFromUnknown } from "@/lib/error-message";
import { api } from "@/lib/api";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";

/**
 * Inline-редактор данных предметов (Items) прямо в ноде «Предметы».
 */
export function ItemsConfigPanel({
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

  const [count, setCount] = useState<number>(1);
  const [descriptions, setDescriptions] = useState<string[]>([""]);

  const projectHash = useMemo(() => {
    if (!projectQ.data) return "";
    return JSON.stringify({
      d: projectQ.data.item_descriptions,
    });
  }, [projectQ.data]);

  useEffect(() => {
    if (!projectQ.data) return;
    const apiDescs = projectQ.data.item_descriptions ?? [];
    const n = Math.max(1, Math.min(5, apiDescs.length || 1));
    setCount(n);
    setDescriptions(
      Array.from({ length: n }, (_, i) => apiDescs[i] ?? "")
    );
    if (apiDescs.length > 0) {
      setShowManual(true);
    }
  }, [projectHash]);

  const setN = (n: number) => {
    const clamped = Math.max(1, Math.min(5, Math.floor(n) || 1));
    setCount(clamped);
    setDescriptions((prev) =>
      Array.from({ length: clamped }, (_, i) => prev[i] ?? "")
    );
  };

  const saveManual = useMutation({
    mutationFn: () =>
      api.patchProject(projectId, {
        item_descriptions: descriptions.map((s) => s.trim()),
      }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["project", projectId] });
      toast.success("Описания предметов сохранены");
    },
    onError: (e) => toast.error(errorMessageFromUnknown(e)),
  });

  const canSaveManual =
    count >= 1 && descriptions.some((d) => d.trim().length >= 3);

  const savedCount = (projectQ.data?.item_descriptions ?? []).filter((d: string) => d.trim().length > 0).length;

  return (
    <div
      className="nodrag nopan nowheel border-t border-cyan-400/20 bg-cyan-500/[0.04]"
      onMouseDown={(e) => e.stopPropagation()}
      onWheel={(e) => e.stopPropagation()}
    >
      <button
        type="button"
        className="flex w-full items-center justify-between gap-2 px-3 py-1.5 text-[10px] text-cyan-200/90 transition hover:bg-cyan-500/[0.08]"
        onClick={() => setOpen((v) => !v)}
      >
        <span className="flex items-center gap-1.5">
          <Package className="h-3 w-3" />
          Предметы
          {savedCount > 0 ? (
            <span className="ml-1 text-cyan-300/90">
              ({savedCount})
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
          <button
            type="button"
            className="flex items-center gap-1 text-[10px] text-muted-foreground hover:text-cyan-300"
            onClick={() => setShowManual((v) => !v)}
          >
            {showManual ? (
              <ChevronDown className="h-3 w-3" />
            ) : (
              <ChevronRight className="h-3 w-3" />
            )}
            Ввести описание
            {savedCount > 0 ? (
              <span className="text-cyan-300/90">· {savedCount}</span>
            ) : null}
          </button>

          {showManual && (
            <div className="flex flex-col gap-2 rounded-md border border-cyan-400/15 bg-cyan-500/[0.03] p-2">
              <label className="flex items-center justify-between gap-2 text-[10px] text-muted-foreground">
                <span>Сколько предметов</span>
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
                    <span className="text-[10px] uppercase tracking-wider text-cyan-300/80">
                      Предмет {i + 1}
                    </span>
                  </div>
                  <Textarea
                    value={desc}
                    placeholder="Опиши предмет: материал, форма, детали, свечение, эпоха…"
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
                className="h-6 w-28 self-center text-[10px]"
                disabled={!canSaveManual || saveManual.isPending}
                onClick={() => saveManual.mutate()}
              >
                {saveManual.isPending ? (
                  <Loader2 className="mr-1 h-3 w-3 animate-spin" />
                ) : null}
                Сохранить
              </Button>

              {!canSaveManual && (
                <p className="text-[9px] text-cyan-400/80">
                  заполните описание хотя бы одного предмета
                </p>
              )}
            </div>
          )}
        </div>
      )}
    </div>
  );
}