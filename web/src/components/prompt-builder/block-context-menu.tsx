"use client";

import { useEffect, useRef, useState, type ReactNode } from "react";
import { createPortal } from "react-dom";
import { Copy, Eye, Loader2, Pencil, TextCursorInput, Trash2 } from "lucide-react";
import { cn } from "@/lib/utils";

export type BlockMenuTarget = {
  kind: string;
  blockId: string;
  label: string;
};

type BlockContextMenuProps = {
  target: BlockMenuTarget | null;
  position: { x: number; y: number } | null;
  busyAction: string | null;
  onClose: () => void;
  onPreview: (target: BlockMenuTarget) => void;
  onDuplicate: (target: BlockMenuTarget) => void;
  onEdit: (target: BlockMenuTarget) => void;
  onRenameLabel?: (target: BlockMenuTarget) => void;
  onRename?: (target: BlockMenuTarget, newId: string) => void;
  onDelete: (target: BlockMenuTarget) => void;
};

export function BlockContextMenu({
  target,
  position,
  busyAction,
  onClose,
  onPreview,
  onDuplicate,
  onEdit,
  onRenameLabel,
  onDelete,
}: BlockContextMenuProps) {
  const menuRef = useRef<HTMLDivElement>(null);
  const [renameOpen, setRenameOpen] = useState(false);
  const [renameValue, setRenameValue] = useState("");
  const [confirmDelete, setConfirmDelete] = useState(false);

  useEffect(() => {
    setRenameOpen(false);
    setConfirmDelete(false);
    setRenameValue(target?.blockId ?? "");
  }, [target?.blockId, target?.kind]);

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
  const clampedY = Math.min(position.y, window.innerHeight - 240);

  const item = (
    action: string,
    label: string,
    icon: ReactNode,
    onClick: () => void,
    danger = false,
  ) => (
    <button
      type="button"
      disabled={Boolean(busyAction)}
      className={cn(
        "pb-ctx-item",
        danger && "pb-ctx-item-danger",
        busyAction === action && "pb-ctx-item-busy",
      )}
      onClick={onClick}
    >
      {busyAction === action ? <Loader2 className="h-3 w-3 animate-spin" /> : icon}
      <span>{label}</span>
    </button>
  );

  return createPortal(
    <div
      ref={menuRef}
      className="pb-ctx-menu"
      style={{ left: clampedX, top: clampedY }}
      onContextMenu={(e) => e.preventDefault()}
    >
      <p className="pb-ctx-head">{target.label || target.blockId}</p>
      <p className="pb-ctx-sub">{target.kind}</p>

      {!renameOpen && !confirmDelete && (
        <>
          {item("preview", "Просмотр", <Eye className="h-3 w-3" />, () => onPreview(target))}
          {item("duplicate", "Дубликат", <Copy className="h-3 w-3" />, () => onDuplicate(target))}
          {item("edit", "Редактировать", <Pencil className="h-3 w-3" />, () => onEdit(target))}
          {onRenameLabel &&
            item(
              "rename_label",
              "Переименовать",
              <TextCursorInput className="h-3 w-3" />,
              () => onRenameLabel(target),
            )}
          {item(
            "delete",
            "Удалить",
            <Trash2 className="h-3 w-3" />,
            () => setConfirmDelete(true),
            true,
          )}
        </>
      )}

      {confirmDelete && (
        <div className="pb-ctx-rename">
          <p className="pb-ctx-warn">Удалить «{target.blockId}»?</p>
          <div className="flex gap-1">
            <button
              type="button"
              className="pb-ctx-mini pb-ctx-mini-danger"
              disabled={Boolean(busyAction)}
              onClick={() => onDelete(target)}
            >
              {busyAction === "delete" ? <Loader2 className="h-3 w-3 animate-spin" /> : "Удалить"}
            </button>
            <button
              type="button"
              className="pb-ctx-mini pb-ctx-mini-ghost"
              onClick={() => setConfirmDelete(false)}
            >
              Отмена
            </button>
          </div>
        </div>
      )}
    </div>,
    document.body,
  );
}
