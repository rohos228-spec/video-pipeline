"use client";

import { useEffect, useState, type ReactNode } from "react";
import { Copy, Loader2, Pencil, Trash2 } from "lucide-react";
import { cn } from "@/lib/utils";
import type { BlockMenuTarget } from "./block-context-menu";

export function BlockActionBar({
  target,
  busyAction,
  compact = false,
  onDuplicate,
  onEdit,
  onDelete,
}: {
  target: BlockMenuTarget | null;
  busyAction?: string | null;
  compact?: boolean;
  onDuplicate: (t: BlockMenuTarget) => void;
  onEdit: (t: BlockMenuTarget) => void;
  onRename?: (t: BlockMenuTarget, newId: string) => void;
  onDelete: (t: BlockMenuTarget) => void;
}) {
  const [renameOpen, setRenameOpen] = useState(false);
  const [renameValue, setRenameValue] = useState("");
  const [confirmDelete, setConfirmDelete] = useState(false);

  useEffect(() => {
    if (!busyAction) {
      setConfirmDelete(false);
      setRenameOpen(false);
    }
  }, [busyAction]);

  if (!target) return null;

  const btn = (
    action: string,
    label: string,
    icon: ReactNode,
    onClick: () => void,
    danger = false,
  ) => (
    <button
      type="button"
      disabled={Boolean(busyAction)}
      className={cn("pb-block-action-btn", danger && "pb-block-action-btn-danger", compact && "pb-block-action-btn-compact")}
      title={label}
      onClick={onClick}
    >
      {busyAction === action ? <Loader2 className="h-3 w-3 animate-spin" /> : icon}
      {!compact && <span>{label}</span>}
    </button>
  );

  if (confirmDelete) {
    return (
      <div className="pb-block-action-bar pb-block-action-bar-delete">
        <span className="pb-block-action-warn">Удалить «{target.blockId}»?</span>
        <button
          type="button"
          className="pb-ctx-mini pb-ctx-mini-danger"
          disabled={Boolean(busyAction)}
          onClick={() => onDelete(target)}
        >
          {busyAction === "delete" ? <Loader2 className="h-3 w-3 animate-spin" /> : "Удалить"}
        </button>
        <button type="button" className="pb-ctx-mini pb-ctx-mini-ghost" onClick={() => setConfirmDelete(false)}>
          Отмена
        </button>
      </div>
    );
  }

  return (
    <div
      className={cn("pb-block-action-bar", compact && "pb-block-action-bar-compact")}
      onClick={(e) => e.stopPropagation()}
      onMouseDown={(e) => e.stopPropagation()}
    >
      {!compact && (
        <span className="pb-block-action-label truncate" title={target.blockId}>
          {target.label || target.blockId}
        </span>
      )}
      <div className="pb-block-action-btns">
        {btn("duplicate", "Дубликат", <Copy className="h-3 w-3" />, () => onDuplicate(target))}
        {btn("edit", "Редакт.", <Pencil className="h-3 w-3" />, () => onEdit(target))}
        {btn("delete", "Удалить", <Trash2 className="h-3 w-3" />, () => setConfirmDelete(true), true)}
      </div>
    </div>
  );
}
