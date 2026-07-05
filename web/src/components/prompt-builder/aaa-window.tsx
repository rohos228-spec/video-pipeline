"use client";

import { cn } from "@/lib/utils";

export function AaaWindow({
  title,
  children,
  className,
  bodyClassName,
  action,
}: {
  title: string;
  children: React.ReactNode;
  className?: string;
  bodyClassName?: string;
  action?: React.ReactNode;
}) {
  return (
    <div className={cn("pb-glass flex min-h-0 flex-col rounded-lg overflow-hidden", className)}>
      <div className="flex shrink-0 items-center justify-between border-b border-white/[0.06] px-2.5 py-1.5">
        <span className="pb-window-title">{title}</span>
        {action}
      </div>
      <div className={cn("min-h-0 flex-1 overflow-hidden", bodyClassName)}>{children}</div>
    </div>
  );
}

export function AaaListItem({
  label,
  sub,
  active,
  depth = 0,
  onClick,
  dot,
}: {
  label: string;
  sub?: string;
  active?: boolean;
  depth?: number;
  onClick?: () => void;
  dot?: "great" | "ok" | "risky" | "blocked";
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      style={{ paddingLeft: 8 + depth * 12 }}
      className={cn(
        "pb-list-item flex w-full items-center gap-1.5 text-left",
        active && "pb-list-item-active",
      )}
    >
      {dot && <span className={cn("pb-fit-dot", `pb-fit-${dot}`)} />}
      <span className="min-w-0 flex-1 truncate">{label}</span>
      {sub && <span className="shrink-0 text-[9px] opacity-50">{sub}</span>}
    </button>
  );
}
