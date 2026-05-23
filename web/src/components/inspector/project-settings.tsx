"use client";

import { useMutation, useQueryClient } from "@tanstack/react-query";
import { GitBranch, Loader2 } from "lucide-react";
import { toast } from "sonner";
import { api } from "@/lib/api";
import type { ProjectDetail } from "@/lib/types";
import { cn } from "@/lib/utils";

export function ProjectSettingsPanel({ project }: { project: ProjectDetail }) {
  const qc = useQueryClient();
  const meta = (project.meta || {}) as Record<string, unknown>;
  const graphOn = Boolean(meta.graph_executor);
  const autoOn = project.auto_mode;

  const patch = useMutation({
    mutationFn: (body: Partial<ProjectDetail> & { meta?: Record<string, unknown> }) =>
      api.patchProject(project.id, body),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["project", project.id] });
      toast.success("Настройки проекта сохранены");
    },
    onError: (e) => toast.error(String(e)),
  });

  const toggleMeta = (key: string, value: boolean) => {
    patch.mutate({ meta: { ...meta, [key]: value } });
  };

  return (
    <div className="flex flex-col gap-3 rounded-xl border border-white/10 bg-white/[0.03] p-3">
      <SectionHeader />
      <ToggleRow
        label="Граф-исполнитель"
        hint="Переходы по связям канваса, пропуск отключённых нод"
        active={graphOn}
        disabled={patch.isPending}
        onClick={() => toggleMeta("graph_executor", !graphOn)}
      />
      <ToggleRow
        label="Авто-продвижение"
        hint="После одобрения — автоматически следующий шаг"
        active={autoOn}
        disabled={patch.isPending}
        onClick={() => patch.mutate({ auto_mode: !autoOn })}
      />
      {patch.isPending && (
        <span className="flex items-center gap-1 text-[10px] text-muted-foreground">
          <Loader2 className="h-3 w-3 animate-spin" />
          Сохранение…
        </span>
      )}
    </div>
  );
}

function SectionHeader() {
  return (
    <div className="flex items-center gap-2 text-[10px] font-semibold uppercase tracking-wider text-muted-foreground">
      <GitBranch className="h-3.5 w-3.5" />
      Настройки пайплайна
    </div>
  );
}

function ToggleRow({
  label,
  hint,
  active,
  disabled,
  onClick,
}: {
  label: string;
  hint: string;
  active: boolean;
  disabled?: boolean;
  onClick: () => void;
}) {
  return (
    <button
      type="button"
      disabled={disabled}
      onClick={onClick}
      className={cn(
        "flex w-full items-start justify-between gap-2 rounded-lg border px-2.5 py-2 text-left transition-colors",
        active ? "border-primary/40 bg-primary/10" : "border-border/60 hover:bg-accent/40",
      )}
    >
      <span className="flex flex-col">
        <span className="text-xs font-medium">{label}</span>
        <span className="text-[10px] text-muted-foreground">{hint}</span>
      </span>
      <span
        className={cn(
          "mt-0.5 h-5 w-9 shrink-0 rounded-full p-0.5 transition-colors",
          active ? "bg-primary" : "bg-muted",
        )}
      >
        <span
          className={cn(
            "block h-4 w-4 rounded-full bg-white shadow transition-transform",
            active && "translate-x-4",
          )}
        />
      </span>
    </button>
  );
}
