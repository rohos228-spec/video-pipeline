"use client";

import { useEffect, useRef, useState } from "react";
import { cn } from "@/lib/utils";

export function EditableLabel({
  value,
  onSave,
  className,
  inputClassName,
  title,
  placeholder,
}: {
  value: string;
  onSave: (next: string) => void | Promise<void>;
  className?: string;
  inputClassName?: string;
  title?: string;
  placeholder?: string;
}) {
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState(value);
  const inputRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    if (!editing) setDraft(value);
  }, [value, editing]);

  useEffect(() => {
    if (editing) inputRef.current?.focus();
  }, [editing]);

  const commit = () => {
    const next = draft.trim();
    setEditing(false);
    if (next && next !== value) void onSave(next);
    else setDraft(value);
  };

  if (editing) {
    return (
      <input
        ref={inputRef}
        className={cn("pb-editable-input", inputClassName)}
        value={draft}
        placeholder={placeholder}
        onChange={(e) => setDraft(e.target.value)}
        onBlur={commit}
        onClick={(e) => e.stopPropagation()}
        onMouseDown={(e) => e.stopPropagation()}
        onKeyDown={(e) => {
          e.stopPropagation();
          if (e.key === "Enter") commit();
          if (e.key === "Escape") {
            setDraft(value);
            setEditing(false);
          }
        }}
      />
    );
  }

  return (
    <span
      className={cn("pb-editable-label", className)}
      title={title ?? "Двойной клик — переименовать"}
      onDoubleClick={(e) => {
        e.preventDefault();
        e.stopPropagation();
        setDraft(value);
        setEditing(true);
      }}
    >
      {value || placeholder || "—"}
    </span>
  );
}
