"use client";

import { cn } from "@/lib/utils";
import {
  ORCHESTRATOR_FIELDS_BY_KIND,
  type OrchestratorField,
} from "@/lib/prompt-builder/orchestrator-vars";
import type { BlockKind } from "@/lib/prompt-builder/types";

export function OrchestratorSettingsPanel({
  slotKind,
  blockLabel,
  slotLabel,
  vars,
  onChangeVar,
  onClose,
  className,
}: {
  slotKind: BlockKind;
  blockLabel: string;
  slotLabel: string;
  vars: Record<string, string | number>;
  onChangeVar: (key: string, value: string | number) => void;
  onClose: () => void;
  className?: string;
}) {
  const fields = ORCHESTRATOR_FIELDS_BY_KIND[slotKind] ?? [];

  return (
    <div className={cn("pb-orchestrator-panel pb-settings-fade w-[240px]", className)}>
      <div className="flex items-start justify-between gap-2 border-b border-black/[0.06] px-3 py-2">
        <div>
          <p className="pb-panel-title">Оркестратор</p>
          <p className="mt-0.5 text-[10px] font-medium text-black/70">{blockLabel}</p>
          <p className="text-[9px] text-black/35">{slotLabel}</p>
        </div>
        <button
          type="button"
          onClick={onClose}
          className="text-[14px] leading-none text-black/30 hover:text-black/55"
        >
          ×
        </button>
      </div>

      <div className="max-h-[min(420px,calc(100vh-120px))] overflow-y-auto px-3 py-2">
        <p className="mb-2 text-[9px] leading-relaxed text-black/35">
          Параметры уходят оркестратору. Финальный агент собирается после всех блоков.
        </p>
        <div className="space-y-3">
          {fields.map((f) => (
            <FieldControl key={f.key} field={f} value={vars[f.key]} onChange={onChangeVar} />
          ))}
        </div>
      </div>
    </div>
  );
}

function FieldControl({
  field,
  value,
  onChange,
}: {
  field: OrchestratorField;
  value: string | number | undefined;
  onChange: (key: string, value: string | number) => void;
}) {
  const v = value ?? "";

  return (
    <label className="block">
      <span className="mb-1 flex items-center justify-between text-[9px] text-black/45">
        {field.label}
        {field.type === "slider" && (
          <span className="font-mono text-black/55">{String(v)}</span>
        )}
      </span>
      {field.type === "slider" && (
        <input
          type="range"
          min={field.min ?? 0}
          max={field.max ?? 100}
          step={field.step ?? 1}
          value={typeof v === "number" ? v : Number(v) || 0}
          onChange={(e) => onChange(field.key, Number(e.target.value))}
          className="pb-range w-full"
        />
      )}
      {field.type === "number" && (
        <input
          type="number"
          min={field.min}
          max={field.max}
          value={v}
          onChange={(e) => onChange(field.key, Number(e.target.value))}
          className="pb-field-input w-full"
        />
      )}
      {field.type === "text" && (
        <input
          type="text"
          value={String(v)}
          placeholder={field.placeholder}
          onChange={(e) => onChange(field.key, e.target.value)}
          className="pb-field-input w-full"
        />
      )}
    </label>
  );
}
