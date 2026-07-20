"use client";

/**
 * Полный клон UI outsee.io/create (type=image|video) + /image:
 * слева История, центр Результат, снизу prompt-dock с моделью/опциями.
 */

import { useEffect, useMemo, useRef, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  ChevronDown,
  Coins,
  History,
  ImageIcon,
  Loader2,
  Sparkles,
  Video,
  X,
  ExternalLink,
} from "lucide-react";
import { toast } from "sonner";
import { api, type ProjectAsset } from "@/lib/api";
import { errorMessageFromUnknown } from "@/lib/error-message";
import { cn } from "@/lib/utils";
import {
  OUTSEE_ACCENT,
  OUTSEE_DETAIL_LEVELS,
  OUTSEE_IMAGE_MODELS,
  OUTSEE_VIDEO_MODELS,
  aspectToStudioId,
  getImageModel,
  getVideoModel,
  outseeCreateUrl,
  resToStudioId,
  slugToStudioId,
  studioAspectToLabel,
  studioIdToSlug,
  studioResToLabel,
  type OutseeMediaType,
} from "@/lib/outsee-catalog";

type Props = {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  projectId: number | null;
};

export function OutseeCreateWorkspace({ open, onOpenChange, projectId }: Props) {
  const qc = useQueryClient();
  const [mediaType, setMediaType] = useState<OutseeMediaType>("image");
  const [imageSlug, setImageSlug] = useState("gpt-image-2");
  const [videoSlug, setVideoSlug] = useState("veo-3-fast");
  const [aspect, setAspect] = useState("9:16");
  const [resolution, setResolution] = useState("2K");
  const [detail, setDetail] = useState("medium");
  const [relax, setRelax] = useState(false);
  const [videoResolution, setVideoResolution] = useState("1080p");
  const [videoRelax, setVideoRelax] = useState(false);
  const [prompt, setPrompt] = useState("");
  const [modelOpen, setModelOpen] = useState(false);
  const [aspectOpen, setAspectOpen] = useState(false);
  const [resOpen, setResOpen] = useState(false);
  const [detailOpen, setDetailOpen] = useState(false);
  const [selectedAssetId, setSelectedAssetId] = useState<string | null>(null);
  const modelRef = useRef<HTMLDivElement>(null);

  const project = useQuery({
    queryKey: ["project", projectId],
    queryFn: () => api.getProject(projectId!),
    enabled: open && projectId != null,
  });

  const assets = useQuery({
    queryKey: ["project-assets", projectId, "outsee-history"],
    queryFn: () => api.listProjectAssets(projectId!, "all"),
    enabled: open && projectId != null,
    refetchInterval: open ? 8000 : false,
  });

  const mediaReview = useQuery({
    queryKey: ["media-review", projectId, mediaType === "image" ? "images" : "videos"],
    queryFn: () =>
      api.listMediaReview(projectId!, mediaType === "image" ? "images" : "videos"),
    enabled: open && projectId != null,
    refetchInterval: open ? 8000 : false,
  });

  useEffect(() => {
    if (!open || !project.data) return;
    const p = project.data;
    setImageSlug(studioIdToSlug(p.image_generator, "image"));
    setVideoSlug(studioIdToSlug(p.video_generator, "video"));
    setAspect(studioAspectToLabel(p.aspect_ratio));
    setResolution(studioResToLabel(p.image_resolution));
    setDetail(p.image_quality || "medium");
    setRelax(Boolean(p.image_relax));
    setVideoResolution(p.video_resolution || "1080p");
    setVideoRelax(Boolean(p.video_relax));
  }, [open, project.data?.id, project.dataUpdatedAt]);

  useEffect(() => {
    if (!open) {
      setModelOpen(false);
      setAspectOpen(false);
      setResOpen(false);
      setDetailOpen(false);
    }
  }, [open]);

  useEffect(() => {
    if (!modelOpen) return;
    const onDown = (e: MouseEvent) => {
      if (modelRef.current && !modelRef.current.contains(e.target as Node)) {
        setModelOpen(false);
      }
    };
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") setModelOpen(false);
    };
    window.addEventListener("mousedown", onDown);
    window.addEventListener("keydown", onKey);
    return () => {
      window.removeEventListener("mousedown", onDown);
      window.removeEventListener("keydown", onKey);
    };
  }, [modelOpen]);

  const imageModel = getImageModel(imageSlug);
  const videoModel = getVideoModel(videoSlug);

  useEffect(() => {
    if (mediaType !== "image") return;
    if (imageModel.resolutions.length && !imageModel.resolutions.includes(resolution)) {
      setResolution(imageModel.resolutions.includes("2K") ? "2K" : imageModel.resolutions[0]!);
    }
    if (imageModel.aspects.length && !imageModel.aspects.includes(aspect)) {
      setAspect(imageModel.aspects.includes("9:16") ? "9:16" : imageModel.aspects[0]!);
    }
  }, [imageSlug, mediaType, imageModel, resolution, aspect]);

  const historyItems = useMemo(() => {
    const fromReview = (mediaReview.data ?? [])
      .filter((f) => f.preview_url || f.file_path)
      .map(
        (f): ProjectAsset => ({
          source: "media-review",
          id: `frame-${f.frame_id}`,
          kind: mediaType === "image" ? "image" : "video",
          path: f.file_path,
          preview_url: f.preview_url,
          label: `frame_${String(f.number).padStart(3, "0")}`,
          frame_id: f.frame_id,
          description: f.image_prompt || f.animation_prompt || f.voiceover_text || null,
        }),
      );
    if (fromReview.length) return fromReview;

    const list = assets.data ?? [];
    const kind = mediaType === "image" ? "image" : "video";
    return list
      .filter((a) => {
        const k = (a.kind || "").toLowerCase();
        if (kind === "image") return k.includes("image") || k.includes("frame") || k === "hero";
        return k.includes("video") || k.includes("clip");
      })
      .filter((a) => a.preview_url || a.path)
      .slice()
      .reverse();
  }, [assets.data, mediaReview.data, mediaType]);

  const selectedAsset: ProjectAsset | null = useMemo(() => {
    if (!historyItems.length) return null;
    if (selectedAssetId) {
      return historyItems.find((a) => a.id === selectedAssetId) ?? historyItems[0]!;
    }
    return historyItems[0]!;
  }, [historyItems, selectedAssetId]);

  useEffect(() => {
    if (selectedAssetId && !historyItems.some((a) => a.id === selectedAssetId)) {
      setSelectedAssetId(null);
    }
  }, [historyItems, selectedAssetId]);

  const save = useMutation({
    mutationFn: async () => {
      if (projectId == null) throw new Error("Выберите проект слева");
      const imgStudio = slugToStudioId(imageSlug, "image");
      const vidStudio = slugToStudioId(videoSlug, "video");
      const body: Record<string, unknown> = {};
      if (imgStudio) {
        body.image_generator = imgStudio;
        body.aspect_ratio = aspectToStudioId(aspect);
        body.image_resolution = resToStudioId(resolution);
        if (imageModel.hasDetail) body.image_quality = detail;
        body.image_relax = relax;
      }
      if (vidStudio) {
        body.video_generator = vidStudio;
        if (videoResolution === "720p" || videoResolution === "1080p") {
          body.video_resolution = videoResolution;
        }
        body.video_relax = videoSlug.includes("veo") ? videoRelax : false;
      }
      return api.patchProject(projectId, body);
    },
    onSuccess: () => {
      if (projectId != null) qc.invalidateQueries({ queryKey: ["project", projectId] });
      toast.success("Настройки outsee сохранены в проект");
    },
    onError: (e) => toast.error(errorMessageFromUnknown(e)),
  });

  const runStep = useMutation({
    mutationFn: async () => {
      if (projectId == null) throw new Error("Выберите проект слева");
      await save.mutateAsync();
      const step = mediaType === "image" ? "img" : "video";
      return api.runProjectStep(projectId, step);
    },
    onSuccess: () => {
      toast.success(mediaType === "image" ? "Запущен шаг img" : "Запущен шаг video");
      if (projectId != null) {
        qc.invalidateQueries({ queryKey: ["project-assets", projectId] });
        qc.invalidateQueries({ queryKey: ["media-review", projectId] });
      }
    },
    onError: (e) => toast.error(errorMessageFromUnknown(e)),
  });

  if (!open) return null;

  const currentSlug = mediaType === "image" ? imageSlug : videoSlug;
  const currentName =
    mediaType === "image" ? imageModel.displayName : videoModel.displayName;
  const currentIcon = mediaType === "image" ? imageModel.icon : videoModel.icon;

  return (
    <div className="fixed inset-0 z-[80] flex flex-col bg-[#0a0a0a] text-white">
      {/* top bar */}
      <header className="flex h-[52px] shrink-0 items-center justify-between border-b border-white/[0.06] bg-[#0f0f0f] px-4">
        <div className="flex items-center gap-3">
          <button
            type="button"
            onClick={() => onOpenChange(false)}
            className="inline-flex h-8 w-8 items-center justify-center rounded-lg border border-white/10 bg-white/[0.03] text-white/70 hover:bg-white/[0.08] hover:text-white"
            title="Закрыть"
          >
            <X className="h-4 w-4" />
          </button>
          <div className="flex items-center gap-2">
            <Sparkles className="h-4 w-4" style={{ color: OUTSEE_ACCENT }} />
            <div className="leading-tight">
              <div className="text-sm font-semibold tracking-tight">Генерация</div>
              <div className="text-[10px] uppercase tracking-[0.16em] text-white/35">
                outsee create
              </div>
            </div>
          </div>
          <div className="ml-4 flex rounded-lg border border-white/10 bg-[#171717] p-0.5">
            {(
              [
                { id: "image" as const, label: "Фото", icon: ImageIcon },
                { id: "video" as const, label: "Видео", icon: Video },
              ] as const
            ).map((t) => {
              const Icon = t.icon;
              const active = mediaType === t.id;
              return (
                <button
                  key={t.id}
                  type="button"
                  onClick={() => setMediaType(t.id)}
                  className={cn(
                    "inline-flex items-center gap-1.5 rounded-md px-3 py-1.5 text-xs font-medium transition",
                    active ? "text-black" : "text-white/50 hover:text-white/80",
                  )}
                  style={active ? { backgroundColor: OUTSEE_ACCENT } : undefined}
                >
                  <Icon className="h-3.5 w-3.5" />
                  {t.label}
                </button>
              );
            })}
          </div>
        </div>
        <div className="flex items-center gap-2">
          {projectId == null && (
            <span className="text-[11px] text-amber-300/90">выберите проект слева</span>
          )}
          {project.data?.slug && (
            <span className="rounded-md border border-white/10 bg-white/[0.03] px-2 py-1 font-mono text-[10px] text-white/45">
              #{projectId} · {project.data.slug}
            </span>
          )}
          <a
            href={outseeCreateUrl(mediaType, currentSlug)}
            target="_blank"
            rel="noreferrer"
            className="inline-flex items-center gap-1.5 rounded-lg border border-white/10 px-2.5 py-1.5 text-[11px] text-white/55 hover:border-white/25 hover:text-white"
          >
            outsee.io
            <ExternalLink className="h-3 w-3" />
          </a>
        </div>
      </header>

      {/* body */}
      <div className="flex min-h-0 flex-1">
        {/* history */}
        <aside className="flex w-[220px] shrink-0 flex-col border-r border-white/[0.06] bg-[#0c0c0c] lg:w-[260px]">
          <div className="flex items-center gap-2 border-b border-white/[0.06] px-3 py-2.5">
            <History className="h-3.5 w-3.5 text-white/40" />
            <span className="text-[11px] font-semibold uppercase tracking-[0.14em] text-white/40">
              История
            </span>
            <span className="ml-auto font-mono text-[10px] text-white/30">
              {historyItems.length}
            </span>
          </div>
          <div className="flex-1 overflow-y-auto p-2">
            {projectId == null ? (
              <EmptyHint text="Нет проекта — история пуста" />
            ) : assets.isLoading || mediaReview.isLoading ? (
              <div className="flex items-center gap-2 px-2 py-6 text-[11px] text-white/40">
                <Loader2 className="h-3.5 w-3.5 animate-spin" />
                загрузка…
              </div>
            ) : historyItems.length === 0 ? (
              <EmptyHint text="Пока нет генераций в проекте" />
            ) : (
              <div className="grid grid-cols-2 gap-1.5">
                {historyItems.map((item) => {
                  const active = selectedAsset?.id === item.id;
                  const url = item.preview_url || "";
                  const isVideo = (item.kind || "").toLowerCase().includes("video");
                  return (
                    <button
                      key={item.id}
                      type="button"
                      onClick={() => setSelectedAssetId(item.id)}
                      className={cn(
                        "group relative aspect-square overflow-hidden rounded-lg border bg-[#141414] transition",
                        active
                          ? "border-[rgba(209,254,23,0.55)] ring-1 ring-[rgba(209,254,23,0.35)]"
                          : "border-white/[0.06] hover:border-white/20",
                      )}
                      title={item.label || item.id}
                    >
                      {url ? (
                        isVideo ? (
                          <video
                            src={url}
                            muted
                            playsInline
                            className="h-full w-full object-cover"
                          />
                        ) : (
                          // eslint-disable-next-line @next/next/no-img-element
                          <img src={url} alt="" className="h-full w-full object-cover" />
                        )
                      ) : (
                        <div className="flex h-full items-center justify-center text-[9px] text-white/25">
                          no preview
                        </div>
                      )}
                      <div className="absolute inset-x-0 bottom-0 bg-gradient-to-t from-black/80 to-transparent px-1.5 py-1">
                        <div className="truncate font-mono text-[9px] text-white/70">
                          {item.label || item.id}
                        </div>
                      </div>
                    </button>
                  );
                })}
              </div>
            )}
          </div>
        </aside>

        {/* result */}
        <section className="relative flex min-w-0 flex-1 flex-col">
          <div className="flex items-center justify-between px-4 pb-1 pt-3 lg:px-6">
            <h2 className="flex items-center gap-2 text-sm font-bold text-white lg:text-base">
              <Sparkles className="h-4 w-4" style={{ color: OUTSEE_ACCENT }} />
              Результат генерации
            </h2>
          </div>
          <div className="flex min-h-0 flex-1 items-center justify-center px-4 pb-[210px] lg:px-6">
            {selectedAsset?.preview_url ? (
              <div className="relative flex max-h-full max-w-full items-center justify-center">
                {(selectedAsset.kind || "").toLowerCase().includes("video") ? (
                  <video
                    src={selectedAsset.preview_url}
                    controls
                    className="max-h-[calc(100vh-280px)] max-w-full rounded-xl border border-white/[0.06] bg-black shadow-[0_12px_40px_rgba(0,0,0,0.55)]"
                  />
                ) : (
                  // eslint-disable-next-line @next/next/no-img-element
                  <img
                    src={selectedAsset.preview_url}
                    alt={selectedAsset.label || ""}
                    className="max-h-[calc(100vh-280px)] max-w-full rounded-xl border border-white/[0.06] bg-black object-contain shadow-[0_12px_40px_rgba(0,0,0,0.55)]"
                  />
                )}
              </div>
            ) : (
              <div className="flex w-full max-w-xs flex-col items-center gap-5 rounded-2xl border border-white/[0.06] bg-white/[0.02] px-6 py-10">
                <div
                  className="flex h-12 w-12 items-center justify-center rounded-full border border-white/10"
                  style={{ background: "rgba(209,254,23,0.08)" }}
                >
                  <ImageIcon className="h-5 w-5 text-white/50" />
                </div>
                <div className="text-center">
                  <div className="text-sm font-medium text-white/80">Нет результата</div>
                  <div className="mt-1 text-[12px] leading-relaxed text-white/40">
                    Выберите кадр в истории или запустите генерацию — настройки уйдут в проект
                    пайплайна.
                  </div>
                </div>
              </div>
            )}
          </div>

          {/* prompt dock — как group/prompt на outsee */}
          <div className="absolute bottom-0 left-0 right-0 z-10 px-3 pb-3 lg:px-5 lg:pb-4">
            <div
              className="border border-white/[0.08] bg-[#171717] shadow-[0_12px_40px_rgba(0,0,0,0.55)] transition-all duration-300 lg:border-[#171717] lg:shadow-xl"
              style={{ borderRadius: 16 }}
            >
              <div className="px-3 pt-3 lg:px-4">
                <textarea
                  value={prompt}
                  onChange={(e) => setPrompt(e.target.value)}
                  placeholder={
                    mediaType === "image"
                      ? "Опишите изображение… (промпт для outsee; пайплайн берёт сцены из Excel)"
                      : "Опишите видео… (для ручного outsee; пайплайн — animation prompts)"
                  }
                  rows={3}
                  className="w-full resize-none bg-transparent text-[13px] leading-relaxed text-white/90 placeholder:text-white/30 focus:outline-none"
                />
              </div>

              <div className="flex flex-wrap items-end gap-2 border-t border-white/[0.06] px-3 py-2.5 lg:px-4">
                {/* model picker */}
                <div className="relative" ref={modelRef}>
                  <ChipButton
                    active={modelOpen}
                    onClick={() => {
                      setModelOpen((v) => !v);
                      setAspectOpen(false);
                      setResOpen(false);
                      setDetailOpen(false);
                    }}
                  >
                    {/* eslint-disable-next-line @next/next/no-img-element */}
                    <img
                      src={currentIcon}
                      alt=""
                      width={18}
                      height={18}
                      className="h-[18px] w-[18px] shrink-0 rounded-md object-cover ring-1 ring-white/10"
                    />
                    <span className="font-medium">{currentName}</span>
                    <ChevronDown className="h-3 w-3 opacity-60" />
                  </ChipButton>

                  {modelOpen && (
                    <ModelPickerPopover
                      mediaType={mediaType}
                      selectedSlug={currentSlug}
                      onSelect={(slug) => {
                        if (mediaType === "image") setImageSlug(slug);
                        else setVideoSlug(slug);
                        setModelOpen(false);
                      }}
                    />
                  )}
                </div>

                {mediaType === "image" && imageModel.chips.includes("aspect") && (
                  <OptionDropdown
                    label="Соотношение"
                    value={aspect}
                    open={aspectOpen}
                    onOpenChange={(v) => {
                      setAspectOpen(v);
                      if (v) {
                        setModelOpen(false);
                        setResOpen(false);
                        setDetailOpen(false);
                      }
                    }}
                    options={imageModel.aspects.map((a) => ({ id: a, label: a }))}
                    onSelect={setAspect}
                    mono
                  />
                )}

                {mediaType === "image" &&
                  imageModel.chips.includes("resolution") &&
                  imageModel.resolutions.length > 0 && (
                    <OptionDropdown
                      label="Разрешение"
                      value={resolution}
                      open={resOpen}
                      onOpenChange={(v) => {
                        setResOpen(v);
                        if (v) {
                          setModelOpen(false);
                          setAspectOpen(false);
                          setDetailOpen(false);
                        }
                      }}
                      options={imageModel.resolutions.map((r) => ({ id: r, label: r }))}
                      onSelect={setResolution}
                      mono
                    />
                  )}

                {mediaType === "image" && imageModel.hasDetail && (
                  <OptionDropdown
                    label="Детализация"
                    value={
                      OUTSEE_DETAIL_LEVELS.find((d) => d.id === detail)?.label ?? "Среднее"
                    }
                    open={detailOpen}
                    onOpenChange={(v) => {
                      setDetailOpen(v);
                      if (v) {
                        setModelOpen(false);
                        setAspectOpen(false);
                        setResOpen(false);
                      }
                    }}
                    options={OUTSEE_DETAIL_LEVELS.map((d) => ({
                      id: d.id,
                      label: d.label,
                      hint: d.hint,
                    }))}
                    onSelect={setDetail}
                  />
                )}

                {mediaType === "video" && videoModel.resolutions.length > 0 && (
                  <OptionDropdown
                    label="Разрешение"
                    value={videoResolution}
                    open={resOpen}
                    onOpenChange={(v) => {
                      setResOpen(v);
                      if (v) setModelOpen(false);
                    }}
                    options={videoModel.resolutions.map((r) => ({ id: r, label: r }))}
                    onSelect={setVideoResolution}
                    mono
                  />
                )}

                {mediaType === "image" && (
                  <LimitToggle on={relax} onChange={setRelax} />
                )}
                {mediaType === "video" && videoSlug.includes("veo") && (
                  <LimitToggle on={videoRelax} onChange={setVideoRelax} />
                )}

                <div className="ml-auto flex items-center gap-2">
                  <button
                    type="button"
                    disabled={save.isPending || projectId == null}
                    onClick={() => save.mutate()}
                    className="rounded-xl border border-white/10 bg-white/[0.04] px-3 py-2.5 text-[11px] font-medium text-white/70 hover:bg-white/[0.08] disabled:opacity-40"
                  >
                    {save.isPending ? "…" : "Сохранить"}
                  </button>
                  <button
                    type="button"
                    disabled={runStep.isPending || projectId == null}
                    onClick={() => runStep.mutate()}
                    className="inline-flex min-w-[140px] items-center justify-center gap-1.5 rounded-xl px-4 py-2.5 text-[12px] font-semibold text-black transition hover:brightness-110 disabled:opacity-40"
                    style={{ backgroundColor: OUTSEE_ACCENT }}
                  >
                    {runStep.isPending ? (
                      <>
                        <Loader2 className="h-3.5 w-3.5 animate-spin" />
                        Отправка…
                      </>
                    ) : (
                      "Генерировать"
                    )}
                  </button>
                </div>
              </div>
            </div>
          </div>
        </section>
      </div>
    </div>
  );
}

function EmptyHint({ text }: { text: string }) {
  return (
    <div className="px-2 py-8 text-center text-[11px] leading-relaxed text-white/35">{text}</div>
  );
}

function ChipButton({
  children,
  onClick,
  active,
}: {
  children: React.ReactNode;
  onClick: () => void;
  active?: boolean;
}) {
  return (
    <button
      type="button"
      data-active={active || undefined}
      onClick={onClick}
      className={cn(
        "inline-flex h-9 items-center gap-1.5 rounded-xl border px-2.5 text-[12px] transition",
        active
          ? "border-[rgba(209,254,23,0.35)] bg-[rgba(209,254,23,0.10)] text-white"
          : "border-white/10 bg-[#222] text-white/85 hover:border-white/20",
      )}
    >
      {children}
    </button>
  );
}

function LimitToggle({ on, onChange }: { on: boolean; onChange: (v: boolean) => void }) {
  return (
    <button
      type="button"
      onClick={() => onChange(!on)}
      className={cn(
        "inline-flex h-9 items-center gap-2 rounded-xl border px-2.5 transition",
        on ? "border-amber-400/35 bg-amber-500/10" : "border-white/10 bg-[#1a1a1a]",
      )}
    >
      <span className={cn("text-xs font-semibold", on ? "text-gray-200" : "text-gray-400")}>
        Безлимит
      </span>
      <span
        className={cn(
          "relative h-4 w-8 rounded-full transition-colors",
          on ? "bg-amber-500" : "bg-zinc-600",
        )}
      >
        <span
          className={cn(
            "absolute top-0.5 h-3 w-3 rounded-full bg-white shadow transition-all",
            on ? "left-[18px]" : "left-[2px]",
          )}
        />
      </span>
    </button>
  );
}

function OptionDropdown({
  label,
  value,
  open,
  onOpenChange,
  options,
  onSelect,
  mono,
}: {
  label: string;
  value: string;
  open: boolean;
  onOpenChange: (v: boolean) => void;
  options: { id: string; label: string; hint?: string }[];
  onSelect: (id: string) => void;
  mono?: boolean;
}) {
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!open) return;
    const onDown = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) onOpenChange(false);
    };
    window.addEventListener("mousedown", onDown);
    return () => window.removeEventListener("mousedown", onDown);
  }, [open, onOpenChange]);

  return (
    <div className="relative" ref={ref}>
      <div className="flex flex-col gap-0.5">
        <span className="hidden px-0.5 text-[10px] text-gray-400 lg:block">{label}</span>
        <ChipButton active={open} onClick={() => onOpenChange(!open)}>
          <span className={cn(mono && "font-mono tabular-nums")}>{value}</span>
          <ChevronDown className="h-3 w-3 opacity-60" />
        </ChipButton>
      </div>
      {open && (
        <div
          className="absolute bottom-full left-0 z-[1000] mb-1 overflow-hidden rounded-xl border shadow-2xl"
          style={{ backgroundColor: "#1a1a1a", borderColor: "rgba(255,255,255,0.1)", minWidth: 140 }}
        >
          <div className="max-h-56 overflow-y-auto p-1.5">
            {options.map((opt) => {
              const active = opt.id === value || opt.label === value;
              return (
                <button
                  key={opt.id}
                  type="button"
                  onClick={() => {
                    onSelect(opt.id);
                    onOpenChange(false);
                  }}
                  className="flex w-full items-center justify-between rounded-lg px-2.5 py-2 text-left text-[12px] transition hover:bg-white/[0.06]"
                  style={{
                    background: active ? "rgba(209,254,23,0.10)" : undefined,
                    color: active ? OUTSEE_ACCENT : "white",
                  }}
                >
                  <span className={cn(mono && "font-mono")}>{opt.label}</span>
                  {opt.hint && (
                    <span className="text-[10px] text-white/35">{opt.hint}</span>
                  )}
                </button>
              );
            })}
          </div>
        </div>
      )}
    </div>
  );
}

function ModelPickerPopover({
  mediaType,
  selectedSlug,
  onSelect,
}: {
  mediaType: OutseeMediaType;
  selectedSlug: string;
  onSelect: (slug: string) => void;
}) {
  const title = mediaType === "image" ? "Модели изображений" : "Модели видео";
  const models =
    mediaType === "image"
      ? OUTSEE_IMAGE_MODELS.map((m) => ({
          id: m.slug,
          displayName: m.displayName,
          description: m.description,
          icon: m.icon,
          price: m.price,
          isTop: m.isTop,
          isNew: m.isNew,
        }))
      : OUTSEE_VIDEO_MODELS.map((m) => ({
          id: m.slug,
          displayName: m.displayName,
          description: m.description,
          icon: m.icon,
          price: m.price,
          isTop: m.isTop,
          isNew: m.isNew,
        }));

  return (
    <div
      className="absolute bottom-full left-0 z-50 mb-3.5 flex max-h-[82vh] flex-col overflow-hidden rounded-2xl border border-white/10 shadow-2xl"
      style={{
        backgroundColor: "#141414",
        width: mediaType === "video" ? 580 : 460,
      }}
      role="dialog"
      aria-label={title}
      onPointerDown={(e) => e.stopPropagation()}
    >
      <div className="border-b border-white/[0.06] px-3 py-2.5">
        <span className="text-[12px] font-semibold text-white/80">{title}</span>
      </div>
      <div
        className="grid gap-1.5 overflow-y-auto p-2"
        style={{
          gridTemplateColumns: "repeat(2, minmax(0, 1fr))",
          flex: "1 1 auto",
          minHeight: 0,
        }}
      >
        {models.map((m) => {
          const active = m.id === selectedSlug;
          const badge = m.isTop
            ? { tone: "top" as const, label: "ТОП" }
            : m.isNew
              ? { tone: "new" as const, label: "НОВОЕ" }
              : null;
          return (
            <button
              key={m.id}
              type="button"
              data-active={active || undefined}
              onClick={() => onSelect(m.id)}
              className={cn(
                "relative flex items-start gap-2.5 rounded-xl border px-2.5 py-2.5 text-left transition",
                active
                  ? "border-[rgba(209,254,23,0.35)] bg-[rgba(209,254,23,0.08)]"
                  : "border-white/[0.06] bg-white/[0.03] hover:bg-white/[0.06]",
              )}
            >
              {badge && (
                <span
                  className={cn(
                    "absolute -top-1.5 right-2 z-10 rounded-md px-1.5 py-0.5 text-[9px] font-bold text-black",
                    badge.tone === "top" ? "bg-[rgba(209,254,23,1)]" : "bg-blue-400",
                  )}
                >
                  {badge.label}
                </span>
              )}
              <div className="flex shrink-0 flex-col items-center">
                {/* eslint-disable-next-line @next/next/no-img-element */}
                <img
                  src={m.icon}
                  alt={m.displayName}
                  className="h-10 w-10 rounded-lg object-cover ring-1 ring-white/10"
                />
                {m.price && (
                  <span className="mt-1 inline-flex items-center gap-0.5 font-mono text-[10px] text-white/55">
                    <Coins className="h-2.5 w-2.5" strokeWidth={2.5} />
                    {m.price}
                  </span>
                )}
              </div>
              <div className="min-w-0 flex-1">
                <p
                  className="truncate text-[12px] font-medium"
                  style={{ color: active ? OUTSEE_ACCENT : "white" }}
                >
                  {m.displayName}
                </p>
                <p className="mt-0.5 line-clamp-2 text-[10px] leading-snug text-white/45">
                  {m.description}
                </p>
              </div>
            </button>
          );
        })}
      </div>
    </div>
  );
}
