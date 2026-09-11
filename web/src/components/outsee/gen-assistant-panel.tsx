"use client";

/**
 * Помощник генерации — занимает место дока генерации (те же размеры,
 * свёрнут = высота дока). Категории/стили — выпадающее вверх меню картинок
 * по кнопке «Категория». Кнопка «Агент ✎» — в ряду управления панели,
 * редактор агента раскрывается поповером вверх. Стрелка ⌃ разворачивает
 * панель вверх поверх изображения, ⌄ сворачивает обратно.
 * Фон плитки стиля — первая успешная генерация по этому агенту (localStorage).
 */

import { useEffect, useMemo, useRef, useState } from "react";
import {
  ChevronDown,
  ChevronsDown,
  ChevronsUp,
  ClipboardList,
  Plus,
  Sparkles,
  X,
} from "lucide-react";
import { toast } from "sonner";
import { api } from "@/lib/api";
import { cn } from "@/lib/utils";
import { OUTSEE_ACCENT, chipOptions, detailLabel } from "@/lib/outsee-catalog";
import {
  GEN_ASSISTANT_CATEGORIES,
  GEN_STYLE_COLORS,
  assembleGenPrompt,
  genPromptVariant,
  isInstructionAgent,
  type GenStyleArt,
  type GenStyleDef,
} from "@/lib/gen-assistant-styles";
import { GenStyleArt as GenStyleArtView } from "@/components/outsee/gen-style-art";

type Props = {
  onClose: () => void;
  /** Промпт из двойного клика по истории — подставляется в слот 1. */
  appliedPrompt?: { text: string; ts: number } | null;
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
  onApplyPrompt: (text: string) => void;
  onGenerate: (text: string) => void;
  /** Агент написал N промптов → сразу генерация всех (по одной на промпт). */
  onGenerateAll: (texts: string[]) => void;
};

type CustomStyle = GenStyleDef & { categoryId: string };

type TextLlmModel = {
  id: string;
  provider: string;
  label: string;
  model: string;
  key_configured: boolean;
  active: boolean;
};

const LS = {
  category: "genAssistant.category",
  style: "genAssistant.style",
  request: "genAssistant.request",
  count: "genAssistant.count",
  agentOverrides: "genAssistant.agentOverrides",
  customStyles: "genAssistant.customStyles",
  pendingStyle: "genAssistant.pendingStyle",
  artPrefix: "genAssistant.styleArt.",
};

const ART_KEYS: GenStyleArt[] = [
  "polka",
  "pixel",
  "noir",
  "clay",
  "knit",
  "infographic",
  "photo",
  "tutor",
  "retro",
];

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

function lsRemove(key: string) {
  try {
    window.localStorage.removeItem(key);
  } catch {
    /* ignore */
  }
}

/** Все сохранённые превью стилей: styleId → url картинки. */
function readArtUrls(): Record<string, string> {
  const out: Record<string, string> = {};
  if (typeof window === "undefined") return out;
  try {
    for (let i = 0; i < window.localStorage.length; i++) {
      const k = window.localStorage.key(i);
      if (k && k.startsWith(LS.artPrefix)) {
        const v = window.localStorage.getItem(k);
        if (v) out[k.slice(LS.artPrefix.length)] = v;
      }
    }
  } catch {
    /* ignore */
  }
  return out;
}

const labelSmCls =
  "w-[62px] shrink-0 font-mono text-[9px] font-semibold uppercase tracking-wider text-white/40";
const selectCls =
  "w-full rounded-lg border border-white/10 bg-[#16161b] px-2.5 py-1.5 text-[12px] text-white/85 focus:border-[#22d3ee]/60 focus:outline-none";
const selectSmCls =
  "min-w-0 flex-1 rounded-lg border border-white/10 bg-[#16161b] px-2 py-1 text-[11px] text-white/85 focus:border-[#22d3ee]/60 focus:outline-none";
const areaCls =
  "w-full resize-none rounded-lg border border-white/10 bg-[#16161b] px-3 py-2 text-[12px] leading-relaxed text-white/90 placeholder-white/30 focus:border-[#22d3ee]/60 focus:outline-none";

/** Картинка фоном на всю плитку (своё превью из генерации или SVG-схема). */
function TileBg({ art, color, artUrl }: { art: GenStyleArt; color: GenStyleDef["color"]; artUrl?: string }) {
  if (artUrl) {
    return (
      // eslint-disable-next-line @next/next/no-img-element
      <img src={artUrl} alt="" className="absolute inset-0 h-full w-full object-cover" />
    );
  }
  return <GenStyleArtView art={art} color={color} />;
}

export function GenAssistantPanel({
  onClose,
  appliedPrompt,
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
  onGenerateAll,
}: Props) {
  const [categoryId, setCategoryId] = useState(() => lsGet(LS.category, GEN_ASSISTANT_CATEGORIES[0].id));
  const [styleId, setStyleId] = useState(() => lsGet(LS.style, ""));
  const [request, setRequest] = useState(() => lsGet(LS.request, ""));
  const [count, setCount] = useState(() => {
    const n = Math.round(Number(lsGet(LS.count, "1")) || 1);
    return Math.max(1, Math.min(4, n));
  });
  const [expanded, setExpanded] = useState(false);
  const [resultsOpen, setResultsOpen] = useState(false);
  const [agentBusy, setAgentBusy] = useState(false);
  const [catMenuOpen, setCatMenuOpen] = useState(false);
  const [previewCat, setPreviewCat] = useState<string | null>(null);
  // Позиция курсора (относительно поповера категорий) — окно стилей открывается поверх, у курсора
  const [stylesPos, setStylesPos] = useState<{ x: number; y: number; w: number } | null>(null);
  const popRef = useRef<HTMLDivElement | null>(null);
  // Окно стилей живёт, пока курсор над категорией/окном; ушёл — исчезает (с задержкой на переход через зазор)
  const hideTimer = useRef<number | null>(null);
  const cancelHide = () => {
    if (hideTimer.current !== null) {
      window.clearTimeout(hideTimer.current);
      hideTimer.current = null;
    }
  };
  const scheduleHide = () => {
    cancelHide();
    hideTimer.current = window.setTimeout(() => setPreviewCat(null), 180);
  };
  // Окно результатов: курсор ушёл с иконки/окна — исчезает (как у категорий)
  const resultsTimer = useRef<number | null>(null);
  const cancelResultsHide = () => {
    if (resultsTimer.current !== null) {
      window.clearTimeout(resultsTimer.current);
      resultsTimer.current = null;
    }
  };
  const scheduleResultsHide = () => {
    cancelResultsHide();
    resultsTimer.current = window.setTimeout(() => setResultsOpen(false), 180);
  };
  const [agentOverrides, setAgentOverrides] = useState<Record<string, string>>(() => {
    try {
      return JSON.parse(lsGet(LS.agentOverrides, "{}")) as Record<string, string>;
    } catch {
      return {};
    }
  });
  const [promptOverrides, setPromptOverrides] = useState<Record<string, string>>({});
  const [customStyles, setCustomStyles] = useState<CustomStyle[]>(() => {
    try {
      return JSON.parse(lsGet(LS.customStyles, "[]")) as CustomStyle[];
    } catch {
      return [];
    }
  });
  const [artUrls, setArtUrls] = useState<Record<string, string>>(() => readArtUrls());
  const [pendingArt, setPendingArt] = useState<{ styleId: string; ts: number; prefix: string } | null>(() => {
    try {
      const raw = lsGet(LS.pendingStyle, "");
      return raw ? (JSON.parse(raw) as { styleId: string; ts: number; prefix: string }) : null;
    } catch {
      return null;
    }
  });

  // добавление стиля
  const [addOpen, setAddOpen] = useState(false);
  const [newName, setNewName] = useState("");
  const [newDesc, setNewDesc] = useState("");
  const [newColor, setNewColor] = useState<GenStyleDef["color"]>("cyan");
  const [newArt, setNewArt] = useState<GenStyleArt>("infographic");
  const [newAgent, setNewAgent] = useState("");

  // LLM (текстовая модель Studio, как в топбаре)
  const [llmModels, setLlmModels] = useState<TextLlmModel[]>([]);
  const [llmBusy, setLlmBusy] = useState(false);

  useEffect(() => lsSet(LS.category, categoryId), [categoryId]);
  useEffect(() => lsSet(LS.style, styleId), [styleId]);
  useEffect(() => lsSet(LS.request, request), [request]);
  useEffect(() => lsSet(LS.count, String(count)), [count]);
  useEffect(() => lsSet(LS.agentOverrides, JSON.stringify(agentOverrides)), [agentOverrides]);
  useEffect(() => lsSet(LS.customStyles, JSON.stringify(customStyles)), [customStyles]);

  useEffect(() => {
    fetch("/api/text-llm", { cache: "no-store" })
      .then((r) => (r.ok ? r.json() : null))
      .then((data: { models?: TextLlmModel[] } | null) => {
        if (data?.models) setLlmModels(data.models);
      })
      .catch(() => undefined);
  }, []);

  const onLlmChange = async (id: string) => {
    const m = llmModels.find((x) => x.id === id);
    if (!m) return;
    setLlmBusy(true);
    try {
      const r = await fetch("/api/text-llm", {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ provider: m.provider, model_id: m.id }),
      });
      if (r.ok) {
        const data = (await r.json()) as { models?: TextLlmModel[] };
        if (data?.models) setLlmModels(data.models);
        window.dispatchEvent(new CustomEvent("canvas-patch-global-model", { detail: { modelId: m.id } }));
      }
    } finally {
      setLlmBusy(false);
    }
  };

  // Категории + пользовательские стили
  const categories = useMemo(
    () =>
      GEN_ASSISTANT_CATEGORIES.map((c) => ({
        ...c,
        styles: [...c.styles, ...customStyles.filter((s) => s.categoryId === c.id)],
      })),
    [customStyles],
  );
  const category = categories.find((c) => c.id === categoryId) ?? categories[0];
  const style: GenStyleDef | null = category.styles.find((s) => s.id === styleId) ?? null;
  const agentText = style ? (agentOverrides[style.id] ?? style.promptCore) : "";

  // Двойной клик по картинке в истории: промпт → слот «Промпт 1» (как ручная
  // правка, с кнопкой сброса), плюс пытаемся распознать стиль по ядру агента.
  useEffect(() => {
    if (!appliedPrompt?.text) return;
    const text = appliedPrompt.text;
    setPromptOverrides((prev) => ({ ...prev, "0": text }));
    for (const c of categories) {
      const hit = c.styles.find((s) => {
        const core = (agentOverrides[s.id] ?? s.promptCore).trim();
        return core.length >= 24 && text.includes(core.slice(0, 40));
      });
      if (hit) {
        setCategoryId(c.id);
        setStyleId(hit.id);
        break;
      }
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [appliedPrompt?.ts]);

  const assembled = useMemo(
    () => assembleGenPrompt({ request, agentText, aspect }),
    [request, agentText, aspect],
  );
  // Длинный агент со слотами — инструкция: её исполняет LLM, дословно в промпт
  // она не идёт (иначе картинка рисует заголовки и скобки шаблона).
  const instructionAgent = useMemo(() => isInstructionAgent(agentText), [agentText]);

  const aspectOptions = useMemo(() => {
    const opts = chipOptions(imageSlug, "aspect");
    return opts.includes(aspect) ? opts : [aspect, ...opts];
  }, [imageSlug, aspect]);
  const resolutionOptions = useMemo(() => {
    const opts = chipOptions(imageSlug, "resolution");
    return opts.includes(resolution) ? opts : [resolution, ...opts];
  }, [imageSlug, resolution]);
  // Детализация есть не у всех моделей (только gpt-image) — как в доке генерации
  const detailOptions = useMemo(() => chipOptions(imageSlug, "detail"), [imageSlug]);

  const promptText = (idx: number) =>
    promptOverrides[String(idx)] ?? genPromptVariant(assembled, idx, count);

  const applyPrompt = (idx: number) => {
    onApplyPrompt(promptText(idx));
    toast.success("Промпт подставлен в поле запроса");
  };

  // Агент пишет ровно `count` промптов и раскладывает их по слотам.
  // Пустой массив — не получилось, тост об ошибке уже показан.
  const requestAgentPrompts = async (): Promise<string[]> => {
    const req = request.trim();
    if (!req) {
      toast.error("Пустой запрос: напишите, что должно быть в кадре");
      return [];
    }
    if (!style || !agentText.trim()) {
      toast.error("Выберите стиль: текст агента пуст");
      return [];
    }
    setAgentBusy(true);
    try {
      const r = await fetch("/api/gen-assistant/prompts", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ request: req, agent_text: agentText, aspect, count }),
      });
      if (!r.ok) {
        const err = (await r.json().catch(() => null)) as { detail?: string } | null;
        throw new Error(err?.detail || `HTTP ${r.status}`);
      }
      const data = (await r.json()) as {
        prompts?: string[];
        source?: string;
        warning?: string | null;
      };
      const prompts = (data.prompts ?? []).filter((p) => typeof p === "string" && p.trim());
      if (!prompts.length) throw new Error("пустой ответ агента");
      setPromptOverrides((prev) => {
        const next = { ...prev };
        prompts.forEach((p, i) => {
          next[String(i)] = p;
        });
        return next;
      });
      // Запоминаем стиль: первая успешная генерация станет фоном его плитки
      const pending = { styleId: style.id, ts: Date.now(), prefix: prompts[0].slice(0, 48) };
      lsSet(LS.pendingStyle, JSON.stringify(pending));
      setPendingArt(pending);
      if (data.warning) toast.warning(String(data.warning));
      return prompts;
    } catch (e) {
      toast.error(
        `Агент недоступен: ${e instanceof Error ? e.message : String(e)} — собрано локально`,
      );
      return [];
    } finally {
      setAgentBusy(false);
    }
  };

  // Кнопка панели: агент пишет промпты → сразу генерация всех.
  // Окно результатов автоматически НЕ открываем — только по клику на 📋.
  const runAgent = async () => {
    const prompts = await requestAgentPrompts();
    if (!prompts.length) return;
    toast.success(`Агент: ${prompts.length} промпт(ов) → генерация запущена`);
    onGenerateAll(prompts);
  };

  const generatePrompt = async (idx: number) => {
    let text = promptText(idx).trim();
    // Слот ещё не заполнен агентом, а агент — инструкция: без прогона через LLM
    // в модель уйдёт голый запрос без стиля. Сначала просим агента написать промпт.
    if (instructionAgent && promptOverrides[String(idx)] === undefined) {
      const prompts = await requestAgentPrompts();
      if (!prompts.length) return;
      text = (prompts[idx] ?? prompts[0]).trim();
    }
    if (!text) {
      toast.error("Промпт пуст — напишите запрос или выберите стиль");
      return;
    }
    // Запоминаем стиль: первая успешная генерация станет фоном его плитки
    if (style) {
      const pending = { styleId: style.id, ts: Date.now(), prefix: text.slice(0, 48) };
      lsSet(LS.pendingStyle, JSON.stringify(pending));
      setPendingArt(pending);
    }
    onGenerate(text);
  };

  // Поллинг истории: ловим первую успешную генерацию по агенту → фон плитки навсегда
  useEffect(() => {
    if (!pendingArt) return;
    if (artUrls[pendingArt.styleId]) {
      setPendingArt(null);
      lsRemove(LS.pendingStyle);
      return;
    }
    let stopped = false;
    const tick = async () => {
      try {
        const items = await api.listOutseeCreateHistory("image", { limit: 12 });
        if (stopped) return;
        const hit = items.find(
          (it) =>
            it.preview_url &&
            it.status !== "failed" &&
            it.prompt &&
            it.prompt.startsWith(pendingArt.prefix),
        );
        if (hit?.preview_url) {
          const url = hit.preview_url;
          lsSet(LS.artPrefix + pendingArt.styleId, url);
          setArtUrls((prev) => ({ ...prev, [pendingArt.styleId]: url }));
          setPendingArt(null);
          lsRemove(LS.pendingStyle);
          toast.success("Превью стиля обновлено картинкой из генерации");
        }
      } catch {
        /* история может быть недоступна */
      }
    };
    void tick();
    const id = window.setInterval(() => void tick(), 4000);
    const giveUp = window.setTimeout(() => {
      stopped = true;
      window.clearInterval(id);
    }, 3 * 60 * 1000);
    return () => {
      stopped = true;
      window.clearInterval(id);
      window.clearTimeout(giveUp);
    };
  }, [pendingArt, artUrls]);

  const saveCustomStyle = (catId: string) => {
    const name = newName.trim();
    const agent = newAgent.trim();
    if (!name || !agent) {
      toast.error("Нужны название и текст агента");
      return;
    }
    const id = `custom_${Date.now().toString(36)}`;
    const cs: CustomStyle = {
      id,
      categoryId: catId,
      art: newArt,
      name,
      file: "custom",
      desc: newDesc.trim() || "Свой стиль",
      color: newColor,
      tags: ["custom"],
      promptCore: agent,
    };
    setCustomStyles((prev) => [...prev, cs]);
    setCategoryId(catId);
    setStyleId(id);
    setAddOpen(false);
    setCatMenuOpen(false);
    setNewName("");
    setNewDesc("");
    setNewAgent("");
    toast.success(`Стиль «${name}» добавлен`);
  };

  const activeLlm = llmModels.find((m) => m.active) ?? llmModels[0];
  // Стили в выпадашке показываем только при наведении на категорию
  const menuCat = previewCat ? (categories.find((c) => c.id === previewCat) ?? null) : null;

  return (
    <div
      className={cn(
        "relative flex w-full min-w-0 items-stretch gap-2 transition-[height] duration-300 ease-out",
        expanded ? "h-[54vh]" : "h-[168px]",
      )}
      style={{ animation: "gaUp 0.25s ease-out" }}
    >
      <style>{`@keyframes gaUp{from{transform:translateY(12px);opacity:0}to{transform:translateY(0);opacity:1}}`}</style>

      {/* левая панель: категории + запрос + промпты (половина ширины) */}
      <div className="flex w-1/2 shrink-0 flex-col rounded-2xl border border-white/15 bg-[#121216]/95 backdrop-blur-2xl ring-1 ring-white/10 shadow-[0_20px_60px_rgba(0,0,0,0.85)]">
      {/* header: кнопка категорий + выбранный стиль + разворот + закрыть */}
      <div className="flex h-[34px] shrink-0 items-center gap-1.5 border-b border-white/[0.08] px-2.5">
        <Sparkles className="h-3.5 w-3.5 shrink-0" style={{ color: OUTSEE_ACCENT }} />
        <button
          type="button"
          onClick={() => {
            setCatMenuOpen((v) => {
              if (!v) setPreviewCat(null);
              return !v;
            });
            setAddOpen(false);
          }}
          className={cn(
            "inline-flex items-center gap-1 rounded-lg px-2 py-1 text-[11px] font-semibold transition",
            catMenuOpen
              ? "bg-[#22d3ee]/15 text-[#22d3ee] ring-1 ring-[#22d3ee]/40"
              : "bg-white/[0.04] text-white/70 hover:bg-white/[0.08] hover:text-white",
          )}
        >
          Категория: {category.name}
          <ChevronDown className={cn("h-3 w-3 transition", catMenuOpen && "rotate-180")} />
        </button>
        {style && (
          <span className="inline-flex min-w-0 items-center gap-1.5 rounded-lg bg-white/[0.04] px-2 py-1">
            <span
              className="h-2 w-2 shrink-0 rounded-full"
              style={{ backgroundColor: GEN_STYLE_COLORS[style.color] }}
            />
            <span className="truncate text-[10px] font-semibold text-white/80">{style.name}</span>
          </span>
        )}
        <span className="ml-auto flex items-center gap-1">
          <button
            type="button"
            onClick={() => setExpanded((v) => !v)}
            title={expanded ? "Свернуть к высоте дока" : "Развернуть вверх поверх изображения"}
            className="inline-flex h-6 w-6 items-center justify-center rounded-lg border border-white/10 bg-white/[0.03] text-white/70 transition hover:bg-white/[0.08] hover:text-white"
          >
            {expanded ? <ChevronsDown className="h-3 w-3" /> : <ChevronsUp className="h-3 w-3" />}
          </button>
          <button
            type="button"
            onClick={onClose}
            title="Скрыть помощника"
            className="inline-flex h-6 w-6 items-center justify-center rounded-lg border border-white/10 bg-white/[0.03] text-white/70 transition hover:bg-white/[0.08] hover:text-white"
          >
            <X className="h-3 w-3" />
          </button>
        </span>
      </div>

      {/* выпадающее меню категорий/стилей — по кнопке, вверх поверх картинки */}
      {catMenuOpen && (
        <>
          <div
            className="fixed inset-0 z-40"
            onClick={() => {
              setCatMenuOpen(false);
              setAddOpen(false);
            }}
          />
          <div
            ref={popRef}
            className="absolute bottom-full left-0 z-50 mb-2 w-full rounded-xl border border-white/15 bg-[#121216]/98 p-2 shadow-[0_20px_50px_rgba(0,0,0,0.85)] backdrop-blur-2xl"
            onMouseEnter={cancelHide}
            onMouseLeave={scheduleHide}
          >
            <div className="grid grid-cols-4 gap-1.5">
              {categories.map((c) => {
                const rep = c.styles[0];
                return (
                  <button
                    key={c.id}
                    type="button"
                    onMouseEnter={(e) => {
                      cancelHide();
                      setPreviewCat(c.id);
                      const rect = popRef.current?.getBoundingClientRect();
                      if (rect) {
                        setStylesPos({ x: e.clientX - rect.left, y: e.clientY - rect.top, w: rect.width });
                      }
                    }}
                    onClick={() => setPreviewCat(c.id)}
                    title={c.name}
                    className={cn(
                      "relative h-[115px] overflow-hidden rounded-none border text-left transition",
                      c.id === menuCat?.id
                        ? "border-[#22d3ee]/70 ring-2 ring-[#22d3ee]/40"
                        : "border-white/10 hover:border-white/30",
                    )}
                  >
                    <TileBg art={c.art} color={rep?.color ?? "cyan"} artUrl={rep ? artUrls[rep.id] : undefined} />
                    <span className="absolute inset-x-0 bottom-0 bg-black/60 px-1 py-0.5 backdrop-blur-sm">
                      <span className="block truncate text-[9px] font-bold leading-tight text-white/95">
                        {c.name}
                      </span>
                    </span>
                  </button>
                );
              })}
            </div>
            {menuCat && (
              <div
                className="absolute z-10 w-[600px] rounded-xl border border-white/15 bg-[#121216]/98 p-2 shadow-[0_20px_50px_rgba(0,0,0,0.85)] backdrop-blur-2xl"
                style={{
                  left: Math.max(4, Math.min((stylesPos?.x ?? 60) - 16, (stylesPos?.w ?? 700) - 604)),
                  // правило 70%: низ окна не ниже 30% высоты плиток категорий —
                  // ячейки категорий остаются открытыми для нажатия минимум на 70%
                  top: Math.min((stylesPos?.y ?? 60) + 8, 8 + Math.round(115 * 0.3)),
                  transform: "translateY(-100%)",
                }}
                onMouseEnter={cancelHide}
                onMouseLeave={scheduleHide}
              >
            <div className="mb-1 px-0.5 font-mono text-[9px] font-bold uppercase tracking-wider text-white/40">
              {menuCat.name} · {menuCat.styles.length} стилей
            </div>
            {!addOpen ? (
              <div className="grid max-h-[330px] grid-cols-3 gap-1.5 overflow-y-auto">
                {menuCat.styles.map((s) => (
                  <button
                    key={s.id}
                    type="button"
                    onClick={() => {
                      setCategoryId(menuCat.id);
                      setStyleId(s.id === styleId && menuCat.id === categoryId ? "" : s.id);
                      setCatMenuOpen(false);
                    }}
                    title={`${s.name} — ${s.desc}`}
                    className={cn(
                      "relative h-[155px] overflow-hidden rounded-none border text-left transition",
                      s.id === styleId && menuCat.id === categoryId
                        ? "border-[#22d3ee]/70 ring-2 ring-[#22d3ee]/40"
                        : "border-white/10 hover:border-white/30",
                    )}
                  >
                    <TileBg art={s.art} color={s.color} artUrl={artUrls[s.id]} />
                    <span className="absolute inset-x-0 bottom-0 bg-black/60 px-1.5 py-0.5 backdrop-blur-sm">
                      <span className="block truncate text-[9px] font-bold leading-tight text-white/95">
                        {s.name}
                      </span>
                    </span>
                  </button>
                ))}
                <button
                  type="button"
                  onClick={() => setAddOpen(true)}
                  title="Добавить свой стиль"
                  className="flex h-[155px] items-center justify-center rounded-none border border-dashed border-white/15 text-white/40 transition hover:border-[#22d3ee]/40 hover:text-[#22d3ee]"
                >
                  <Plus className="h-4 w-4" />
                </button>
              </div>
            ) : (
              /* форма нового стиля */
              <div className="max-h-[220px] space-y-1.5 overflow-y-auto">
                <input
                  value={newName}
                  onChange={(e) => setNewName(e.target.value)}
                  placeholder="Название стиля"
                  className={selectCls}
                />
                <input
                  value={newDesc}
                  onChange={(e) => setNewDesc(e.target.value)}
                  placeholder="Короткое описание (необязательно)"
                  className={selectCls}
                />
                <div className="flex items-center gap-1">
                  {(Object.keys(GEN_STYLE_COLORS) as GenStyleDef["color"][]).map((c) => (
                    <button
                      key={c}
                      type="button"
                      onClick={() => setNewColor(c)}
                      className={cn(
                        "h-4 w-4 rounded-full transition",
                        newColor === c && "ring-2 ring-white/70 ring-offset-1 ring-offset-black",
                      )}
                      style={{ backgroundColor: GEN_STYLE_COLORS[c] }}
                    />
                  ))}
                </div>
                <div className="grid grid-cols-9 gap-1">
                  {ART_KEYS.map((a) => (
                    <button
                      key={a}
                      type="button"
                      onClick={() => setNewArt(a)}
                      className={cn(
                        "relative h-[24px] overflow-hidden rounded-md border transition",
                        newArt === a ? "border-[#22d3ee]/70 ring-1 ring-[#22d3ee]/50" : "border-white/10",
                      )}
                    >
                      <GenStyleArtView art={a} color={newColor} />
                    </button>
                  ))}
                </div>
                <textarea
                  value={newAgent}
                  onChange={(e) => setNewAgent(e.target.value)}
                  rows={3}
                  placeholder="Текст агента стиля (promptCore): EN-ядро + «Не …» негативы…"
                  className={cn(areaCls, "font-mono text-[11px]")}
                />
                <div className="flex gap-1.5">
                  <button
                    type="button"
                    onClick={() => saveCustomStyle(menuCat.id)}
                    className="rounded-lg bg-gradient-to-r from-[#22d3ee] to-[#0ea5e9] px-2.5 py-1 text-[10px] font-extrabold uppercase tracking-wider text-black transition hover:brightness-110"
                  >
                    Сохранить
                  </button>
                  <button
                    type="button"
                    onClick={() => setAddOpen(false)}
                    className="rounded-lg border border-white/10 bg-white/[0.04] px-2.5 py-1 text-[10px] font-medium text-white/60 transition hover:text-white"
                  >
                    Отмена
                  </button>
                </div>
              </div>
            )}
              </div>
            )}
          </div>
        </>
      )}

      {/* тело: запрос + промпты */}
      <div className="min-h-0 flex-1 space-y-2 overflow-y-auto px-2.5 py-2">
          <div className="flex items-stretch gap-1.5">
            <textarea
              value={request}
              onChange={(e) => setRequest(e.target.value)}
              rows={2}
              placeholder="Ваш запрос: что должно быть в кадре…"
              className={cn(areaCls, "min-w-0 flex-1")}
            />
            <button
              type="button"
              onClick={() => setResultsOpen((v) => !v)}
              title="Результаты: собранные промпты"
              className={cn(
                "flex w-[64px] shrink-0 flex-col items-center justify-center gap-1 rounded-lg border transition",
                resultsOpen
                  ? "border-[#22d3ee]/50 bg-[#22d3ee]/15 text-[#22d3ee]"
                  : "border-white/10 bg-white/[0.04] text-white/70 hover:border-[#22d3ee]/40 hover:bg-[#22d3ee]/10 hover:text-white",
              )}
            >
              <ClipboardList className="h-7 w-7" />
              <span className="font-mono text-[9px] font-bold leading-none">{count} шт</span>
            </button>
          </div>
          <div className="flex items-center gap-1.5">
            <button
              type="button"
              disabled={generating || agentBusy}
              onClick={() => void runAgent()}
              title="Агент напишет промпты по запросу и стилю — и сразу запустит генерацию"
              className="inline-flex items-center justify-center rounded-lg bg-gradient-to-r from-[#22d3ee] to-[#0ea5e9] px-2.5 py-1.5 text-[10px] font-extrabold uppercase tracking-wider text-black shadow-[0_0_15px_rgba(34,211,238,0.3)] transition hover:brightness-110 disabled:opacity-40"
            >
              {agentBusy ? "Агент пишет…" : "Сгенерировать"}
            </button>
            <button
              type="button"
              onClick={() => applyPrompt(0)}
              className="inline-flex items-center rounded-lg border border-white/10 bg-white/[0.04] px-2 py-1.5 text-[10px] font-medium text-white/70 transition hover:border-[#22d3ee]/40 hover:bg-[#22d3ee]/10 hover:text-white"
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
              className="w-[40px] rounded-lg border border-white/10 bg-[#16161b] px-1 py-1.5 text-center text-[11px] text-white/85 focus:border-[#22d3ee]/60 focus:outline-none"
            />
          </div>
      </div>

      {/* результаты промптов — окно вверх по иконке справа от запроса (механика как у категорий) */}
      {resultsOpen && (
        <>
          <div className="fixed inset-0 z-40" onClick={() => setResultsOpen(false)} />
          <div
            className="absolute bottom-full left-0 z-50 mb-2 max-h-[50vh] w-full space-y-2 overflow-y-auto rounded-xl border border-white/15 bg-[#121216]/98 p-2 shadow-[0_20px_50px_rgba(0,0,0,0.85)] backdrop-blur-2xl"
            onMouseEnter={cancelResultsHide}
            onMouseLeave={scheduleResultsHide}
          >
            {Array.from({ length: count }, (_, idx) => {
              const key = String(idx);
              const overridden = promptOverrides[key] !== undefined;
              const text = promptText(idx);
              return (
                <div key={key} className="rounded-xl border border-white/10 bg-white/[0.02]">
                  <div className="flex items-center gap-2 border-b border-white/[0.06] px-2.5 py-1">
                    <span className="text-[10px] font-semibold text-white/85">
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
                        сбросить
                      </button>
                    )}
                    <span className="ml-auto font-mono text-[9px] text-white/35">
                      {text.length} симв.
                    </span>
                  </div>
                  <div className="space-y-1.5 px-2.5 py-1.5">
                    <textarea
                      value={text}
                      onChange={(e) =>
                        setPromptOverrides((prev) => ({ ...prev, [key]: e.target.value }))
                      }
                      rows={expanded ? 12 : 6}
                      className={cn(areaCls, "font-mono text-[10px]")}
                    />
                    <div className="flex items-center gap-1.5">
                      <button
                        type="button"
                        disabled={generating || agentBusy}
                        onClick={() => void generatePrompt(idx)}
                        className="inline-flex items-center justify-center rounded-lg bg-gradient-to-r from-[#22d3ee] to-[#0ea5e9] px-2.5 py-1 text-[10px] font-extrabold uppercase tracking-wider text-black transition hover:brightness-110 disabled:opacity-40"
                      >
                        Сгенерировать
                      </button>
                      {instructionAgent && !overridden && (
                        <span className="font-mono text-[9px] text-[#22d3ee]/70">
                          агент-инструкция: промпт напишет LLM
                        </span>
                      )}
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
          </div>
        </>
      )}
      </div>

      {/* распорка: правая панель прижата к правому краю */}
      <div className="min-w-0 flex-1" />

      {/* правая панель — отдельное меню у правого края: агент + управление */}
      <div className="-mr-3 flex w-[580px] shrink-0 flex-col space-y-1.5 overflow-y-auto rounded-2xl border border-white/15 bg-[#121216]/95 px-2.5 py-2 backdrop-blur-2xl ring-1 ring-white/10 shadow-[0_20px_60px_rgba(0,0,0,0.85)] lg:-mr-5">
          <div className="flex items-center gap-1.5">
            <span className={labelSmCls}>LLM</span>
            <select
              value={activeLlm?.id ?? ""}
              disabled={llmBusy || !llmModels.length}
              onChange={(e) => void onLlmChange(e.target.value)}
              className={selectSmCls}
              title="Текстовая модель Studio (как в топбаре)"
            >
              {!llmModels.length && <option value="">загрузка…</option>}
              {llmModels.map((m) => (
                <option key={m.id} value={m.id}>
                  {m.label}
                  {m.key_configured ? "" : " · нет ключа"}
                </option>
              ))}
            </select>
          </div>
          <div className="flex items-center gap-1.5">
            <span className={labelSmCls}>Модель</span>
            <button
              type="button"
              onClick={onOpenModelPicker}
              className="flex min-w-0 flex-1 items-center justify-between gap-2 rounded-lg border border-white/10 bg-[#16161b] px-2 py-1 text-[11px] text-white/85 transition hover:border-[#22d3ee]/40"
            >
              <span className="truncate">{modelName}</span>
              <ChevronDown className="h-3 w-3 shrink-0 opacity-60" />
            </button>
          </div>
          <div className="flex items-center gap-1.5">
            <span className={labelSmCls}>Формат</span>
            <select
              value={aspect}
              onChange={(e) => onAspectChange(e.target.value)}
              className={selectSmCls}
            >
              {aspectOptions.map((a) => (
                <option key={a} value={a}>
                  {a}
                </option>
              ))}
            </select>
          </div>
          <div className="flex items-center gap-1.5">
            <span className={labelSmCls}>Разреш.</span>
            <select
              value={resolution}
              onChange={(e) => onResolutionChange(e.target.value)}
              className={selectSmCls}
            >
              {resolutionOptions.map((r) => (
                <option key={r} value={r}>
                  {r}
                </option>
              ))}
            </select>
          </div>
          {detailOptions.length > 0 && (
            <div className="flex items-center gap-1.5">
              <span className={labelSmCls}>Детализац.</span>
              <select
                value={detail}
                onChange={(e) => onDetailChange(e.target.value)}
                className={selectSmCls}
              >
                {detailOptions.map((d) => (
                  <option key={d} value={d}>
                    {detailLabel(d)}
                  </option>
                ))}
              </select>
            </div>
          )}
          {/* агент: весь текст сразу, редактируется, сохраняется автоматически */}
          {style && (
            <div className="space-y-1 pt-1">
              <div className="flex items-center gap-2 px-0.5">
                <span
                  className="h-2 w-2 shrink-0 rounded-full"
                  style={{ backgroundColor: GEN_STYLE_COLORS[style.color] }}
                />
                <span className="text-[10px] font-semibold text-white/85">Агент «{style.name}»</span>
                <span className="font-mono text-[8px] text-white/35">
                  {instructionAgent ? "инструкция — исполняет LLM" : "ядро стиля — идёт в промпт"}
                </span>
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
                    className="ml-auto rounded-md bg-[#22d3ee]/15 px-1.5 py-0.5 text-[9px] font-bold uppercase tracking-wider text-[#22d3ee] transition hover:bg-[#22d3ee]/25"
                    title="Вернуть исходный текст агента"
                  >
                    сбросить
                  </button>
                )}
              </div>
              <textarea
                value={agentText}
                onChange={(e) =>
                  setAgentOverrides((prev) => ({ ...prev, [style.id]: e.target.value }))
                }
                rows={9}
                className={cn(areaCls, "font-mono text-[11px]")}
              />
            </div>
          )}
      </div>

    </div>
  );
}
