"use client";

import { useEffect, useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { CheckSquare, Download, FileText, Loader2, Maximize2, Save } from "lucide-react";
import { toast } from "sonner";
import { api, type StylePassport, type StylePassportField } from "@/lib/api";
import { errorMessageFromUnknown } from "@/lib/error-message";
import { Button } from "@/components/ui/button";
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { cn } from "@/lib/utils";
import { MarkdownRenderer } from "@/components/gpt/markdown-renderer";
import { STYLE_AGENT_QUERY_KEY } from "./style-extract-panel";

const AXES: { key: keyof StylePassport; label: string }[] = [
  { key: "format", label: "Формат" },
  { key: "visual", label: "Рисовка" },
  { key: "motion", label: "Анимация / монтаж" },
  { key: "voice", label: "Озвучка" },
  { key: "music", label: "Музыка" },
  { key: "sfx", label: "SFX" },
  { key: "narrative", label: "Повествование" },
  { key: "linkage", label: "Связка VO↔план" },
  { key: "script", label: "Сценарий (метрики)" },
];

const MAPPING_KEYS: { key: string; label: string; multiline?: boolean }[] = [
  { key: "visual_core_en", label: "visual_style (EN)", multiline: true },
  { key: "lighting_en", label: "lighting (EN)" },
  { key: "negative_en", label: "negative (EN, через запятую)" },
  { key: "anim_negative_en", label: "anim_negative (EN)" },
  { key: "camera_motion_en", label: "camera_motion (EN)" },
  { key: "composition_en", label: "composition (EN)" },
  { key: "character_anatomy_en", label: "character_anatomy (EN)" },
  { key: "voice_tone_ru", label: "voice_tone (RU)", multiline: true },
  { key: "narrative_ru", label: "narrative_structure (RU)", multiline: true },
  { key: "forbidden_phrases_ru", label: "forbidden_phrases (RU, через запятую)" },
  { key: "music_suno_core_en", label: "Suno core (EN)" },
  { key: "frame_style_lock_en", label: "style lock для кадров (EN)", multiline: true },
];

const CONF_CLASS: Record<string, string> = {
  high: "border-emerald-400/50 bg-emerald-500/15 text-emerald-100",
  medium: "border-amber-400/50 bg-amber-500/15 text-amber-100",
  low: "border-zinc-500/50 bg-zinc-600/20 text-zinc-300",
};

function fmtValue(v: unknown): string {
  if (v == null) return "—";
  if (Array.isArray(v)) return v.map(String).join(", ");
  if (typeof v === "object") {
    return Object.entries(v as Record<string, unknown>)
      .map(([k, n]) => `${k} ${typeof n === "number" ? Math.round(n * 100) + "%" : String(n)}`)
      .join(", ");
  }
  if (typeof v === "boolean") return v ? "да" : "нет";
  return String(v);
}

function mappingToText(v: unknown): string {
  if (v == null) return "";
  if (Array.isArray(v)) return v.map(String).join(", ");
  return String(v);
}

function textToMapping(key: string, text: string): unknown {
  const t = text.trim();
  if (!t) return null;
  if (key === "negative_en" || key === "anim_negative_en" || key === "forbidden_phrases_ru") {
    return t.split(/[,;\n]/).map((s) => s.trim()).filter(Boolean);
  }
  return t;
}

/** Панель ноды «Проверка стиля»: паспорт по осям, правка mapping, утверждение → материализация. */
export function HitlStylePanel({ projectId }: { projectId: number }) {
  const qc = useQueryClient();
  const [open, setOpen] = useState(true);
  const [tab, setTab] = useState<"fields" | "mapping" | "report">("fields");
  const [axis, setAxis] = useState<string>("visual");
  const [edited, setEdited] = useState<StylePassport | null>(null);
  const [showReport, setShowReport] = useState(false);
  const [reportView, setReportView] = useState<"full" | "variance">("full");
  const [reportOpen, setReportOpen] = useState(false);

  const snap = useQuery({
    queryKey: [STYLE_AGENT_QUERY_KEY, projectId, "hitl"],
    queryFn: () => api.getStyleAgent(projectId),
    staleTime: 1500,
    refetchInterval: (q) => (q.state.data?.job?.status === "running" ? 2500 : 8000),
  });
  const hasDraft = !!snap.data?.passport?.hasDraft;
  const passportQ = useQuery({
    queryKey: [STYLE_AGENT_QUERY_KEY, projectId, "passport", snap.data?.passport?.draftAt ?? ""],
    queryFn: () => api.getStylePassport(projectId),
    enabled: hasDraft,
    staleTime: 3000,
  });
  const reportQ = useQuery({
    queryKey: [STYLE_AGENT_QUERY_KEY, projectId, "report", snap.data?.passport?.approvedAt ?? ""],
    queryFn: () => api.getStyleAgentReport(projectId),
    enabled: showReport || reportOpen || tab === "report",
    staleTime: 5000,
  });

  useEffect(() => {
    if (passportQ.data?.passport) setEdited(structuredClone(passportQ.data.passport));
  }, [passportQ.data]);

  const invalidate = () => void qc.invalidateQueries({ queryKey: [STYLE_AGENT_QUERY_KEY, projectId] });

  const save = useMutation({
    mutationFn: () => api.putStylePassport(projectId, edited as StylePassport),
    onSuccess: () => {
      toast.success("Черновик сохранён");
      invalidate();
    },
    onError: (e) => toast.error(errorMessageFromUnknown(e)),
  });
  const approve = useMutation({
    mutationFn: () => api.approveStylePassport(projectId, edited, true),
    onSuccess: (r) => {
      const blocks = r.report.blocks?.length ?? 0;
      toast.success(`Стиль «${r.report.style_id}» утверждён · блоков ${blocks}${r.report.frame_style ? ` · ${r.report.frame_style}` : ""}`);
      invalidate();
      setShowReport(true);
      setTab("report");
    },
    onError: (e) => toast.error(errorMessageFromUnknown(e)),
  });

  const p = edited;
  const fields = useMemo(() => {
    const ax = (p?.[axis as keyof StylePassport] ?? {}) as Record<string, StylePassportField>;
    return Object.entries(ax).filter(([, f]) => f && typeof f === "object" && "value" in f);
  }, [p, axis]);
  const stats = useMemo(() => {
    const out = { high: 0, medium: 0, low: 0 };
    if (!p) return out;
    for (const a of AXES) {
      const ax = (p[a.key] ?? {}) as Record<string, StylePassportField>;
      for (const f of Object.values(ax)) {
        if (f && typeof f === "object" && f.confidence in out) out[f.confidence] += 1;
      }
    }
    return out;
  }, [p]);
  const approvedAt = snap.data?.passport?.approvedAt;
  const applyInfo = (snap.data?.apply ?? {}) as { next_steps?: string[]; skipped?: string[]; frame_style?: string; blocks?: string[] };

  return (
    <div
      className="nodrag nopan border-t border-fuchsia-500/25 bg-gradient-to-b from-fuchsia-500/10 to-black/20 px-3 py-2.5"
      onMouseDown={(e) => e.stopPropagation()}
      onClick={(e) => e.stopPropagation()}
    >
      <button type="button" className="flex w-full items-center gap-2.5 text-left" onClick={() => setOpen((v) => !v)}>
        <span className="flex h-9 w-9 shrink-0 items-center justify-center rounded-xl border border-fuchsia-400/35 bg-fuchsia-500/20">
          <CheckSquare className="h-4 w-4 text-fuchsia-200" />
        </span>
        <span className="min-w-0 flex-1">
          <span className="block text-[12px] font-semibold tracking-tight text-foreground">
            Паспорт стиля {p?.label ? `· ${p.label}` : ""}
          </span>
          <span className="mt-0.5 block text-[9px] text-muted-foreground">
            {!hasDraft
              ? "черновика нет — запустите «Разбор стиля»"
              : approvedAt
                ? `утверждён ${new Date(approvedAt).toLocaleString("ru-RU")}`
                : `high ${stats.high} · medium ${stats.medium} · low ${stats.low}`}
            {" · "}
            {open ? "свернуть" : "показать"}
          </span>
        </span>
      </button>

      {open && hasDraft && p ? (
        <div className="mt-2.5 space-y-2">
          <div className="flex flex-wrap gap-1">
            {(["fields", "mapping", "report"] as const).map((t) => (
              <button
                key={t}
                type="button"
                onClick={() => setTab(t)}
                className={cn(
                  "rounded-md border px-1.5 py-0.5 text-[9px] font-medium",
                  tab === t ? "border-fuchsia-400/70 bg-fuchsia-500/25 text-fuchsia-50" : "border-white/10 text-muted-foreground",
                )}
              >
                {t === "fields" ? "Поля" : t === "mapping" ? "Промпты" : "Отчёт"}
              </button>
            ))}
            <span className="ml-auto text-[9px] text-muted-foreground">
              {p.sources?.count ?? 0} роликов · LLM {p.llm?.calls_used ?? 0}/{p.llm?.calls_limit ?? 0}
            </span>
          </div>

          {tab === "fields" ? (
            <>
              <div className="flex flex-wrap gap-1">
                {AXES.map((a) => (
                  <button
                    key={a.key}
                    type="button"
                    onClick={() => setAxis(a.key)}
                    className={cn(
                      "rounded-md border px-1.5 py-0.5 text-[9px]",
                      axis === a.key ? "border-fuchsia-400/70 bg-fuchsia-500/25 text-fuchsia-50" : "border-white/10 text-muted-foreground",
                    )}
                  >
                    {a.label}
                  </button>
                ))}
              </div>
              <ul className="max-h-56 space-y-1 overflow-auto pr-1 text-[9.5px]">
                {fields.length === 0 ? <li className="text-muted-foreground">нет данных по оси</li> : null}
                {fields.map(([key, f]) => (
                  <li key={key} className="rounded-md border border-white/10 bg-black/25 px-1.5 py-1">
                    <div className="flex items-center gap-1.5">
                      <span className="font-mono text-[9px] text-fuchsia-100/90">{key}</span>
                      <span className={cn("rounded border px-1 text-[8.5px]", CONF_CLASS[f.confidence] ?? CONF_CLASS.low)}>
                        {f.confidence} · {f.support}
                      </span>
                      <select
                        className="ml-auto h-5 rounded border border-white/10 bg-black/40 text-[8.5px]"
                        value={f.confidence}
                        title="Оператор может поднять/понизить уверенность: low не материализуется"
                        onChange={(e) =>
                          setEdited((prev) => {
                            if (!prev) return prev;
                            const next = structuredClone(prev);
                            const ax = next[axis as keyof StylePassport] as Record<string, StylePassportField>;
                            ax[key] = { ...ax[key], confidence: e.target.value as StylePassportField["confidence"], support: "manual" };
                            return next;
                          })
                        }
                      >
                        <option value="high">high</option>
                        <option value="medium">medium</option>
                        <option value="low">low</option>
                      </select>
                    </div>
                    <div className="mt-0.5 break-words text-zinc-200">{fmtValue(f.value)}</div>
                    {f.note ? <div className="mt-0.5 text-[8.5px] text-amber-200/80">{f.note}</div> : null}
                  </li>
                ))}
              </ul>
            </>
          ) : null}

          {tab === "mapping" ? (
            <div className="max-h-64 space-y-1.5 overflow-auto pr-1 text-[9.5px]">
              <label className="flex flex-col gap-0.5">
                <span className="text-muted-foreground">style_id · label</span>
                <div className="flex gap-1">
                  <input
                    className="h-6 w-28 rounded-md border border-white/10 bg-black/30 px-1.5 font-mono text-[10px]"
                    value={p.style_id}
                    onChange={(e) => setEdited((prev) => (prev ? { ...prev, style_id: e.target.value } : prev))}
                  />
                  <input
                    className="h-6 flex-1 rounded-md border border-white/10 bg-black/30 px-1.5 text-[10px]"
                    value={p.label}
                    onChange={(e) => setEdited((prev) => (prev ? { ...prev, label: e.target.value } : prev))}
                  />
                </div>
              </label>
              {MAPPING_KEYS.map((m) => (
                <label key={m.key} className="flex flex-col gap-0.5">
                  <span className="text-muted-foreground">{m.label}</span>
                  {m.multiline ? (
                    <textarea
                      rows={3}
                      className="rounded-md border border-white/10 bg-black/30 px-1.5 py-1 text-[10px]"
                      value={mappingToText(p.mapping?.[m.key])}
                      onChange={(e) =>
                        setEdited((prev) =>
                          prev ? { ...prev, mapping: { ...(prev.mapping ?? {}), [m.key]: textToMapping(m.key, e.target.value) } } : prev,
                        )
                      }
                    />
                  ) : (
                    <input
                      className="h-6 rounded-md border border-white/10 bg-black/30 px-1.5 text-[10px]"
                      value={mappingToText(p.mapping?.[m.key])}
                      onChange={(e) =>
                        setEdited((prev) =>
                          prev ? { ...prev, mapping: { ...(prev.mapping ?? {}), [m.key]: textToMapping(m.key, e.target.value) } } : prev,
                        )
                      }
                    />
                  )}
                </label>
              ))}
              {p.variance_notes?.length ? (
                <div className="rounded-md border border-amber-400/30 bg-amber-500/10 p-1.5 text-[9px] text-amber-100">
                  {p.variance_notes.map((n, i) => (
                    <div key={i}>• {n}</div>
                  ))}
                </div>
              ) : null}
            </div>
          ) : null}

          {tab === "report" ? (
            <div className="max-h-[32rem] space-y-1.5 overflow-auto pr-1 text-[9.5px]">
              {approvedAt ? (
                <div className="rounded-md border border-emerald-400/30 bg-emerald-500/10 p-1.5 text-emerald-100">
                  <div className="font-semibold">Записано в Studio</div>
                  {applyInfo.blocks?.length ? <div>блоки: {applyInfo.blocks.join(", ")}</div> : null}
                  {applyInfo.frame_style ? <div>style lock: {applyInfo.frame_style}</div> : null}
                  {(applyInfo.next_steps ?? []).map((s, i) => (
                    <div key={i}>→ {s}</div>
                  ))}
                  {(applyInfo.skipped ?? []).map((s, i) => (
                    <div key={`s${i}`} className="text-amber-200">
                      пропущено: {s}
                    </div>
                  ))}
                </div>
              ) : null}
              <div className="flex flex-wrap items-center gap-1">
                {(["full", "variance"] as const).map((v) => (
                  <button
                    key={v}
                    type="button"
                    onClick={() => setReportView(v)}
                    className={cn(
                      "rounded-md border px-1.5 py-0.5 text-[9px]",
                      reportView === v ? "border-fuchsia-400/70 bg-fuchsia-500/25 text-fuchsia-50" : "border-white/10 text-muted-foreground",
                    )}
                  >
                    {v === "full" ? "Полный разбор" : "Разброс"}
                  </button>
                ))}
                <button
                  type="button"
                  className="ml-auto inline-flex items-center gap-1 text-[9px] text-fuchsia-200 underline-offset-2 hover:underline"
                  onClick={() => setReportOpen(true)}
                  title="Открыть отчёт во весь экран (таблицы планов читаются целиком)"
                >
                  <Maximize2 className="h-3 w-3" />
                  развернуть
                </button>
                <a
                  className="inline-flex items-center gap-1 text-[9px] text-fuchsia-200 underline-offset-2 hover:underline"
                  href={api.styleReportMdUrl(projectId)}
                  target="_blank"
                  rel="noreferrer"
                  title="style_report.md: планы с таймкодами, связка закадра, сценарий, приёмы, blueprint"
                >
                  <Download className="h-3 w-3" />
                  .md
                </a>
                {p.blueprint?.chain?.length ? (
                  <span className="text-[8.5px] text-muted-foreground" title={(p.blueprint.new_nodes_needed ?? []).join("; ")}>
                    blueprint: {p.blueprint.chain.length} нод · новых {p.blueprint.chain.filter((n) => n.new).length}
                  </span>
                ) : null}
              </div>
              {reportQ.isLoading ? <Loader2 className="h-3 w-3 animate-spin" /> : null}
              {reportView === "full" ? (
                reportQ.data?.report ? (
                  <div className="rounded-md border border-white/10 bg-black/30 p-2 [&_table]:text-[9px] [&_td]:px-1.5 [&_td]:py-1 [&_th]:px-1.5 [&_th]:py-1">
                    <MarkdownRenderer content={reportQ.data.report} />
                  </div>
                ) : reportQ.isSuccess ? (
                  <div className="text-muted-foreground">Отчёт пуст — перезапустите «Разбор стиля».</div>
                ) : null
              ) : reportQ.data?.variance_report ? (
                <pre className="whitespace-pre-wrap rounded-md border border-white/10 bg-black/30 p-1.5 font-mono text-[8.5px] text-zinc-300">
                  {reportQ.data.variance_report}
                </pre>
              ) : null}
            </div>
          ) : null}

          <Dialog open={reportOpen} onOpenChange={setReportOpen}>
            <DialogContent className="max-h-[92vh] w-[96vw] max-w-[1400px] overflow-hidden p-0 sm:max-w-[1400px]">
              <DialogHeader className="border-b border-white/10 px-5 py-3">
                <DialogTitle className="flex items-center gap-2 text-sm">
                  <FileText className="h-4 w-4 text-fuchsia-300" />
                  Разбор стиля {p.label ? `· ${p.label}` : ""}
                  <a
                    className="ml-auto inline-flex items-center gap-1 text-[11px] font-normal text-fuchsia-200 underline-offset-2 hover:underline"
                    href={api.styleReportMdUrl(projectId)}
                    target="_blank"
                    rel="noreferrer"
                  >
                    <Download className="h-3 w-3" />
                    style_report.md
                  </a>
                </DialogTitle>
                <DialogDescription className="text-[11px]">
                  Планы с таймкодами, связка закадра и картинки, сценарий, приёмы, особенности, blueprint цепочки нод и шаблоны промптов.
                </DialogDescription>
              </DialogHeader>
              <div className="nodrag nopan max-h-[80vh] overflow-auto px-5 py-3" onWheel={(e) => e.stopPropagation()}>
                {reportQ.data?.report ? <MarkdownRenderer content={reportQ.data.report} /> : <Loader2 className="h-4 w-4 animate-spin" />}
              </div>
            </DialogContent>
          </Dialog>

          {snap.data?.passport?.problems?.length ? (
            <div className="rounded-md border border-red-400/30 bg-red-500/10 p-1.5 text-[9px] text-red-200">
              {snap.data.passport.problems.join("; ")}
            </div>
          ) : null}

          <div className="flex flex-wrap gap-1">
            <Button type="button" size="sm" variant="outline" className="h-7 text-[10px]" disabled={save.isPending} onClick={() => save.mutate()}>
              {save.isPending ? <Loader2 className="h-3 w-3 animate-spin" /> : <Save className="h-3 w-3" />}
              Сохранить черновик
            </Button>
            <Button
              type="button"
              size="sm"
              className="h-7 bg-emerald-600 text-[10px] hover:bg-emerald-500"
              disabled={approve.isPending || snap.data?.job?.status === "running"}
              onClick={() => approve.mutate()}
              title="Записать блоки промптов, пресет стиля и style lock; патч параметров проекта"
            >
              {approve.isPending ? <Loader2 className="h-3 w-3 animate-spin" /> : <CheckSquare className="h-3 w-3" />}
              {approvedAt ? "Переутвердить" : "Утвердить → в Studio"}
            </Button>
            <Button
              type="button"
              size="sm"
              variant="ghost"
              className="h-7 text-[10px]"
              onClick={() => {
                setShowReport(true);
                setTab("report");
              }}
            >
              <FileText className="h-3 w-3" />
              Отчёт
            </Button>
          </div>
        </div>
      ) : null}
    </div>
  );
}
