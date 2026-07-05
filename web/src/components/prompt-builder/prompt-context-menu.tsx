"use client";

import { useEffect, useRef } from "react";
import { createPortal } from "react-dom";
import { Loader2, Pencil, Trash2 } from "lucide-react";
import { cn } from "@/lib/utils";

export type PromptMenuTarget = {
  promptId: string;
  label: string;
};

type PromptContextMenuProps = {
  target: PromptMenuTarget | null;
  position: { x: number; y: number } | null;
  busyAction?: string | null;
  onClose: () => void;
  onPreview?: (target: PromptMenuTarget) => void;
  onRename?: (target: PromptMenuTarget) => void;
  onDelete?: (target: PromptMenuTarget) => void;
};

export function PromptContextMenu({
  target,
  position,
  busyAction,
  onClose,
  onRename,
  onDelete,
}: PromptContextMenuProps) {
  const menuRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!target || !position) return;
    const onPointer = (e: MouseEvent) => {
      if (menuRef.current?.contains(e.target as Node)) return;
      onClose();
    };
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    window.addEventListener("mousedown", onPointer);
    window.addEventListener("keydown", onKey);
    return () => {
      window.removeEventListener("mousedown", onPointer);
      window.removeEventListener("keydown", onKey);
    };
  }, [target, position, onClose]);

  if (!target || !position || typeof document === "undefined") return null;

  const clampedX = Math.min(position.x, window.innerWidth - 220);
  const clampedY = Math.min(position.y, window.innerHeight - 120);

  return createPortal(
    <div
      ref={menuRef}
      className="pb-ctx-menu"
      style={{ left: clampedX, top: clampedY }}
      onContextMenu={(e) => e.preventDefault()}
    >
      <p className="pb-ctx-head">{target.label}</p>
      <p className="pb-ctx-sub">меню промта</p>
      <button
        type="button"
        disabled={Boolean(busyAction)}
        className={cn("pb-ctx-item", busyAction === "rename" && "pb-ctx-item-busy")}
        onClick={() => onRename?.(target)}
      >
        {busyAction === "rename" ? (
          <Loader2 className="h-3 w-3 animate-spin" />
        ) : (
          <Pencil className="h-3 w-3" />
        )}
        <span>Переименовать</span>
      </button>
      {onDelete && (
        <button
          type="button"
          disabled={Boolean(busyAction)}
          className="pb-ctx-item pb-ctx-item-danger"
          onClick={() => onDelete(target)}
        >
          {busyAction === "delete" ? (
            <Loader2 className="h-3 w-3 animate-spin" />
          ) : (
            <Trash2 className="h-3 w-3" />
          )}
          <span>Удалить промт</span>
        </button>
      )}
    </div>,
    document.body,
  );
}
