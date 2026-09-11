"use client";

/**
 * Помощник генерации — выдвижная панель в окне «Генерация».
 * Категория → стиль (агент с promptCore) → ваш запрос → собранные промпты.
 * «Управление» выпадает под запросом и дублирует ключевые настройки окна
 * (модель / формат / разрешение / детализация) через колбэки воркспейса.
 */

import { useEffect, useMemo, useState } from "react";
import { ChevronDown, Sparkles, X } from "lucide-react";
import { toast } from "sonner";
import { cn } from "@/lib/utils";
import {
  OUTSEE_ACCENT,
  OUTSEE_DETAIL_LEVELS,
  chipOptions,
} from "@/lib/outsee-catalog";
import {
  GEN_ASSISTANT_CATEGORIES,
  GEN_STYLE_COLORS,
  assembleGenPrompt,
  genPromptVariant,
  type GenStyleDef,
} from "@/lib/gen-assistant-styles";

type Props = {
  open: boolean;
  onClose: () => void;
  /** Текущая image-модель окна (slug) — для списков форматов/разрешений. */
  imageSlug: string;
  modelName: string;
  aspect: string;
  resolution: string;
  detail: string;
  generating: boolean;
  onAspectChange: (v: string) => void;
  onResolutionChange: (v: string) => void;
  onDetailChange: (v: string) => void;
  onOpenModelPicker: () => void;
  /** Подставить текст в поле промпта окна. */
  onApplyPrompt: (text: string) => void;
  /** Подставить текст и запустить генерацию. */
  onGenerate: (text: string) => void;
};

const LS = {
  category: "genAssistant.category",
  style: "genAssistant.style",
  request: "genAssistant.request",
  count: "genAssistant.count",
  agentOverrides: "genAssistant.agentOverrides",
};

function lsGet(key: string, fallback: string): string {
  if (typeof window === "undefined") return fallback;
  try {
    return window.localStorage.getItem(key) ?? fallback;
  } catch {
    return fallback;
  }
}

function lsSet(key: string, value: string) {
  try {
    window.localStorage.setItem(key, value);
  } catch {
    /* ignore */
  }
}

const labelCls =
  "w-[92px] shrink-0 font-mono text-[10px] font-semibold uppercase tracking-wider text-white/40";
const selectCls =
  "w-full rounded-lg border border-white/10 bg-[#16161b] px-2.5 py-1.5 text-[12px] text-white/85 focus:border-[#22d3ee]/60 focus:outline-none";
const areaCls =
  "w-full resize-none rounded-lg border border-white/10 bg-[#16161b] px-3 py-2 text-[12px] leading-relaxed text-white/90 placeholder-white/30 focus:border-[#22d3ee]/60 focus:outline-none";

export function GenAssistantPanel({
  open,
  onClose,
  imageSlug,
  modelName,
  aspect,
  resolution,
  detail,
  generating,
  onAspectChange,
  onResolutionChange,
  onDetailChange,
  onOpenModelPicker,
  onApplyPrompt,
  onGenerate,
}: Props) {
  const [categoryId, setCategoryId] = useState(() => lsGet(LS.category, GEN_ASSISTANT_CATEGORIES[0].id));
  const [styleId, setStyleId] = useState(() => lsGet(LS.style, ""));
  const [request, setRequest] = useState(() => lsGet(LS.request, ""));
  const [count, setCount] = useState(() => {
    const n = Math.round(Number(lsGet(LS.count, "1")) || 1);
    return Math.max(1, Math.min(4, n));
  });
  const [manageOpen, setManageOpen] = useState(false);
  const [agentOpen, setAgentOpen] = useState(false);
  const [agentOverrides, setAgentOverrides] = useState<Record<string, string>>(() => {
    try {
      return JSON.parse(lsGet(LS.agentOverrides, "{}")) as Record<string, string>;
    } catch {
      return {};
    }
  });
  const [promptOverrides, setPromptOverrides] = useState<Record<string, string>>({});

  useEffect(() => lsSet(LS.category, categoryId), [categoryId]);
  useEffect(() => lsSet(LS.style, styleId), [styleId]);
  useEffect(() => lsSet(LS.request, request), [request]);
  useEffect(() => lsSet(LS.count, String(count)), [count]);
  useEffect(() => lsSet(LS.agentOverrides, JSON.stringify(agentOverrides)), [agentOverrides]);

  const category =
    GEN_ASSISTANT_CATEGORIES.find((c) => c.id === categoryId) ?? GEN_ASSISTANT_CATEGORIES[0];
  const style: GenStyleDef | null = category.styles.find((s) => s.id === styleId) ?? null;
  const agentText = style ? (agentOverrides[style.id] ?? style.promptCore) : "";

  const assembled = useMemo(
    () => assembleGenPrompt({ request, agentText, aspect }),
    [request, agentText, aspect],
  );

  const aspectOptions = useMemo(() => {
    const opts = chipOptions(imageSlug, "aspect");
    return opts.includes(aspect) ? opts : [aspect, ...opts];
  }, [imageSlug, aspect]);
  const resolutionOptions = useMemo(() => {
    const opts = chipOptions(imageSlug, "resolution");
    return opts.includes(resolution) ? opts : [resolution, ...opts];
  }, [imageSlug, resolution]);

  const promptText = (idx: number) =>
    promptOverrides[String(idx)] ?? genPromptVariant(assembled, idx, count);

  const applyPrompt = (idx: number) => {
    onApplyPrompt(promptText(idx));
    toast.success("Промпт подставлен в поле запроса");
  };
  const generatePrompt = (idx: number) => {
    const text = promptText(idx).trim();
    if (!text) {
      toast.error("Промпт пуст — напишите запрос или выберите стиль");
      return;
    }
    onGenerate(text);
  };

  return (
    <div
      className={cn(
        "absolute inset-y-0 right-0 z-30 flex w-[400px] max-w-[92vw] flex-col border-l border-white/10 bg-[#0d0d11]/98 backdrop-blur-xl transition-transform duration-300 ease-out",
        open ? "translate-x-0" : "translate-x-full pointer-events-none",
      )}
    >
      {/* header */}
      <div className="flex h-[52px] shrink-0 items-center gap-2 border-b border-white/10 px-4">
        <Sparkles className="h-4 w-4" style={{ color: OUTSEE_ACCENT }} />
        <div className="leading-tight">
          <div className="text-sm font-bold tracking-tight text-white/95">Помощник промпта</div>
          <div className="text-[10px] uppercase tracking-[0.16em] text-white/40">
            стиль → запрос → промпт
          </div>
        </div>
        <button
          type="button"
          onClick={onClose}
          className="ml-auto inline-flex h-8 w-8 items-center justify-center rounded-lg border border-white/10 bg-white/[0.03] text-white/70 transition hover:bg-white/[0.08] hover:text-white"
        >
          <X className="h-4 w-4" />
        </button>
      </div>

      <div className="flex-1 space-y-4 overflow-y-auto px-4 py-3.5">
        {/* ── Категория и стили ── */}
        <section>
          <div className="mb-1.5 font-mono text-[10px] font-semibold uppercase tracking-wider text-white/40">
            Категория
          </div>
          <select
            value={categoryId}
            onChange={(e) => {
              setCategoryId(e.target.value);
              setStyleId("");
            }}
            className={selectCls}
          >
            {GEN_ASSISTANT_CATEGORIES.map((c) => (
              <option key={c.id} value={c.id}>
                {c.name}
              </option>
            ))}
          </select>
          <div className="mt-2 grid grid-cols-2 gap-1.5">
            {category.styles.map((s) => {
              const active = s.id === styleId;
              const color = GEN_STYLE_COLORS[s.color];
              return (
                <button
                  key={s.id}
                  type="button"
                  onClick={() => setStyleId(active ? "" : s.id)}
                  className={cn(
                    "rounded-xl border px-2.5 py-2 text-left transition",
                    active
                      ? "border-[#22d3ee]/60 bg-[#22d3ee]/10 ring-1 ring-[#22d3ee]/40"
                      : "border-white/10 bg-white/[0.03] hover:border-white/25 hover:bg-white/[0.06]",
                  )}
                >
                  <div className="flex items-center gap-1.5">
                    <span
                      className="h-2 w-2 shrink-0 rounded-full"
                      style={{ backgroundColor: color }}
                    />
                    <span className="truncate text-[11px] font-semibold text-white/90">
                      {s.name}
                    </span>
                  </div>
                  <div
                    className="mt-0.5 text-[10px] leading-snug text-white/45"
                    style={{
                      display: "-webkit-box",
                      WebkitLineClamp: 2,
                      WebkitBoxOrient: "vertical",
                      overflow: "hidden",
                    }}
                  >
                    {s.desc}
                  </div>
                </button>
              );
            })}
          </div>

          {/* агент стиля */}
          {style && (
            <div className="mt-2 rounded-xl border border-white/10 bg-white/[0.02]">
              <button
                type="button"
                onClick={() => setAgentOpen((v) => !v)}
                className="flex w-full items-center gap-2 px-3 py-2 text-left"
              >
                <span className="text-[11px] font-semibold text-white/80">
                  Агент «{style.name}»
                </span>
                {agentOverrides[style.id] !== undefined && (
                  <span className="rounded-md bg-[#22d3ee]/15 px-1.5 py-0.5 text-[9px] font-bold uppercase tracking-wider text-[#22d3ee]">
                    изменён
                  </span>
                )}
                <span className="ml-auto truncate font-mono text-[9px] text-white/30">
                  {style.file}
                </span>
                <ChevronDown
                  className={cn("h-3.5 w-3.5 text-white/40 transition", agentOpen && "rotate-180")}
                />
              </button>
              <div
                className="overflow-hidden transition-all duration-300"
                style={{ maxHeight: agentOpen ? 260 : 0, opacity: agentOpen ? 1 : 0 }}
              >
                <div className="space-y-1.5 px-3 pb-2.5">
                  <textarea
                    value={agentText}
                    onChange={(e) =>
                      setAgentOverrides((prev) => ({ ...prev, [style.id]: e.target.value }))
                    }
                    rows={6}
                    className={cn(areaCls, "font-mono text-[11px]")}
                  />
                  {agentOverrides[style.id] !== undefined && (
                    <button
                      type="button"
                      onClick={() =>
                        setAgentOverrides((prev) => {
                          const next = { ...prev };
                          delete next[style.id];
                          return next;
                        })
                      }
                      className="text-[10px] font-medium text-white/50 underline decoration-dotted underline-offset-2 hover:text-white"
                    >
                      сбросить к исходному агенту
                    </button>
                  )}
                </div>
              </div>
            </div>
          )}
        </section>

        {/* ── Ваш запрос ── */}
        <section>
          <div className="mb-1.5 font-mono text-[10px] font-semibold uppercase tracking-wider text-white/40">
            Ваш запрос
          </div>
          <textarea
            value={request}
            onChange={(e) => setRequest(e.target.value)}
            rows={3}
            placeholder="Опишите своими словами, что должно быть в кадре…"
            className={areaCls}
          />
          <div className="mt-2 flex items-center gap-1.5">
            <button
              type="button"
              disabled={generating}
              onClick={() => generatePrompt(0)}
              className="inline-flex items-center justify-center rounded-lg bg-gradient-to-r from-[#22d3ee] to-[#0ea5e9] px-3 py-1.5 text-[11px] font-extrabold uppercase tracking-wider text-black shadow-[0_0_15px_rgba(34,211,238,0.3)] transition hover:brightness-110 disabled:opacity-40"
            >
              Сгенерировать
            </button>
            <button
              type="button"
              onClick={() => applyPrompt(0)}
              className="inline-flex items-center rounded-lg border border-white/10 bg-white/[0.04] px-2.5 py-1.5 text-[11px] font-medium text-white/70 transition hover:border-[#22d3ee]/40 hover:bg-[#22d3ee]/10 hover:text-white"
            >
              Только промпт
            </button>
            <input
              type="number"
              min={1}
              max={4}
              value={count}
              title="Количество промптов"
              onChange={(e) => {
                const n = Math.max(1, Math.min(4, Math.round(Number(e.target.value) || 1)));
                setCount(n);
              }}
              className="w-[44px] rounded-lg border border-white/10 bg-[#16161b] px-1.5 py-1.5 text-center text-[11px] text-white/85 focus:border-[#22d3ee]/60 focus:outline-none"
            />
            <button
              type="button"
              onClick={() => setManageOpen((v) => !v)}
              className={cn(
                "ml-auto inline-flex items-center gap-1 rounded-lg px-2.5 py-1.5 text-[11px] font-medium transition",
                manageOpen
                  ? "bg-[#22d3ee]/15 text-[#22d3ee] ring-1 ring-[#22d3ee]/40"
                  : "bg-white/[0.04] text-white/60 hover:bg-white/[0.08] hover:text-white",
              )}
            >
              Управление
              <ChevronDown className={cn("h-3 w-3 transition", manageOpen && "rotate-180")} />
            </button>
          </div>

          {/* ── Управление: выпадает под запросом ── */}
          <div
            className="overflow-hidden transition-all duration-300 ease-out"
            style={{
              maxHeight: manageOpen ? 320 : 0,
              opacity: manageOpen ? 1 : 0,
              transform: manageOpen ? "translateY(0)" : "translateY(-6px)",
            }}
          >
            <div className="mt-2 space-y-2 rounded-xl border border-white/10 bg-white/[0.02] px-3 py-2.5">
              <div className="flex items-center gap-2">
                <span className={labelCls}>Модель</span>
                <button
                  type="button"
                  onClick={onOpenModelPicker}
                  className="flex min-w-0 flex-1 items-center justify-between gap-2 rounded-lg border border-white/10 bg-[#16161b] px-2.5 py-1.5 text-[12px] text-white/85 transition hover:border-[#22d3ee]/40"
                >
                  <span className="truncate">{modelName}</span>
                  <ChevronDown className="h-3 w-3 shrink-0 opacity-60" />
                </button>
              </div>
              <div className="flex items-center gap-2">
                <span className={labelCls}>Формат</span>
                <select
                  value={aspect}
                  onChange={(e) => onAspectChange(e.target.value)}
                  className={selectCls}
                >
                  {aspectOptions.map((a) => (
                    <option key={a} value={a}>
                      {a}
                    </option>
                  ))}
                </select>
              </div>
              <div className="flex items-center gap-2">
                <span className={labelCls}>Разрешение</span>
                <select
                  value={resolution}
                  onChange={(e) => onResolutionChange(e.target.value)}
                  className={selectCls}
                >
                  {resolutionOptions.map((r) => (
                    <option key={r} value={r}>
                      {r}
                    </option>
                  ))}
                </select>
              </div>
              <div className="flex items-center gap-2">
                <span className={labelCls}>Детализация</span>
                <select
                  value={detail}
                  onChange={(e) => onDetailChange(e.target.value)}
                  className={selectCls}
                >
                  {OUTSEE_DETAIL_LEVELS.map((d) => (
                    <option key={d.id} value={d.id}>
                      {d.label} · {d.hint}
                    </option>
                  ))}
                </select>
              </div>
            </div>
          </div>
        </section>

        {/* ── Собранные промпты (вертикально) ── */}
        <section className="space-y-2.5 pb-2">
          {Array.from({ length: count }, (_, idx) => {
            const key = String(idx);
            const overridden = promptOverrides[key] !== undefined;
            const text = promptText(idx);
            return (
              <div key={key} className="rounded-xl border border-white/10 bg-white/[0.02]">
                <div className="flex items-center gap-2 border-b border-white/[0.06] px-3 py-1.5">
                  <span className="text-[11px] font-semibold text-white/85">
                    {count > 1 ? `Промпт ${idx + 1} из ${count}` : "Собранный промпт"}
                  </span>
                  {overridden && (
                    <button
                      type="button"
                      onClick={() =>
                        setPromptOverrides((prev) => {
                          const next = { ...prev };
                          delete next[key];
                          return next;
                        })
                      }
                      className="rounded-md bg-[#22d3ee]/15 px-1.5 py-0.5 text-[9px] font-bold uppercase tracking-wider text-[#22d3ee] transition hover:bg-[#22d3ee]/25"
                      title="Вернуть автосборку"
                    >
                      сбросить правки
                    </button>
                  )}
                  <span className="ml-auto font-mono text-[9px] text-white/35">
                    {text.length} симв.
                  </span>
                </div>
                <div className="space-y-2 px-3 py-2.5">
                  <textarea
                    value={text}
                    onChange={(e) =>
                      setPromptOverrides((prev) => ({ ...prev, [key]: e.target.value }))
                    }
                    rows={count > 1 ? 5 : 7}
                    className={cn(areaCls, "font-mono text-[11px]")}
                  />
                  <div className="flex items-center gap-1.5">
                    <button
                      type="button"
                      disabled={generating}
                      onClick={() => generatePrompt(idx)}
                      className="inline-flex items-center justify-center rounded-lg bg-gradient-to-r from-[#22d3ee] to-[#0ea5e9] px-3 py-1.5 text-[10px] font-extrabold uppercase tracking-wider text-black transition hover:brightness-110 disabled:opacity-40"
                    >
                      Сгенерировать
                    </button>
                    <button
                      type="button"
                      onClick={() => applyPrompt(idx)}
                      className="inline-flex items-center rounded-lg border border-white/10 bg-white/[0.04] px-2.5 py-1.5 text-[10px] font-medium text-white/70 transition hover:border-[#22d3ee]/40 hover:bg-[#22d3ee]/10 hover:text-white"
                    >
                      В поле запроса
                    </button>
                    {count > 1 && (
                      <span className="ml-auto font-mono text-[9px] text-white/35">
                        вариант {idx + 1}
                      </span>
                    )}
                  </div>
                </div>
              </div>
            );
          })}
        </section>
      </div>
    </div>
  );
}
