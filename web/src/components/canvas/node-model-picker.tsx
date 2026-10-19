"use client";

import { useEffect, useState } from "react";
import { createPortal } from "react-dom";
import { ModelBrandBackdrop } from "./node-model-logos";
import { getNodeCustomBg } from "@/lib/node-custom-bg";
import { nodeBgFor, promptBgFor } from "@/components/prompts/prompt-card-catalog";
import { useQuery } from "@tanstack/react-query";
import {
  Check,
  ChevronDown,
  Copy,
  Image as ImageIcon,
  Moon,
  Sparkles,
  X as XIcon,
} from "lucide-react";
import { toast } from "sonner";
import { cn } from "@/lib/utils";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import {
  DEFAULT_TEXT_MODEL_ID,
  catalogForNodeType,
  defaultModelIdForNodeType,
  findCatalogModel,
  formatUsdPrice,
  localCatalog,
  MEDIA_NODE_TYPES,
  type CatalogModel,
  type ModelCatalogPayload,
} from "@/lib/node-model-catalog";
import {
  clampMediaOption,
  mediaOptionsForModel,
  qualityLabel,
  type MediaQualityId,
} from "@/lib/node-media-options";

type CatalogResponse = {
  catalog?: ModelCatalogPayload;
};

export type NodeMediaPatch = {
  modelId: string;
  modelChannel: "stable";
  imageResolution?: string;
  imageQuality?: MediaQualityId;
  aspectRatio?: string;
};

function HexIcon({ className }: { className?: string }) {
  return (
    <svg viewBox="0 0 16 16" className={className} aria-hidden>
      <path
        d="M8 1.2 14.2 4.8v6.4L8 14.8 1.8 11.2V4.8L8 1.2Z"
        fill="none"
        stroke="currentColor"
        strokeWidth="1.4"
      />
    </svg>
  );
}

function VendorGlyph({ icon, className }: { icon: string; className?: string }) {
  if (icon === "spark") return <Sparkles className={className} />;
  if (icon === "moon") return <Moon className={className} />;
  if (icon === "image") return <ImageIcon className={className} />;
  if (icon === "hex") return <HexIcon className={className} />;
  return <span className={cn("font-semibold leading-none", className)}>{icon}</span>;
}

function patchNodeModel(nodeKey: string, patch: NodeMediaPatch, isText?: boolean) {
  window.dispatchEvent(
    new CustomEvent("canvas-patch-node-data", {
      detail: { nodeKey, patch, globalText: isText },
    }),
  );
  window.dispatchEvent(new CustomEvent("canvas-save-workflow"));
}

function buildMediaPatch(
  modelId: string,
  opts: {
    imageResolution?: string | null;
    imageQuality?: string | null;
    aspectRatio?: string | null;
  },
): NodeMediaPatch {
  const media = mediaOptionsForModel(modelId);
  const patch: NodeMediaPatch = { modelId, modelChannel: "stable" };
  if (!media) return patch;
  if (media.resolutions.length) {
    patch.imageResolution = clampMediaOption(
      opts.imageResolution,
      media.resolutions,
      media.defaultResolution,
    );
  }
  if (media.qualities.length) {
    const q = clampMediaOption(
      opts.imageQuality,
      media.qualities.map((x) => x.id),
      media.defaultQuality,
    ) as MediaQualityId;
    patch.imageQuality = q;
  }
  if (media.aspects.length) {
    patch.aspectRatio = clampMediaOption(
      opts.aspectRatio,
      media.aspects,
      media.defaultAspect,
    );
  }
  return patch;
}

export type ReasoningEffortUi = "low" | "med" | "high" | "ultra";
const EFFORTS: { id: ReasoningEffortUi; label: string; note: string }[] = [
  { id: "low", label: "low", note: "быстро, дёшево" },
  { id: "med", label: "med", note: "короче" },
  { id: "high", label: "high", note: "по умолчанию" },
  { id: "ultra", label: "ultra", note: "максимум (xhigh)" },
];
const ACC = "#2fd5b5";

export function normalizeEffort(v?: string | null): ReasoningEffortUi {
  const x = (v || "").trim().toLowerCase();
  if (!x) return "high";
  if (x === "low") return "low";
  if (x === "med" || x === "medium") return "med";
  if (x === "ultra" || x === "xhigh") return "ultra";
  return "high";
}

function vendorNote(m: CatalogModel): string {
  if (m.kind === "text") {
    return `${formatUsdPrice(m.pricing.input_usd_per_m)} вход · ${formatUsdPrice(m.pricing.output_usd_per_m)} выход / 1M`;
  }
  if (m.kind === "image") return `${formatUsdPrice(m.pricing.usd_per_image)} за изображение`;
  return m.provider === "kie" ? "видео · Kie" : "видео · VIA";
}

export function NodeModelPicker({
  nodeKey,
  nodeType,
  modelId,
  imageResolution,
  imageQuality,
  aspectRatio,
  reasoningEffort,
}: {
  nodeKey: string;
  nodeType: string;
  modelId?: string | null;
  imageResolution?: string | null;
  imageQuality?: string | null;
  aspectRatio?: string | null;
  reasoningEffort?: string | null;
}) {
  const [open, setOpen] = useState(false);
  const [catalogOpen, setCatalogOpen] = useState(false);
  const [anchor, setAnchor] = useState<DOMRect | null>(null);
  const fallbackId = defaultModelIdForNodeType(nodeType);
  const selectedId = (modelId || "").trim() || fallbackId;

  const catalogQuery = useQuery({
    queryKey: ["text-llm-catalog"],
    queryFn: async (): Promise<CatalogResponse> => {
      const r = await fetch("/api/text-llm", { cache: "no-store" });
      if (!r.ok) throw new Error("catalog");
      return r.json() as Promise<CatalogResponse>;
    },
    staleTime: 60_000,
  });

  const catalog = catalogForNodeType(catalogQuery.data?.catalog ?? localCatalog(), nodeType);
  const selected = findCatalogModel(catalog, selectedId) ?? findCatalogModel(localCatalog(), selectedId);
  const isTextLlm = (selected?.kind ?? "text") === "text" && !MEDIA_NODE_TYPES.has(nodeType);
  const effort = normalizeEffort(reasoningEffort);
  const media = mediaOptionsForModel(selectedId);
  const priceBit = selected?.pricing.usd_per_video
    ? `${formatUsdPrice(selected.pricing.usd_per_video)}/клип`
    : selected?.pricing.usd_per_image
      ? `${formatUsdPrice(selected.pricing.usd_per_image)}/фото`
      : null;
  const isSelectedT2V =
    selected?.kind === "video" &&
    (selected.id.endsWith("-t2v") || Boolean(selected.api_model?.endsWith("-t2v")));
  const summaryBits = [
    selected?.label || selectedId,
    isSelectedT2V ? "T2V" : null,
    priceBit,
    media?.resolutions.length
      ? clampMediaOption(imageResolution, media.resolutions, media.defaultResolution)
      : null,
    media?.qualities.length
      ? qualityLabel(
          clampMediaOption(
            imageQuality,
            media.qualities.map((q) => q.id),
            media.defaultQuality,
          ),
        )
      : null,
    media?.aspects.length
      ? clampMediaOption(aspectRatio, media.aspects, media.defaultAspect)
      : null,
  ].filter(Boolean);

  return (
    <>
      <div className="nodrag nopan nowheel mt-1 flex w-full min-w-0 items-center gap-1">
      <button
        type="button"
        title="Выбрать модель"
        data-vp-model-btn
        className={cn(
          "flex min-w-0 flex-1 items-center gap-1.5 rounded-lg border px-2 py-1 text-left transition-all duration-200",
          open
            ? "border-[#2fd5b5]/70 bg-[#2fd5b5]/10"
            : "border-white/10 bg-black/30 hover:border-[#2fd5b5]/40 hover:bg-[#2fd5b5]/5",
        )}
        onPointerDown={(e) => e.stopPropagation()}
        onMouseDown={(e) => e.stopPropagation()}
        onClick={(e) => {
          e.stopPropagation();
          e.preventDefault();
          setAnchor((e.currentTarget as HTMLElement).getBoundingClientRect());
          setOpen(true);
        }}
      >
        <span className="min-w-0 truncate text-xs sm:text-[13px] font-semibold tracking-normal text-zinc-100">
          {summaryBits.join(" · ")}
        </span>
        {isTextLlm ? (
          <span
            data-vp-effort-chip
            className="shrink-0 rounded-[5px] border border-[#2fd5b5]/45 bg-[#2fd5b5]/15 px-1.5 py-px text-[11px] font-semibold uppercase leading-[1.35] tracking-wide text-[#7cf0d9]"
          >
            {effort}
          </span>
        ) : null}
        <span className="flex-1" />
        <ChevronDown
          className={cn(
            "h-3 w-3 shrink-0 text-[#2fd5b5]/80 transition-transform duration-200",
            open && "rotate-180",
          )}
        />
      </button>
      <button
        type="button"
        data-vp-model-catalog
        title="Каталог моделей"
        className="shrink-0 rounded-lg border border-white/15 bg-black/30 px-1.5 py-1 text-[10px] font-medium text-white/70 hover:border-[#b49bff]/50 hover:text-[#d9ccff]"
        onPointerDown={(e) => e.stopPropagation()}
        onMouseDown={(e) => e.stopPropagation()}
        onClick={(e) => {
          e.stopPropagation();
          e.preventDefault();
          setCatalogOpen(true);
        }}
      >
        Каталог
      </button>
      </div>
      <ModelNodeMenu
        open={open}
        anchor={anchor}
        onClose={() => setOpen(false)}
        nodeKey={nodeKey}
        nodeType={nodeType}
        selectedId={selectedId}
        catalog={catalog}
        isTextLlm={isTextLlm}
        effort={effort}
        imageResolution={imageResolution}
        imageQuality={imageQuality}
        aspectRatio={aspectRatio}
      />
      <ModelCatalogDialog
        open={catalogOpen}
        onOpenChange={setCatalogOpen}
        nodeKey={nodeKey}
        nodeType={nodeType}
        selectedId={selectedId}
        catalog={catalog}
        imageResolution={imageResolution}
        imageQuality={imageQuality}
        aspectRatio={aspectRatio}
      />
    </>
  );
}

function ModelNodeMenu({
  open,
  anchor,
  onClose,
  nodeKey,
  nodeType,
  selectedId,
  catalog,
  isTextLlm,
  effort,
  imageResolution,
  imageQuality,
  aspectRatio,
}: {
  open: boolean;
  anchor: DOMRect | null;
  onClose: () => void;
  nodeKey: string;
  nodeType: string;
  selectedId: string;
  catalog: ModelCatalogPayload;
  isTextLlm: boolean;
  effort: ReasoningEffortUi;
  imageResolution?: string | null;
  imageQuality?: string | null;
  aspectRatio?: string | null;
}) {
  const [mounted, setMounted] = useState(false);
  const [vis, setVis] = useState(false);
  const selectedModel = findCatalogModel(catalog, selectedId);
  const [vendor, setVendor] = useState<string>(selectedModel?.vendor || "openai");
  const [tab, setTab] = useState<"models" | "thinking">("models");

  useEffect(() => {
    if (open) {
      setMounted(true);
      const next = findCatalogModel(catalog, selectedId)?.vendor;
      if (next) setVendor(next);
      setTab("models");
      const r = requestAnimationFrame(() => requestAnimationFrame(() => setVis(true)));
      return () => cancelAnimationFrame(r);
    }
    setVis(false);
    const t = window.setTimeout(() => setMounted(false), 350);
    return () => window.clearTimeout(t);
    // eslint-disable-next-line react-hooks/exhaustive-deps -- catalog identity churns every render
  }, [open, selectedId]);

  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open, onClose]);

  if (!mounted || typeof document === "undefined") return null;

  const activeVendor =
    catalog.vendors.find((v) => v.id === vendor) ??
    catalog.vendors.find((v) => v.models.some((m) => m.id === selectedId)) ??
    catalog.vendors[0];
  const models = activeVendor?.models ?? [];
  const bg = getNodeCustomBg(nodeKey) || nodeBgFor(nodeKey, promptBgFor("default", "default", nodeKey));
  const W = 560;
  const H = 620;
  const vw = window.innerWidth;
  const vh = window.innerHeight;
  const left = Math.max(12, Math.min((anchor?.left ?? (vw - W) / 2) - 16, vw - W - 12));
  const top = Math.max(12, Math.min((anchor?.top ?? (vh - H) / 2) - 40, vh - H - 12));
  const origin = anchor ? `${anchor.left - left + 40}px ${anchor.top - top + 12}px` : "50% 50%";

  const applyEffort = (id: ReasoningEffortUi) => {
    window.dispatchEvent(
      new CustomEvent("canvas-patch-node-data", { detail: { nodeKey, patch: { reasoningEffort: id } } }),
    );
    window.dispatchEvent(new CustomEvent("canvas-save-workflow"));
    toast.success(`Сила мышления: ${id}`);
  };

  return createPortal(
    <div
      className={cn(
        "fixed inset-0 z-[220] transition-colors duration-[350ms]",
        vis ? "bg-black/45" : "bg-black/0",
      )}
      onPointerDown={(e) => {
        e.stopPropagation();
        if (e.target === e.currentTarget) onClose();
      }}
      onWheel={(e) => e.stopPropagation()}
    >
      <aside
        data-vp-model-menu
        style={{ left, top, width: W, maxHeight: H, transformOrigin: origin, fontFamily: "Rubik, system-ui, sans-serif" }}
        className={cn(
          "nowheel nopan absolute flex flex-col overflow-hidden rounded-[14px] border border-white/[0.09] bg-[#161618] text-left text-white shadow-[0_30px_90px_rgba(0,0,0,0.65)]",
          "transition-[opacity,transform] duration-[350ms] ease-[cubic-bezier(0.65,0,0.35,1)]",
          vis ? "scale-100 opacity-100" : "scale-[0.92] opacity-0",
        )}
        onPointerDown={(e) => e.stopPropagation()}
      >
        <div
          className="relative h-[60px] shrink-0 overflow-hidden bg-cover"
          style={{ backgroundImage: `url(${bg})`, backgroundPosition: "center top" }}
        >
          <div className="pointer-events-none absolute inset-0 bg-gradient-to-t from-black/60 via-black/15 to-black/30" />
          <div className="absolute inset-0 flex items-center px-4 pr-14 text-[17px] font-medium leading-[1.2] text-white [text-shadow:0_1px_6px_rgba(0,0,0,0.6)]">
            <span className="truncate">Модель: {selectedModel?.label || selectedId}</span>
          </div>
          <button
            type="button"
            title="Закрыть"
            onClick={onClose}
            className="absolute right-2 top-1/2 z-[1] flex h-9 w-9 -translate-y-1/2 items-center justify-center rounded-md bg-black/45 text-white/85 backdrop-blur-sm hover:bg-black/70 hover:text-white"
          >
            <XIcon className="h-4 w-4" />
          </button>
        </div>
        {isTextLlm ? (
          <div className="shrink-0 p-3 pb-0">
            <div className="grid grid-cols-2 border border-white/10">
              {(
                [
                  ["models", "Модели"],
                  ["thinking", "Сила мышления"],
                ] as const
              ).map(([id, label]) => (
                <button
                  key={id}
                  type="button"
                  onClick={() => setTab(id)}
                  className={cn(
                    "flex h-12 items-center justify-center px-3 text-[14px] font-medium uppercase tracking-[0.08em] transition-colors",
                    tab === id ? "bg-[#2fd5b5]/20 text-[#9ff5e4]" : "text-white/60 hover:bg-white/[0.05] hover:text-white",
                  )}
                >
                  {label}
                </button>
              ))}
            </div>
          </div>
        ) : null}
        {tab === "models" || !isTextLlm ? (
          <div className="flex min-h-0 flex-1 flex-col gap-3 p-3">
            {catalog.vendors.length > 1 ? (
              <div className="flex shrink-0 flex-wrap gap-2">
                {catalog.vendors.map((v) => {
                  const active = v.id === activeVendor?.id;
                  return (
                    <button
                      key={v.id}
                      type="button"
                      onClick={() => setVendor(v.id)}
                      className={cn(
                        "flex items-center gap-2 rounded-[10px] border px-3.5 py-2 text-[14px] font-medium transition-colors",
                        active
                          ? "border-[#2fd5b5]/60 bg-[#2fd5b5]/15 text-[#9ff5e4]"
                          : "border-white/[0.08] bg-white/[0.04] text-white/65 hover:bg-white/[0.08] hover:text-white",
                      )}
                    >
                      <VendorGlyph icon={v.icon} className="h-4 w-4" />
                      {v.label}
                      <span className="text-[12px] text-white/40">{v.count}</span>
                    </button>
                  );
                })}
              </div>
            ) : null}
            <div className="flex min-h-0 flex-1 flex-col gap-2 overflow-y-auto pr-0.5">
              {models.map((m) => (
                <ModelRow
                  key={m.id}
                  model={m}
                  selected={m.id === selectedId}
                  imageResolution={m.id === selectedId ? imageResolution : null}
                  imageQuality={m.id === selectedId ? imageQuality : null}
                  aspectRatio={m.id === selectedId ? aspectRatio : null}
                  onApply={(patch) => {
                    const isText =
                      m.kind === "text" || (!patch.imageResolution && !patch.aspectRatio && !patch.imageQuality);
                    patchNodeModel(nodeKey, patch, isText);
                    onClose();
                    if (isText) {
                      toast.success(`Модель «${m.label}» применена ко всем текстовым нодам`);
                    } else {
                      toast.success(`Модель: ${m.label}`);
                    }
                  }}
                />
              ))}
            </div>
          </div>
        ) : (
          <div className="flex min-h-0 flex-1 flex-col gap-2 overflow-y-auto p-3">
            {EFFORTS.map((e) => {
              const active = e.id === effort;
              return (
                <button
                  key={e.id}
                  type="button"
                  data-vp-effort={e.id}
                  onClick={() => applyEffort(e.id)}
                  className={cn(
                    "flex items-center gap-4 rounded-[10px] border px-4 py-3.5 text-left transition-colors",
                    active
                      ? "border-[#2fd5b5]/60 bg-[#2fd5b5]/[0.12]"
                      : "border-white/[0.07] bg-white/[0.035] hover:bg-white/[0.07]",
                  )}
                >
                  <span
                    className={cn(
                      "w-16 text-[18px] font-semibold uppercase tracking-wide",
                      active ? "text-[#9ff5e4]" : "text-white/85",
                    )}
                  >
                    {e.label}
                  </span>
                  <span className="flex-1 text-[14px] text-white/55">{e.note}</span>
                  {active ? <Check className="h-5 w-5 text-[#2fd5b5]" /> : null}
                </button>
              );
            })}
            <p className="px-1 pt-1 text-[13px] leading-snug text-white/45">
              Сохраняется для этой ноды и передаётся в запрос как reasoning_effort (ultra → xhigh).
            </p>
          </div>
        )}
      </aside>
    </div>,
    document.body,
  );
}

function ModelRow({
  model,
  selected,
  imageResolution,
  imageQuality,
  aspectRatio,
  onApply,
}: {
  model: CatalogModel;
  selected: boolean;
  imageResolution?: string | null;
  imageQuality?: string | null;
  aspectRatio?: string | null;
  onApply: (patch: NodeMediaPatch) => void;
}) {
  const media = mediaOptionsForModel(model.id);
  const hasMediaMenus = Boolean(
    media && (media.resolutions.length || media.qualities.length || media.aspects.length),
  );
  const [resolution, setResolution] = useState(() =>
    clampMediaOption(imageResolution, media?.resolutions ?? [], media?.defaultResolution ?? ""),
  );
  const [quality, setQuality] = useState<MediaQualityId>(
    () =>
      clampMediaOption(
        imageQuality,
        media?.qualities.map((x) => x.id) ?? [],
        media?.defaultQuality ?? "medium",
      ) as MediaQualityId,
  );
  const [aspect, setAspect] = useState(() =>
    clampMediaOption(aspectRatio, media?.aspects ?? [], media?.defaultAspect ?? ""),
  );
  const [expanded, setExpanded] = useState(selected && hasMediaMenus);
  const label = model.label.replace(/\s*\(1K\/2K\/4K\)\s*$/i, "");
  const apply = () =>
    onApply(buildMediaPatch(model.id, { imageResolution: resolution, imageQuality: quality, aspectRatio: aspect }));
  return (
    <div
      data-vp-model-row={model.id}
      className={cn(
        "relative shrink-0 overflow-hidden rounded-[10px] border transition-colors",
        selected ? "border-[#2fd5b5]/60 bg-[#2fd5b5]/[0.12]" : "border-white/[0.07] bg-white/[0.035] hover:bg-white/[0.07]",
      )}
    >
      <ModelBrandBackdrop id={model.id} label={model.label} />
      <button
        type="button"
        className="relative z-[1] flex min-h-[64px] w-full items-center gap-3 px-4 py-3 text-left"
        onClick={() => (hasMediaMenus ? setExpanded((v) => !v) : apply())}
      >
        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-2">
            <span className={cn("truncate text-[16px] font-medium", selected ? "text-[#9ff5e4]" : "text-white")}>
              {label}
            </span>
            {model.is_top ? (
              <span className="shrink-0 rounded-[5px] bg-[#2fd5b5]/15 px-1.5 py-px text-[11px] font-semibold uppercase text-[#7cf0d9]">
                топ
              </span>
            ) : null}
          </div>
          <div className="mt-0.5 truncate text-[13px] text-white/50">
            {model.provider} · {vendorNote(model)}
          </div>
        </div>
        {selected ? <Check className="h-5 w-5 shrink-0" style={{ color: ACC }} /> : null}
      </button>
      {hasMediaMenus && media && expanded ? (
        <div className="relative z-[1] space-y-2 px-4 pb-3">
          <div className="flex gap-2">
            {media.resolutions.length ? (
              <MiniMenu
                label="Качество"
                value={resolution}
                options={media.resolutions.map((r) => ({ id: r, label: r }))}
                onChange={setResolution}
              />
            ) : null}
            {media.qualities.length ? (
              <MiniMenu
                label="Детализация"
                value={quality}
                options={media.qualities}
                onChange={(id) => setQuality(id as MediaQualityId)}
              />
            ) : null}
            {media.aspects.length ? (
              <MiniMenu
                label="Соотношение"
                value={aspect}
                options={media.aspects.map((a) => ({ id: a, label: a }))}
                onChange={setAspect}
              />
            ) : null}
          </div>
          <button
            type="button"
            onClick={apply}
            className="w-full rounded-[10px] border border-[#2fd5b5]/45 bg-[#2fd5b5]/15 px-3 py-2.5 text-[14px] font-medium text-[#9ff5e4] hover:bg-[#2fd5b5]/25"
          >
            Выбрать
          </button>
        </div>
      ) : null}
    </div>
  );
}

function MiniMenu({
  label,
  value,
  options,
  onChange,
}: {
  label: string;
  value: string;
  options: { id: string; label: string }[];
  onChange: (id: string) => void;
}) {
  const [open, setOpen] = useState(false);
  if (!options.length) return null;
  return (
    <div className="relative min-w-0 flex-1">
      <button
        type="button"
        title={label}
        className="flex w-full items-center justify-between gap-1 rounded-lg border border-white/10 bg-white/[0.04] px-3 py-2 text-left text-[13px] text-white/80 hover:border-[#2fd5b5]/40"
        onClick={(e) => {
          e.stopPropagation();
          setOpen((v) => !v);
        }}
      >
        <span className="truncate">
          <span className="text-white/35">{label}: </span>
          {options.find((o) => o.id === value)?.label ?? value}
        </span>
        <ChevronDown className={cn("h-3 w-3 shrink-0 text-white/40", open && "rotate-180")} />
      </button>
      {open ? (
        <div className="absolute left-0 right-0 top-full z-30 mt-1 max-h-40 overflow-y-auto rounded-lg border border-white/12 bg-[#1c1c1f] p-1 shadow-xl">
          {options.map((o) => (
            <button
              key={o.id}
              type="button"
              className={cn(
                "flex w-full items-center justify-between rounded-md px-3 py-2 text-left text-[14px]",
                o.id === value ? "bg-[#2fd5b5]/15 text-[#9ff5e4]" : "text-white/75 hover:bg-white/[0.05]",
              )}
              onClick={(e) => {
                e.stopPropagation();
                onChange(o.id);
                setOpen(false);
              }}
            >
              {o.label}
              {o.id === value ? <Check className="h-3 w-3" /> : null}
            </button>
          ))}
        </div>
      ) : null}
    </div>
  );
}

function ModelCatalogDialog({
  open,
  onOpenChange,
  nodeKey,
  nodeType,
  selectedId,
  catalog,
  imageResolution,
  imageQuality,
  aspectRatio,
}: {
  open: boolean;
  onOpenChange: (v: boolean) => void;
  nodeKey: string;
  nodeType: string;
  selectedId: string;
  catalog: ModelCatalogPayload;
  imageResolution?: string | null;
  imageQuality?: string | null;
  aspectRatio?: string | null;
}) {
  const selectedModel = findCatalogModel(catalog, selectedId);
  const [vendor, setVendor] = useState<string>(selectedModel?.vendor || "anthropic");

  useEffect(() => {
    if (!open) return;
    const next = findCatalogModel(catalog, selectedId)?.vendor;
    if (next) setVendor(next);
    // eslint-disable-next-line react-hooks/exhaustive-deps -- catalog identity churns every render
  }, [open, selectedId]);

  const activeVendor =
    catalog.vendors.find((v) => v.id === vendor) ??
    catalog.vendors.find((v) => v.models.some((m) => m.id === selectedId)) ??
    catalog.vendors[0];
  const models = activeVendor?.models ?? [];

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent
        overlayClassName="z-[220] bg-black/70"
        className={cn(
          "z-[221] flex max-h-[min(88vh,860px)] w-[min(1080px,94vw)] max-w-none flex-col gap-0 overflow-hidden border-white/10 bg-[#101010] p-0 sm:rounded-2xl",
          "shadow-[0_30px_90px_rgba(0,0,0,0.72)]",
        )}
        onPointerDown={(e) => e.stopPropagation()}
        onMouseDown={(e) => e.stopPropagation()}
      >
        <DialogHeader className="sr-only">
          <DialogTitle>Выбор модели</DialogTitle>
          <DialogDescription>
            Каталог моделей с ценами и параметрами генерации ({nodeType})
          </DialogDescription>
        </DialogHeader>
        <div className="flex items-center justify-between gap-3 border-b border-white/8 px-4 py-3 pr-12">
          <div className="flex min-w-0 flex-1 items-center gap-1.5 overflow-x-auto [scrollbar-width:none] [&::-webkit-scrollbar]:hidden">
            {catalog.vendors.map((v) => {
              const active = v.id === activeVendor?.id;
              return (
                <button
                  key={v.id}
                  type="button"
                  onClick={() => setVendor(v.id)}
                  className={cn(
                    "flex shrink-0 items-center gap-1.5 rounded-full border px-2.5 py-1.5 text-[11px] font-semibold uppercase tracking-wide transition-all duration-200",
                    active
                      ? "border-[#b49bff] bg-[#b49bff]/12 text-[#d9ccff] shadow-[0_0_18px_rgba(180,155,255,0.22)]"
                      : "border-transparent text-white/45 hover:border-white/10 hover:bg-white/[0.04] hover:text-white/75",
                  )}
                >
                  <VendorGlyph icon={v.icon} className="h-3.5 w-3.5" />
                  {v.label}
                  <span className={cn("rounded-md px-1 text-[10px]", active ? "text-[#b49bff]" : "text-white/30")}>
                    {v.count}
                  </span>
                </button>
              );
            })}
          </div>
        </div>
        <div className="min-h-0 flex-1 overflow-y-auto px-4 py-4">
          <div
            className={cn(
              "grid gap-3",
              models.length <= 2
                ? "grid-cols-1 sm:grid-cols-2"
                : "grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4",
            )}
          >
            {models.map((m, idx) => (
              <ModelCard
                key={m.id}
                model={m}
                selected={m.id === selectedId}
                delay={idx * 30}
                imageResolution={m.id === selectedId ? imageResolution : null}
                imageQuality={m.id === selectedId ? imageQuality : null}
                aspectRatio={m.id === selectedId ? aspectRatio : null}
                onApply={(patch) => {
                  const isText = m.kind === "text" || (!patch.imageResolution && !patch.aspectRatio && !patch.imageQuality);
                  patchNodeModel(nodeKey, patch, isText);
                  onOpenChange(false);
                  if (isText) {
                    toast.success(`Модель «${m.label}» применена ко всем текстовым нодам`);
                  } else {
                    toast.success(`Модель: ${m.label}`);
                  }
                }}
              />
            ))}
          </div>
        </div>
      </DialogContent>
    </Dialog>
  );
}


function ModelCard({
  model,
  selected,
  onApply,
  delay,
  imageResolution,
  imageQuality,
  aspectRatio,
}: {
  model: CatalogModel;
  selected: boolean;
  onApply: (patch: NodeMediaPatch) => void;
  delay: number;
  imageResolution?: string | null;
  imageQuality?: string | null;
  aspectRatio?: string | null;
}) {
  const [copied, setCopied] = useState(false);
  const [cacheOpen, setCacheOpen] = useState(false);
  const media = mediaOptionsForModel(model.id);
  const [resolution, setResolution] = useState(() =>
    clampMediaOption(imageResolution, media?.resolutions ?? [], media?.defaultResolution ?? "2K"),
  );
  const [quality, setQuality] = useState<MediaQualityId>(() =>
    clampMediaOption(
      imageQuality,
      (media?.qualities ?? []).map((q) => q.id),
      media?.defaultQuality ?? "medium",
    ) as MediaQualityId,
  );
  const [aspect, setAspect] = useState(() =>
    clampMediaOption(aspectRatio, media?.aspects ?? [], media?.defaultAspect ?? "16:9"),
  );

  useEffect(() => {
    const next = mediaOptionsForModel(model.id);
    if (!next) return;
    setResolution(clampMediaOption(imageResolution, next.resolutions, next.defaultResolution));
    setQuality(
      clampMediaOption(
        imageQuality,
        next.qualities.map((q) => q.id),
        next.defaultQuality,
      ) as MediaQualityId,
    );
    setAspect(clampMediaOption(aspectRatio, next.aspects, next.defaultAspect));
  }, [model.id, imageResolution, imageQuality, aspectRatio]);

  const hasCache =
    typeof model.pricing.cache_read_usd_per_m === "number" ||
    typeof model.pricing.cache_create_usd_per_m === "number";
  const isImage = model.kind === "image";
  const isT2V =
    model.kind === "video" &&
    (model.id.endsWith("-t2v") || Boolean(model.api_model?.endsWith("-t2v")));
  const hasMediaMenus = Boolean(
    media && (media.resolutions.length || media.qualities.length || media.aspects.length),
  );

  return (
    <div
      style={{ animationDelay: `${delay}ms` }}
      className={cn(
        "node-model-card group relative flex flex-col rounded-2xl border bg-[#171717] p-3.5 text-left transition-all duration-200",
        "hover:-translate-y-0.5 hover:border-[#b49bff]/45 hover:shadow-[0_12px_32px_rgba(0,0,0,0.45)]",
        selected
          ? "border-[#b49bff] shadow-[0_0_0_1px_rgba(180,155,255,0.45),0_0_28px_rgba(140,100,255,0.22)]"
          : "border-white/8",
      )}
    >
      {selected && (
        <span className="absolute right-3 top-3 flex h-5 w-5 items-center justify-center rounded-full bg-[#b49bff] text-[#1a1228]">
          <Check className="h-3 w-3" strokeWidth={3} />
        </span>
      )}
      <button
        type="button"
        className="min-w-0 pr-6 text-left"
        onClick={() =>
          onApply(
            buildMediaPatch(model.id, {
              imageResolution: resolution,
              imageQuality: quality,
              aspectRatio: aspect,
            }),
          )
        }
      >
        <div className="flex items-start justify-between gap-2">
          <div className="flex flex-wrap items-center gap-1.5 text-[13px] font-semibold tracking-tight text-white">
            <span className="truncate">{model.label.replace(/\s*\(1K\/2K\/4K\)\s*$/i, "")}</span>
            {model.is_top && (
              <span className="inline-flex shrink-0 items-center rounded-md border border-[#b49bff]/40 bg-[#b49bff]/15 px-1.5 py-0.5 text-[9px] font-bold uppercase tracking-wider text-[#d9ccff]">
                ТОП
              </span>
            )}
            {isT2V && (
              <span className="inline-flex shrink-0 items-center rounded-md border border-amber-400/50 bg-amber-500/20 px-1.5 py-0.5 text-[9px] font-bold tracking-wider text-amber-300 shadow-[0_0_8px_rgba(245,158,11,0.25)]">
                Только текст (T2V)
              </span>
            )}
          </div>
          <span className="inline-flex shrink-0 items-center gap-1 rounded-full border border-emerald-400/25 bg-emerald-500/10 px-1.5 py-0.5 text-[9px] font-semibold uppercase tracking-wide text-emerald-300">
            <span className="h-1.5 w-1.5 rounded-full bg-emerald-400 shadow-[0_0_6px_rgba(52,211,153,0.9)]" />
            онлайн
          </span>
        </div>
      </button>

      <div className="mt-3 rounded-xl border border-white/[0.04] bg-black/30 px-3 py-2.5">
        <div className="flex items-center justify-between text-[9px] font-semibold uppercase tracking-[0.14em] text-white/35">
          <span>Цена</span>
          <span>
            {isImage
              ? "$ / фото"
              : model.kind === "video"
                ? model.id === "veo-3-1-lite"
                  ? "$ / клип (~8с)"
                  : "$ / клип (~5с)"
                : "$ / 1M токенов"}
          </span>
        </div>
        {isImage ? (
          <div className="mt-2">
            <div className="text-[22px] font-semibold tabular-nums leading-none text-[#c4b2ff]">
              {formatUsdPrice(model.pricing.usd_per_image)}
            </div>
            <div className="mt-1 text-[9px] uppercase tracking-wider text-white/35">за изображение</div>
          </div>
        ) : model.kind === "video" ? (
          <div className="mt-2 flex items-end justify-between gap-2">
            <div>
              <div className="text-[22px] font-semibold tabular-nums leading-none text-[#c4b2ff]">
                {formatUsdPrice(model.pricing.usd_per_video)}
              </div>
              <div className="mt-1 text-[9px] uppercase tracking-wider text-white/35">
                {model.id === "veo-3-1-lite" ? "за 8с клип" : "за 5с клип"}
              </div>
            </div>
            <div className="text-right">
              <div className="text-[13px] font-semibold leading-none text-white/80">
                {model.provider === "kie" ? "Kie" : "Outsee"}
              </div>
              <div className="mt-1 text-[9px] uppercase tracking-wider text-white/35">
                {model.provider === "kie" ? "KIE_API_KEY" : "OUTSEE_API_KEY"}
              </div>
            </div>
          </div>
        ) : (
          <div className="mt-2 flex items-end justify-between gap-2">
            <div>
              <div className="text-[22px] font-semibold tabular-nums leading-none text-[#c4b2ff]">
                {formatUsdPrice(model.pricing.input_usd_per_m)}
              </div>
              <div className="mt-1 text-[9px] uppercase tracking-wider text-white/35">вход</div>
            </div>
            <div className="mb-3 text-[11px] tracking-[0.2em] text-[#b49bff]/70">❯❯❯</div>
            <div className="text-right">
              <div className="text-[22px] font-semibold tabular-nums leading-none text-[#c4b2ff]">
                {formatUsdPrice(model.pricing.output_usd_per_m)}
              </div>
              <div className="mt-1 text-[9px] uppercase tracking-wider text-white/35">выход</div>
            </div>
          </div>
        )}
        {hasCache && !isImage && model.kind !== "video" ? (
          <div
            className={cn(
              "grid transition-all duration-300",
              cacheOpen || "group-hover:grid",
              cacheOpen
                ? "mt-2 grid-rows-[1fr] opacity-100"
                : "grid-rows-[0fr] opacity-80 group-hover:mt-2 group-hover:grid-rows-[1fr] group-hover:opacity-100",
            )}
          >
            <div className="overflow-hidden">
              <button
                type="button"
                className="flex items-center gap-1 text-[10px] text-white/40 hover:text-white/70"
                onClick={(e) => {
                  e.stopPropagation();
                  setCacheOpen((v) => !v);
                }}
              >
                <span className={cn("transition-transform", cacheOpen && "rotate-90")}>▶</span>
                Цены кэша
              </button>
              <div className="mt-1.5 grid grid-cols-2 gap-2 rounded-lg bg-white/[0.03] px-2 py-1.5 text-[10px]">
                <div>
                  <div className="text-white/35">чтение</div>
                  <div className="font-medium tabular-nums text-[#c4b2ff]">
                    {formatUsdPrice(model.pricing.cache_read_usd_per_m)}
                  </div>
                </div>
                <div>
                  <div className="text-white/35">запись</div>
                  <div className="font-medium tabular-nums text-[#c4b2ff]">
                    {formatUsdPrice(model.pricing.cache_create_usd_per_m)}
                  </div>
                </div>
              </div>
            </div>
          </div>
        ) : null}
      </div>

      {isT2V && (
        <div className="mt-2.5 rounded-xl border border-amber-400/25 bg-amber-500/10 p-2.5 text-[11px] leading-relaxed text-amber-200/90 shadow-[0_0_12px_rgba(245,158,11,0.1)]">
          <span className="font-semibold text-amber-300">Внимание (T2V):</span> модель генерирует случайное видео только по тексту и <u>не оживляет</u> стартовый кадр персонажа.
        </div>
      )}

      {hasMediaMenus && media ? (
        <div className="mt-3 space-y-1.5" onClick={(e) => e.stopPropagation()}>
          <div className="flex gap-1.5">
            {media.resolutions.length ? (
              <MiniMenu
                label="Качество"
                value={resolution}
                options={media.resolutions.map((r) => ({ id: r, label: r }))}
                onChange={setResolution}
              />
            ) : null}
            {media.qualities.length ? (
              <MiniMenu
                label="Детализация"
                value={quality}
                options={media.qualities}
                onChange={(id) => setQuality(id as MediaQualityId)}
              />
            ) : null}
          </div>
          {media.aspects.length ? (
            <MiniMenu
              label="Соотношение"
              value={aspect}
              options={media.aspects.map((a) => ({ id: a, label: a }))}
              onChange={setAspect}
            />
          ) : null}
          <button
            type="button"
            className="w-full rounded-lg border border-[#b49bff]/40 bg-[#b49bff]/10 px-2 py-1.5 text-[11px] font-semibold text-[#d9ccff] hover:bg-[#b49bff]/18"
            onClick={() =>
              onApply(
                buildMediaPatch(model.id, {
                  imageResolution: resolution,
                  imageQuality: quality,
                  aspectRatio: aspect,
                }),
              )
            }
          >
            Выбрать
          </button>
        </div>
      ) : (
        <button
          type="button"
          className="mt-3 w-full rounded-lg border border-white/10 bg-white/[0.03] px-2 py-1.5 text-[11px] font-medium text-white/70 hover:border-[#b49bff]/40 hover:text-[#d9ccff]"
          onClick={() => onApply(buildMediaPatch(model.id, {}))}
        >
          Выбрать
        </button>
      )}

      <div className="mt-3 flex items-center justify-between gap-2">
        <code className="truncate font-mono text-[10px] text-white/35">{model.id}</code>
        <span
          role="button"
          tabIndex={0}
          title="Скопировать id"
          className="inline-flex h-6 w-6 items-center justify-center rounded-md text-white/35 transition hover:bg-white/8 hover:text-white/80"
          onClick={(e) => {
            e.stopPropagation();
            void navigator.clipboard.writeText(model.id).then(() => {
              setCopied(true);
              toast.success("id скопирован");
              window.setTimeout(() => setCopied(false), 1200);
            });
          }}
        >
          {copied ? <Check className="h-3.5 w-3.5 text-emerald-300" /> : <Copy className="h-3.5 w-3.5" />}
        </span>
      </div>
    </div>
  );
}

export function selectedModelLabel(
  modelId: string | null | undefined,
  nodeType: string,
  catalog?: ModelCatalogPayload | null,
): string {
  const id = (modelId || "").trim() || defaultModelIdForNodeType(nodeType);
  const found = findCatalogModel(catalog ?? localCatalog(), id);
  return found?.label || id || DEFAULT_TEXT_MODEL_ID;
}
