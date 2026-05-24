"use client";

import type { MouseEvent, ReactNode } from "react";
import {
  AlertCircle,
  CheckCircle2,
  Circle,
  Hourglass,
  Loader2,
  MinusCircle,
  Sparkles,
} from "lucide-react";
import type { HITLDTO, NodeRunStatus, ProjectStatus } from "@/lib/types";
import { cn } from "@/lib/utils";
import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover";
import { formatRunStatus } from "@/lib/format-labels";
import { isProjectRunningStatus } from "@/lib/project-running";
import {
  hitlKindForNodeType,
  resolveHitlBadgeState,
  type HitlBadgeState,
} from "./node-hitl-badge";

const STATUS_VISUAL: Record<
  NodeRunStatus,
  { icon: typeof Circle; className: string; connector: string; label: string }
> = {
  pending: {
    icon: Circle,
    className: "border-muted-foreground/50 bg-muted/90 text-muted-foreground",
    connector: "border-muted-foreground/50",
    label: "Ожидание",
  },
  running: {
    icon: Loader2,
    className: "border-primary/60 bg-primary/20 text-primary shadow-primary/20",
    connector: "border-primary/50",
    label: "Генерация",
  },
  waiting_hitl: {
    icon: Hourglass,
    className: "border-amber-400/70 bg-amber-500/25 text-amber-300",
    connector: "border-amber-400/70",
    label: "Проверка",
  },
  done: {
    icon: CheckCircle2,
    className: "border-emerald-500/60 bg-emerald-500/20 text-emerald-400",
    connector: "border-emerald-500/60",
    label: "Завершено",
  },
  failed: {
    icon: AlertCircle,
    className: "border-destructive/60 bg-destructive/20 text-destructive",
    connector: "border-destructive/60",
    label: "Ошибка",
  },
  skipped: {
    icon: MinusCircle,
    className: "border-muted-foreground/40 bg-muted/70 text-muted-foreground",
    connector: "border-muted-foreground/40",
    label: "Пропуск",
  },
};

const HITL_LABELS: Record<HitlBadgeState, string> = {
  auto_gpt: "Автопроверка GPT",
  manual_idle: "Ручная проверка",
  pending: "Ожидает одобрения",
  regenerating: "Перегенерация",
  approved: "Одобрено",
  rejected: "Отклонено",
};

const NODE_PROJECT_RUNNING: Partial<Record<string, ProjectStatus>> = {
  plan: "planning",
  script: "scripting",
  split: "splitting",
  hero: "generating_hero",
  items: "generating_items",
  enrich_1: "enriching_1",
  enrich_2: "enriching_2",
  enrich_3: "enriching_3",
  enrich_4: "enriching_4",
  enrich_5: "enriching_5",
  image_prompts: "generating_image_prompts",
  images: "generating_images",
  animation_prompts: "generating_animation_prompts",
  videos: "generating_videos",
  audio: "generating_audio",
  assemble: "assembling",
  publish: "publishing",
};

export function NodeGenerationBadge({
  nodeType,
  status,
  progress,
  progressText,
  error,
  attempts,
  projectStatus,
  generationActive,
  autoMode,
  hitlList,
  onOpenHitl,
}: {
  nodeType: string;
  status: NodeRunStatus;
  progress: number;
  progressText: string | null;
  error: string | null;
  attempts: number;
  projectStatus?: ProjectStatus | string | null;
  generationActive?: boolean;
  autoMode: boolean;
  hitlList: HITLDTO[];
  onOpenHitl?: (e: MouseEvent) => void;
}) {
  const visual = STATUS_VISUAL[status];
  const Icon = visual.icon;
  const hitlState = resolveHitlBadgeState({
    nodeType,
    nodeStatus: status,
    autoMode,
    hitlList,
  });
  const expectedStatus = NODE_PROJECT_RUNNING[nodeType];
  const stepRunning =
    Boolean(expectedStatus) &&
    (projectStatus === expectedStatus || (generationActive && status === "running"));
  const showHitlAction = Boolean(hitlState && onOpenHitl && hitlKindForNodeType(nodeType));

  return (
    <>
      <div
        className={cn(
          "pointer-events-none absolute -top-5 left-1/2 z-10 h-5 w-px -translate-x-1/2 border-l-2 border-dashed",
          visual.connector,
        )}
      />
      <Popover>
        <PopoverTrigger asChild>
          <button
            type="button"
            onMouseDown={(e) => e.stopPropagation()}
            className={cn(
              "nodrag nopan absolute -top-12 left-1/2 z-20 flex h-7 w-7 -translate-x-1/2 items-center justify-center rounded-full border-2 shadow-md transition hover:scale-110 hover:brightness-110",
              visual.className,
              status === "running" && "animate-pulse",
            )}
            title={`Статус генерации: ${visual.label}`}
          >
            <Icon className={cn("h-4 w-4", status === "running" && "animate-spin")} />
          </button>
        </PopoverTrigger>
        <PopoverContent side="top" className="w-80" onOpenAutoFocus={(e) => e.preventDefault()}>
          <div className="space-y-2">
            <SectionTitle title="Статус генерации" />
            <Row label="Нода" value={visual.label} />
            <Row label="Run" value={formatRunStatus(status)} />
            {status === "running" && progress > 0 && (
              <Row label="Прогресс" value={`${progress}%`} />
            )}
            {progressText && <Row label="Детали" value={progressText} mono />}
            {error && status === "failed" && (
              <p className="rounded-md bg-destructive/10 px-2 py-1.5 text-[11px] text-destructive">
                {error}
              </p>
            )}
            {attempts > 0 && <Row label="Попытки" value={String(attempts)} />}
            {stepRunning && (
              <Row
                label="Проект"
                value={
                  isProjectRunningStatus(projectStatus)
                    ? "Шаг выполняется на сервере"
                    : "Завершение задачи…"
                }
              />
            )}
            {hitlState && (
              <>
                <SectionTitle
                  title="Проверка (HITL)"
                  icon={<Sparkles className="h-3 w-3 text-violet-400" />}
                />
                <Row label="Режим" value={HITL_LABELS[hitlState]} />
              </>
            )}
            {showHitlAction && (
              <button
                type="button"
                className="mt-1 w-full rounded-lg border border-amber-400/40 bg-amber-500/10 px-2 py-1.5 text-[11px] font-medium text-amber-200 transition hover:bg-amber-500/20"
                onClick={onOpenHitl}
              >
                Открыть проверку
              </button>
            )}
          </div>
        </PopoverContent>
      </Popover>
    </>
  );
}

function SectionTitle({ title, icon }: { title: string; icon?: ReactNode }) {
  return (
    <div className="flex items-center gap-1.5 border-b border-white/10 pb-1.5 text-xs font-semibold">
      {icon}
      {title}
    </div>
  );
}

function Row({ label, value, mono }: { label: string; value: string; mono?: boolean }) {
  return (
    <div className="flex items-start justify-between gap-3 text-[11px]">
      <span className="shrink-0 text-muted-foreground">{label}</span>
      <span className={cn("text-right text-foreground", mono && "font-mono text-[10px]")}>
        {value}
      </span>
    </div>
  );
}
