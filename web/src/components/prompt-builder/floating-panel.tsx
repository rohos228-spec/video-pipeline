"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { cn } from "@/lib/utils";

const MIN_W = 180;
const MIN_H = 120;
const MAX_W = 520;
const MAX_H = 640;

export function FloatingPanel({
  open,
  title,
  subtitle,
  initialPosition,
  initialSize = { w: 248, h: 220 },
  onClose,
  onPin,
  children,
  className,
}: {
  open: boolean;
  title: string;
  subtitle?: string;
  initialPosition: { x: number; y: number };
  initialSize?: { w: number; h: number };
  onClose: () => void;
  onPin?: () => void;
  children: React.ReactNode;
  className?: string;
}) {
  const [pos, setPos] = useState(initialPosition);
  const [size, setSize] = useState(initialSize);
  const dragRef = useRef<{ sx: number; sy: number; ox: number; oy: number } | null>(null);
  const resizeRef = useRef<{ sx: number; sy: number; ow: number; oh: number } | null>(null);

  useEffect(() => {
    if (open) {
      setPos(initialPosition);
      setSize(initialSize);
    }
  // eslint-disable-next-line react-hooks/exhaustive-deps -- reset only when panel opens
  }, [open]);

  const onPointerMove = useCallback((e: PointerEvent) => {
    if (dragRef.current) {
      const d = dragRef.current;
      setPos({
        x: d.ox + e.clientX - d.sx,
        y: d.oy + e.clientY - d.sy,
      });
    }
    if (resizeRef.current) {
      const r = resizeRef.current;
      setSize({
        w: Math.min(MAX_W, Math.max(MIN_W, r.ow + e.clientX - r.sx)),
        h: Math.min(MAX_H, Math.max(MIN_H, r.oh + e.clientY - r.sy)),
      });
    }
  }, []);

  const onPointerUp = useCallback(() => {
    dragRef.current = null;
    resizeRef.current = null;
  }, []);

  useEffect(() => {
    window.addEventListener("pointermove", onPointerMove);
    window.addEventListener("pointerup", onPointerUp);
    return () => {
      window.removeEventListener("pointermove", onPointerMove);
      window.removeEventListener("pointerup", onPointerUp);
    };
  }, [onPointerMove, onPointerUp]);

  if (!open || typeof document === "undefined") return null;

  return createPortal(
    <div
      className={cn("pb-floating-panel pb-settings-fade", className)}
      style={{ left: pos.x, top: pos.y, width: size.w, height: size.h }}
      onMouseEnter={() => onPin?.()}
      onPointerDown={() => onPin?.()}
    >
      <div
        className="pb-floating-panel-header"
        onPointerDown={(e) => {
          if ((e.target as HTMLElement).closest("button")) return;
          e.currentTarget.setPointerCapture(e.pointerId);
          dragRef.current = { sx: e.clientX, sy: e.clientY, ox: pos.x, oy: pos.y };
          onPin?.();
        }}
      >
        <div className="min-w-0 flex-1">
          <p className="truncate text-[11px] font-semibold pb-text">{title}</p>
          {subtitle && <p className="truncate text-[9px] pb-text-muted">{subtitle}</p>}
        </div>
        <button
          type="button"
          className="pb-floating-panel-close"
          aria-label="Закрыть"
          onClick={(e) => {
            e.stopPropagation();
            onClose();
          }}
        >
          ×
        </button>
      </div>
      <div className="pb-floating-panel-body">{children}</div>
      <div
        className="pb-floating-panel-resize"
        onPointerDown={(e) => {
          e.stopPropagation();
          e.currentTarget.setPointerCapture(e.pointerId);
          resizeRef.current = { sx: e.clientX, sy: e.clientY, ow: size.w, oh: size.h };
          onPin?.();
        }}
        aria-hidden
      />
    </div>,
    document.body,
  );
}
