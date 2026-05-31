"use client";

import type { MouseEvent } from "react";
import { Check, Circle, HelpCircle, Loader2, Sparkles, X } from "lucide-react";
import type { HITLDTO, ProjectStatus } from "@/lib/types";
import { cn } from "@/lib/utils";

export type HitlBadgeState =
  | "auto_gpt"
  | "manual_idle"
  | "pending"
  | "regenerating"
  | "approved"
  | "rejected";

// Маппинг типа ноды на HITL-kind, который к ней относится.
// Расширен на основные content-ноды, чтобы каждая нода имела статус-
// кружок сверху (как просил юзер): "ручная проверка как у обычной,
// автопроверка как у массовой генерации".
const HITL_NODE_TO_KIND: Record<string, string> = {
  plan: "approve_plan",
  script: "approve_script",
  hero: "approve_hero",
  images: "approve_images",
  videos: "approve_videos",
  assemble: "approve_final",
  // Старые dedicated HITL-ноды (для обратной совместимости).
  hitl_hero: "approve_hero",
  hitl_images: "approve_images",
  hitl_videos: "approve_videos",
  hitl_final: "approve_final",
};

export function hitlKindForNodeType(nodeType: string): string | null {
  return HITL_NODE_TO_KIND[nodeType] ?? null;
}

/** Пайплайн уже ушёл дальше *_ready — HITL-gate для этой ноды закрыт. */
const STATUS_ORDER: ProjectStatus[] = [
  "new",
  "planning",
  "plan_ready",
  "scripting",
  "script_ready",
  "splitting",
  "frames_ready",
  "generating_hero",
  "hero_ready",
  "generating_items",
  "items_ready",
  "enriching_1",
  "enrich_1_ready",
  "enriching_2",
  "enrich_2_ready",
  "enriching_3",
  "enrich_3_ready",
  "enriching_4",
  "enrich_4_ready",
  "enriching_5",
  "enrich_5_ready",
  "generating_image_prompts",
  "image_prompts_ready",
  "generating_images",
  "images_ready",
  "generating_animation_prompts",
  "animation_prompts_ready",
  "generating_videos",
  "videos_ready",
  "generating_audio",
  "audio_ready",
  "assembling",
  "assembled",
  "publishing",
  "published",
  "paused",
  "failed",
];

const HITL_GATE_READY: Record<string, ProjectStatus> = {
  plan: "plan_ready",
  script: "script_ready",
  hero: "hero_ready",
  images: "images_ready",
  videos: "videos_ready",
  assemble: "assembled",
};

export function hitlGatePassedForNode(
  nodeType: string,
  projectStatus: ProjectStatus | string | undefined | null,
): boolean {
  const gate = HITL_GATE_READY[nodeType];
  if (!gate || !projectStatus) return false;
  const curIdx = STATUS_ORDER.indexOf(projectStatus as ProjectStatus);
  const gateIdx = STATUS_ORDER.indexOf(gate);
  if (curIdx < 0 || gateIdx < 0) return false;
  return curIdx > gateIdx;
}

export function resolveHitlBadgeState(opts: {
  nodeType: string;
  nodeStatus: string;
  projectStatus?: ProjectStatus | string | null;
  autoMode: boolean;
  aiControl?: boolean;
  hitlList: HITLDTO[];
}): HitlBadgeState | null {
  const kind = hitlKindForNodeType(opts.nodeType);
  if (!kind) return null;

  if (opts.aiControl) return "auto_gpt";

  const hitl = opts.hitlList
    .filter((h) => h.kind === kind)
    .sort((a, b) => b.id - a.id)[0];

  const gatePassed = hitlGatePassedForNode(opts.nodeType, opts.projectStatus);

  if (opts.nodeStatus === "running") return "regenerating";
  if (hitl?.decision === "regenerate") return "regenerating";
  if (hitl?.decision === "rejected") return "rejected";
  if (hitl?.decision === "approved" || gatePassed || opts.nodeStatus === "done") {
    return "approved";
  }
  if (hitl?.decision === "pending") return "pending";
  if (opts.nodeStatus === "waiting_hitl") return "pending";
  return "manual_idle";
}

export function NodeHitlBadge({
  state,
  onClick,
}: {
  state: HitlBadgeState;
  onClick?: (e: MouseEvent) => void;
}) {
  const cfg = BADGE[state];
  const Icon = cfg.icon;
  const clickable = Boolean(onClick);
  return (
    <>
      <div
        className={cn(
          "pointer-events-none absolute -top-5 left-1/2 z-10 h-5 w-px -translate-x-1/2 border-l-2 border-dashed",
          cfg.connectorClass,
        )}
      />
      <button
        type="button"
        onClick={onClick}
        onMouseDown={(e) => e.stopPropagation()}
        disabled={!clickable}
        className={cn(
          "nodrag nopan absolute -top-12 left-1/2 z-20 flex h-7 w-7 -translate-x-1/2 items-center justify-center rounded-full border-2 shadow-md transition",
          cfg.className,
          clickable && "cursor-pointer hover:scale-110 hover:brightness-110",
          !clickable && "cursor-default",
        )}
        title={
          clickable
            ? `${cfg.title} — открыть проверку (как в Telegram)`
            : cfg.title
        }
      >
        <Icon
          className={cn("h-4 w-4", state === "regenerating" && "animate-spin")}
        />
      </button>
    </>
  );
}

const BADGE: Record<
  HitlBadgeState,
  {
    icon: typeof HelpCircle;
    className: string;
    title: string;
    connectorClass: string;
  }
> = {
  auto_gpt: {
    icon: Sparkles,
    className: "border-violet-400/60 bg-violet-500/20 text-violet-300",
    title: "Автопроверка GPT (как в массовой генерации)",
    connectorClass: "border-violet-400/60",
  },
  manual_idle: {
    icon: Circle,
    className: "border-muted-foreground/50 bg-muted text-muted-foreground",
    title: "Ручная проверка",
    connectorClass: "border-muted-foreground/50",
  },
  pending: {
    icon: HelpCircle,
    className: "border-amber-400/70 bg-amber-500/25 text-amber-300",
    title: "Ожидает одобрения",
    connectorClass: "border-amber-400/70",
  },
  regenerating: {
    icon: Loader2,
    className: "border-primary/50 bg-primary/20 text-primary",
    title: "Перегенерация",
    connectorClass: "border-primary/50",
  },
  approved: {
    icon: Check,
    className: "border-emerald-500/60 bg-emerald-500/25 text-emerald-400",
    title: "Одобрено",
    connectorClass: "border-emerald-500/60",
  },
  rejected: {
    icon: X,
    className: "border-destructive/60 bg-destructive/20 text-destructive",
    title: "Отклонено",
    connectorClass: "border-destructive/60",
  },
};
