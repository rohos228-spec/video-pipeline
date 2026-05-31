"use client";

import { useCallback } from "react";
import { cn } from "@/lib/utils";

export function SidebarResizeHandle({
  width,
  onWidthChange,
  minWidth,
  maxWidth,
}: {
  width: number;
  onWidthChange: (width: number) => void;
  minWidth: number;
  maxWidth: number;
}) {
  const onMouseDown = useCallback(
    (event: React.MouseEvent) => {
      event.preventDefault();
      event.stopPropagation();

      const startX = event.clientX;
      const startWidth = width;

      const onMove = (ev: MouseEvent) => {
        onWidthChange(Math.min(maxWidth, Math.max(minWidth, startWidth + ev.clientX - startX)));
      };

      const onUp = () => {
        document.body.style.cursor = "";
        document.body.style.userSelect = "";
        window.removeEventListener("mousemove", onMove);
        window.removeEventListener("mouseup", onUp);
      };

      document.body.style.cursor = "col-resize";
      document.body.style.userSelect = "none";
      window.addEventListener("mousemove", onMove);
      window.addEventListener("mouseup", onUp);
    },
    [maxWidth, minWidth, onWidthChange, width],
  );

  return (
    <div
      role="separator"
      aria-orientation="vertical"
      aria-valuenow={width}
      title="Потяните, чтобы изменить ширину панели"
      onMouseDown={onMouseDown}
      className={cn(
        "absolute right-0 top-0 z-30 h-full w-2 translate-x-1/2 cursor-col-resize",
        "group/handle touch-none select-none",
      )}
    >
      <span
        className={cn(
          "absolute inset-y-0 right-1/2 w-px translate-x-1/2 bg-transparent transition-colors",
          "group-hover/handle:bg-primary/35 group-active/handle:bg-primary/55",
        )}
      />
    </div>
  );
}
