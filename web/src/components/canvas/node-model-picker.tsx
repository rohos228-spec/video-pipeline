"use client";

import { useEffect, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import {
  Check,
  ChevronDown,
  Copy,
  Image as ImageIcon,
  Moon,
  Sparkles,
} from "lucide-react";
import { toast } from "sonner";
import { cn } from "@/lib/utils";
import {
  DEFAULT_TEXT_MODEL_ID,
  defaultModelIdForNodeType,
  findCatalogModel,
  formatUsdPrice,
  localCatalog,
  type CatalogModel,
  type ModelCatalogPayload,
  type ModelChannel,
} from "@/lib/node-model-catalog";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";

type CatalogResponse = {
  catalog?: ModelCatalogPayload;
  catalog_channels?: Record<ModelChannel, ModelCatalogPayload>;
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

function patchNodeModel(nodeKey: string, modelId: string, channel: ModelChannel) {
  window.dispatchEvent(
    new CustomEvent("canvas-patch-node-data", {
      detail: { nodeKey, patch: { modelId, modelChannel: channel } },
    }),
  );
  window.dispatchEvent(new CustomEvent("canvas-save-workflow"));
}

export function NodeModelPicker({
  nodeKey,
  nodeType,
  modelId,
  modelChannel,
}: {
  nodeKey: string;
  nodeType: string;
  modelId?: string | null;
  modelChannel?: ModelChannel | string | null;
}) {
  const [open, setOpen] = useState(false);
  const channel: ModelChannel = modelChannel === "stable" ? "stable" : "cheap";
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

  const cheap = catalogQuery.data?.catalog_channels?.cheap ?? localCatalog("cheap");
  const stable = catalogQuery.data?.catalog_channels?.stable ?? localCatalog("stable");
  const catalog = channel === "stable" ? stable : cheap;
  const selected = findCatalogModel(catalog, selectedId) ?? findCatalogModel(localCatalog(channel), selectedId);

  return (
    <>
      <button
        type="button"
        title="Выбрать модель"
        className={cn(
          "nodrag nopan nowheel mt-1 flex w-full min-w-0 items-center gap-1.5 rounded-lg border px-2 py-1 text-left transition-all duration-200",
          open
            ? "border-[#b49bff]/70 bg-[#b49bff]/10 shadow-[0_0_16px_rgba(180,155,255,0.18)]"
            : "border-white/10 bg-black/30 hover:border-[#b49bff]/40 hover:bg-[#b49bff]/5",
        )}
        onPointerDown={(e) => e.stopPropagation()}
        onMouseDown={(e) => e.stopPropagation()}
        onClick={(e) => {
          e.stopPropagation();
          e.preventDefault();
          setOpen(true);
        }}
      >
        <span className="min-w-0 flex-1 truncate text-[11px] font-medium tracking-tight text-foreground/95">
          {selected?.label || selectedId}
        </span>
        <ChevronDown
          className={cn(
            "h-3 w-3 shrink-0 text-[#b49bff]/80 transition-transform duration-200",
            open && "rotate-180",
          )}
        />
      </button>
      <ModelCatalogDialog
        open={open}
        onOpenChange={setOpen}
        nodeKey={nodeKey}
        selectedId={selectedId}
        channel={channel}
        cheap={cheap}
        stable={stable}
      />
    </>
  );
}

function ModelCatalogDialog({
  open,
  onOpenChange,
  nodeKey,
  selectedId,
  channel,
  cheap,
  stable,
}: {
  open: boolean;
  onOpenChange: (v: boolean) => void;
  nodeKey: string;
  selectedId: string;
  channel: ModelChannel;
  cheap: ModelCatalogPayload;
  stable: ModelCatalogPayload;
}) {
  const catalog = channel === "stable" ? stable : cheap;
  const selectedModel = findCatalogModel(catalog, selectedId);
  const [vendor, setVendor] = useState<string>(selectedModel?.vendor || "anthropic");

  useEffect(() => {
    if (!open) return;
    const next = findCatalogModel(catalog, selectedId)?.vendor;
    if (next) setVendor(next);
  }, [open, selectedId, catalog]);

  const activeVendor = catalog.vendors.find((v) => v.id === vendor) ?? catalog.vendors[0];
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
          <DialogDescription>Каталог моделей vibecode с ценами входа и выхода</DialogDescription>
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
          <div className="flex shrink-0 items-center gap-1 rounded-full border border-white/10 bg-black/40 p-0.5">
            {(["cheap", "stable"] as const).map((ch) => {
              const on = channel === ch;
              return (
                <button
                  key={ch}
                  type="button"
                  onClick={() => patchNodeModel(nodeKey, selectedId, ch)}
                  className={cn(
                    "rounded-full px-2.5 py-1 text-[10px] font-semibold uppercase tracking-wide transition-all duration-200",
                    on
                      ? "bg-[#b49bff] text-[#1a1228] shadow-[0_0_12px_rgba(180,155,255,0.45)]"
                      : "text-white/40 hover:text-white/70",
                  )}
                >
                  {ch === "cheap" ? "Дешёвый канал" : "Стабильный канал"}
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
                : models.length <= 4
                  ? "grid-cols-1 sm:grid-cols-2 lg:grid-cols-4"
                  : "grid-cols-1 sm:grid-cols-2 lg:grid-cols-4",
            )}
          >
            {models.map((m, idx) => (
              <ModelCard
                key={m.id}
                model={m}
                selected={m.id === selectedId}
                delay={idx * 30}
                onPick={() => {
                  patchNodeModel(nodeKey, m.id, channel);
                  onOpenChange(false);
                  toast.success(`Модель: ${m.label}`);
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
  onPick,
  delay,
}: {
  model: CatalogModel;
  selected: boolean;
  onPick: () => void;
  delay: number;
}) {
  const [copied, setCopied] = useState(false);
  const [cacheOpen, setCacheOpen] = useState(false);
  const hasCache =
    typeof model.pricing.cache_read_usd_per_m === "number" ||
    typeof model.pricing.cache_create_usd_per_m === "number";
  const isImage = model.kind === "image";

  return (
    <button
      type="button"
      onClick={onPick}
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
      <div className="flex items-start justify-between gap-2 pr-6">
        <div className="min-w-0">
          <div className="truncate text-[13px] font-semibold tracking-tight text-white">
            {model.label.replace(/\s*\(1K\/2K\/4K\)\s*$/i, "")}
          </div>
        </div>
        <span className="inline-flex shrink-0 items-center gap-1 rounded-full border border-emerald-400/25 bg-emerald-500/10 px-1.5 py-0.5 text-[9px] font-semibold uppercase tracking-wide text-emerald-300">
          <span className="h-1.5 w-1.5 rounded-full bg-emerald-400 shadow-[0_0_6px_rgba(52,211,153,0.9)]" />
          онлайн
        </span>
      </div>
      {model.resolution ? (
        <span className="mt-1 w-fit rounded-md border border-white/10 bg-white/[0.04] px-1.5 py-0.5 text-[9px] font-medium text-white/45">
          {model.resolution}
        </span>
      ) : null}

      <div className="mt-3 rounded-xl border border-white/[0.04] bg-black/30 px-3 py-2.5">
        <div className="flex items-center justify-between text-[9px] font-semibold uppercase tracking-[0.14em] text-white/35">
          <span>Цена</span>
          <span>{isImage ? "$ / фото" : "$ / 1M токенов"}</span>
        </div>
        {isImage ? (
          <div className="mt-2">
            <div className="text-[22px] font-semibold tabular-nums leading-none text-[#c4b2ff]">
              {formatUsdPrice(model.pricing.usd_per_image)}
            </div>
            <div className="mt-1 text-[9px] uppercase tracking-wider text-white/35">за изображение</div>
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
        {hasCache && !isImage ? (
          <div
            className={cn(
              "grid transition-all duration-300",
              cacheOpen || "group-hover:grid",
              cacheOpen ? "mt-2 grid-rows-[1fr] opacity-100" : "grid-rows-[0fr] opacity-80 group-hover:mt-2 group-hover:grid-rows-[1fr] group-hover:opacity-100",
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
    </button>
  );
}

export function selectedModelLabel(
  modelId: string | null | undefined,
  nodeType: string,
  catalog?: ModelCatalogPayload | null,
): string {
  const id = (modelId || "").trim() || defaultModelIdForNodeType(nodeType);
  const found = findCatalogModel(catalog ?? localCatalog("cheap"), id);
  return found?.label || id || DEFAULT_TEXT_MODEL_ID;
}
