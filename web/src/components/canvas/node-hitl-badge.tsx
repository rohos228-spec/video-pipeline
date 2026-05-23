"use client";

import { Check, Circle, HelpCircle, Loader2, Sparkles, X } from "lucide-react";
import type { HITLDTO } from "@/lib/types";
import { cn } from "@/lib/utils";

export type HitlBadgeState =
  | "auto_gpt"
  | "manual_idle"
  | "pending"
  | "regenerating"
  | "approved"
  | "rejected";

const HITL_NODE_TO_KIND: Record<string, string> = {
  hitl_hero: "approve_hero",
  hitl_images: "approve_images",
  hitl_videos: "approve_videos",
  hitl_final: "approve_final",
};

export function hitlKindForNodeType(nodeType: string): string | null {
  return HITL_NODE_TO_KIND[nodeType] ?? null;
}

export function resolveHitlBadgeState(opts: {
  nodeType: string;
  nodeStatus: string;
  autoMode: boolean;
  hitlList: HITLDTO[];
}): HitlBadgeState | null {
  const kind = hitlKindForNodeType(opts.nodeType);
  if (!kind) return null;

  if (opts.autoMode) return "auto_gpt";

  const hitl = opts.hitlList
    .filter((h) => h.kind === kind)
    .sort((a, b) => b.id - a.id)[0];

  if (opts.nodeStatus === "running") return "regenerating";
  if (hitl?.decision === "pending") return "pending";
  if (hitl?.decision === "regenerate") return "regenerating";
  if (hitl?.decision === "approved") return "approved";
  if (hitl?.decision === "rejected") return "rejected";
  if (opts.nodeStatus === "waiting_hitl") return "pending";
  return "manual_idle";
}

export function NodeHitlBadge({ state }: { state: HitlBadgeState }) {
  const cfg = BADGE[state];
  const Icon = cfg.icon;
  return (
    <div
      className={cn(
        "absolute -top-3 left-1/2 z-20 flex h-6 w-6 -translate-x-1/2 items-center justify-center rounded-full border-2 shadow-md",
        cfg.className,
      )}
      title={cfg.title}
    >
      <Icon className={cn("h-3.5 w-3.5", state === "regenerating" && "animate-spin")} />
    </div>
  );
}

const BADGE: Record<
  HitlBadgeState,
  { icon: typeof HelpCircle; className: string; title: string }
> = {
  auto_gpt: {
    icon: Sparkles,
    className: "border-violet-400/60 bg-violet-500/20 text-violet-300",
    title: "Автопроверка GPT (как в массовой генерации)",
  },
  manual_idle: {
    icon: Circle,
    className: "border-muted-foreground/50 bg-muted text-muted-foreground",
    title: "Ручная проверка",
  },
  pending: {
    icon: HelpCircle,
    className: "border-amber-400/70 bg-amber-500/25 text-amber-300",
    title: "Ожидает одобрения",
  },
  regenerating: {
    icon: Loader2,
    className: "border-primary/50 bg-primary/20 text-primary",
    title: "Перегенерация",
  },
  approved: {
    icon: Check,
    className: "border-emerald-500/60 bg-emerald-500/25 text-emerald-400",
    title: "Одобрено",
  },
  rejected: {
    icon: X,
    className: "border-destructive/60 bg-destructive/20 text-destructive",
    title: "Отклонено",
  },
};
