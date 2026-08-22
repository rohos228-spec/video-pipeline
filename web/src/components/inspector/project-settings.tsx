"use client";

import { useEffect, useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { GitBranch, Loader2 } from "lucide-react";
import { toast } from "sonner";
import { errorMessageFromUnknown } from "@/lib/error-message";
import { api } from "@/lib/api";
import type { ProjectDetail } from "@/lib/types";
import { MassFactoryPanel } from "@/components/inspector/mass-factory-panel";
import { StreamsPanel } from "@/components/inspector/streams-panel";

/** Настройки проекта в инспекторе (без «Контроль пайплайна» — только ИИ). */
export function ProjectSettingsPanel({ project }: { project: ProjectDetail }) {
  return (
    <div className="flex flex-col gap-3">
      <StreamsPanel project={project} />
      <div className="flex flex-col gap-3 rounded-xl border border-white/10 bg-white/[0.03] p-3">
        <div className="flex items-center gap-2 text-[10px] font-semibold uppercase tracking-wider text-muted-foreground">
          <GitBranch className="h-3.5 w-3.5" />
          Настройки пайплайна
        </div>
        <p className="text-[10px] text-muted-foreground">
          Контроль шагов — только ИИ. Автопродвижение — тумблер сверху справа, рядом с
          генерацией. Потоки — блок выше.
        </p>
        <MassFactoryPanel project={project} />
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
        "pointer-events-auto flex w-full items-start justify-between gap-2.5 rounded-xl border px-2.5 py-1.5 text-left transition-all backdrop-blur-md shadow-sm " +
          (autoOn
            ? "border-primary/50 bg-primary/15 ring-1 ring-primary/30"
            : "border-white/20 bg-card/90 hover:border-white/35 hover:bg-card")
      }
    >
      <span className="flex flex-col">
        <span className="text-xs font-semibold text-foreground">Автопродвижение</span>
        <span className="text-[10px] text-muted-foreground">
          После ▶ продолжает шаги; контроль — ИИ
        </span>
      </span>
      <span
        className={
          "mt-0.5 h-5 w-9 shrink-0 rounded-full p-0.5 transition-colors border " +
          (autoOn
            ? "bg-primary border-primary"
            : "bg-black/60 border-white/40 shadow-inner")
        }
      >
        <span
          className={
            "block h-3.5 w-3.5 rounded-full bg-white shadow-md transition-transform " +
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
