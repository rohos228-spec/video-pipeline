"use client";

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
      <AutoAdvanceToggle
        project={project}
        className={
          "flex w-full items-start justify-between gap-2 rounded-lg border px-2.5 py-2 text-left transition-colors " +
          (project.auto_mode
            ? "border-primary/40 bg-primary/10"
            : "border-border/60 bg-card/40 hover:bg-accent/40")
        }
      />
      <p className="text-[10px] text-muted-foreground">
        Контроль шагов — только ИИ. Без автопродвижения следующий шаг только по ▶.
      </p>
      <MassFactoryPanel project={project} />
    </div>
  );
}

/** Тумблер автопродвижения (инспектор). */
export function AutoAdvanceToggle({
  project,
  className,
}: {
  project: ProjectDetail;
  className?: string;
}) {
  const qc = useQueryClient();
  const autoOn = project.auto_mode;
  const patch = useMutation({
    mutationFn: (body: Partial<ProjectDetail>) => api.patchProject(project.id, body),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["project", project.id] });
      toast.success(autoOn ? "Автопродвижение выкл" : "Автопродвижение вкл");
    },
    onError: (e) => toast.error(errorMessageFromUnknown(e)),
  });

  return (
    <button
      type="button"
      disabled={patch.isPending}
      onClick={() => patch.mutate({ auto_mode: !autoOn })}
      className={
        className ??
        "flex items-start justify-between gap-2 rounded-lg border px-2.5 py-1.5 text-left transition-colors " +
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
