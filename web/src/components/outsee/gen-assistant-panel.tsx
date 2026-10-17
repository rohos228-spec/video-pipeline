"use client";

/**
 * Помощник: одно большое поле, три вкладки.
 * Запрос — вход. Агент — стиль (не в картинку). Промпт — результат, уходит в GPT.
 * По умолчанию открыт Промпт — это главное, что смотришь и правишь.
 */

import { useEffect, useMemo, useRef, useState } from "react";
import {
  ChevronDown,
  ChevronsDown,
  ChevronsUp,
  Paperclip,
  Plus,
  Sparkles,
  X,
} from "lucide-react";
import { toast } from "sonner";
import { api } from "@/lib/api";
import { cn } from "@/lib/utils";
import { chipOptions, detailLabel } from "@/lib/outsee-catalog";
import {
  GEN_ASSISTANT_CATEGORIES,
  GEN_STYLE_COLORS,
  assembleGenPrompt,
  genPromptVariant,
  isUnfilledAssistantPrompt,
  assistantRefHandle,
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
  /** Иконка модели картинки (как в доке генерации), не подпись GPT. */
  modelIcon?: string | null;
  onApplyPrompt: (text: string) => void;
  onGenerate: (text: string) => void;
  /** Сразу карточки в «Генерации», ещё до ответа LLM. */
  onPrepareGenerate: (preview: string, count: number) => string[];
  onFailGenerate: (draftIds: string[]) => void;
  /** Агент написал N промптов → сразу генерация всех (по одной на промпт). */
  onGenerateAll: (texts: string[], draftIds?: string[]) => void;
  expanded: boolean;
  onExpandedChange: (v: boolean) => void;
  references: { id: string; url: string; name: string }[];
  maxReferences: number;
  onAddReferenceFiles: (files: File[]) => void;
  onRemoveReference: (id: string) => void;
};

type EditorTab = "request" | "agent" | "prompt";

const STAGE_TABS: { id: EditorTab; label: string }[] = [
  { id: "request", label: "Запрос" },
  { id: "agent", label: "Агент" },
  { id: "prompt", label: "Промпт" },
];

type CustomStyle = GenStyleDef & { categoryId: string; artUrl?: string };

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

const selectCls =
  "w-full rounded-md border border-white/12 bg-[#16161b] px-2.5 py-1.5 text-[13px] text-white/85 focus:border-white/25 focus:outline-none";
const areaCls =
  "w-full resize-none rounded-md border border-white/12 bg-[#16161b] px-3 py-2 text-[13px] font-normal leading-relaxed text-white/90 placeholder-white/35 focus:border-white/25 focus:outline-none";
const chipCls = (active: boolean) =>
  cn(
    "inline-flex h-7 items-center gap-1 px-1 text-[12px] font-medium transition",
    active ? "text-white" : "text-white/55 hover:text-white",
  );

const STYLE_COVER_FILE: Record<string, string> = {
  infographic_tutor: "/gen-styles/infographic_tutor.jpg",
  custom_mtwuuy1z: "/gen-styles/custom_mtwuuy1z.jpg",
  custom_mtwxwlev: "/gen-styles/custom_mtwxwlev.jpg",
  custom_mtxfckrd: "/gen-styles/custom_mtxfckrd.jpg",
  custom_mty20b3s: "/gen-styles/custom_mty20b3s.jpg",
};
const COVER_CACHE = "c6";

function bustCover(u: string): string {
  if (!u.startsWith("/gen-styles/") && !u.includes("/api/files?path=")) return u;
  return u.includes("?") ? `${u}&v=${COVER_CACHE}` : `${u}?v=${COVER_CACHE}`;
}

async function persistStyleCover(
  styleId: string,
  src: { file?: File; path?: string | null },
): Promise<string> {
  const fd = new FormData();
  fd.append("style_id", styleId);
  if (src.file) fd.append("file", src.file);
  if (src.path) fd.append("source_path", src.path);
  const r = await fetch("/api/gen-assistant/style-cover", { method: "POST", body: fd });
  if (!r.ok) {
    const err = (await r.json().catch(() => null)) as { detail?: string } | null;
    throw new Error(err?.detail || `HTTP ${r.status}`);
  }
  const data = (await r.json()) as { cover?: string };
  const cover = (data.cover || "").trim();
  if (!cover) throw new Error("пустая обложка");
  return cover;
}

function styleTileSrcs(
  s: { id: string; cover?: string; artUrl?: string } | undefined,
  artUrls: Record<string, string>,
): string[] {
  if (!s) return [];
  const out: string[] = [];
  const add = (u?: string) => {
    let v = (u || "").trim();
    if (!v) return;
    if (v.startsWith("blob:") || v.startsWith("file:")) return;
    v = bustCover(v);
    if (!out.includes(v)) out.push(v);
  };
  add(STYLE_COVER_FILE[s.id]);
  add(s.cover);
  add(s.artUrl);
  if (!STYLE_COVER_FILE[s.id]) add(artUrls[s.id]);
  return out;
}

/** Фото обложки поверх SVG. JPG/PNG — слой z-10; эскиз только пока фото не загрузилось. */
function TileBg({
  art,
  color,
  srcs,
}: {
  art: GenStyleArt;
  color: GenStyleDef["color"];
  srcs: string[];
}) {
  const [i, setI] = useState(0);
  const [photoOk, setPhotoOk] = useState(false);
  const src = srcs[i];
  return (
    <>
      {!photoOk ? <GenStyleArtView art={art} color={color} /> : null}
      {src ? (
        // eslint-disable-next-line @next/next/no-img-element
        <img
          src={src}
          alt=""
          className="pointer-events-none absolute inset-0 z-10 h-full w-full object-cover"
          onLoad={() => setPhotoOk(true)}
          onError={() => {
            setPhotoOk(false);
            setI((n) => n + 1);
          }}
        />
      ) : null}
    </>
  );
}

export function GenAssistantPanel({
  onClose,
  appliedPrompt,
  imageSlug,
  modelName,
  aspect,
  resolution,
  detail,
  generating: _generating,
  onAspectChange,
  onResolutionChange,
  onDetailChange,
  onOpenModelPicker,
  modelIcon,
  onApplyPrompt,
  onGenerate,
  onPrepareGenerate,
  onFailGenerate,
  onGenerateAll,
  expanded,
  onExpandedChange,
  references,
  maxReferences,
  onAddReferenceFiles,
  onRemoveReference,
}: Props) {
  const [categoryId, setCategoryId] = useState(() => lsGet(LS.category, GEN_ASSISTANT_CATEGORIES[0].id));
  const [styleId, setStyleId] = useState(() => lsGet(LS.style, ""));
  const [request, setRequest] = useState(() => lsGet(LS.request, ""));
  const [count, setCount] = useState(() => {
    const n = Math.round(Number(lsGet(LS.count, "1")) || 1);
    return Math.max(1, Math.min(4, n));
  });
  const [editorTab, setEditorTab] = useState<EditorTab>("request");
  const [promptSlot, setPromptSlot] = useState(0);
  const [menu, setMenu] = useState<null | "llm" | "aspect" | "resolution" | "detail">(null);
  const dockRef = useRef<HTMLDivElement | null>(null);
  const [agentError, setAgentError] = useState("");
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
    if (addOpen) return;
    cancelHide();
    hideTimer.current = window.setTimeout(() => setPreviewCat(null), 180);
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
  // Референсы и пожелание пользователя → агент собирает LLM по картинкам
  const [newRefs, setNewRefs] = useState<File[]>([]);
  const [newHint, setNewHint] = useState("");
  const [buildBusy, setBuildBusy] = useState(false);
  const refInput = useRef<HTMLInputElement | null>(null);
  const genRefInput = useRef<HTMLInputElement | null>(null);
  const requestAreaRef = useRef<HTMLTextAreaElement | null>(null);
  const promptAreaRef = useRef<HTMLTextAreaElement | null>(null);
  const refPreviews = useMemo(() => newRefs.map((f) => URL.createObjectURL(f)), [newRefs]);
  useEffect(() => {
    return () => refPreviews.forEach((u) => URL.revokeObjectURL(u));
  }, [refPreviews]);

  // LLM (текстовая модель Studio, как в топбаре)
  const [llmModels, setLlmModels] = useState<TextLlmModel[]>([]);
  const [llmBusy, setLlmBusy] = useState(false);

  useEffect(() => lsSet(LS.category, categoryId), [categoryId]);
  useEffect(() => lsSet(LS.style, styleId), [styleId]);
  useEffect(() => {
    if (request.trim()) lsSet(LS.request, request);
  }, [request]);
  useEffect(() => lsSet(LS.count, String(count)), [count]);
  useEffect(() => {
    setPromptSlot((s) => Math.max(0, Math.min(s, count - 1)));
  }, [count]);
  useEffect(() => lsSet(LS.agentOverrides, JSON.stringify(agentOverrides)), [agentOverrides]);
  const customStylesHydrated = useRef(false);
  const diskStylesReady = useRef(false);
  const diskAgentsReady = useRef(false);
  useEffect(() => {
    // Не затирать LS пустым [] при гидрации (window на SSR нет → стейт []).
    if (!customStylesHydrated.current) {
      customStylesHydrated.current = true;
      try {
        const fromLs = (JSON.parse(lsGet(LS.customStyles, "[]")) as CustomStyle[]).filter(
          (s) => s?.id,
        );
        if (Array.isArray(fromLs) && fromLs.length > 0 && customStyles.length === 0) {
          setCustomStyles(fromLs);
          return;
        }
      } catch {
        /* ignore */
      }
    }
    if (customStyles.length === 0) {
      const existing = lsGet(LS.customStyles, "");
      if (existing && existing !== "[]") return;
    }
    lsSet(LS.customStyles, JSON.stringify(customStyles));
    if (diskStylesReady.current && customStyles.length > 0) {
      void fetch("/api/gen-assistant/custom-styles", {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ styles: customStyles }),
      }).catch(() => undefined);
    }
  }, [customStyles]);

  useEffect(() => {
    if (!diskAgentsReady.current) return;
    const agents = Object.fromEntries(
      Object.entries(agentOverrides).filter(([, v]) => (v || "").trim()),
    );
    if (!Object.keys(agents).length) return;
    void fetch("/api/gen-assistant/agent-overrides", {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ agents }),
    }).catch(() => undefined);
  }, [agentOverrides]);

  useEffect(() => {
    let cancelled = false;
    fetch("/api/gen-assistant/custom-styles", { cache: "no-store" })
      .then((r) => (r.ok ? r.json() : null))
      .then((data: { styles?: CustomStyle[] } | null) => {
        if (cancelled) return;
        const remote = Array.isArray(data?.styles) ? data.styles : [];
        if (remote.length) {
          for (const s of remote) {
            const cover = (s.cover || s.artUrl || "").trim();
            if (cover.startsWith("/gen-styles/")) lsSet(LS.artPrefix + s.id, cover);
          }
          setArtUrls(readArtUrls());
          setCustomStyles((prev) => {
            const byId = new Map<string, CustomStyle>();
            for (const s of [...prev, ...remote]) {
              if (!s?.id) continue;
              const old = byId.get(s.id);
              if (!old) {
                byId.set(s.id, s);
                continue;
              }
              const cover = [old.cover, old.artUrl, s.cover, s.artUrl]
                .map((u) => (u || "").trim())
                .find((u) => u.startsWith("/gen-styles/"));
              byId.set(s.id, cover ? { ...s, cover, artUrl: cover } : s);
            }
            return Array.from(byId.values());
          });
        }
        diskStylesReady.current = true;
      })
      .catch(() => {
        diskStylesReady.current = true;
      });
    fetch("/api/gen-assistant/agent-overrides", { cache: "no-store" })
      .then((r) => (r.ok ? r.json() : null))
      .then((data: { agents?: Record<string, string> } | null) => {
        if (cancelled) return;
        const remote = data?.agents && typeof data.agents === "object" ? data.agents : {};
        setAgentOverrides((prev) => {
          const out: Record<string, string> = {};
          for (const src of [prev, remote]) {
            for (const [k, v] of Object.entries(src)) {
              const text = (v || "").trim();
              if (!text) continue;
              out[k] = text;
            }
          }
          return out;
        });
        diskAgentsReady.current = true;
      })
      .catch(() => {
        diskAgentsReady.current = true;
      });
    return () => {
      cancelled = true;
    };
  }, []);

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

  useEffect(() => {
    if (!menu) return;
    const onDown = (e: MouseEvent) => {
      if (dockRef.current && !dockRef.current.contains(e.target as Node)) setMenu(null);
    };
    window.addEventListener("mousedown", onDown);
    return () => window.removeEventListener("mousedown", onDown);
  }, [menu]);

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
    setPromptSlot(0);
    setEditorTab("prompt");
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
    () =>
      assembleGenPrompt({
        request,
        agentText,
        aspect,
        refLabels: references.map(
          (r, i) => `${assistantRefHandle(i)} — ${r.name || "референс"}`,
        ),
      }),
    [request, agentText, aspect, references],
  );
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

  useEffect(() => {
    if (editorTab !== "prompt") return;
    const el = promptAreaRef.current;
    if (!el) return;
    el.style.height = "auto";
    const min = 56;
    const max = Math.round(window.innerHeight * 0.42);
    el.style.height = `${Math.min(max, Math.max(min, el.scrollHeight))}px`;
  }, [editorTab, promptSlot, promptOverrides, assembled, count]);

  const applyPrompt = (idx: number) => {
    onApplyPrompt(promptText(idx));
    toast.success("Промпт подставлен в поле запроса");
  };

  const insertRefHandle = (handle: string) => {
    if (!expanded) onExpandedChange(true);
    setEditorTab("request");
    const el = requestAreaRef.current;
    const start = el?.selectionStart ?? request.length;
    const end = el?.selectionEnd ?? request.length;
    const before = request.slice(0, start);
    const after = request.slice(end);
    const padL = before && !/\s$/.test(before) ? " " : "";
    const padR = after && !/^\s/.test(after) ? " " : "";
    const next = `${before}${padL}${handle}${padR}${after}`;
    setRequest(next);
    requestAnimationFrame(() => {
      const area = requestAreaRef.current;
      if (!area) return;
      const pos = start + padL.length + handle.length + padR.length;
      area.focus();
      area.setSelectionRange(pos, pos);
    });
  };

  const notifyAgentError = (msg: string) => {
    setAgentError(msg);
    toast.error(msg, { duration: 12_000, position: "top-center" });
  };

  const lastRequestRef = useRef(request);
  const startGenerationNow = async () => {
    const typed = request.trim();
    const req =
      typed ||
      lastRequestRef.current.trim() ||
      lsGet(LS.request, "").trim();
    if (!req) {
      notifyAgentError("Пустой запрос: напишите, что должно быть в кадре");
      return;
    }
    lastRequestRef.current = req;
    if (!style || !agentText.trim()) {
      notifyAgentError("Выберите стиль: текст агента пуст");
      return;
    }
    setRequest("");
    setPromptOverrides({});
    setAgentError("");
    setEditorTab("request");
    const draftIds = onPrepareGenerate(req, count);
    try {
      const r = await fetch("/api/gen-assistant/prompts", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          request: req,
          agent_text: agentText,
          aspect,
          count,
          ref_labels: references.map(
            (r, i) => `${assistantRefHandle(i)} — ${r.name || "референс"}`,
          ),
        }),
      });
      if (!r.ok) {
        const err = (await r.json().catch(() => null)) as { detail?: string } | null;
        throw new Error(err?.detail || `HTTP ${r.status}`);
      }
      const data = (await r.json()) as {
        prompts?: string[];
        source?: string;
      };
      if (data.source === "local") {
        throw new Error("Агент не собрал промпт — генерация не запущена");
      }
      const prompts = (data.prompts || [])
        .map((t) => t.trim())
        .filter((t) => t && !isUnfilledAssistantPrompt(t, req));
      if (!prompts.length) {
        throw new Error("Промпт не собран агентом — генерация не запущена");
      }
      const pending = { styleId: style.id, ts: Date.now(), prefix: prompts[0].slice(0, 48) };
      lsSet(LS.pendingStyle, JSON.stringify(pending));
      setPendingArt(pending);
      toast.success(`Генерация запущена: ${prompts.length}`, {
        duration: 6_000,
        position: "top-center",
      });
      onFailGenerate(draftIds.slice(prompts.length));
      onGenerateAll(prompts, draftIds.slice(0, prompts.length));
    } catch (e) {
      onFailGenerate(draftIds);
      notifyAgentError(
        e instanceof Error ? e.message : "Агент не собрал промпт — генерация не запущена",
      );
    }
  };

  const applyStyleCover = (styleId: string, url: string) => {
    lsSet(LS.artPrefix + styleId, url);
    setArtUrls((prev) => ({ ...prev, [styleId]: url }));
    setCustomStyles((prev) =>
      prev.map((s) => (s.id === styleId ? { ...s, cover: url, artUrl: url } : s)),
    );
  };

  // Поллинг истории: первая успешная генерация → /gen-styles/{id} на плитке
  useEffect(() => {
    if (!pendingArt) return;
    let stopped = false;
    const tick = async () => {
      try {
        const items = await api.listOutseeCreateHistory("image", { limit: 12 });
        if (stopped) return;
        const hit = items.find(
          (it) =>
            it.status === "done" &&
            Boolean(it.preview_url || it.path) &&
            it.prompt &&
            it.prompt.startsWith(pendingArt.prefix),
        );
        if (!hit) return;
        let url = hit.preview_url || "";
        if (hit.path) {
          try {
            url = await persistStyleCover(pendingArt.styleId, { path: hit.path });
          } catch {
            /* оставляем /api/files — плитка всё равно покажет */
          }
        }
        if (!url) return;
        applyStyleCover(pendingArt.styleId, url);
        setPendingArt(null);
        lsRemove(LS.pendingStyle);
        toast.success("Превью стиля обновлено картинкой из генерации");
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

  // Референсы + пожелание → готовый текст агента (vision-LLM разбирает стиль).
  const buildAgentFromRefs = async () => {
    if (!newRefs.length) {
      toast.error("Приложите хотя бы одну картинку-референс");
      return;
    }
    setBuildBusy(true);
    try {
      const fd = new FormData();
      newRefs.forEach((f) => fd.append("files", f));
      fd.append("request", newHint.trim());
      fd.append("name_hint", newName.trim());
      const r = await fetch("/api/gen-assistant/build-agent", { method: "POST", body: fd });
      if (!r.ok) {
        const err = (await r.json().catch(() => null)) as { detail?: string } | null;
        throw new Error(err?.detail || `HTTP ${r.status}`);
      }
      const data = (await r.json()) as {
        name?: string;
        desc?: string;
        agent?: string;
        chars?: number;
        bytes?: number;
        warning?: string;
      };
      if (!data.agent) throw new Error("пустой ответ");
      setNewAgent(data.agent);
      if (!newName.trim() && data.name) setNewName(data.name);
      if (!newDesc.trim() && data.desc) setNewDesc(data.desc);
      if (data.warning) toast.warning(data.warning);
      toast.success(`Агент собран: ${data.chars ?? data.agent.length} симв. / ${data.bytes ?? 0} байт`);
    } catch (e) {
      toast.error(`Не удалось собрать агента: ${e instanceof Error ? e.message : String(e)}`);
    } finally {
      setBuildBusy(false);
    }
  };

  const saveCustomStyle = async (catId: string) => {
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
    setAgentOverrides((prev) => ({ ...prev, [id]: agent }));
    setCategoryId(catId);
    setStyleId(id);
    setAddOpen(false);
    setCatMenuOpen(true);
    setPreviewCat(catId);
    setNewName("");
    setNewDesc("");
    setNewAgent("");
    setNewRefs([]);
    setNewHint("");
    toast.success(`Стиль «${name}» добавлен`);
  };

  const activeLlm = llmModels.find((m) => m.active) ?? llmModels[0];
  // Стили в выпадашке показываем только при наведении на категорию
  const menuCat = previewCat ? (categories.find((c) => c.id === previewCat) ?? null) : null;

  return (
    <div
      ref={dockRef}
      className={cn(
        "relative flex min-w-0 flex-1 flex-col bg-[#121216]/95 backdrop-blur-2xl",
        expanded ? "rounded-xl border border-white/12" : "rounded-lg border border-white/12",
      )}
      style={{ animation: "gaUp 0.25s ease-out" }}
    >
      <style>{`@keyframes gaUp{from{transform:translateY(12px);opacity:0}to{transform:translateY(0);opacity:1}}`}</style>

      {/* header: категория + стиль + свернуть + закрыть */}
      <div className="flex h-8 shrink-0 items-center gap-1.5 px-2">
        <Sparkles className="h-3.5 w-3.5 shrink-0 text-white/50" />
        <button
          type="button"
          onClick={() => {
            setCatMenuOpen((v) => {
              if (!v) setPreviewCat(categoryId);
              return !v;
            });
            setAddOpen(false);
          }}
          className={cn(
            "inline-flex items-center gap-1 px-1 py-1 text-[13px] font-medium transition",
            catMenuOpen ? "text-white" : "text-white/70 hover:text-white",
          )}
        >
          {category.name}
          <ChevronDown className={cn("h-3.5 w-3.5 transition", catMenuOpen && "rotate-180")} />
        </button>
        {style && expanded ? (
          <span className="inline-flex min-w-0 items-center gap-1.5 px-1 py-1">
            <span
              className="h-1.5 w-1.5 shrink-0 rounded-full"
              style={{ backgroundColor: GEN_STYLE_COLORS[style.color] }}
            />
            <span className="truncate text-[12px] font-medium text-white/70">{style.name}</span>
          </span>
        ) : null}
        <input
          ref={genRefInput}
          type="file"
          multiple
          accept="image/png,image/jpeg,image/webp,image/gif"
          className="hidden"
          onChange={(e) => {
            const files = Array.from(e.target.files ?? []);
            if (files.length) onAddReferenceFiles(files);
            e.target.value = "";
          }}
        />
        {expanded ? (
          <div className="flex shrink-0 items-center gap-0.5">
            {STAGE_TABS.map((t) => {
              const active = editorTab === t.id;
              return (
                <button
                  key={t.id}
                  type="button"
                  onClick={() => {
                    setEditorTab(t.id);
                    if (t.id === "prompt") onExpandedChange(true);
                  }}
                  className={cn(
                    "h-7 px-2 text-[13px] font-medium transition",
                    active ? "text-white" : "text-white/45 hover:text-white/80",
                  )}
                >
                  {t.label}
                </button>
              );
            })}
            {editorTab === "prompt" && count > 1 &&
              Array.from({ length: count }, (_, idx) => (
                <button
                  key={idx}
                  type="button"
                  onClick={() => setPromptSlot(idx)}
                  className={cn(
                    "h-7 w-6 text-[12px] font-medium transition",
                    promptSlot === idx ? "text-white" : "text-white/40 hover:text-white",
                  )}
                >
                  {idx + 1}
                </button>
              ))}
            {editorTab === "agent" && style && agentOverrides[style.id] !== undefined && (
              <button
                type="button"
                onClick={() =>
                  setAgentOverrides((prev) => {
                    const next = { ...prev };
                    delete next[style.id];
                    return next;
                  })
                }
                className="text-[12px] font-medium text-white/45 transition hover:text-white"
                title="Вернуть исходный текст агента"
              >
                Сбросить
              </button>
            )}
            {editorTab === "prompt" && promptOverrides[String(promptSlot)] !== undefined && (
              <button
                type="button"
                onClick={() =>
                  setPromptOverrides((prev) => {
                    const next = { ...prev };
                    delete next[String(promptSlot)];
                    return next;
                  })
                }
                className="text-[12px] font-medium text-white/45 transition hover:text-white"
                title="Вернуть автосборку"
              >
                Сбросить
              </button>
            )}
          </div>
        ) : null}
        <div className="flex min-w-0 items-center gap-1 overflow-x-auto">
          <button
            type="button"
            onClick={() => {
              if (!expanded) onExpandedChange(true);
              if (maxReferences <= 0) return;
              genRefInput.current?.click();
            }}
            disabled={maxReferences > 0 && references.length >= maxReferences}
            title={
              maxReferences <= 0
                ? "Эта модель не принимает референсы"
                : "Приложить картинку. Клик по имени вставит @image в запрос"
            }
            className={cn(
              "inline-flex h-7 shrink-0 items-center gap-1.5 px-1.5 text-[13px] font-medium transition",
              references.length > 0 ? "text-white" : "text-white/70 hover:text-white",
              "disabled:cursor-not-allowed disabled:opacity-40",
            )}
          >
            <Paperclip className="h-3.5 w-3.5 text-white/70" />
            Референсы
            {maxReferences > 0 ? (
              <span className="font-mono text-[11px] tabular-nums text-white/45">
                {references.length}/{maxReferences}
              </span>
            ) : null}
          </button>
          {references.map((ref, idx) => {
            const handle = assistantRefHandle(idx);
            const fileName = (ref.name || "").trim() || `картинка ${idx + 1}`;
            return (
              <div
                key={ref.id}
                className="flex min-w-0 items-center gap-1 py-0.5 pl-0.5 pr-1"
              >
                {/* eslint-disable-next-line @next/next/no-img-element */}
                <img
                  src={ref.url}
                  alt=""
                  className="h-6 w-6 shrink-0 rounded object-cover"
                />
                <div className="min-w-0 leading-tight">
                  <button
                    type="button"
                    onClick={() => {
                      if (!expanded) onExpandedChange(true);
                      insertRefHandle(handle);
                    }}
                    title={`Вставить ${handle} в запрос`}
                    className="block font-mono text-[12px] font-medium text-white hover:underline"
                  >
                    {handle}
                  </button>
                  <span className="block max-w-[120px] truncate text-[11px] text-white/45" title={fileName}>
                    {fileName}
                  </span>
                </div>
                <button
                  type="button"
                  onClick={() => onRemoveReference(ref.id)}
                  title="Убрать"
                  className="shrink-0 self-start pt-0.5 text-white/35 transition hover:text-white"
                >
                  <X className="h-3.5 w-3.5" />
                </button>
              </div>
            );
          })}
        </div>
        <span className="ml-auto flex items-center gap-1">
          <button
            type="button"
            onClick={() => {
              const next = !expanded;
              onExpandedChange(next);
            }}
            title={expanded ? "Свернуть" : "Развернуть"}
            className="inline-flex h-7 w-6 items-center justify-center text-white/50 transition hover:text-white"
          >
            {expanded ? <ChevronsDown className="h-3.5 w-3.5" /> : <ChevronsUp className="h-3.5 w-3.5" />}
          </button>
          <button
            type="button"
            onClick={onClose}
            title="Скрыть помощника"
            className="inline-flex h-7 w-6 items-center justify-center text-white/50 transition hover:text-white"
          >
            <X className="h-3.5 w-3.5" />
          </button>
        </span>
      </div>

      {/* категории — поповер вверх, панель не раскрывается */}
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
            className="absolute bottom-full left-0 z-50 mb-2 w-[min(100%,720px)] rounded-xl border border-white/12 bg-[#121216]/98 p-2.5 shadow-[0_16px_40px_rgba(0,0,0,0.7)] backdrop-blur-2xl"
            onMouseEnter={cancelHide}
            onMouseLeave={scheduleHide}
          >
            <div className="grid grid-cols-4 gap-2">
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
                      "flex flex-col overflow-hidden rounded-md border text-left transition",
                      c.id === menuCat?.id
                        ? "border-white/30 bg-white/[0.04]"
                        : "border-white/12 hover:border-white/25",
                    )}
                  >
                    <span className="relative h-[72px] w-full overflow-hidden">
                      <TileBg
                        art={c.art}
                        color={rep?.color ?? "cyan"}
                        srcs={[]}
                      />
                    </span>
                    <span className="px-1.5 py-1.5 text-center text-[12px] font-medium leading-tight text-white/85">
                      {c.name}
                    </span>
                  </button>
                );
              })}
            </div>
            {menuCat && (
              <div
                className="absolute z-10 w-[600px] rounded-xl border border-white/12 bg-[#121216]/98 p-2.5 shadow-[0_16px_40px_rgba(0,0,0,0.7)] backdrop-blur-2xl"
                style={{
                  left: Math.max(4, Math.min((stylesPos?.x ?? 60) - 16, (stylesPos?.w ?? 700) - 604)),
                  top: Math.min((stylesPos?.y ?? 60) + 8, 8 + Math.round(104 * 0.3)),
                  transform: "translateY(-100%)",
                }}
                onMouseEnter={cancelHide}
                onMouseLeave={scheduleHide}
              >
            {addOpen ? (
              <div className="max-h-[360px] space-y-1.5 overflow-y-auto">
                <div className="text-[13px] font-medium text-white/85">Новый стиль · {menuCat.name}</div>
                <div className="flex flex-wrap items-center gap-1">
                  {refPreviews.map((url, i) => (
                    <span key={url} className="relative h-[46px] w-[46px] overflow-hidden rounded-md border border-white/15">
                      {/* eslint-disable-next-line @next/next/no-img-element */}
                      <img src={url} alt="" className="h-full w-full object-cover" />
                      <button
                        type="button"
                        onClick={() => setNewRefs((prev) => prev.filter((_, k) => k !== i))}
                        title="Убрать"
                        className="absolute right-0 top-0 bg-black/70 px-0.5 text-[12px] leading-none text-white/80 hover:text-white"
                      >
                        ×
                      </button>
                    </span>
                  ))}
                  <button
                    type="button"
                    onClick={() => refInput.current?.click()}
                    title="Добавить картинки-референсы"
                    className="flex h-[46px] w-[46px] items-center justify-center rounded-md border border-dashed border-white/20 text-white/45 transition hover:border-white/40 hover:text-white"
                  >
                    <Plus className="h-3.5 w-3.5" />
                  </button>
                  <input
                    ref={refInput}
                    type="file"
                    accept="image/png,image/jpeg,image/webp,image/gif"
                    multiple
                    className="hidden"
                    onChange={(e) => {
                      const picked = Array.from(e.target.files ?? []);
                      if (picked.length) setNewRefs((prev) => [...prev, ...picked]);
                      e.target.value = "";
                    }}
                  />
                </div>
                <textarea
                  value={newHint}
                  onChange={(e) => setNewHint(e.target.value)}
                  rows={2}
                  placeholder="Что взять из картинок…"
                  className={cn(areaCls, "text-[12px]")}
                />
                <button
                  type="button"
                  disabled={buildBusy || !newRefs.length}
                  onClick={() => void buildAgentFromRefs()}
                  className="w-full rounded-md bg-white px-2.5 py-1.5 text-[13px] font-medium text-black transition hover:bg-white/90 disabled:opacity-40"
                >
                  {buildBusy ? "Разбираю картинки…" : "Собрать агента по картинкам"}
                </button>
                <input
                  value={newName}
                  onChange={(e) => setNewName(e.target.value)}
                  placeholder="Название стиля"
                  className={selectCls}
                />
                <textarea
                  value={newAgent}
                  onChange={(e) => setNewAgent(e.target.value)}
                  rows={4}
                  placeholder="Текст агента"
                  className={cn(areaCls, "font-mono text-[12px]")}
                />
                <div className="flex gap-1.5">
                  <button type="button" onClick={() => void saveCustomStyle(menuCat.id)} className="rounded-md bg-white px-2.5 py-1.5 text-[13px] font-medium text-black">
                    Сохранить
                  </button>
                  <button type="button" onClick={() => setAddOpen(false)} className="rounded-md px-2.5 py-1.5 text-[13px] font-medium text-white/60 hover:text-white">
                    Отмена
                  </button>
                </div>
              </div>
            ) : (
            <>
            <div className="mb-2 px-0.5 text-[12px] font-medium text-white/50">
              {menuCat.name}
            </div>
            <div className="grid max-h-[280px] grid-cols-3 gap-2 overflow-y-auto">
                {menuCat.styles.map((s) => (
                  <button
                    key={s.id}
                    type="button"
                    onClick={() => {
                      setCategoryId(menuCat.id);
                      setStyleId(s.id);
                      setEditorTab("request");
                      setCatMenuOpen(false);
                    }}
                    title={s.name}
                    className={cn(
                      "flex flex-col overflow-hidden rounded-md border text-left transition",
                      s.id === styleId && menuCat.id === categoryId
                        ? "border-white/30 bg-white/[0.04]"
                        : "border-white/12 hover:border-white/25",
                    )}
                  >
                    <span className="relative h-[90px] w-full overflow-hidden">
                      <TileBg
                        art={s.art}
                        color={s.color}
                        srcs={styleTileSrcs(
                          { id: s.id, cover: s.cover, artUrl: (s as CustomStyle).artUrl },
                          artUrls,
                        )}
                      />
                    </span>
                    <span className="px-1.5 py-1.5 text-center text-[12px] font-medium leading-tight text-white/85">
                      {s.name}
                    </span>
                  </button>
                ))}
                <button
                  type="button"
                  onClick={() => {
                    setCategoryId(menuCat.id);
                    setAddOpen(true);
                  }}
                  title="Добавить свой стиль по картинкам"
                  className="flex min-h-[124px] items-center justify-center rounded-md border border-dashed border-white/15 text-white/40 transition hover:border-white/30 hover:text-white/70"
                >
                  <Plus className="h-4 w-4" />
                </button>
              </div>
            </>
            )}
              </div>
            )}
          </div>
        </>
      )}

      {expanded ? (
      <div className="flex min-h-0 flex-col px-2 pb-1.5 pt-0">
          {editorTab === "request" && (
            <textarea
              ref={requestAreaRef}
              value={request}
              onChange={(e) => setRequest(e.target.value)}
              onKeyDown={(e) => {
                if ((e.ctrlKey || e.metaKey) && e.key === "Enter") {
                  e.preventDefault();
                  void startGenerationNow();
                }
              }}
              rows={2}
              placeholder="Что должно быть в кадре"
              className={cn(areaCls, "min-w-0")}
            />
          )}
          {editorTab === "agent" && (
            <textarea
              value={agentText}
              onChange={(e) => {
                if (!style) return;
                setAgentOverrides((prev) => ({ ...prev, [style.id]: e.target.value }));
              }}
              rows={2}
              placeholder={style ? "Текст агента стиля" : "Выберите стиль"}
              disabled={!style}
              className={cn(areaCls, "font-mono text-[13px] disabled:opacity-50")}
            />
          )}
          {editorTab === "prompt" && (
            <textarea
              ref={promptAreaRef}
              value={promptText(promptSlot)}
              onChange={(e) =>
                setPromptOverrides((prev) => ({ ...prev, [String(promptSlot)]: e.target.value }))
              }
              rows={2}
              placeholder="Собранный промпт"
              className={cn(areaCls, "max-h-[42vh] overflow-y-auto font-mono text-[13px]")}
            />
          )}

        <div className="mt-1.5 flex shrink-0 flex-wrap items-center gap-1.5">
          <button
            type="button"
            onClick={() => void startGenerationNow()}
            title="Сразу в генерацию, промпт собирается в фоне"
            className="inline-flex h-7 items-center justify-center rounded-md bg-gradient-to-r from-[#22d3ee] to-[#0ea5e9] px-3 text-[13px] font-medium text-black shadow-[0_0_14px_rgba(34,211,238,0.28)] transition hover:brightness-110"
          >
            Сгенерировать
          </button>
          <button
            type="button"
            onClick={() => {
              setEditorTab("prompt");
              applyPrompt(promptSlot);
            }}
            className="inline-flex h-7 items-center rounded-md px-2 text-[13px] font-medium text-white/45 transition hover:text-white"
          >
            Только промпт
          </button>
          <input
            type="number"
            min={1}
            max={4}
            value={count}
            title="Сколько промптов напишет агент"
            onChange={(e) => {
              const n = Math.max(1, Math.min(4, Math.round(Number(e.target.value) || 1)));
              setCount(n);
            }}
            className="h-7 w-10 rounded-md border border-white/12 bg-[#16161b] px-1 text-center text-[13px] text-white/85 focus:border-white/25 focus:outline-none"
          />
        {agentError ? (
          <div
            role="alert"
            className="rounded-md border border-red-500/40 bg-red-500/15 px-2 py-1 text-[11px] leading-tight text-red-200"
          >
            {agentError}
          </div>
        ) : null}

          <div className="relative flex items-center gap-1">
            <span className="shrink-0 px-1 text-[12px] font-medium text-white/40">
              LLM
            </span>
            <button
              type="button"
              disabled={llmBusy || !llmModels.length}
              onClick={() => setMenu((v) => (v === "llm" ? null : "llm"))}
              title="Текстовая модель — без формата и разрешения"
              className={chipCls(menu === "llm")}
            >
              <span className="max-w-[140px] truncate">{activeLlm?.label ?? "загрузка…"}</span>
              <ChevronDown className="h-3 w-3 opacity-60" />
            </button>
            {menu === "llm" && (
              <div className="absolute bottom-full left-0 z-[80] mb-1.5 min-w-[220px] overflow-hidden rounded-xl border border-white/15 bg-[#121216]/98 p-1 shadow-[0_12px_32px_rgba(0,0,0,0.85)]">
                {llmModels.map((m) => (
                  <button
                    key={m.id}
                    type="button"
                    onClick={() => {
                      void onLlmChange(m.id);
                      setMenu(null);
                    }}
                    className={cn(
                      "flex w-full items-center justify-between rounded-lg px-2.5 py-1.5 text-left text-[11px] transition",
                      m.id === activeLlm?.id
                        ? "bg-white/10 text-white"
                        : "text-white/75 hover:bg-white/[0.06] hover:text-white",
                    )}
                  >
                    <span className="truncate">{m.label}</span>
                    {!m.key_configured ? (
                      <span className="ml-2 font-mono text-[9px] text-white/35">нет ключа</span>
                    ) : null}
                  </button>
                ))}
              </div>
            )}
          </div>

          <div className="flex w-fit min-w-0 flex-wrap items-center gap-1">
            {modelIcon ? (
              // eslint-disable-next-line @next/next/no-img-element
              <img
                src={modelIcon}
                alt=""
                width={18}
                height={18}
                className="h-[18px] w-[18px] shrink-0 rounded-md object-cover ring-1 ring-white/10"
              />
            ) : null}
            <span className="shrink-0 px-1 text-[12px] font-medium text-white/50" title="Модель картинки">
              Изображение
            </span>
            <button
              type="button"
              onClick={() => {
                setMenu(null);
                onOpenModelPicker();
              }}
              className={chipCls(false)}
              title="Модель картинки"
            >
              <span className="max-w-[150px] truncate">{modelName}</span>
              <ChevronDown className="h-3 w-3 opacity-60" />
            </button>
            <div className="relative">
              <button
                type="button"
                onClick={() => setMenu((v) => (v === "aspect" ? null : "aspect"))}
                className={chipCls(menu === "aspect")}
                title="Формат (только GPT)"
              >
                <span className="font-mono tabular-nums">{aspect}</span>
                <ChevronDown className="h-3 w-3 opacity-60" />
              </button>
              {menu === "aspect" && (
                <div className="absolute bottom-full left-0 z-[80] mb-1.5 min-w-[88px] rounded-xl border border-white/15 bg-[#121216]/98 p-1 shadow-[0_12px_32px_rgba(0,0,0,0.85)]">
                  {aspectOptions.map((a) => (
                    <button
                      key={a}
                      type="button"
                      onClick={() => {
                        onAspectChange(a);
                        setMenu(null);
                      }}
                      className={cn(
                        "flex w-full rounded-lg px-2.5 py-1.5 text-left font-mono text-[11px] transition",
                        a === aspect ? "bg-[#22d3ee]/15 text-[#22d3ee]" : "text-white/80 hover:bg-white/[0.06]",
                      )}
                    >
                      {a}
                    </button>
                  ))}
                </div>
              )}
            </div>
            <div className="relative">
              <button
                type="button"
                onClick={() => setMenu((v) => (v === "resolution" ? null : "resolution"))}
                className={chipCls(menu === "resolution")}
                title="Разрешение (только GPT)"
              >
                <span className="font-mono tabular-nums">{resolution}</span>
                <ChevronDown className="h-3 w-3 opacity-60" />
              </button>
              {menu === "resolution" && (
                <div className="absolute bottom-full left-0 z-[80] mb-1.5 min-w-[72px] rounded-xl border border-white/15 bg-[#121216]/98 p-1 shadow-[0_12px_32px_rgba(0,0,0,0.85)]">
                  {resolutionOptions.map((r) => (
                    <button
                      key={r}
                      type="button"
                      onClick={() => {
                        onResolutionChange(r);
                        setMenu(null);
                      }}
                      className={cn(
                        "flex w-full rounded-lg px-2.5 py-1.5 text-left font-mono text-[11px] transition",
                        r === resolution ? "bg-[#22d3ee]/15 text-[#22d3ee]" : "text-white/80 hover:bg-white/[0.06]",
                      )}
                    >
                      {r}
                    </button>
                  ))}
                </div>
              )}
            </div>
            {detailOptions.length > 0 && (
              <div className="relative">
                <button
                  type="button"
                  onClick={() => setMenu((v) => (v === "detail" ? null : "detail"))}
                  className={chipCls(menu === "detail")}
                  title="Детализация (только GPT)"
                >
                  {detailLabel(detail)}
                  <ChevronDown className="h-3 w-3 opacity-60" />
                </button>
                {menu === "detail" && (
                  <div className="absolute bottom-full left-0 z-[80] mb-1.5 min-w-[110px] rounded-xl border border-white/15 bg-[#121216]/98 p-1 shadow-[0_12px_32px_rgba(0,0,0,0.85)]">
                    {detailOptions.map((d) => (
                      <button
                        key={d}
                        type="button"
                        onClick={() => {
                          onDetailChange(d);
                          setMenu(null);
                        }}
                        className={cn(
                          "flex w-full rounded-lg px-2.5 py-1.5 text-left text-[11px] transition",
                          d === detail ? "bg-[#22d3ee]/15 text-[#22d3ee]" : "text-white/80 hover:bg-white/[0.06]",
                        )}
                      >
                        {detailLabel(d)}
                      </button>
                    ))}
                  </div>
                )}
              </div>
            )}
          </div>
        </div>
      </div>
      ) : null}
    </div>
  );
}
