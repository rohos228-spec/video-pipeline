"use client";

import { useEffect, useState } from "react";
import { cn } from "@/lib/utils";

type TextLlmModel = {
  id: string;
  provider: string;
  label: string;
  site: string;
  model: string;
  key_configured: boolean;
  active: boolean;
};

type TextLlmStatus = {
  active_provider: string;
  active_label: string;
  active_model: string;
  models: TextLlmModel[];
};

export function TextLlmPicker() {
  const [status, setStatus] = useState<TextLlmStatus | null>(null);
  const [busy, setBusy] = useState(false);

  const reload = () => {
    fetch("/api/text-llm", { cache: "no-store" })
      .then((r) => (r.ok ? r.json() : null))
      .then((data: TextLlmStatus | null) => {
        if (data?.models) setStatus(data);
      })
      .catch(() => undefined);
  };

  useEffect(() => {
    reload();
  }, []);

  const onChange = async (provider: string, modelId: string) => {
    setBusy(true);
    try {
      const r = await fetch("/api/text-llm", {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ provider, model_id: modelId }),
      });
      if (r.ok) {
        const data = (await r.json()) as TextLlmStatus;
        setStatus(data);
      }
    } finally {
      setBusy(false);
    }
  };

  if (!status?.models?.length) return null;

  const active = status.models.find((m) => m.active) || status.models[0];

  return (
    <select
      className={cn(
        "h-8 max-w-[200px] truncate rounded-md border border-white/10 bg-black/50 px-2 text-[11px] text-white/85",
        "focus:outline-none focus:ring-1 focus:ring-primary/40",
        busy && "opacity-60",
      )}
      disabled={busy}
      value={active.id}
      title={`${status.active_label}\nGPT 5.6 Sol / 5.5 — vibecode.moe; kie и Kimi остаются в списке`}
      onChange={(e) => {
        const m = status.models.find((x) => x.id === e.target.value);
        if (m) void onChange(m.provider, m.id);
      }}
    >
      {status.models.map((m) => (
        <option key={m.id} value={m.id} disabled={!m.key_configured && m.provider !== "kie"}>
          {m.label}
          {!m.key_configured && m.provider !== "kie" ? " · нет ключа" : ""}
        </option>
      ))}
    </select>
  );
}
