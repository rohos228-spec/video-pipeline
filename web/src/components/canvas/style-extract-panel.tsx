"use client";

import { useEffect, useRef, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Bot, Calculator, Loader2, Play, Square, Trash2, Upload } from "lucide-react";
import { toast } from "sonner";
import { api, type StyleAgentEstimate, type StyleAgentSettings } from "@/lib/api";
import { errorMessageFromUnknown } from "@/lib/error-message";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";

const AGENT_LABELS: Record<string, string> = {
  visual: "рисовка",
  motion: "анимация/монтаж",
  voice: "озвучка/звук",
  narrative: "повествование",
};

const STAGE_LABELS: Record<string, string> = {
  start: "старт",
  extract: "извлечение (ffmpeg/ASR)",
  agent: "специалист",
  aggregate: "агрегация",
  synth: "синтез паспорта",
  done: "готово",
};

function formatBytes(n: number): string {
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(0)} КБ`;
  return `${(n / (1024 * 1024)).toFixed(1)} МБ`;
}

export const STYLE_AGENT_QUERY_KEY = "style-agent";

/** Панель ноды «Разбор стиля»: ролики → лимит LLM → оценка → запуск → черновик паспорта. */
export function StyleExtractPanel({ projectId, nodeKey }: { projectId: number; nodeKey: string }) {
  const qc = useQueryClient();
  const fileRef = useRef<HTMLInputElement>(null);
  const [open, setOpen] = useState(true);
  const [estimate, setEstimate] = useState<StyleAgentEstimate | null>(null);
  const [draft, setDraft] = useState<Partial<StyleAgentSettings>>({});

  const snap = useQuery({
    queryKey: [STYLE_AGENT_QUERY_KEY, projectId, nodeKey],
    queryFn: () => api.getStyleAgent(projectId, nodeKey),
    staleTime: 1500,
    refetchInterval: (q) => (q.state.data?.job?.status === "running" ? 2500 : 6000),
  });
  const data = snap.data;
  const settings: StyleAgentSettings | undefined = data?.settings;
  const job = data?.job;
  const running = job?.status === "running";
  const videos = data?.videos ?? [];

  const invalidate = () => void qc.invalidateQueries({ queryKey: [STYLE_AGENT_QUERY_KEY, projectId] });

  useEffect(() => {
    setEstimate(null);
  }, [videos.length, settings?.max_llm_calls, settings?.max_sheets_per_video, settings?.skip_llm]);

  const upload = useMutation({
    mutationFn: (file: File) => api.uploadStyleRef(projectId, file),
    onSuccess: (r) => {
      toast.success(`Ролик: ${r.name}`);
      invalidate();
    },
    onError: (e) => toast.error(errorMessageFromUnknown(e)),
  });
  const remove = useMutation({
    mutationFn: (name: string) => api.deleteStyleRef(projectId, name),
    onSuccess: invalidate,
    onError: (e) => toast.error(errorMessageFromUnknown(e)),
  });
  const patch = useMutation({
    mutationFn: (p: Partial<StyleAgentSettings>) => api.patchStyleAgentSettings(projectId, p),
    onSuccess: () => {
      setDraft({});
      invalidate();
    },
    onError: (e) => toast.error(errorMessageFromUnknown(e)),
  });
  const est = useMutation({
    mutationFn: () => api.estimateStyleAgent(projectId, nodeKey, draft),
    onSuccess: (r) => setEstimate(r),
    onError: (e) => toast.error(errorMessageFromUnknown(e)),
  });
  const run = useMutation({
    mutationFn: () => api.runStyleAgent(projectId, nodeKey, draft),
    onSuccess: (r) => {
      setEstimate(r.estimate);
      toast.success(`Разбор запущен · лимит ${r.estimate.plan.max_calls} LLM-вызовов`);
      setDraft({});
      invalidate();
    },
    onError: (e) => toast.error(errorMessageFromUnknown(e)),
  });
  const stop = useMutation({
    mutationFn: () => api.stopStyleAgent(projectId),
    onSuccess: () => {
      toast.message("Останавливаю…");
      invalidate();
    },
    onError: (e) => toast.error(errorMessageFromUnknown(e)),
  });

  const eff = { ...(settings ?? {}), ...draft } as StyleAgentSettings;
  const llmUsed = job?.llm?.used ?? 0;
  const llmLimit = job?.llm?.limit ?? eff.max_llm_calls ?? 0;
  const progress = (job?.progress ?? {}) as Record<string, unknown>;

  const commitNumber = (key: keyof StyleAgentSettings, raw: string, int = true) => {
    const n = int ? parseInt(raw, 10) : parseFloat(raw);
    if (Number.isNaN(n)) return;
    patch.mutate({ [key]: n } as Partial<StyleAgentSettings>);
  };

  return (
    <div
      className="nodrag nopan border-t border-fuchsia-500/25 bg-gradient-to-b from-fuchsia-500/10 to-black/20 px-3 py-2.5"
      onMouseDown={(e) => e.stopPropagation()}
      onClick={(e) => e.stopPropagation()}
    >
      <button type="button" className="flex w-full items-center gap-2.5 text-left" onClick={() => setOpen((v) => !v)}>
        <span className="flex h-9 w-9 shrink-0 items-center justify-center rounded-xl border border-fuchsia-400/35 bg-fuchsia-500/20">
          <Bot className="h-4 w-4 text-fuchsia-200" />
        </span>
        <span className="min-w-0 flex-1">
          <span className="block text-[12px] font-semibold tracking-tight text-foreground">Агент разбора стиля</span>
          <span className="mt-0.5 block text-[9px] text-muted-foreground">
            {videos.length ? `${videos.length} ролик(ов)` : "нет роликов"} · LLM {llmUsed}/{llmLimit}
            {job?.status && job.status !== "idle" ? ` · ${job.status}` : ""}
            {data?.passport?.hasDraft ? " · черновик паспорта есть" : ""}
            {" · "}
            {open ? "свернуть" : "показать"}
          </span>
        </span>
      </button>

      {open ? (
        <div className="mt-2.5 space-y-2.5">
          {/* Ролики */}
          <div className="space-y-1">
            <div className="flex items-center justify-between">
              <span className="text-[10px] font-semibold text-fuchsia-100/90">Референсные ролики</span>
              <Button
                type="button"
                size="sm"
                variant="outline"
                className="h-6 text-[10px]"
                disabled={upload.isPending || running}
                onClick={() => fileRef.current?.click()}
                title="mp4/mov/webm — или подключите ноду «Хранилище» с видео стрелкой"
              >
                {upload.isPending ? <Loader2 className="h-3 w-3 animate-spin" /> : <Upload className="h-3 w-3" />}
                Загрузить
              </Button>
              <input
                ref={fileRef}
                type="file"
                multiple
                accept=".mp4,.mov,.webm,.mkv,.m4v"
                className="hidden"
                onChange={(e) => {
                  const list = e.target.files;
                  if (!list?.length) return;
                  Array.from(list).forEach((f) => upload.mutate(f));
                  e.target.value = "";
                }}
              />
            </div>
            {videos.length === 0 ? (
              <p className="text-[9.5px] text-muted-foreground">
                Загрузите 3–5 роликов одного автора или подключите «Хранилище» с видео. 1 ролик = все поля low.
              </p>
            ) : (
              <ul className="max-h-28 space-y-0.5 overflow-auto pr-1">
                {videos.map((v) => (
                  <li key={v.name} className="flex items-center gap-1.5 rounded-md border border-white/10 px-1.5 py-0.5 text-[9.5px]">
                    <span className={cn("h-1.5 w-1.5 shrink-0 rounded-full", v.analyzed ? "bg-emerald-400" : "bg-zinc-500")} />
                    <span className="min-w-0 flex-1 truncate" title={v.name}>
                      {v.name}
                    </span>
                    <span className="shrink-0 text-muted-foreground">{formatBytes(v.size)}</span>
                    {v.sheets.length > 0 ? (
                      <a
                        className="shrink-0 text-fuchsia-200 underline-offset-2 hover:underline"
                        href={api.styleSheetUrl(projectId, v.name, v.sheets[0])}
                        target="_blank"
                        rel="noreferrer"
                        title={`Листы: ${v.sheets.join(", ")}`}
                      >
                        лист
                      </a>
                    ) : null}
                    {v.uploaded ? (
                      <button
                        type="button"
                        className="shrink-0 text-destructive"
                        disabled={running || remove.isPending}
                        onClick={() => remove.mutate(v.name)}
                        title="Удалить ролик"
                      >
                        <Trash2 className="h-3 w-3" />
                      </button>
                    ) : (
                      <span className="shrink-0 text-[8.5px] text-muted-foreground">файлы</span>
                    )}
                  </li>
                ))}
              </ul>
            )}
          </div>

          {/* Настройки */}
          <div className="grid grid-cols-2 gap-x-2 gap-y-1.5 text-[9.5px]">
            <label className="flex flex-col gap-0.5">
              <span className="text-muted-foreground">Лимит LLM-вызовов на запуск</span>
              <input
                type="number"
                min={1}
                max={200}
                className="h-6 rounded-md border border-white/10 bg-black/30 px-1.5 text-[10px]"
                value={eff.max_llm_calls ?? 12}
                disabled={running}
                onChange={(e) => setDraft((d) => ({ ...d, max_llm_calls: parseInt(e.target.value, 10) || 1 }))}
                onBlur={(e) => commitNumber("max_llm_calls", e.target.value)}
              />
            </label>
            <label className="flex flex-col gap-0.5">
              <span className="text-muted-foreground">Листов кадров на ролик</span>
              <input
                type="number"
                min={1}
                max={8}
                className="h-6 rounded-md border border-white/10 bg-black/30 px-1.5 text-[10px]"
                value={eff.max_sheets_per_video ?? 4}
                disabled={running}
                onChange={(e) => setDraft((d) => ({ ...d, max_sheets_per_video: parseInt(e.target.value, 10) || 1 }))}
                onBlur={(e) => commitNumber("max_sheets_per_video", e.target.value)}
              />
            </label>
            <label className="flex flex-col gap-0.5">
              <span className="text-muted-foreground">style_id (латиница)</span>
              <input
                type="text"
                className="h-6 rounded-md border border-white/10 bg-black/30 px-1.5 text-[10px]"
                value={eff.style_id ?? ""}
                placeholder="авто из synth"
                disabled={running}
                onChange={(e) => setDraft((d) => ({ ...d, style_id: e.target.value }))}
                onBlur={(e) => patch.mutate({ style_id: e.target.value })}
              />
            </label>
            <label className="flex flex-col gap-0.5">
              <span className="text-muted-foreground">Язык речи (ASR)</span>
              <input
                type="text"
                className="h-6 rounded-md border border-white/10 bg-black/30 px-1.5 text-[10px]"
                value={eff.language ?? "ru"}
                disabled={running}
                onChange={(e) => setDraft((d) => ({ ...d, language: e.target.value }))}
                onBlur={(e) => patch.mutate({ language: e.target.value })}
              />
            </label>
          </div>
          <div className="flex flex-wrap gap-1">
            {Object.keys(AGENT_LABELS).map((a) => {
              const on = (eff.agents ?? []).includes(a);
              return (
                <button
                  key={a}
                  type="button"
                  disabled={running}
                  onClick={() => {
                    const cur = new Set(eff.agents ?? []);
                    if (cur.has(a)) cur.delete(a);
                    else cur.add(a);
                    patch.mutate({ agents: Array.from(cur) });
                  }}
                  className={cn(
                    "rounded-md border px-1.5 py-0.5 text-[9px] font-medium",
                    on
                      ? "border-fuchsia-400/70 bg-fuchsia-500/25 text-fuchsia-50"
                      : "border-white/10 text-muted-foreground hover:border-white/20",
                  )}
                >
                  {on ? "✓ " : ""}
                  {AGENT_LABELS[a]}
                </button>
              );
            })}
            <button
              type="button"
              disabled={running}
              onClick={() => patch.mutate({ skip_llm: !eff.skip_llm })}
              className={cn(
                "rounded-md border px-1.5 py-0.5 text-[9px] font-medium",
                eff.skip_llm ? "border-amber-400/70 bg-amber-500/25 text-amber-50" : "border-white/10 text-muted-foreground",
              )}
              title="Только ffmpeg/ASR-метрики, без вызовов модели"
            >
              {eff.skip_llm ? "✓ " : ""}без LLM
            </button>
          </div>

          {/* Оценка */}
          {estimate ? (
            <div className="rounded-lg border border-white/10 bg-black/25 p-2 text-[9.5px]">
              <div className="flex flex-wrap items-center gap-x-2">
                <span className="font-semibold text-fuchsia-100">
                  {estimate.plan.planned_total} вызовов · ≈ ${estimate.cost.usd.toFixed(3)}–{estimate.cost.usd_hi.toFixed(3)}
                </span>
                <span className="text-muted-foreground">{estimate.model.label ?? estimate.model.id ?? "модель по умолчанию"}</span>
              </div>
              <div className="mt-0.5 text-muted-foreground">
                роликов {estimate.plan.videos_covered}/{estimate.plan.n_videos} ·{" "}
                {Object.entries(estimate.plan.per_video)
                  .map(([a, n]) => `${AGENT_LABELS[a] ?? a} ×${n}`)
                  .join(", ")}
                {estimate.plan.with_synth ? " + синтез" : ""}
              </div>
              {estimate.plan.hint ? (
                <div className={cn("mt-0.5", estimate.plan.fits ? "text-zinc-300" : "text-amber-300")}>{estimate.plan.hint}</div>
              ) : null}
            </div>
          ) : null}

          {/* Кнопки */}
          <div className="flex flex-wrap gap-1">
            <Button
              type="button"
              size="sm"
              variant="outline"
              className="h-7 text-[10px]"
              disabled={est.isPending || running}
              onClick={() => est.mutate()}
            >
              {est.isPending ? <Loader2 className="h-3 w-3 animate-spin" /> : <Calculator className="h-3 w-3" />}
              Оценить
            </Button>
            {running ? (
              <Button type="button" size="sm" variant="destructive" className="h-7 text-[10px]" disabled={stop.isPending} onClick={() => stop.mutate()}>
                <Square className="h-3 w-3" />
                Стоп
              </Button>
            ) : (
              <Button
                type="button"
                size="sm"
                className="h-7 bg-fuchsia-600 text-[10px] hover:bg-fuchsia-500"
                disabled={run.isPending || videos.length === 0}
                onClick={() => run.mutate()}
                title="Запустить разбор (лимит LLM-вызовов жёсткий)"
              >
                {run.isPending ? <Loader2 className="h-3 w-3 animate-spin" /> : <Play className="h-3 w-3" />}
                Разобрать
              </Button>
            )}
          </div>

          {/* Прогресс / результат */}
          {job && job.status !== "idle" ? (
            <div className="rounded-lg border border-white/10 bg-black/25 p-2 text-[9.5px]">
              <div className="flex items-center gap-1.5">
                {running ? <Loader2 className="h-3 w-3 animate-spin text-amber-300" /> : null}
                <span className="font-semibold">
                  {job.status === "running"
                    ? STAGE_LABELS[String(job.stage ?? "")] ?? job.stage
                    : job.status === "done"
                      ? "Черновик паспорта готов → нода «Проверка стиля»"
                      : job.status === "failed"
                        ? "Ошибка"
                        : job.status === "stopped"
                          ? "Остановлено (частичный паспорт сохранён)"
                          : job.status}
                </span>
                <span className="ml-auto text-muted-foreground">
                  LLM {llmUsed}/{llmLimit}
                </span>
              </div>
              {running && (progress.video || progress.agent) ? (
                <div className="mt-0.5 text-muted-foreground">
                  {String(progress.video ?? "")}
                  {progress.agent ? ` · ${AGENT_LABELS[String(progress.agent)] ?? String(progress.agent)}` : ""}
                  {typeof progress.index === "number" && typeof progress.total === "number"
                    ? ` · ${(progress.index as number) + 1}/${progress.total as number}`
                    : ""}
                </div>
              ) : null}
              {job.error ? <div className="mt-0.5 text-red-300">{job.error}</div> : null}
            </div>
          ) : null}
        </div>
      ) : null}
    </div>
  );
}
