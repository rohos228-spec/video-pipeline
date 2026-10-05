"use client";
import {useEffect, useId, useState} from "react";
import {Button} from "@/components/ui/button";
import {
  getCatalog, listMacros, macroTitle, planFromMacro, planFromText, previewFromError, previewPlan, describeError,
  type ActionSpecJson, type MacroJson, type PlanPreviewResponse, type SceneContext,
} from "@/lib/action-layer";

interface Props {
  projectId: number | null;
  sceneId?: number | null;
  frameId?: number | null;
  disabled?: boolean;
  onPlan: (r: PlanPreviewResponse, requestText: string) => void;
}

export function CommandBar({projectId, sceneId, frameId, disabled, onPlan}: Props) {
  const inputId = useId();
  const [text, setText] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [macros, setMacros] = useState<MacroJson[] | null | undefined>(undefined);
  const [catalog, setCatalog] = useState<ActionSpecJson[]>([]);
  const [actionId, setActionId] = useState("");
  const [paramsText, setParamsText] = useState("{}");
  const [textAvailable, setTextAvailable] = useState(true);

  useEffect(() => {
    let cancelled = false;
    listMacros().then((m) => { if (!cancelled) setMacros(m); }).catch(() => { if (!cancelled) setMacros(null); });
    getCatalog().then((c) => { if (!cancelled) setCatalog(c.actions); }).catch(() => undefined);
    return () => { cancelled = true; };
  }, []);

  const ctx = (): SceneContext | null =>
    projectId == null ? null : {project_id: projectId, scene_id: sceneId ?? null, frame_id: frameId ?? null};

  async function guarded(fn: () => Promise<PlanPreviewResponse>, requestText: string, textRoute = false) {
    setBusy(true); setError("");
    try {
      onPlan(await fn(), requestText);
    } catch (e) {
      const pv = previewFromError(e);
      if (pv) onPlan({plan_id: "", preview: pv}, requestText);
      else {
        setError(describeError(e));
        if (textRoute && typeof e === "object" && e !== null && (e as {status?: number}).status === 404) setTextAvailable(false);
      }
    } finally {
      setBusy(false);
    }
  }

  const c = ctx();
  const off = disabled || busy || c == null;
  const selected = catalog.find((a) => a.id === actionId);

  function previewAction() {
    let params: Record<string, unknown>;
    try {
      const parsed: unknown = JSON.parse(paramsText || "{}");
      if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) throw new Error("bad");
      params = parsed as Record<string, unknown>;
    } catch {
      setError("Параметры должны быть JSON-объектом, например {\"project_id\": 1}.");
      return;
    }
    const label = selected?.title_ru ?? actionId;
    void guarded(() => previewPlan([{action_id: actionId, params}], label), label);
  }

  return (
    <div className="space-y-2" data-testid="action-command-bar">
      <form
        className="flex items-center gap-2"
        onSubmit={(e) => {
          e.preventDefault();
          if (c && text.trim()) void guarded(() => planFromText(text.trim(), c), text.trim(), true);
        }}
      >
        <label htmlFor={inputId} className="sr-only">Команда для плана действий</label>
        <input
          id={inputId}
          value={text}
          onChange={(e) => setText(e.target.value)}
          disabled={off || !textAvailable}
          placeholder={c == null ? "сначала открой проект" : textAvailable ? "что сделать? например: поменяй местами планы 2 и 3" : "составление плана из текста недоступно на сервере"}
          className="h-8 flex-1 rounded-md border border-white/10 bg-black/40 px-2 text-xs focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-primary disabled:opacity-40"
        />
        <Button type="submit" size="sm" variant="outline" className="h-8 text-xs" disabled={off || !textAvailable || !text.trim()}>
          {busy ? "Составляю…" : "Составить план"}
        </Button>
      </form>

      {macros && macros.length > 0 && (
        <div className="flex flex-wrap items-center gap-1.5" role="group" aria-label="Умные кнопки">
          <span className="text-[11px] text-white/45">Умные кнопки:</span>
          {macros.map((m) => (
            <Button key={String(m.id)} type="button" size="sm" variant="outline" className="h-7 text-[11px]"
              title={m.description_ru ?? m.description} disabled={off}
              onClick={() => c && void guarded(() => planFromMacro(m.id, c), macroTitle(m))}>
              {macroTitle(m)}
            </Button>
          ))}
        </div>
      )}
      {macros === null && <p className="text-[11px] text-white/35">Умные кнопки недоступны на этом сервере.</p>}

      <details className="text-[11px] text-white/70">
        <summary className="cursor-pointer text-white/50 focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-primary">Выбрать действие вручную</summary>
        <div className="mt-1.5 space-y-1.5">
          <label className="block">
            <span className="text-white/50">Действие</span>
            <select value={actionId} onChange={(e) => setActionId(e.target.value)}
              className="ml-2 h-7 max-w-full rounded border border-white/10 bg-black/40 text-xs">
              <option value="">— выберите —</option>
              {catalog.map((a) => <option key={a.id} value={a.id}>{a.title_ru} ({a.id})</option>)}
            </select>
          </label>
          {selected && <p className="text-white/45">{selected.description_ru}</p>}
          <label className="block">
            <span className="text-white/50">Параметры (JSON)</span>
            <textarea value={paramsText} onChange={(e) => setParamsText(e.target.value)} rows={2}
              className="mt-0.5 w-full rounded border border-white/10 bg-black/40 p-1 font-mono text-xs" />
          </label>
          <Button type="button" size="sm" variant="outline" className="h-7 text-xs" disabled={off || !actionId} onClick={previewAction}>
            Показать план
          </Button>
        </div>
      </details>
      {error && <p role="alert" className="text-[11px] text-red-300">{error}</p>}
    </div>
  );
}
