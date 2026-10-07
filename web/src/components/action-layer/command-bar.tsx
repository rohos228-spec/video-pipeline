"use client";
import {useEffect, useId, useState} from "react";
import {Button} from "@/components/ui/button";
import {
  getCatalog, listMacros, macroTitle, planFromMacro, planFromText, previewFromError, previewPlan, describeError, isRetryable,
  buildMacroParams, macroInitialValues, paramHint, paramLabel, type ActionSpecJson, type MacroJson, type PlanPreviewResponse, type SceneContext,
} from "@/lib/action-layer";
import {isUploading, readyIds, type AttachmentItem} from "@/lib/attachment-view";
import {AttachmentPicker} from "./attachment-picker";

interface Props {
  projectId: number | null;
  sceneId?: number | null;
  frameId?: number | null;
  disabled?: boolean;
  onPlan: (r: PlanPreviewResponse, requestText: string) => void;
  /** Прикреплённые файлы живут в родителе: их же отправляют и при повторе запроса после уточнения. */
  attachments: AttachmentItem[];
  onAttachments: (fn: (prev: AttachmentItem[]) => AttachmentItem[]) => void;
}

export function CommandBar({projectId, sceneId, frameId, disabled, onPlan, attachments, onAttachments}: Props) {
  const inputId = useId();
  const [text, setText] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  // Повтор последнего запроса после временного сбоя планировщика (сервер пометил ошибку retryable).
  const [retry, setRetry] = useState<(() => void) | null>(null);
  const [macros, setMacros] = useState<MacroJson[] | null | undefined>(undefined);
  const [catalog, setCatalog] = useState<ActionSpecJson[]>([]);
  const [actionId, setActionId] = useState("");
  const [paramsText, setParamsText] = useState("{}");
  const [textAvailable, setTextAvailable] = useState(true);
  const [openMacro, setOpenMacro] = useState<MacroJson | null>(null);
  const [macroValues, setMacroValues] = useState<Record<string, string | boolean>>({});

  useEffect(() => {
    let cancelled = false;
    listMacros().then((m) => { if (!cancelled) setMacros(m); }).catch(() => { if (!cancelled) setMacros(null); });
    getCatalog().then((c) => { if (!cancelled) setCatalog(c.actions); }).catch(() => undefined);
    return () => { cancelled = true; };
  }, []);

  const ctx = (): SceneContext | null =>
    projectId == null ? null : {project_id: projectId, selected_scene: sceneId ?? null, selected_frame: frameId ?? null};

  async function guarded(fn: () => Promise<PlanPreviewResponse>, requestText: string, textRoute = false) {
    setBusy(true); setError(""); setRetry(null);
    try {
      const r = await fn();
      setOpenMacro(null);
      onPlan(r, requestText);
    } catch (e) {
      const pv = previewFromError(e);
      if (pv) onPlan({plan_id: null, preview: pv}, requestText);
      else {
        setError(describeError(e));
        if (isRetryable(e)) setRetry(() => () => void guarded(fn, requestText, textRoute));
        if (textRoute && typeof e === "object" && e !== null && (e as {status?: number}).status === 404) setTextAvailable(false);
      }
    } finally {
      setBusy(false);
    }
  }

  const c = ctx();
  const off = disabled || busy || c == null;
  const uploading = isUploading(attachments);
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
          if (c && text.trim() && !uploading) void guarded(() => planFromText(text.trim(), c, readyIds(attachments)), text.trim(), true);
        }}
      >
        <label htmlFor={inputId} className="sr-only">Команда для плана действий</label>
        <input
          id={inputId}
          value={text}
          onChange={(e) => setText(e.target.value)}
          disabled={off || !textAvailable}
          placeholder={c == null ? "сначала открой проект" : textAvailable ? "что сделать? например: поменяй местами картинки кадров 2 и 3" : "составление плана из текста недоступно на сервере"}
          className="h-8 flex-1 rounded-md border border-white/10 bg-black/40 px-2 text-xs focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-primary disabled:opacity-40"
        />
        <Button type="submit" size="sm" variant="outline" className="h-8 text-xs" disabled={off || !textAvailable || !text.trim() || uploading}
          title={uploading ? "Дождитесь конца загрузки файла" : undefined}>
          {busy ? "Составляю…" : "Составить план"}
        </Button>
      </form>
      <AttachmentPicker projectId={projectId} items={attachments} onItems={onAttachments} disabled={disabled || busy || !textAvailable} />

      {macros && macros.length > 0 && (
        <div className="flex flex-wrap items-center gap-1.5" role="group" aria-label="Умные кнопки">
          <span className="text-white/45">Умные кнопки:</span>
          {macros.map((m) => {
            const unavailable = m.available === false;
            return (
              <Button key={m.id} type="button" size="sm" variant="outline" className="h-7 text-[11px]"
                aria-pressed={openMacro?.id === m.id}
                title={unavailable ? `Недоступно: нет действий ${(m.missing_action_ids ?? []).join(", ")}` : m.description_ru}
                disabled={off || unavailable}
                onClick={() => {
                  if (openMacro?.id === m.id) { setOpenMacro(null); return; }
                  setOpenMacro(m); setError("");
                  setMacroValues(macroInitialValues(m, projectId ?? 0));
                }}>
                {macroTitle(m)}
              </Button>
            );
          })}
          {macros.filter((m) => m.available === false).map((m) => (
            <span key={`u${m.id}`} className="text-white/40">«{macroTitle(m)}» недоступна: нет {(m.missing_action_ids ?? []).join(", ")}</span>
          ))}
        </div>
      )}
      {openMacro && c && (
        <form className="space-y-1.5 rounded border border-white/10 p-2" aria-label={`Параметры: ${macroTitle(openMacro)}`} data-testid="macro-form"
          onSubmit={(e) => {
            e.preventDefault();
            const {params, errors} = buildMacroParams(openMacro, macroValues);
            if (errors.length) { setError(errors.join("; ")); return; }
            const m = openMacro;
            void guarded(() => planFromMacro(m.id, params, c, `[умная кнопка] ${macroTitle(m)}`), macroTitle(m));
          }}>
          <p className="font-semibold text-white/85">{macroTitle(openMacro)}</p>
          {openMacro.description_ru && <p className="text-white/50">{openMacro.description_ru}</p>}
          {Object.entries(openMacro.params_schema?.properties ?? {}).filter(([k]) => k !== "project_id").map(([k, sch]) => {
            const required = (openMacro.params_schema?.required ?? []).includes(k);
            const label = sch.title ?? paramLabel(k);
            const hint = paramHint(k);
            const val = macroValues[k];
            const set = (v: string | boolean) => setMacroValues((p) => ({...p, [k]: v}));
            return (
              <label key={k} className="block">
                <span className="text-white/60">{label}{required ? " *" : ""}</span>
                {hint && <span className="ml-1 text-white/35">({hint})</span>}
                {sch.type === "boolean" ? (
                  <input type="checkbox" className="ml-2" checked={Boolean(val)} onChange={(e) => set(e.target.checked)} />
                ) : sch.enum ? (
                  <select value={String(val ?? "")} onChange={(e) => set(e.target.value)} className="ml-2 h-7 rounded border border-white/10 bg-black/40">
                    {!required && <option value="">—</option>}
                    {sch.enum.map((o) => <option key={String(o)} value={String(o)}>{String(o)}</option>)}
                  </select>
                ) : (sch.maxLength ?? 0) > 200 || k === "text" || k === "prompt" || k === "instruction" ? (
                  <textarea rows={2} value={String(val ?? "")} onChange={(e) => set(e.target.value)} required={required}
                    className="mt-0.5 w-full rounded border border-white/10 bg-black/40 p-1" />
                ) : (
                  <input type={sch.type === "integer" || sch.type === "number" ? "number" : "text"} value={String(val ?? "")}
                    min={sch.minimum} max={sch.maximum} required={required} onChange={(e) => set(e.target.value)}
                    className="ml-2 h-7 w-40 rounded border border-white/10 bg-black/40 px-1" />
                )}
              </label>
            );
          })}
          <p className="text-white/40">Проект: #{projectId}. Будет показан план, ничего не запустится.</p>
          <div className="flex gap-2">
            <Button type="submit" size="sm" variant="outline" className="h-7 text-xs" disabled={off}>Показать план</Button>
            <Button type="button" size="sm" variant="ghost" className="h-7 text-xs" onClick={() => setOpenMacro(null)}>Закрыть</Button>
          </div>
        </form>
      )}
      {macros === null && <p className="text-white/35">Умные кнопки недоступны на этом сервере.</p>}

      <details className="text-white/70">
        <summary className="cursor-pointer text-white/50 focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-primary">Выбрать действие вручную</summary>
        <div className="mt-1.5 space-y-1.5">
          <label className="block">
            <span className="text-white/50">Действие</span>
            <select value={actionId} onChange={(e) => {
                const id = e.target.value;
                setActionId(id);
                const a = catalog.find((x) => x.id === id);
                const props = a?.params_schema.properties ?? {};
                setParamsText("project_id" in props && projectId != null ? JSON.stringify({project_id: projectId}) : "{}");
              }}
              className="ml-2 h-7 max-w-full rounded border border-white/10 bg-black/40 text-xs">
              <option value="">— выберите —</option>
              {catalog.map((a) => <option key={a.id} value={a.id}>{a.title_ru}</option>)}
            </select>
          </label>
          {selected && <p className="text-white/45">{selected.description_ru}</p>}
          {selected && Object.keys(selected.params_schema.properties ?? {}).length > 0 && (
            <ul className="space-y-0.5 text-white/45" aria-label="Поля параметров">
              {Object.keys(selected.params_schema.properties ?? {}).map((k) => (
                <li key={k}>
                  <span className="text-white/65">{paramLabel(k)}</span>
                  {(selected.params_schema.required ?? []).includes(k) ? " *" : ""}
                  {paramHint(k) ? ` (${paramHint(k)})` : ""}{paramLabel(k) !== k && <>: <code className="text-white/40">{k}</code></>}
                </li>
              ))}
            </ul>
          )}
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
      {error && (
        <div className="flex flex-wrap items-center gap-2">
          <p role="alert" className="text-red-300">{error}</p>
          {retry && (
            <Button type="button" size="sm" variant="outline" className="h-7 text-xs" data-testid="planner-retry"
              disabled={off} title="Временный сбой планировщика: отправить тот же запрос ещё раз" onClick={retry}>
              Повторить
            </Button>
          )}
        </div>
      )}
    </div>
  );
}
