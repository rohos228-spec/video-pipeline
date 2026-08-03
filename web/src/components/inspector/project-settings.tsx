"use client";

import { useEffect, useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { GitBranch, Loader2 } from "lucide-react";
import { toast } from "sonner";
import { errorMessageFromUnknown } from "@/lib/error-message";
import { api } from "@/lib/api";
import type { ProjectDetail } from "@/lib/types";
import { MassFactoryPanel } from "@/components/inspector/mass-factory-panel";

/** Настройки проекта в инспекторе (без «Контроль пайплайна» — только ИИ). */
export function ProjectSettingsPanel({ project }: { project: ProjectDetail }) {
  return (
    <div className="flex flex-col gap-3 rounded-xl border border-white/10 bg-white/[0.03] p-3">
      <div className="flex items-center gap-2 text-[10px] font-semibold uppercase tracking-wider text-muted-foreground">
        <GitBranch className="h-3.5 w-3.5" />
        Настройки пайплайна
      </div>
      <p className="text-[10px] text-muted-foreground">
        Контроль шагов — только ИИ. Автопродвижение — тумблер сверху справа, рядом с
        генерацией.
      </p>
      <ImgStreamsControl project={project} />
      <MassFactoryPanel project={project} />
    </div>
  );
}

/** Параллельные потоки генерации картинок пайплайна (0..4). */
function ImgStreamsControl({ project }: { project: ProjectDetail }) {
  const qc = useQueryClient();
  const meta = (project.meta || {}) as Record<string, unknown>;
  const current = Math.max(
    0,
    Math.min(4, Number(meta.img_streams ?? 1) || 1),
  );
  const [local, setLocal] = useState(current);

  useEffect(() => {
    setLocal(current);
  }, [current]);

  const patch = useMutation({
    mutationFn: (n: number) =>
      api.patchProject(project.id, { meta: { img_streams: n } }),
    onSuccess: (_data, n) => {
      toast.success(
        n === 0
          ? "Потоки картинок: 0 (без генерации)"
          : `Потоки картинок: ${n}`,
      );
      void qc.invalidateQueries({ queryKey: ["project", project.id] });
    },
    onError: (e) => toast.error(errorMessageFromUnknown(e)),
  });

  return (
    <div className="rounded-lg border border-border/50 bg-card/40 px-2.5 py-2">
      <div className="mb-1.5 text-xs font-medium">Потоки картинок (img)</div>
      <p className="mb-2 text-[10px] leading-snug text-muted-foreground">
        0 — не звать провайдера; 1 — по одному; 2–4 — параллельно (общий лимит с
        Create Outsee ≤4).
      </p>
      <div className="flex flex-wrap gap-1">
        {[0, 1, 2, 3, 4].map((n) => (
          <button
            key={n}
            type="button"
            disabled={patch.isPending}
            onClick={() => {
              setLocal(n);
              patch.mutate(n);
            }}
            className={
              "h-7 min-w-7 rounded-md px-2 text-xs font-medium transition-colors " +
              (local === n
                ? "bg-primary text-primary-foreground"
                : "bg-muted/60 text-muted-foreground hover:bg-accent/50")
            }
          >
            {n}
          </button>
        ))}
      </div>
    </div>
  );
}

/** Тумблер автопродвижения (верхняя панель канваса, рядом с Run/генерацией). */
export function AutoAdvanceToggle({
  project,
  className,
}: {
  project: ProjectDetail;
  className?: string;
}) {
  const qc = useQueryClient();
  /** Мгновенный отклик: пока PATCH ждёт SQLite/воркер, UI уже переключён. */
  const [optimistic, setOptimistic] = useState<boolean | null>(null);
  const autoOn = optimistic ?? Boolean(project.auto_mode);

  useEffect(() => {
    if (optimistic !== null && Boolean(project.auto_mode) === optimistic) {
      setOptimistic(null);
    }
  }, [project.auto_mode, optimistic]);

  const patch = useMutation({
    mutationFn: (next: boolean) => api.patchProject(project.id, { auto_mode: next }),
    onMutate: async (next) => {
      setOptimistic(next);
      await qc.cancelQueries({ queryKey: ["project", project.id] });
      const prev = qc.getQueryData<ProjectDetail>(["project", project.id]);
      if (prev) {
        qc.setQueryData<ProjectDetail>(["project", project.id], {
          ...prev,
          auto_mode: next,
        });
      }
      return { prev };
    },
    onSuccess: (_data, next) => {
      toast.success(next ? "Автопродвижение вкл" : "Автопродвижение выкл");
    },
    onError: (e, _next, ctx) => {
      setOptimistic(null);
      if (ctx?.prev) {
        qc.setQueryData(["project", project.id], ctx.prev);
      }
      toast.error(errorMessageFromUnknown(e));
    },
    onSettled: () => {
      void qc.invalidateQueries({ queryKey: ["project", project.id] });
    },
  });

  return (
    <button
      type="button"
      aria-pressed={autoOn}
      aria-busy={patch.isPending}
      onClick={() => patch.mutate(!autoOn)}
      className={
        className ??
        "pointer-events-auto flex w-full items-start justify-between gap-2 rounded-lg border px-2.5 py-1.5 text-left transition-colors " +
          (autoOn
            ? "border-primary/40 bg-primary/10"
            : "border-border/60 bg-card/70 hover:bg-accent/40")
      }
    >
      <span className="flex flex-col">
        <span className="text-xs font-medium">Автопродвижение</span>
        <span className="text-[10px] text-muted-foreground">
          После ▶ продолжает шаги; контроль — ИИ
        </span>
      </span>
      <span
        className={
          "mt-0.5 h-5 w-9 shrink-0 rounded-full p-0.5 transition-colors " +
          (autoOn ? "bg-primary" : "bg-muted")
        }
      >
        <span
          className={
            "block h-4 w-4 rounded-full bg-white shadow transition-transform " +
            (autoOn ? "translate-x-4" : "")
          }
        />
      </span>
      {patch.isPending ? (
        <Loader2 className="mt-0.5 h-3.5 w-3.5 shrink-0 animate-spin text-muted-foreground" />
      ) : null}
    </button>
  );
}
