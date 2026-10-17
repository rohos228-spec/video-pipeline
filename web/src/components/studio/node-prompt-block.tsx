"use client";

import { useEffect, useRef, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import { api } from "@/lib/api";
import { errorMessageFromUnknown } from "@/lib/error-message";

/** Flat prompt editor at the bottom of the node settings: pick / view / edit / save / rename + node description. */
export function NodePromptBlock({
  stepCode,
  activeVariant,
  onActivate,
  nodeKey,
  description,
}: {
  stepCode: string;
  activeVariant?: string;
  onActivate?: (name: string) => void;
  nodeKey: string;
  description: string;
}) {
  const qc = useQueryClient();
  const files = useQuery({
    queryKey: ["prompt-files", stepCode],
    queryFn: () => api.listPromptFiles(stepCode),
    enabled: Boolean(stepCode),
  });
  const list = files.data ?? [];
  const [sel, setSel] = useState<string | null>(null);
  useEffect(() => {
    if (sel && list.some((f) => f.name === sel)) return;
    const act = list.find((f) => f.name === activeVariant);
    setSel(act?.name ?? list[0]?.name ?? null);
  }, [list, activeVariant, sel]);
  const content = useQuery({
    queryKey: ["prompt-file", stepCode, sel],
    queryFn: () => api.getPromptFile(stepCode, sel!),
    enabled: Boolean(sel),
  });
  const [draft, setDraft] = useState("");
  const [nameDraft, setNameDraft] = useState("");
  const [descDraft, setDescDraft] = useState(description);
  useEffect(() => setDraft(content.data?.content ?? ""), [content.data?.content]);
  useEffect(() => setNameDraft(sel ?? ""), [sel]);
  useEffect(() => setDescDraft(description), [description]);
  const inval = () => {
    qc.invalidateQueries({ queryKey: ["prompt-files"] });
    qc.invalidateQueries({ queryKey: ["prompt-file"] });
    qc.invalidateQueries({ queryKey: ["prompt-global-active"] });
  };
  const save = useMutation({
    mutationFn: async () => {
      let name = sel!;
      const nn = nameDraft.trim();
      if (nn && nn !== name) {
        const info = await api.renamePromptFile(stepCode, name, nn);
        name = info.name;
      }
      if (draft !== (content.data?.content ?? "")) await api.savePromptFile(stepCode, name, draft);
      return name;
    },
    onSuccess: (name) => {
      setSel(name);
      inval();
      toast.success("Промт сохранён");
    },
    onError: (e) => toast.error(errorMessageFromUnknown(e)),
  });
  const fileRef = useRef<HTMLInputElement>(null);
  const upload = useMutation({
    mutationFn: (f: File) => api.uploadPromptFile(stepCode, f),
    onSuccess: (info) => { setSel(info.name); inval(); toast.success("Промт загружен"); },
    onError: (e) => toast.error(errorMessageFromUnknown(e)),
  });
  const del = useMutation({
    mutationFn: () => api.deletePromptFile(stepCode, sel!),
    onSuccess: () => { setSel(null); inval(); toast.success("Промт удалён"); },
    onError: (e) => toast.error(errorMessageFromUnknown(e)),
  });
  const delDisabled = !sel || sel === activeVariant || Boolean(list.find((f) => f.name === sel)?.is_default) || del.isPending;
  const descDirty = descDraft.trim() !== description.trim();
  const dirty = draft !== (content.data?.content ?? "") || (nameDraft.trim() && nameDraft.trim() !== sel);
  return (
    <div data-vp-prompt-block className="flex flex-col gap-3">
      <span className="text-[11px] uppercase tracking-[0.12em] text-white/50">Промт ноды</span>
      <div className="flex items-center gap-2">
        <select
          className="h-9 min-w-0 flex-1 rounded-md border border-white/10 bg-[#0a0a0a] px-2 text-[14px] text-white"
          value={sel ?? ""}
          onChange={(e) => setSel(e.target.value)}
        >
          {list.map((f) => (
            <option key={f.name} value={f.name}>
              {f.title || f.name}
              {f.name === activeVariant ? "  · активен" : ""}
            </option>
          ))}
        </select>
        {sel && sel !== activeVariant && onActivate ? (
          <button type="button" className="h-9 shrink-0 rounded-md border border-white/25 px-3 text-[13px] text-white hover:border-white/60" onClick={() => onActivate(sel)}>
            Сделать активным
          </button>
        ) : null}
        <input ref={fileRef} type="file" accept=".txt,.md,text/plain" className="hidden" onChange={(e) => { const f = e.target.files?.[0]; if (f) upload.mutate(f); e.target.value = ""; }} />
        <button type="button" data-vp-prompt-upload title="Загрузить промт" aria-label="Загрузить промт" disabled={upload.isPending} className="h-9 w-9 shrink-0 rounded-md border border-white/15 text-[16px] text-white/80 hover:border-white/50 disabled:opacity-40" onClick={() => fileRef.current?.click()}>⤒</button>
        <button type="button" data-vp-prompt-delete title={sel === activeVariant ? "Активный промт удалить нельзя" : "Удалить промт"} aria-label="Удалить промт" disabled={delDisabled} className="h-9 w-9 shrink-0 rounded-md border border-white/15 text-[15px] text-red-300/90 hover:border-red-400/60 disabled:opacity-30" onClick={() => { if (sel && window.confirm(`Удалить промт «${sel}»?`)) del.mutate(); }}>✕</button>
      </div>
      {(() => {
        const cur = list.find((f) => f.name === sel);
        const d = cur?.description || (content.data?.content ?? "").split(/\n/).map((l) => l.replace(/^[#>*\-\s]+/, "").trim()).find((l) => l.length >= 20)?.split(/(?<=[.!?])\s/)[0];
        return d ? <p data-vp-prompt-desc className="text-[13px] leading-relaxed text-white/60">{d}</p> : null;
      })()}
      <label className="flex flex-col gap-1">
        <span className="text-[13px] text-white/60">Название промта (файл)</span>
        <input className="h-9 rounded-md border border-white/10 bg-[#0a0a0a] px-2 text-[14px] text-white" value={nameDraft} onChange={(e) => setNameDraft(e.target.value)} />
      </label>
      <label className="flex flex-col gap-1">
        <span className="text-[13px] text-white/60">Текст промта</span>
        <textarea
          className="nowheel min-h-[220px] rounded-md border border-white/10 bg-[#0a0a0a] p-2 font-mono text-[12.5px] leading-snug text-white"
          value={content.isLoading ? "Загрузка…" : draft}
          onChange={(e) => setDraft(e.target.value)}
          spellCheck={false}
        />
      </label>
      <button
        type="button"
        disabled={!sel || !dirty || save.isPending}
        className="h-9 w-fit rounded-md border border-white/25 px-5 text-[13px] text-white hover:border-white/60 disabled:opacity-40"
        onClick={() => save.mutate()}
      >
        {save.isPending ? "Сохранение…" : "Сохранить промт"}
      </button>
      <label className="mt-2 flex flex-col gap-1">
        <span className="text-[13px] text-white/60">Описание ноды</span>
        <textarea
          className="nowheel min-h-[64px] rounded-md border border-white/10 bg-[#0a0a0a] p-2 text-[14px] text-white"
          value={descDraft}
          onChange={(e) => setDescDraft(e.target.value)}
        />
      </label>
      <button
        type="button"
        disabled={!descDirty}
        className="h-9 w-fit rounded-md border border-white/25 px-5 text-[13px] text-white hover:border-white/60 disabled:opacity-40"
        onClick={() => {
          window.dispatchEvent(new CustomEvent("canvas-patch-node-data", { detail: { nodeKey, patch: { description: descDraft.trim() } } }));
          toast.success("Описание сохранено");
        }}
      >
        Сохранить описание
      </button>
    </div>
  );
}
