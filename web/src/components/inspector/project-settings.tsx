"use client";

import { useMutation, useQueryClient } from "@tanstack/react-query";
import { GitBranch, Loader2, Sparkles, UserRound } from "lucide-react";
import { toast } from "sonner";
import { errorMessageFromUnknown } from "@/lib/error-message";
import { api } from "@/lib/api";
import type { ProjectDetail } from "@/lib/types";
import { cn } from "@/lib/utils";
import { MassFactoryPanel } from "@/components/inspector/mass-factory-panel";
import {
  readControlMode,
  type ControlMode,
} from "@/lib/control-mode";

export function ProjectSettingsPanel({ project }: { project: ProjectDetail }) {
  const qc = useQueryClient();
  const meta = (project.meta || {}) as Record<string, unknown>;
  const graphOn = Boolean(meta.graph_executor);
  const autoOn = project.auto_mode;
  const controlMode = readControlMode(meta);

  const patch = useMutation({
    mutationFn: (body: Partial<ProjectDetail> & { meta?: Record<string, unknown> }) =>
      api.patchProject(project.id, body),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["project", project.id] });
      toast.success("Настройки проекта сохранены");
    },
    onError: (e) => toast.error(errorMessageFromUnknown(e)),
  });

  const toggleMeta = (key: string, value: boolean) => {
    patch.mutate({ meta: { ...meta, [key]: value } });
  };

  const setControlMode = (mode: ControlMode) => {
    const ai = mode === "ai";
    patch.mutate({
      auto_mode: ai,
      meta: {
        ...meta,
        ai_control: ai,
        graph_executor: meta.graph_executor ?? true,
      },
    });
  };

  return (
    <div className="flex flex-col gap-3 rounded-xl border border-white/10 bg-white/[0.03] p-3">
      <SectionHeader />
      <ControlModeSwitch
        mode={controlMode}
        disabled={patch.isPending}
        onChange={setControlMode}
      />
      <MassFactoryPanel project={project} />
      <ToggleRow
        label="Граф-исполнитель"
        hint="Вкл: порядок по стрелкам на канвасе (сохраните граф). Выкл: фиксированная цепочка шагов"
        active={graphOn}
        disabled={patch.isPending}
        onClick={() => toggleMeta("graph_executor", !graphOn)}
      />
      <ToggleRow
        label="Автопродвижение"
        hint="По шагам без одобрения — следующий шаг стартует сразу после завершения текущего"
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

function ControlModeSwitch({
  mode,
  disabled,
  onChange,
}: {
  mode: ControlMode;
  disabled?: boolean;
  onChange: (m: ControlMode) => void;
}) {
  const ai = mode === "ai";
  return (
    <div className="flex flex-col gap-2 rounded-lg border border-border/60 px-2.5 py-2">
      <span className="text-xs font-medium">Контроль пайплайна</span>
      <span className="text-[10px] text-muted-foreground">
        Слева — ручные проверки в UI. Справа — ИИ-контроль и GPT-автоодобрение (как в
        массовой генерации).
      </span>
      <button
        type="button"
        disabled={disabled}
        onClick={() => onChange(ai ? "manual" : "ai")}
        className={cn(
          "relative flex h-10 w-full items-center rounded-full border p-1 transition",
          ai ? "border-red-500/40 bg-red-950/30" : "border-emerald-500/40 bg-emerald-950/20",
        )}
      >
        <span
          className={cn(
            "flex flex-1 items-center justify-center gap-1 text-[10px] font-semibold uppercase tracking-wide",
            !ai ? "text-emerald-400" : "text-muted-foreground",
          )}
        >
          <UserRound className="h-3 w-3" />
          Ручной
        </span>
        <span
          className={cn(
            "flex flex-1 items-center justify-center gap-1 text-[10px] font-semibold uppercase tracking-wide",
            ai ? "text-red-400" : "text-muted-foreground",
          )}
        >
          <Sparkles className="h-3 w-3" />
          ИИ
        </span>
        <span
          className={cn(
            "absolute top-1 h-8 w-[calc(50%-4px)] rounded-full shadow-md transition-all",
            ai
              ? "left-[calc(50%+2px)] bg-gradient-to-br from-red-600 to-red-500"
              : "left-1 bg-gradient-to-br from-emerald-600 to-emerald-500",
          )}
        />
      </button>
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
