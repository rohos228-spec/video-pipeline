"use client";

/**
 * KIE Create — индивидуальная генерация через kie.ai.
 * Схема-ориентированная форма: каталог/поля/цены приходят с бэкенда
 * (GET /api/kie-create/catalog), цена в $ пересчитывается на каждый чих.
 * Файлы грузятся в kie через /api/kie-create/upload → публичный URL.
 */

import { useEffect, useMemo, useRef, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import {
  ChevronDown,
  Coins,
  ImagePlus,
  Link2,
  Loader2,
  Paperclip,
  Sparkles,
  X,
} from "lucide-react";
import { toast } from "sonner";
import { api } from "@/lib/api";
import type { KieField, KieModelSpec } from "@/lib/api";
import { errorMessageFromUnknown } from "@/lib/error-message";
import { cn } from "@/lib/utils";
import { OUTSEE_ACCENT } from "@/lib/outsee-catalog";

type Values = Record<string, unknown>;

const FILE_ACCEPT: Record<string, string> = {
  images: "image/png,image/jpeg,image/webp,image/bmp,image/gif",
  videos: "video/mp4,video/quicktime,video/webm",
  audios: "audio/mpeg,audio/wav,audio/mp4,audio/x-m4a",
};

function fieldVisible(f: KieField, values: Values): boolean {
  if (!f.show_if) return true;
  return Object.entries(f.show_if).every(([k, want]) => {
    const got = values[k];
    if (typeof want === "boolean") {
      const truthy = got === true || String(got).toLowerCase() === "true";
      return truthy === want;
    }
    return String(got ?? "") === String(want);
  });
}

function estimateKie(
  model: KieModelSpec,
  values: Values,
  creditUsd: number,
): { credits: number; usd: number } {
  const p = model.pricing;
  const merged: Values = {};
  for (const f of model.fields) {
    if (f.default !== undefined) merged[f.name] = f.default;
  }
  Object.assign(merged, values);
  const hasFiles = (kinds: string[]) =>
    model.fields.some((f) => {
      if (!kinds.includes(f.kind)) return false;
      const v = merged[f.name];
      if (Array.isArray(v)) return v.length > 0;
      return typeof v === "string" && v.trim() !== "";
    });
  let credits: number | undefined;
  for (const r of p.rules || []) {
    const ok = Object.entries(r.when).every(([k, want]) => {
      if (k === "_has_images") return hasFiles(["images"]) === Boolean(want);
      if (k === "_has_videos") return hasFiles(["videos"]) === Boolean(want);
      const got = merged[k];
      if (typeof want === "boolean") {
        const truthy = got === true || String(got).toLowerCase() === "true";
        return truthy === want;
      }
      return String(got ?? "") === String(want);
    });
    if (ok) {
      credits = r.credits;
      break;
    }
  }
  if (credits === undefined) credits = p.default ?? 0;
  let total = credits;
  if (p.unit === "sec") {
    const d = Number(merged["duration"]) || 5;
    total = credits * Math.max(1, d);
  } else if (p.unit === "1k_chars") {
    const t = String(merged["text"] || merged["dialogue"] || "");
    total = credits * Math.max(1, Math.ceil(t.length / 1000));
  }
  const rounded = Math.round(total * 100) / 100;
  return { credits: rounded, usd: Math.round(rounded * creditUsd * 10000) / 10000 };
}

function FileField({
  field,
  values,
  onChange,
}: {
  field: KieField;
  values: Values;
  onChange: (name: string, v: string[]) => void;
}) {
  const inputRef = useRef<HTMLInputElement>(null);
  const [busy, setBusy] = useState(false);
  const [urlDraft, setUrlDraft] = useState("");
  const items: string[] = Array.isArray(values[field.name])
    ? (values[field.name] as string[])
    : [];
  const max = field.max_items ?? 99;
  const canAdd = items.length < max;

  const addUrl = () => {
    const u = urlDraft.trim();
    if (!u) return;
    if (!/^https?:\/\//.test(u)) {
      toast.error("Нужен http(s) URL");
      return;
    }
    onChange(field.name, [...items, u]);
    setUrlDraft("");
  };

  return (
    <div className="rounded-xl border border-white/[0.08] bg-white/[0.02] px-2.5 py-2">
      <div className="mb-1.5 flex items-center gap-2 text-[10px] font-semibold uppercase tracking-wider text-white/40">
        <Paperclip className="h-3 w-3" />
        {field.label}
        {field.required ? <span className="text-red-400">*</span> : null}
        <span className="ml-auto font-mono normal-case text-white/30">
          {items.length}/{max}
        </span>
      </div>
      {items.length > 0 && (
        <div className="mb-2 flex flex-wrap gap-1.5">
          {items.map((u, i) => (
            <span
              key={`${u}-${i}`}
              className="inline-flex max-w-full items-center gap-1.5 rounded-lg border border-white/10 bg-white/[0.04] px-1.5 py-1 text-[10px] text-white/60"
            >
              {field.kind === "images" ? (
                // eslint-disable-next-line @next/next/no-img-element
                <img src={u} alt="" className="h-7 w-7 rounded object-cover ring-1 ring-white/15" />
              ) : (
                <span className="max-w-[180px] truncate font-mono">{u.split("/").pop()}</span>
              )}
              <button
                type="button"
                className="text-white/40 hover:text-white"
                onClick={() => onChange(field.name, items.filter((_, j) => j !== i))}
              >
                <X className="h-3 w-3" />
              </button>
            </span>
          ))}
        </div>
      )}
      {canAdd && (
        <div className="flex items-center gap-1.5">
          <input
            ref={inputRef}
            type="file"
            accept={FILE_ACCEPT[field.kind] || "*/*"}
            className="hidden"
            onChange={(e) => {
              const f = e.target.files?.[0];
              if (!f) return;
              setBusy(true);
              api
                .kieUpload(f)
                .then((r) => {
                  onChange(field.name, [...items, r.url]);
                  toast.success("Файл загружен в kie");
                })
                .catch((err) => toast.error(errorMessageFromUnknown(err)))
                .finally(() => setBusy(false));
              e.target.value = "";
            }}
          />
          <button
            type="button"
            disabled={busy}
            onClick={() => inputRef.current?.click()}
            className="inline-flex h-8 items-center gap-1.5 rounded-lg border border-dashed border-white/25 bg-white/[0.03] px-2.5 text-[11px] text-white/70 hover:border-white/40 disabled:opacity-50"
          >
            {busy ? (
              <Loader2 className="h-3.5 w-3.5 animate-spin" />
            ) : (
              <ImagePlus className="h-3.5 w-3.5" />
            )}
            Загрузить
          </button>
          <div className="flex min-w-0 flex-1 items-center gap-1">
            <Link2 className="h-3 w-3 shrink-0 text-white/30" />
            <input
              value={urlDraft}
              onChange={(e) => setUrlDraft(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Enter") {
                  e.preventDefault();
                  addUrl();
                }
              }}
              placeholder="или вставь URL"
              className="h-8 min-w-0 flex-1 rounded-lg border border-white/10 bg-white/[0.03] px-2 text-[11px] text-white/80 outline-none placeholder:text-white/25 focus:border-white/25"
            />
            <button
              type="button"
              onClick={addUrl}
              className="h-8 shrink-0 rounded-lg border border-white/10 px-2 text-[11px] text-white/60 hover:text-white"
            >
              +
            </button>
          </div>
        </div>
      )}
      {field.desc ? (
        <div className="mt-1 text-[9px] leading-snug text-white/30">{field.desc}</div>
      ) : null}
    </div>
  );
}

export function KieCreatePanel({
  onGenerated,
}: {
  onGenerated: (historyId: string) => void;
}) {
  const catalogQ = useQuery({ queryKey: ["kie-catalog"], queryFn: api.kieCatalog });
  const creditsQ = useQuery({
    queryKey: ["kie-credits"],
    queryFn: api.kieCredits,
    refetchInterval: 60_000,
  });

  const [category, setCategory] = useState("video");
  const [modelId, setModelId] = useState("veo-3-1");
  const [values, setValues] = useState<Values>({});
  const [modelOpen, setModelOpen] = useState(false);
  const [filter, setFilter] = useState("");
  const [sending, setSending] = useState(false);
  const modelRef = useRef<HTMLDivElement>(null);

  const catalog = catalogQ.data;
  const models = useMemo(
    () => (catalog?.models ?? []).filter((m) => m.category === category),
    [catalog, category],
  );
  const model = useMemo(
    () => catalog?.models.find((m) => m.id === modelId) ?? null,
    [catalog, modelId],
  );

  useEffect(() => {
    if (!catalog) return;
    if (!catalog.models.some((m) => m.id === modelId && m.category === category)) {
      const first = catalog.models.find((m) => m.category === category);
      if (first) setModelId(first.id);
    }
  }, [catalog, category, modelId]);

  useEffect(() => {
    setValues({});
  }, [modelId]);

  useEffect(() => {
    if (!modelOpen) return;
    const onDown = (e: MouseEvent) => {
      if (modelRef.current && !modelRef.current.contains(e.target as Node)) {
        setModelOpen(false);
      }
    };
    window.addEventListener("mousedown", onDown);
    return () => window.removeEventListener("mousedown", onDown);
  }, [modelOpen]);

  const setVal = (name: string, v: unknown) =>
    setValues((prev) => ({ ...prev, [name]: v }));

  const price = useMemo(() => {
    if (!model || !catalog) return null;
    return estimateKie(model, values, catalog.credit_usd);
  }, [model, values, catalog]);

  const filteredModels = useMemo(() => {
    const q = filter.trim().toLowerCase();
    if (!q) return models;
    return models.filter(
      (m) =>
        m.label.toLowerCase().includes(q) || m.id.toLowerCase().includes(q),
    );
  }, [models, filter]);

  const missingRequired = useMemo(() => {
    if (!model) return [] as string[];
    return model.fields
      .filter((f) => f.required && fieldVisible(f, values))
      .filter((f) => {
        const v = values[f.name] ?? f.default;
        if (v === undefined || v === null) return true;
        if (typeof v === "string") return v.trim() === "";
        if (Array.isArray(v)) return v.length === 0;
        return false;
      })
      .map((f) => f.label);
  }, [model, values]);

  const submit = async () => {
    if (!model || sending) return;
    if (missingRequired.length) {
      toast.error(`Заполни: ${missingRequired.join(", ")}`);
      return;
    }
    setSending(true);
    try {
      const res = await api.kieGenerate({ model_id: model.id, values });
      toast.success(
        `Запущено: ${model.label} · $${res.estimate.usd.toFixed(3)} (${res.estimate.credits} кр)`,
      );
      if (res.job?.history_id) onGenerated(res.job.history_id);
      creditsQ.refetch();
    } catch (e) {
      toast.error(errorMessageFromUnknown(e));
    } finally {
      setSending(false);
    }
  };

  if (catalogQ.isLoading) {
    return (
      <div className="flex items-center justify-center gap-2 rounded-2xl border border-white/[0.08] bg-[#171717] px-4 py-8 text-[12px] text-white/50">
        <Loader2 className="h-4 w-4 animate-spin" /> Каталог kie.ai…
      </div>
    );
  }
  if (!catalog) {
    return (
      <div className="rounded-2xl border border-red-500/30 bg-red-500/5 px-4 py-6 text-center text-[12px] text-red-300">
        Каталог kie не загрузился
      </div>
    );
  }

  return (
    <div className="min-w-0 flex-1 rounded-2xl border border-white/[0.08] bg-[#171717] shadow-[0_12px_40px_rgba(0,0,0,0.55)]">
      {/* категории + модель + кредиты */}
      <div className="flex flex-wrap items-center gap-1.5 border-b border-white/[0.06] px-3 py-2">
        {(catalog.categories || []).map((c) => (
          <button
            key={c.id}
            type="button"
            onClick={() => setCategory(c.id)}
            className={cn(
              "rounded-lg px-2.5 py-1.5 text-[11px] font-medium transition",
              category === c.id
                ? "bg-[rgba(209,254,23,0.14)] text-[rgba(209,254,23,1)]"
                : "text-white/45 hover:text-white/80",
            )}
          >
            {c.label}
          </button>
        ))}
        <div className="relative ml-auto" ref={modelRef}>
          <button
            type="button"
            onClick={() => setModelOpen((o) => !o)}
            className="inline-flex h-8 items-center gap-2 rounded-lg border border-white/10 bg-white/[0.04] px-2.5 text-[12px] font-semibold text-white/85 hover:border-white/25"
          >
            {model?.label ?? "модель"}
            <ChevronDown className="h-3.5 w-3.5 text-white/40" />
          </button>
          {modelOpen && (
            <div className="absolute bottom-9 right-0 z-30 max-h-[300px] w-[280px] overflow-y-auto rounded-xl border border-white/10 bg-[#101010] p-1.5 shadow-2xl">
              <input
                autoFocus
                value={filter}
                onChange={(e) => setFilter(e.target.value)}
                placeholder="поиск модели…"
                className="mb-1 h-8 w-full rounded-lg border border-white/10 bg-white/[0.04] px-2 text-[11px] outline-none placeholder:text-white/25"
              />
              {filteredModels.map((m) => (
                <button
                  key={m.id}
                  type="button"
                  onClick={() => {
                    setModelId(m.id);
                    setModelOpen(false);
                    setFilter("");
                  }}
                  className={cn(
                    "block w-full rounded-lg px-2 py-1.5 text-left text-[11px]",
                    m.id === modelId
                      ? "bg-[rgba(209,254,23,0.12)] text-white"
                      : "text-white/65 hover:bg-white/[0.05]",
                  )}
                >
                  <div className="font-medium">{m.label}</div>
                  <div className="truncate text-[9px] text-white/35">{m.desc}</div>
                </button>
              ))}
              {!filteredModels.length && (
                <div className="px-2 py-3 text-center text-[10px] text-white/30">
                  ничего не найдено
                </div>
              )}
            </div>
          )}
        </div>
        <span
          className="inline-flex items-center gap-1 rounded-md border border-white/10 bg-white/[0.03] px-2 py-1 font-mono text-[10px] text-white/50"
          title="Баланс kie.ai"
        >
          <Coins className="h-3 w-3" />
          {creditsQ.data?.credits != null
            ? `${creditsQ.data.credits.toFixed(0)} кр · $${creditsQ.data.usd?.toFixed(2)}`
            : "—"}
        </span>
      </div>

      {model && (
        <>
          <div className="max-h-[300px] overflow-y-auto px-3 py-2">
            <div className="mb-1 text-[10px] leading-snug text-white/35">{model.desc}</div>
            <div className="grid grid-cols-1 gap-2 lg:grid-cols-2">
              {model.fields.filter((f) => fieldVisible(f, values)).map((f) => {
                const v = values[f.name] ?? f.default;
                if (f.kind === "textarea" || f.kind === "dialogue") {
                  return (
                    <div key={f.name} className="lg:col-span-2">
                      <div className="mb-1 text-[10px] font-semibold uppercase tracking-wider text-white/40">
                        {f.label}
                        {f.required ? <span className="text-red-400"> *</span> : null}
                      </div>
                      <textarea
                        value={String(v ?? "")}
                        onChange={(e) => setVal(f.name, e.target.value)}
                        rows={f.kind === "dialogue" ? 3 : 2}
                        placeholder={f.kind === "dialogue" ? "Голос | текст реплики" : ""}
                        className="w-full resize-y rounded-xl border border-white/10 bg-white/[0.03] px-3 py-2 text-[12px] text-white/90 outline-none placeholder:text-white/25 focus:border-white/25"
                      />
                      {f.desc ? (
                        <div className="mt-0.5 whitespace-pre-line text-[9px] text-white/30">
                          {f.desc}
                        </div>
                      ) : null}
                    </div>
                  );
                }
                if (f.kind === "select") {
                  return (
                    <div key={f.name}>
                      <div className="mb-1 text-[10px] font-semibold uppercase tracking-wider text-white/40">
                        {f.label}
                      </div>
                      <div className="flex flex-wrap gap-1">
                        {(f.options || []).map((o) => (
                          <button
                            key={o}
                            type="button"
                            onClick={() => setVal(f.name, o)}
                            className={cn(
                              "rounded-lg border px-2 py-1 text-[10px] font-medium transition",
                              String(v ?? "") === o
                                ? "border-[rgba(209,254,23,0.45)] bg-[rgba(209,254,23,0.12)] text-[rgba(209,254,23,1)]"
                                : "border-white/10 bg-white/[0.03] text-white/50 hover:text-white/80",
                            )}
                          >
                            {o || "—"}
                          </button>
                        ))}
                      </div>
                      {f.desc ? (
                        <div className="mt-0.5 text-[9px] text-white/30">{f.desc}</div>
                      ) : null}
                    </div>
                  );
                }
                if (f.kind === "toggle") {
                  const on = v === true || String(v).toLowerCase() === "true";
                  return (
                    <div key={f.name} className="flex items-start gap-2">
                      <button
                        type="button"
                        role="switch"
                        aria-checked={on}
                        onClick={() => setVal(f.name, !on)}
                        className={cn(
                          "mt-0.5 inline-flex h-5 w-9 shrink-0 items-center rounded-full border transition",
                          on
                            ? "justify-end border-[rgba(209,254,23,0.5)] bg-[rgba(209,254,23,0.25)]"
                            : "justify-start border-white/15 bg-white/[0.05]",
                        )}
                      >
                        <span
                          className={cn(
                            "mx-0.5 h-3.5 w-3.5 rounded-full",
                            on ? "bg-[rgba(209,254,23,1)]" : "bg-white/35",
                          )}
                        />
                      </button>
                      <div>
                        <div className="text-[11px] text-white/75">{f.label}</div>
                        {f.desc ? (
                          <div className="text-[9px] text-white/30">{f.desc}</div>
                        ) : null}
                      </div>
                    </div>
                  );
                }
                if (f.kind === "number") {
                  return (
                    <div key={f.name}>
                      <div className="mb-1 text-[10px] font-semibold uppercase tracking-wider text-white/40">
                        {f.label}
                      </div>
                      <input
                        type="number"
                        value={v === undefined || v === null ? "" : String(v)}
                        min={f.min}
                        max={f.max}
                        step={f.step ?? 1}
                        onChange={(e) =>
                          setVal(
                            f.name,
                            e.target.value === "" ? undefined : Number(e.target.value),
                          )
                        }
                        className="h-8 w-28 rounded-lg border border-white/10 bg-white/[0.03] px-2 text-[12px] text-white/85 outline-none focus:border-white/25"
                      />
                      {f.desc ? (
                        <div className="mt-0.5 text-[9px] text-white/30">{f.desc}</div>
                      ) : null}
                    </div>
                  );
                }
                if (f.kind === "images" || f.kind === "videos" || f.kind === "audios") {
                  return (
                    <div key={f.name} className="lg:col-span-2">
                      <FileField field={f} values={values} onChange={setVal} />
                    </div>
                  );
                }
                // text
                return (
                  <div key={f.name}>
                    <div className="mb-1 text-[10px] font-semibold uppercase tracking-wider text-white/40">
                      {f.label}
                      {f.required ? <span className="text-red-400"> *</span> : null}
                    </div>
                    <input
                      value={String(v ?? "")}
                      onChange={(e) => setVal(f.name, e.target.value)}
                      className="h-8 w-full rounded-lg border border-white/10 bg-white/[0.03] px-2 text-[12px] text-white/85 outline-none placeholder:text-white/25 focus:border-white/25"
                    />
                    {f.desc ? (
                      <div className="mt-0.5 text-[9px] text-white/30">{f.desc}</div>
                    ) : null}
                  </div>
                );
              })}
            </div>
          </div>

          {/* цена + запуск */}
          <div className="flex items-center gap-3 border-t border-white/[0.06] px-3 py-2.5">
            <div className="min-w-0">
              <div className="flex items-baseline gap-2">
                <span
                  className="text-[15px] font-bold"
                  style={{ color: OUTSEE_ACCENT }}
                >
                  ${price ? price.usd.toFixed(3) : "—"}
                </span>
                <span className="font-mono text-[10px] text-white/40">
                  {price ? `${price.credits} кр` : ""}
                </span>
              </div>
              {model.pricing?.note ? (
                <div className="truncate text-[9px] text-white/30" title={model.pricing.note}>
                  {model.pricing.note}
                </div>
              ) : null}
            </div>
            <button
              type="button"
              disabled={sending || !catalog.configured}
              onClick={() => void submit()}
              className={cn(
                "ml-auto inline-flex h-10 items-center gap-2 rounded-xl px-5 text-[13px] font-bold text-black transition",
                sending || !catalog.configured
                  ? "cursor-not-allowed bg-white/20"
                  : "hover:brightness-110",
              )}
              style={{ backgroundColor: sending ? undefined : OUTSEE_ACCENT }}
              title={catalog.configured ? "" : "KIE_API_KEY не задан в .env"}
            >
              {sending ? (
                <Loader2 className="h-4 w-4 animate-spin" />
              ) : (
                <Sparkles className="h-4 w-4" />
              )}
              Сгенерировать
            </button>
          </div>
          {!catalog.configured && (
            <div className="border-t border-white/[0.06] px-3 py-1.5 text-[10px] text-amber-300/80">
              KIE_API_KEY не задан в .env — генерация недоступна
            </div>
          )}
        </>
      )}
    </div>
  );
}
