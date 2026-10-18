"use client";

/**
 * Каталог промтов ноды: боковая панель у ноды на канвасе (постерные обложки).
 * Ранее: каталог в стиле «Каталог стилей» (Генерация → Помощник):
 * тёмная сетка карточек с превью, бейдж сверху-слева, заголовок + краткое
 * описание снизу, фильтр-чипы со счётчиками, поиск, бирюзовая рамка «Выбран»,
 * нижняя панель выбранного. Клик по карточке — окно с крупным изображением,
 * описанием и полным текстом промта (копирование).
 */

import type React from "react";
import { useEffect, useLayoutEffect, useMemo, useState } from "react";
import { createPortal } from "react-dom";
import { ChevronDown, Copy, Download, ExternalLink, ImageOff, Search, Upload, X } from "lucide-react";
import { toast } from "sonner";
import { StarRating } from "@/components/ui/star-rating";
import type { NodeResultItem as BaseItem } from "@/lib/node-result-resolver";

export type NodeResultItem = BaseItem & { category?: string; description?: string };
import { cn } from "@/lib/utils";
import { PROMPT_BG_GEN } from "@/lib/prompt-bg-gen";
import { nodeTypeFromKey, stepCodeForNodeKey } from "@/lib/node-key";



/** Короткое описание: первое предложение промта, до ~140 символов. */
export function promptShortDesc(text: string | null | undefined): string {
  const t = (text ?? "").replace(/\s+/g, " ").trim();
  if (!t) return "Пустой промт";
  const m = t.match(/^(.{20,160}?[.!?…])(\s|$)/);
  const first = m ? m[1] : t;
  return first.length > 140 ? `${first.slice(0, 137).trimEnd()}…` : first;
}

function catSeed(c: string) {
  let h = 0;
  for (const ch of c) h = (h * 31 + ch.charCodeAt(0)) % 997;
  return h + 1;
}

async function copyText(text: string) {
  try {
    await navigator.clipboard.writeText(text);
    toast.success("Промт скопирован");
  } catch {
    toast.error("Не удалось скопировать");
  }
}


export function PromptDetailModal({
  item,
  onClose,
  onPrev,
  onNext,
}: {
  item: NodeResultItem;
  onClose: () => void;
  onPrev?: () => void;
  onNext?: () => void;
}) {
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        e.stopPropagation();
        onClose();
      } else if (e.key === "ArrowLeft" && onPrev) onPrev();
      else if (e.key === "ArrowRight" && onNext) onNext();
    };
    window.addEventListener("keydown", onKey, true);
    return () => window.removeEventListener("keydown", onKey, true);
  }, [onClose, onPrev, onNext]);

  const content = item.content ?? "";
  const body = (
    <div
      data-prompt-detail-open=""
      className="fixed inset-0 z-[10000] flex items-center justify-center p-4"
      onPointerDown={(e) => e.stopPropagation()}
    >
      <div
        className="absolute inset-0 bg-black/75 backdrop-blur-[3px] animate-in fade-in duration-200"
        onClick={onClose}
      />
      <div className="relative z-10 flex max-h-[94vh] w-[min(96vw,1100px)] flex-col overflow-hidden rounded-none border border-white/15 bg-[#101015] shadow-[0_0_80px_rgba(0,0,0,0.9)] animate-in zoom-in-95 duration-200">
        <div className="flex h-12 shrink-0 items-center justify-between border-b border-white/10 px-4">
          <div className="flex items-center gap-2">
            <span className="rounded bg-[#22d3ee]/15 px-1.5 py-0.5 text-[13px] font-semibold text-[#22d3ee]">
              {item.category ?? (item.frameNumber != null ? `Кадр ${item.frameNumber}` : "Промт")}
            </span>
            <span className="text-[17px] font-semibold text-white">{item.label}</span>
          </div>
          <div className="flex items-center gap-1">
            {onPrev && (
              <button type="button" onClick={onPrev} className="rounded-lg px-2 py-1 text-[14px] text-white/60 hover:bg-white/10 hover:text-white">
                ← Пред.
              </button>
            )}
            {onNext && (
              <button type="button" onClick={onNext} className="rounded-lg px-2 py-1 text-[14px] text-white/60 hover:bg-white/10 hover:text-white">
                След. →
              </button>
            )}
            <button
              type="button"
              onClick={onClose}
              title="Закрыть (Esc)"
              className="inline-flex h-8 w-8 items-center justify-center rounded-lg text-white/50 hover:bg-white/10 hover:text-white"
            >
              <X className="h-4 w-4" />
            </button>
          </div>
        </div>
        <div className="min-h-0 flex-1 overflow-y-auto">
          {/* 1. Крупное изображение */}
          <div className="relative flex items-center justify-center bg-black">
            {item.previewUrl ? (
              <>
                {/* eslint-disable-next-line @next/next/no-img-element */}
                <img src={item.previewUrl} alt={item.label} className="max-h-[62vh] w-full object-contain" />
                <a
                  href={item.previewUrl}
                  target="_blank"
                  rel="noreferrer"
                  className="absolute right-3 top-3 inline-flex items-center gap-1 rounded-md bg-black/70 px-2 py-1 text-[13px] text-white/80 backdrop-blur hover:text-white"
                >
                  <ExternalLink className="h-3 w-3" /> Оригинал
                </a>
              </>
            ) : (
              <div className="flex h-[200px] w-full flex-col items-center justify-center gap-2 text-white/35">
                <ImageOff className="h-8 w-8" />
                <span className="text-[14px]">Изображение для этого промта ещё не сгенерировано</span>
              </div>
            )}
          </div>
          {/* 2. Описание */}
          <div className="space-y-1 border-b border-white/10 px-5 py-4">
            <div className="text-[13px] font-semibold uppercase tracking-wider text-white/45">Описание</div>
            <div className="text-[15px] leading-relaxed text-white/85">{item.description || promptShortDesc(content)}</div>
          </div>
          {/* 3. Полный промт */}
          <div className="space-y-2 px-5 py-4">
            <div className="flex items-center justify-between">
              <div className="text-[13px] font-semibold uppercase tracking-wider text-white/45">
                Промт · {content.length} симв.
              </div>
              <button
                type="button"
                onClick={() => void copyText(content)}
                className="inline-flex items-center gap-1.5 rounded-lg bg-[#22d3ee] px-3 py-1.5 text-[13px] font-semibold text-black hover:brightness-110"
              >
                <Copy className="h-3.5 w-3.5" /> Копировать
              </button>
            </div>
            <pre className="whitespace-pre-wrap break-words rounded-lg border border-white/10 bg-[#16161d] p-3 font-mono text-[14px] leading-relaxed text-white/85 select-text">
              {content || "—"}
            </pre>
          </div>
        </div>
      </div>
    </div>
  );
  return typeof document !== "undefined" ? createPortal(body, document.body) : body;
}


const COND = 'Impact, "Arial Narrow", "Roboto Condensed", sans-serif';

function hashSeed(s: string) {
  let h = 7;
  for (const ch of s) h = (h * 31 + ch.charCodeAt(0)) % 100003;
  return h;
}

/** Ч/б постерная обложка 16:9 (без картинок — CSS-графика). */
const BG_RULES: [RegExp, number][] = [
  [/action|экшн|действ|площадк/i, 1],
  [/camera|камер|shot|шот|кадр/i, 2],
  [/style|стил|mood|light/i, 3],
  [/world|мир|scene|сцен(?!ар)|location|локац/i, 4],
  [/scenario|сценар|plan|план|script|скелет|skeleton|каркас/i, 5],
  [/character|персон|persona|hero|герой|registry/i, 6],
  [/qc|check|провер/i, 7],
  [/audio|voice|озвуч|звук|music/i, 8],
  [/montage|монтаж|assemble|сборк|edit/i, 9],
  [/story|истор|сюжет|narrat/i, 10],
];

function genBgFor(id: string, nodeKey?: string | null): string | null {
  // Тема (topic) has no step code / prompts: always topic/default
  if (nodeKey && nodeTypeFromKey(nodeKey) === "topic" && PROMPT_BG_GEN["topic/default"]) return `/prompt-bg/gen/${PROMPT_BG_GEN["topic/default"]}.webp?v=2`;
  if (!id) return null;
  const step = nodeKey ? stepCodeForNodeKey(nodeKey) : undefined;
  const steps = step ? [step, ...(step.startsWith("enrich_") ? ["excel_gpt"] : []), ...(step === "hero" ? ["hero_style"] : [])] : [];
  for (const s of steps) {
    const f = PROMPT_BG_GEN[`${s}/${id}`];
    if (f) return `/prompt-bg/gen/${f}.webp?v=2`;
  }
  if (!step || !steps.some((s) => Object.keys(PROMPT_BG_GEN).some((k) => k.startsWith(s + "/")))) {
    const hit = Object.keys(PROMPT_BG_GEN).find((k) => k.slice(k.indexOf("/") + 1) === id);
    if (hit && id !== "default" && id !== "norm") return `/prompt-bg/gen/${PROMPT_BG_GEN[hit]}.webp?v=2`;
  }
  return null;
}

/** Per-node backgrounds: /prompt-bg/node/<step>.webp (node image first, prompt image as fallback layer). */
const NODE_BG_TYPES = new Set(["topic", "plan", "script", "hero", "items", "excel_gpt", "image_prompts", "images", "animation_prompts", "videos", "audio", "music", "sfx_plan", "sfx_gen", "assemble", "publish"]);
export function nodeBgFor(nodeKey: string | null | undefined, fallback: string | null): string | null {
  if (!nodeKey) return fallback;
  const t = nodeTypeFromKey(nodeKey);
  const typ = t.startsWith("enrich_") ? "excel_gpt" : t;
  if (!typ || !NODE_BG_TYPES.has(typ)) return fallback;
  const url = `/prompt-bg/node/${typ}.webp?v=2`;
  return fallback ? `${url}), url(${fallback}` : url;
}

export function promptBgFor(id: string, label: string, nodeKey?: string | null): string {
  const gen = genBgFor(id, nodeKey);
  const name = `${label} ${id}`;
  const hit = BG_RULES.find(([re]) => re.test(name));
  const n = hit ? hit[1] : (hashSeed(id) % 10) + 1;
  const theme = `/prompt-bg/${String(n).padStart(2, "0")}.webp`;
  // layered: generated image on top, theme image shows if gen file is not there yet
  return gen ? `${gen}), url(${theme}` : theme;
}

function PosterCover({ id, label, nodeKey }: { id: string; label: string; nodeKey?: string }) {
  return (
    <div
      className="absolute inset-0 overflow-hidden bg-[#0c0c0e] bg-cover bg-center"
      style={{ backgroundImage: `url(${promptBgFor(id, label, nodeKey)})` }}
    />
  );
}

export type CatalogPanelItem = NodeResultItem & { sizeLabel?: string };

/**
 * Боковая панель «Каталог промтов ноды» — крепится к ноде на канвасе линией.
 * Клик — выбрать и активировать, двойной клик — просмотр промта.
 */
export function NodeCatalogPanel({
  nodeKey,
  items,
  activeId,
  onActivate,
  onOpenSettings,
  onClose,
  downloadUrl,
  onUpload,
  embedded = false,
}: {
  embedded?: boolean;
  nodeKey: string;
  items: CatalogPanelItem[];
  activeId?: string | null;
  onActivate: (id: string) => void;
  onOpenSettings: () => void;
  onClose: () => void;
  downloadUrl?: (id: string) => string;
  onUpload?: (id: string, file: File) => Promise<void>;
}) {
  const [detail, setDetail] = useState<string | null>(null);
  const [uploading, setUploading] = useState(false);
  const [q, setQ] = useState("");
  const [cat, setCat] = useState<string>("Все");
  const [catOpen, setCatOpen] = useState(false);
  const [preview, setPreview] = useState<number | null>(null);
  const [anchor, setAnchor] = useState<{ l: number; x: number; y: number; h: number } | null>(null);

  useLayoutEffect(() => {
    const find = () => {
      const el = document.querySelector(
        `.react-flow__node[data-id="${CSS.escape(nodeKey)}"]`,
      ) as HTMLElement | null;
      if (!el) return setAnchor(null);
      const r = el.getBoundingClientRect();
      setAnchor({ l: r.left, x: r.right, y: r.top, h: r.height });
    };
    find();
    const t = window.setInterval(find, 400);
    window.addEventListener("resize", find);
    return () => {
      window.clearInterval(t);
      window.removeEventListener("resize", find);
    };
  }, [nodeKey]);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key !== "Escape" || document.querySelector("[data-prompt-detail-open]")) return;
      if (detail) setDetail(null);
      else if (!embedded) onClose();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose, detail]);

  const cats = useMemo(() => {
    const m = new Map<string, number>();
    for (const it of items) m.set(it.category || "Прочее", (m.get(it.category || "Прочее") ?? 0) + 1);
    return [["Все", items.length] as [string, number], ...Array.from(m.entries())];
  }, [items]);

  const shown = useMemo(() => {
    const t = q.trim().toLowerCase();
    return items.filter(
      (it) =>
        (cat === "Все" || (it.category || "Прочее") === cat) &&
        (!t || `${it.label} ${it.description ?? ""} ${it.content ?? ""}`.toLowerCase().includes(t)),
    );
  }, [items, q, cat]);

  if (typeof document === "undefined") return null;

  const W = 560;
  const vw = window.innerWidth;
  const vh = window.innerHeight;
  const gap = 36;
  const D = 420;
  const dGap = 12;
  let side: "right" | "left" = "right";
  let left: number;
  if (!anchor) left = Math.max(16, vw - W - 24);
  else if (anchor.x + gap + W <= vw - 8) left = anchor.x + gap;
  else if (anchor.l - gap - W >= 8) {
    left = anchor.l - gap - W;
    side = "left";
  } else left = Math.max(8, vw - W - 8);
  const top = Math.min(Math.max(56, anchor ? anchor.y - 8 : 80), Math.max(56, vh - 520));
  const lineY = anchor ? anchor.y + Math.min(28, anchor.h / 2) : 0;
  // свободные интервалы по X, не занятые каталогом и нодой
  const busy: [number, number][] = [[left, left + W]];
  if (anchor) busy.push([anchor.l, anchor.x]);
  const cands: number[] = [left + W + dGap, left - dGap - D];
  if (anchor) cands.push(anchor.x + dGap, anchor.l - dGap - D);
  const free = (x: number, w: number) =>
    x >= 8 && x + w <= vw - 8 && busy.every(([a, b]) => x + w <= a || x >= b);
  let dLeft = cands.find((x) => free(x, D)) ?? -1;
  let dWidth = D;
  if (dLeft < 0) {
    // ужимаем до самого широкого свободного промежутка
    const edges = [8, ...busy.flat(), vw - 8].sort((x, y) => x - y);
    let best: [number, number] = [8, 8];
    for (let i = 0; i < edges.length - 1; i++) {
      const x0 = edges[i] + dGap;
      const x1 = edges[i + 1] - dGap;
      if (x1 - x0 > best[1] - best[0] && free(x0, Math.max(1, x1 - x0))) best = [x0, x1];
    }
    dLeft = best[0];
    dWidth = Math.max(280, best[1] - best[0]);
  }
  const detailItem = detail ? items.find((it) => it.id === detail) ?? null : null;
  useEffect(() => {
    if (!embedded) return;
    window.dispatchEvent(
      new CustomEvent("vp-catalog-detail", {
        detail: { nodeKey, item: detailItem ? { id: detailItem.id, label: detailItem.label } : null },
      }),
    );
  }, [embedded, nodeKey, detailItem?.id, detailItem?.label]);
  useEffect(() => {
    if (!embedded) return;
    return () => {
      window.dispatchEvent(new CustomEvent("vp-catalog-detail", { detail: { nodeKey, item: null } }));
    };
  }, [embedded, nodeKey]);

  const catalogBody = (
    <>
        <div className="relative grid shrink-0 grid-cols-[1fr_auto]" style={{ gap: 1 }}>
          <label className="flex h-9 items-center gap-2 bg-[#141417] px-3">
            <Search className="h-3.5 w-3.5 text-zinc-500" />
            <input
              value={q}
              onChange={(e) => setQ(e.target.value)}
              placeholder="Поиск по промтам, описанию, тексту…"
              className="h-full flex-1 bg-transparent text-[14px] text-zinc-200 outline-none placeholder:text-zinc-500"
            />
          </label>
          <button
            type="button"
            onClick={() => setCatOpen((o) => !o)}
            className="flex h-9 items-center gap-2 bg-[#141417] px-3 text-[13px] tracking-[0.08em] text-zinc-300 uppercase hover:bg-[#1b1b1f]"
          >
            {cat === "Все" ? "Категории" : cat}
            <ChevronDown className="h-3.5 w-3.5" />
          </button>
          {catOpen ? (
            <div className="absolute top-full right-0 z-10 flex min-w-[180px] flex-col bg-[#2e2e34]" style={{ gap: 1, padding: 1 }}>
              {cats.map(([c, n]) => (
                <button
                  key={c}
                  type="button"
                  onClick={() => {
                    setCat(c);
                    setCatOpen(false);
                  }}
                  className={cn(
                    "flex items-center justify-between px-3 py-2 text-left text-[14px]",
                    c === cat ? "bg-white text-black" : "bg-[#141417] text-zinc-300 hover:bg-[#1b1b1f]",
                  )}
                >
                  <span>{c}</span>
                  <span className="text-[13px] opacity-60">{n}</span>
                </button>
              ))}
            </div>
          ) : null}
        </div>
        <div className="min-h-0 flex-auto overflow-y-auto">
          {shown.length === 0 ? (
            <div className="bg-[#141417] px-4 py-10 text-center text-[14px] text-zinc-500">Ничего не найдено</div>
          ) : (
            <div className="grid grid-cols-3" style={{ gap: 1 }}>
              {shown.map((it) => {
                const active = it.id === activeId;
                return (
                  <button
                    key={it.id}
                    type="button"
                    title={`${it.label} — открыть промт`}
                    onClick={() => setDetail(it.id)}
                    className="group relative aspect-video overflow-hidden bg-[#0c0c0e] text-left"
                  >
                    <PosterCover id={it.id} label={it.label} nodeKey={nodeKey} />
                    {active || it.id === detail ? (
                      <span className={cn("pointer-events-none absolute inset-0 z-[2] outline -outline-offset-2", active ? "outline-2 outline-white" : "outline-1 outline-white/50")} />
                    ) : (
                      <span className="pointer-events-none absolute inset-0 z-[2] bg-black/0 transition-colors group-hover:bg-white/[0.06]" />
                    )}
                    <div className="pointer-events-none absolute inset-0 z-[1] flex flex-col items-start justify-start p-3 pr-4 text-left">
                    <div className="w-full break-words text-[15px] font-medium leading-[1.2] tracking-[0.01em] text-white [text-shadow:0_1px_6px_rgba(0,0,0,0.6)]">
                      <span className="vp-hl">{it.label.replace(/\.(md|txt)$/i, "")}</span>
                    </div>
                    {(it.description && it.description !== it.label ? it.description : it.category) ? (
                      <div className="mt-1 line-clamp-2 w-full text-[11px] font-normal leading-[1.35] text-white/75 [text-shadow:0_1px_4px_rgba(0,0,0,0.6)]">
                        <span className="vp-hl">{it.description && it.description !== it.label ? it.description : it.category}</span>
                      </div>
                    ) : null}
                  </div>
                  <StarRating kind="prompt" id={`${nodeKey ? nodeTypeFromKey(nodeKey) : "prompt"}/${it.id}`} size={11} className="absolute bottom-1.5 left-2 z-[3]" />
                  {it.sizeLabel ? <span className="pointer-events-none vp-hl absolute bottom-1.5 right-2 z-[1] px-1.5 py-0.5 text-[11px] tracking-[0.04em] text-zinc-300 tabular-nums">{it.sizeLabel}</span> : null}
</button>
                );
              })}
            </div>
          )}
        </div>
    </>
  );
  const actionsGrid = detailItem ? (
          <div data-vp-detail-actions className={cn("grid grid-cols-3", embedded ? "nodrag flex-1 border border-white/10 [&>*]:!h-12" : "shrink-0")} style={{ gap: 1 }}>
            <button
              type="button"
              disabled={detailItem.id === activeId}
              onClick={() => onActivate(detailItem.id)}
              className={cn(
                "flex h-9 items-center justify-center text-[13px] tracking-[0.08em] uppercase",
                detailItem.id === activeId ? "bg-white font-medium text-black" : "bg-[#141417] text-zinc-200 hover:bg-[#1b1b1f] hover:text-white",
              )}
            >
              {detailItem.id === activeId ? "Активен" : "Сделать активным"}
            </button>
            <a
              href={downloadUrl ? downloadUrl(detailItem.id) : undefined}
              download
              className="flex h-9 items-center justify-center gap-1.5 bg-[#141417] text-[13px] tracking-[0.08em] text-zinc-300 uppercase hover:bg-[#1b1b1f] hover:text-white"
            >
              <Download className="h-3.5 w-3.5" /> Скачать
            </a>
            <label
              className={cn(
                "flex h-9 cursor-pointer items-center justify-center gap-1.5 bg-[#141417] text-[13px] tracking-[0.08em] text-zinc-300 uppercase hover:bg-[#1b1b1f] hover:text-white",
                uploading && "pointer-events-none opacity-50",
              )}
              title="Заменить текст этого промта содержимым файла"
            >
              <Upload className="h-3.5 w-3.5" /> {uploading ? "Загрузка…" : "Загрузить"}
              <input
                type="file"
                accept=".md,.txt,text/plain,text/markdown"
                className="hidden"
                onChange={async (e) => {
                  const f = e.target.files?.[0];
                  e.target.value = "";
                  if (!f || !onUpload) return;
                  setUploading(true);
                  try {
                    await onUpload(detailItem.id, f);
                  } finally {
                    setUploading(false);
                  }
                }}
              />
            </label>
          </div>
  ) : null;
  const [actionsTarget, setActionsTarget] = useState<HTMLElement | null>(null);
  useLayoutEffect(() => {
    if (!embedded || !detailItem) { setActionsTarget(null); return; }
    let raf = 0;
    const find = () => {
      const el = document.getElementById(`vp-detail-actions-${nodeKey}`);
      if (el) setActionsTarget(el); else raf = requestAnimationFrame(find);
    };
    find();
    return () => cancelAnimationFrame(raf);
  }, [embedded, nodeKey, detailItem?.id]);
  useEffect(() => {
    if (!embedded) return;
    const on = (ev: Event) => { if ((ev as CustomEvent<{ nodeKey: string }>).detail?.nodeKey === nodeKey) setDetail(null); };
    window.addEventListener("vp-catalog-back", on);
    return () => window.removeEventListener("vp-catalog-back", on);
  }, [embedded, nodeKey]);
  const detailBody = detailItem ? (
    <>
          {embedded ? null : (
          <div
            className="relative h-[60px] shrink-0 overflow-hidden bg-[#141417] bg-cover"
            style={{ backgroundImage: `url(${promptBgFor(detailItem.id, detailItem.label, nodeKey)})`, backgroundPosition: "center top" }}
          >
            <div className="pointer-events-none absolute inset-0 bg-gradient-to-t from-black/60 via-black/10 to-black/25" />
            <div className="absolute inset-0 flex items-center px-4 pr-14 text-[16px] font-medium leading-[1.2] text-white [text-shadow:0_1px_6px_rgba(0,0,0,0.6)]" style={{ fontFamily: "Rubik, sans-serif" }}>
              <span className="break-words">{detailItem.label.replace(/\.(md|txt)$/i, "")}</span>
            </div>
            <button
              type="button"
              onClick={() => setDetail(null)}
              aria-label="Закрыть"
              className="absolute right-2 top-1/2 z-[1] flex h-8 w-8 -translate-y-1/2 items-center justify-center rounded-md bg-black/45 text-white/85 backdrop-blur-sm hover:bg-black/70 hover:text-white"
            >
              <X className="h-4 w-4" />
            </button>
          </div>
          )}
          {embedded ? (actionsTarget ? createPortal(actionsGrid, actionsTarget) : null) : actionsGrid}
          <div className="flex shrink-0 items-center justify-between bg-[#141417] px-4 py-1.5 text-[13px] tracking-[0.08em] text-zinc-500 uppercase">
            <span>Промт агента</span>
            <span>{detailItem.sizeLabel ?? ""}</span>
          </div>
          <pre className="min-h-0 flex-1 overflow-auto bg-[#111114] px-4 py-3 font-mono text-[13.5px] leading-relaxed whitespace-pre-wrap text-zinc-300">
            {detailItem.content ?? ""}
          </pre>
    </>
  ) : null;
  if (embedded) {
    return (
      <div className={cn("relative flex min-h-0 flex-col overflow-hidden rounded-[10px] bg-[#26262b] text-zinc-200", detailBody ? "h-[560px] shrink-0" : "flex-auto")} style={{ gap: 1, border: "1px solid #2e2e34" }}>
        {detailBody ? (
          <div className="vp-detail flex min-h-0 flex-1 flex-col bg-[#1a1a1f]" style={{ gap: 1 }}>
            {detailBody}
          </div>
        ) : (
          catalogBody
        )}
      </div>
    );
  }

  return createPortal(
    <>
      {anchor && (left > anchor.x || left + W < anchor.l) ? (
        <svg className="pointer-events-none fixed inset-0 z-[96]" width={vw} height={vh}>
          <path
            d={
              side === "right"
                ? `M${anchor.x} ${lineY} H${anchor.x + (left - anchor.x) / 2} V${top + 20} H${left}`
                : `M${anchor.l} ${lineY} H${left + W + (anchor.l - left - W) / 2} V${top + 20} H${left + W}`
            }
            fill="none"
            stroke="rgba(255,255,255,0.55)"
            strokeWidth={1}
          />
          <rect x={(side === "right" ? anchor.x : anchor.l) - 3} y={lineY - 3} width={6} height={6} fill="#fff" />
        </svg>
      ) : null}
      <div
        role="dialog"
        aria-label="Каталог промтов ноды"
        onPointerDown={(e) => e.stopPropagation()}
        className="fixed z-[97] flex max-h-[min(640px,calc(100vh-72px))] flex-col bg-[#26262b] text-zinc-200 shadow-[0_24px_60px_rgba(0,0,0,0.6)]"
        style={{ left, top, width: W, gap: 1, border: "1px solid #2e2e34" }}
      >
        <div className="grid shrink-0 grid-cols-[1fr_1fr_44px]" style={{ gap: 1 }}>
          <div
            className="flex h-11 items-center bg-white px-4 text-[17px] tracking-[0.04em] text-black uppercase"
            style={{ fontFamily: COND }}
          >
            Каталог промтов ноды
          </div>
          <button
            type="button"
            onClick={onOpenSettings}
            className="flex h-11 items-center bg-[#141417] px-4 text-[17px] tracking-[0.04em] text-zinc-400 uppercase transition-colors hover:bg-[#1b1b1f] hover:text-white"
            style={{ fontFamily: COND }}
          >
            Настройки ноды
          </button>
          <button
            type="button"
            onClick={onClose}
            aria-label="Закрыть"
            className="flex h-11 items-center justify-center bg-[#141417] text-zinc-400 hover:bg-[#1b1b1f] hover:text-white"
          >
            <X className="h-4 w-4" />
          </button>
        </div>
        {catalogBody}
      </div>
      {detailItem ? (
        <div
          role="dialog"
          aria-label="Промт ноды"
          onPointerDown={(e) => e.stopPropagation()}
          className="fixed z-[97] flex max-h-[min(640px,calc(100vh-72px))] flex-col bg-[#26262b] text-zinc-200 shadow-[0_24px_60px_rgba(0,0,0,0.6)]"
          style={{ left: dLeft, top, width: dWidth, gap: 1, border: "1px solid #2e2e34" }}
        >
          {detailBody}
        </div>
      ) : null}
      {preview != null && items[preview] ? (
        <PromptDetailModal
          item={items[preview]}
          onClose={() => setPreview(null)}
          onPrev={preview > 0 ? () => setPreview(preview - 1) : undefined}
          onNext={preview < items.length - 1 ? () => setPreview(preview + 1) : undefined}
        />
      ) : null}
    </>,
    document.body,
  );
}
