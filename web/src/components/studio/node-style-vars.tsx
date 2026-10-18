"use client";

import { useEffect, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import { errorMessageFromUnknown } from "@/lib/error-message";

type StyleVar = { key: string; label: string; default: string; value: string; overridden: boolean; type: "text" | "textarea" | "select"; options?: string[] | null };
type Resp = { prompt_name: string; vars: StyleVar[] };

async function j<T>(res: Response): Promise<T> {
  if (!res.ok) throw new Error((await res.text()) || res.statusText);
  return res.json() as Promise<T>;
}

/** Flat «Стиль» section: {{STYLE:key}} vars of the ACTIVE prompt (defaults from sidecar, overrides in project meta). */
export function NodeStyleVars({ projectId, stepCode, nodeKey, slotId, activeVariant }: { projectId: number; stepCode: string; nodeKey: string; slotId?: string; activeVariant?: string }) {
  const qc = useQueryClient();
  const qs = new URLSearchParams({ step_code: stepCode, node_key: nodeKey, ...(slotId ? { slot_id: slotId } : {}) }).toString();
  const qk = ["style-vars", projectId, stepCode, nodeKey, slotId, activeVariant];
  const q = useQuery({ queryKey: qk, queryFn: () => fetch(`/api/projects/${projectId}/style-vars?${qs}`).then((r) => j<Resp>(r)) });
  const [draft, setDraft] = useState<Record<string, string>>({});
  useEffect(() => {
    const d: Record<string, string> = {};
    for (const v of q.data?.vars ?? []) d[v.key] = v.value;
    setDraft(d);
  }, [q.data]);
  const save = useMutation({
    mutationFn: (values: Record<string, string | null>) =>
      fetch(`/api/projects/${projectId}/style-vars`, {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ step_code: stepCode, node_key: nodeKey, slot_id: slotId, prompt_name: q.data?.prompt_name, values }),
      }).then((r) => j<Resp>(r)),
    onSuccess: (data) => {
      qc.setQueryData(qk, data);
      toast.success("Стиль сохранён");
    },
    onError: (e) => toast.error(errorMessageFromUnknown(e)),
  });
  const vars = q.data?.vars ?? [];
  if (!vars.length) return null;
  const changed = Object.fromEntries(vars.filter((v) => (draft[v.key] ?? v.value) !== v.value).map((v) => [v.key, draft[v.key]]));
  const cls = "rounded-md border border-white/10 bg-[#0a0a0a] px-2 text-[14px] text-white";
  return (
    <div data-vp-style-vars className="flex flex-col gap-3">
      <span className="text-[11px] uppercase tracking-[0.12em] text-white/50">Стиль</span>
      {vars.map((v) => (
        <label key={v.key} className="flex flex-col gap-1" data-style-key={v.key}>
          <span className="flex items-center gap-2 text-[13px] text-white/60">
            {v.label}
            {v.overridden ? <span className="text-[11px] text-white/40">· изменено</span> : null}
            <span className="flex-1" />
            {v.overridden ? (
              <button type="button" data-style-reset className="text-[12px] text-white/50 underline-offset-2 hover:text-white hover:underline" onClick={(e) => { e.preventDefault(); save.mutate({ [v.key]: null }); }}>
                сбросить
              </button>
            ) : null}
          </span>
          {v.type === "select" ? (
            <select className={cls + " h-9"} value={draft[v.key] ?? v.value} onChange={(e) => setDraft({ ...draft, [v.key]: e.target.value })}>
              {Array.from(new Set([...(v.options ?? []), draft[v.key] ?? v.value])).map((o) => <option key={o} value={o}>{o}</option>)}
            </select>
          ) : v.type === "textarea" ? (
            <textarea className={cls + " nowheel min-h-[72px] py-2"} value={draft[v.key] ?? v.value} onChange={(e) => setDraft({ ...draft, [v.key]: e.target.value })} />
          ) : (
            <input className={cls + " h-9"} value={draft[v.key] ?? v.value} onChange={(e) => setDraft({ ...draft, [v.key]: e.target.value })} />
          )}
        </label>
      ))}
      <button type="button" data-style-save disabled={!Object.keys(changed).length || save.isPending} className="h-9 w-fit rounded-md border border-white/25 px-5 text-[13px] text-white hover:border-white/60 disabled:opacity-40" onClick={() => save.mutate(changed)}>
        {save.isPending ? "Сохранение…" : "Сохранить стиль"}
      </button>
    </div>
  );
}
