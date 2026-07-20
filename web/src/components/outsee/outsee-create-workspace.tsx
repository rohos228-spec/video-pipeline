"use client";

/**
 * Outsee Create (глобально):
 * — настройки общие (data/outsee_create_settings.json), не project
 * — история общая по всем проектам
 * — typetoggle Фото / Видео / Аудио + feed Все/Фото/Видео/Аудио
 * — полный picker моделей как на outsee.io/create
 */

import { useEffect, useMemo, useRef, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  ChevronDown,
  Coins,
  History,
  ImageIcon,
  Loader2,
  Music,
  Sparkles,
  Video,
  X,
  ExternalLink,
} from "lucide-react";
import { toast } from "sonner";
import { api } from "@/lib/api";
import { errorMessageFromUnknown } from "@/lib/error-message";
import { cn } from "@/lib/utils";
import {
  OUTSEE_ACCENT,
  OUTSEE_CHIP_LABELS,
  OUTSEE_DETAIL_LEVELS,
  OUTSEE_FEED_TABS,
  OUTSEE_TYPE_TABS,
  chipOptions,
  clampToOptions,
  detailLabel,
  dockChipsForModel,
  getAudioModel,
  getImageModel,
  getVideoModel,
  outseeCreateUrl,
  pickerModelsForType,
  slugToStudioId,
  supportsRelax,
  type OutseeChip,
  type OutseeFeedKind,
  type OutseeMediaType,
} from "@/lib/outsee-catalog";

type Props = {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  /** Опционально: «применить к проекту» — не источник настроек. */
  projectId: number | null;
};

type HistoryItem = {
  id: string;
  kind: string;
  preview_url: string | null;
  label: string;
  project_id: number | null;
  project_slug: string | null;
  prompt: string | null;
};

export function OutseeCreateWorkspace({ open, onOpenChange, projectId }: Props) {
  const qc = useQueryClient();
  const [mediaType, setMediaType] = useState<OutseeMediaType>("image");
  const [feedKind, setFeedKind] = useState<OutseeFeedKind>("all");
  const [imageSlug, setImageSlug] = useState("gpt-image-2");
  const [videoSlug, setVideoSlug] = useState("kling-3-0");
  const [audioSlug, setAudioSlug] = useState("suno-5-5");
  const [aspect, setAspect] = useState("16:9");
  const [resolution, setResolution] = useState("2K");
  const [detail, setDetail] = useState("medium");
  const [relax, setRelax] = useState(false);
  const [videoResolution, setVideoResolution] = useState("1080p");
  const [videoRelax, setVideoRelax] = useState(false);
  const [duration, setDuration] = useState("5");
  const [generateAudio, setGenerateAudio] = useState(false);
  const [orientation, setOrientation] = useState<"video" | "image">("video");
  const [motionQuality, setMotionQuality] = useState("std");
  const [instrumental, setInstrumental] = useState(false);
  const [prompt, setPrompt] = useState("");
  const [modelOpen, setModelOpen] = useState(false);
  const [openChip, setOpenChip] = useState<OutseeChip | null>(null);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [settingsHydrated, setSettingsHydrated] = useState(false);
  const modelRef = useRef<HTMLDivElement>(null);

  const settingsQ = useQuery({
    queryKey: ["outsee-create-settings"],
    queryFn: api.getOutseeCreateSettings,
    enabled: open,
  });

  const historyQ = useQuery({
    queryKey: ["outsee-create-history", feedKind],
    queryFn: () => api.listOutseeCreateHistory(feedKind),
    enabled: open,
    refetchInterval: open ? 10000 : false,
  });

  useEffect(() => {
    if (!open || !settingsQ.data || settingsHydrated) return;
    const s = settingsQ.data;
    const mt = (s.media_type as OutseeMediaType) || "image";
    setMediaType(mt === "audio" || mt === "video" || mt === "image" ? mt : "image");
    setImageSlug(String(s.image_slug || "gpt-image-2"));
    setVideoSlug(String(s.video_slug || "kling-3-0"));
    setAudioSlug(String(s.audio_slug || "suno-5-5"));
    setAspect(String(s.aspect || "16:9"));
    setResolution(String(s.image_resolution || "2K"));
    setDetail(String(s.image_quality || "medium"));
    setRelax(Boolean(s.image_relax));
    setVideoResolution(String(s.video_resolution || "1080p"));
    setVideoRelax(Boolean(s.video_relax));
    setDuration(String(s.duration || "5"));
    setGenerateAudio(Boolean(s.generate_audio));
    setOrientation(s.orientation === "image" ? "image" : "video");
    setMotionQuality(String(s.motion_quality || "std"));
    setInstrumental(Boolean(s.instrumental));
    setPrompt(String(s.prompt || ""));
    setSettingsHydrated(true);
  }, [open, settingsQ.data, settingsHydrated]);

  useEffect(() => {
    if (!open) {
      setModelOpen(false);
      setOpenChip(null);
      setSettingsHydrated(false);
    }
  }, [open]);

  useEffect(() => {
    if (!modelOpen) return;
    const onDown = (e: MouseEvent) => {
      if (modelRef.current && !modelRef.current.contains(e.target as Node)) setModelOpen(false);
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

  const activeSlug =
    mediaType === "image" ? imageSlug : mediaType === "video" ? videoSlug : audioSlug;
  const imageModel = getImageModel(imageSlug);
  const videoModel = getVideoModel(videoSlug);
  const audioModel = getAudioModel(audioSlug);
  const dockChips = dockChipsForModel(activeSlug, mediaType);

  const currentName =
    mediaType === "image"
      ? imageModel.displayName
      : mediaType === "video"
        ? videoModel.displayName
        : audioModel.displayName;
  const currentIcon =
    mediaType === "image"
      ? imageModel.icon
      : mediaType === "video"
        ? videoModel.icon
        : audioModel.icon;

  useEffect(() => {
    if (mediaType === "image") {
      const aspects = chipOptions(imageSlug, "aspect");
      const resolutions = chipOptions(imageSlug, "resolution");
      if (aspects.length) setAspect((a) => clampToOptions(a, aspects, "16:9"));
      if (resolutions.length) setResolution((r) => clampToOptions(r, resolutions, "2K"));
      return;
    }
    if (mediaType === "video") {
      const aspects = chipOptions(videoSlug, "aspect");
      const resolutions = chipOptions(videoSlug, "resolution");
      const durations = chipOptions(videoSlug, "duration");
      if (aspects.length) setAspect((a) => clampToOptions(a, aspects, "16:9"));
      if (resolutions.length) {
        setVideoResolution((r) => clampToOptions(r, resolutions, resolutions[0]));
      }
      if (durations.length) {
        setDuration((d) => clampToOptions(d, durations, durations[0]));
      }
    }
  }, [imageSlug, videoSlug, mediaType]);

  const applyModelDefaults = (slug: string, kind: OutseeMediaType) => {
    if (kind === "image") {
      const m = getImageModel(slug);
      const d = m.defaults;
      const aspects = chipOptions(slug, "aspect");
      const resolutions = chipOptions(slug, "resolution");
      if (d.aspectRatio) setAspect(clampToOptions(d.aspectRatio, aspects, "16:9"));
      if (d.imageResolution && resolutions.length) {
        setResolution(clampToOptions(d.imageResolution, resolutions, "2K"));
      }
      if (m.chips.includes("detail")) setDetail(d.detailLevel || "medium");
      return;
    }
    if (kind === "audio") {
      const m = getAudioModel(slug);
      setInstrumental(Boolean(m.defaults.instrumental));
      return;
    }
    const m = getVideoModel(slug);
    const d = m.defaults;
    const aspects = chipOptions(slug, "aspect");
    const resolutions = chipOptions(slug, "resolution");
    const durations = chipOptions(slug, "duration");
    if (d.aspectRatio && aspects.length) {
      setAspect(clampToOptions(d.aspectRatio, aspects, "16:9"));
    }
    if (d.resolution && resolutions.length) {
      setVideoResolution(clampToOptions(d.resolution, resolutions, resolutions[0]));
    }
    if (d.duration != null && durations.length) {
      setDuration(clampToOptions(String(d.duration), durations, durations[0]));
    }
    if (m.chips.includes("audio")) setGenerateAudio(Boolean(d.generateAudio));
    if (m.chips.includes("quality")) setMotionQuality(d.motionQuality || "std");
  };

  const settingsPayload = (): Record<string, unknown> => ({
    media_type: mediaType,
    image_slug: imageSlug,
    video_slug: videoSlug,
    audio_slug: audioSlug,
    aspect,
    image_resolution: resolution,
    image_quality: detail,
    image_relax: relax,
    video_resolution: videoResolution,
    video_relax: videoRelax,
    duration,
    generate_audio: generateAudio,
    orientation,
    motion_quality: motionQuality,
    instrumental,
    prompt,
  });

  const saveGlobal = useMutation({
    mutationFn: () => api.putOutseeCreateSettings(settingsPayload()),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["outsee-create-settings"] });
      toast.success("Глобальные настройки Create сохранены");
    },
    onError: (e) => toast.error(errorMessageFromUnknown(e)),
  });

  const applyToProject = useMutation({
    mutationFn: async () => {
      if (projectId == null) throw new Error("Выберите проект слева");
      await api.putOutseeCreateSettings(settingsPayload());
      const body: Record<string, unknown> = {};
      const imgStudio = slugToStudioId(imageSlug, "image");
      const vidStudio = slugToStudioId(videoSlug, "video");
      if (imgStudio) {
        body.image_generator = imgStudio;
        body.aspect_ratio = aspect.replace(":", "_");
        body.image_resolution = resolution.toLowerCase();
        if (imageModel.chips.includes("detail")) body.image_quality = detail;
        body.image_relax = relax;
      }
      if (vidStudio) {
        body.video_generator = vidStudio;
        const vr = videoResolution.toLowerCase();
        if (vr === "720p" || vr === "1080p") body.video_resolution = vr;
        body.video_relax = supportsRelax(videoSlug, "video") ? videoRelax : false;
      }
      return api.patchProject(projectId, body);
    },
    onSuccess: () => {
      if (projectId != null) qc.invalidateQueries({ queryKey: ["project", projectId] });
      toast.success("Настройки применены к проекту");
    },
    onError: (e) => toast.error(errorMessageFromUnknown(e)),
  });

  const runStep = useMutation({
    mutationFn: async () => {
      if (projectId == null) throw new Error("Для запуска пайплайна выберите проект");
      await applyToProject.mutateAsync();
      const step = mediaType === "video" ? "video" : mediaType === "audio" ? "audio" : "img";
      return api.runProjectStep(projectId, step);
    },
    onSuccess: () => {
      toast.success("Шаг запущен");
      qc.invalidateQueries({ queryKey: ["outsee-create-history"] });
    },
    onError: (e) => toast.error(errorMessageFromUnknown(e)),
  });

  const historyItems: HistoryItem[] = useMemo(
    () => (historyQ.data as HistoryItem[] | undefined) ?? [],
    [historyQ.data],
  );

  const selected = useMemo(() => {
    if (!historyItems.length) return null;
    if (selectedId) return historyItems.find((h) => h.id === selectedId) ?? historyItems[0]!;
    return historyItems[0]!;
  }, [historyItems, selectedId]);

  if (!open) return null;

  const TypeIcon = ({ id }: { id: OutseeMediaType }) => {
    if (id === "video") return <Video className="h-4 w-4" strokeWidth={1.7} />;
    if (id === "audio") return <Music className="h-4 w-4" strokeWidth={1.7} />;
    return <ImageIcon className="h-4 w-4" strokeWidth={1.7} />;
  };

  return (
    <div className="fixed inset-0 z-[80] flex flex-col bg-[#0a0a0a] text-white">
      <header className="flex h-[52px] shrink-0 items-center justify-between border-b border-white/[0.06] bg-[#0f0f0f] px-4">
        <div className="flex items-center gap-3">
          <button
            type="button"
            onClick={() => onOpenChange(false)}
            className="inline-flex h-8 w-8 items-center justify-center rounded-lg border border-white/10 bg-white/[0.03] text-white/70 hover:bg-white/[0.08]"
          >
            <X className="h-4 w-4" />
          </button>
          <div className="flex items-center gap-2">
            <Sparkles className="h-4 w-4" style={{ color: OUTSEE_ACCENT }} />
            <div className="leading-tight">
              <div className="text-sm font-semibold tracking-tight">Генерация</div>
              <div className="text-[10px] uppercase tracking-[0.16em] text-white/35">
                outsee create · глобально
              </div>
            </div>
          </div>
        </div>
        <div className="flex items-center gap-2">
          <span className="hidden text-[11px] text-white/40 sm:inline">
            настройки и история общие для Studio
          </span>
          {projectId != null && (
            <span className="rounded-md border border-white/10 bg-white/[0.03] px-2 py-1 font-mono text-[10px] text-white/45">
              проект #{projectId}
            </span>
          )}
          <a
            href={outseeCreateUrl(mediaType, activeSlug)}
            target="_blank"
            rel="noreferrer"
            className="inline-flex items-center gap-1.5 rounded-lg border border-white/10 px-2.5 py-1.5 text-[11px] text-white/55 hover:text-white"
          >
            outsee.io
            <ExternalLink className="h-3 w-3" />
          </a>
        </div>
      </header>

      <div className="flex min-h-0 flex-1">
        {/* History + feed filter */}
        <aside className="flex w-[240px] shrink-0 flex-col border-r border-white/[0.06] bg-[#0c0c0c] lg:w-[280px]">
          <div className="flex items-center gap-2 border-b border-white/[0.06] px-3 py-2.5">
            <History className="h-3.5 w-3.5 text-white/40" />
            <span className="text-[11px] font-semibold uppercase tracking-[0.14em] text-white/40">
              История
            </span>
            <span className="ml-auto font-mono text-[10px] text-white/30">
              {historyItems.length}
            </span>
          </div>
          <div className="flex flex-wrap gap-1 border-b border-white/[0.06] p-2">
            {OUTSEE_FEED_TABS.map((t) => (
              <button
                key={t.id}
                type="button"
                onClick={() => setFeedKind(t.id)}
                className={cn(
                  "rounded-md px-2 py-1 text-[10px] font-semibold uppercase tracking-wider transition",
                  feedKind === t.id
                    ? "text-black"
                    : "bg-white/[0.04] text-white/45 hover:text-white/80",
                )}
                style={feedKind === t.id ? { backgroundColor: OUTSEE_ACCENT } : undefined}
              >
                {t.label}
              </button>
            ))}
          </div>
          <div className="flex-1 overflow-y-auto p-2">
            {historyQ.isLoading ? (
              <div className="flex items-center gap-2 px-2 py-6 text-[11px] text-white/40">
                <Loader2 className="h-3.5 w-3.5 animate-spin" />
                загрузка…
              </div>
            ) : historyItems.length === 0 ? (
              <div className="px-2 py-8 text-center text-[11px] text-white/35">
                Пока нет генераций ни в одном проекте
              </div>
            ) : (
              <div className="grid grid-cols-2 gap-1.5">
                {historyItems.map((item) => {
                  const active = selected?.id === item.id;
                  const isVideo = item.kind === "video";
                  return (
                    <button
                      key={item.id}
                      type="button"
                      onClick={() => setSelectedId(item.id)}
                      className={cn(
                        "group relative aspect-square overflow-hidden rounded-lg border bg-[#141414]",
                        active
                          ? "border-[rgba(209,254,23,0.55)] ring-1 ring-[rgba(209,254,23,0.35)]"
                          : "border-white/[0.06] hover:border-white/20",
                      )}
                      title={`${item.label}${item.project_slug ? ` · ${item.project_slug}` : ""}`}
                    >
                      {item.preview_url ? (
                        isVideo ? (
                          <video src={item.preview_url} muted playsInline className="h-full w-full object-cover" />
                        ) : (
                          // eslint-disable-next-line @next/next/no-img-element
                          <img src={item.preview_url} alt="" className="h-full w-full object-cover" />
                        )
                      ) : (
                        <div className="flex h-full items-center justify-center text-[9px] text-white/25">
                          {item.kind}
                        </div>
                      )}
                      <div className="absolute inset-x-0 bottom-0 bg-gradient-to-t from-black/85 to-transparent px-1.5 py-1">
                        <div className="truncate font-mono text-[9px] text-white/70">{item.label}</div>
                        {item.project_slug && (
                          <div className="truncate text-[8px] text-white/40">{item.project_slug}</div>
                        )}
                      </div>
                    </button>
                  );
                })}
              </div>
            )}
          </div>
        </aside>

        {/* Result + dock */}
        <section className="relative flex min-w-0 flex-1 flex-col">
          <div className="flex items-center justify-between px-4 pb-1 pt-3 lg:px-6">
            <h2 className="flex items-center gap-2 text-sm font-bold text-white lg:text-base">
              <Sparkles className="h-4 w-4" style={{ color: OUTSEE_ACCENT }} />
              Результат генерации
            </h2>
          </div>
          <div className="flex min-h-0 flex-1 items-center justify-center px-4 pb-[230px] lg:px-6">
            {selected?.preview_url ? (
              selected.kind === "video" ? (
                <video
                  src={selected.preview_url}
                  controls
                  className="max-h-[calc(100vh-300px)] max-w-full rounded-xl border border-white/[0.06] bg-black"
                />
              ) : selected.kind === "audio" ? (
                <div className="flex w-full max-w-md flex-col items-center gap-4 rounded-2xl border border-white/[0.06] bg-white/[0.02] p-8">
                  <Music className="h-8 w-8 text-white/40" />
                  <div className="text-sm text-white/70">{selected.label}</div>
                  <audio src={selected.preview_url} controls className="w-full" />
                </div>
              ) : (
                // eslint-disable-next-line @next/next/no-img-element
                <img
                  src={selected.preview_url}
                  alt=""
                  className="max-h-[calc(100vh-300px)] max-w-full rounded-xl border border-white/[0.06] object-contain"
                />
              )
            ) : (
              <div className="flex w-full max-w-xs flex-col items-center gap-4 rounded-2xl border border-white/[0.06] bg-white/[0.02] px-6 py-10 text-center">
                <ImageIcon className="h-8 w-8 text-white/30" />
                <div className="text-sm text-white/70">Нет результата</div>
                <div className="text-[12px] text-white/40">
                  Выбери тип слева (Фото / Видео / Аудио), модель в списке и сохрани настройки.
                </div>
              </div>
            )}
          </div>

          {/* prompt dock + vertical type toggle */}
          <div className="absolute bottom-0 left-0 right-0 z-10 px-3 pb-3 lg:px-5 lg:pb-4">
            <div className="flex items-end gap-2">
              {/* cs-typetoggle */}
              <div className="flex shrink-0 flex-col gap-2">
                {OUTSEE_TYPE_TABS.map((t) => {
                  const active = mediaType === t.id;
                  return (
                    <button
                      key={t.id}
                      type="button"
                      onClick={() => {
                        setMediaType(t.id);
                        setFeedKind(t.id);
                        setModelOpen(false);
                      }}
                      aria-pressed={active}
                      className={cn(
                        "flex min-w-[72px] flex-col items-center gap-1 rounded-xl border px-2.5 py-2.5 transition",
                        active
                          ? "border-[rgba(209,254,23,0.45)] bg-[rgba(209,254,23,0.12)] text-[rgba(209,254,23,1)]"
                          : "border-white/10 bg-[#171717] text-white/45 hover:text-white/80",
                      )}
                    >
                      <TypeIcon id={t.id} />
                      <span className="font-mono text-[10px] font-bold uppercase tracking-[0.08em]">
                        {t.label}
                      </span>
                    </button>
                  );
                })}
              </div>

              <div
                className="min-w-0 flex-1 border border-white/[0.08] bg-[#171717] shadow-[0_12px_40px_rgba(0,0,0,0.55)]"
                style={{ borderRadius: 16 }}
              >
                <div className="px-3 pt-3 lg:px-4">
                  <textarea
                    value={prompt}
                    onChange={(e) => setPrompt(e.target.value)}
                    placeholder={
                      mediaType === "audio"
                        ? "Текст / описание трека…"
                        : mediaType === "video"
                          ? "Опишите видео…"
                          : "Опишите изображение…"
                    }
                    rows={3}
                    className="w-full resize-none bg-transparent text-[13px] leading-relaxed text-white/90 placeholder:text-white/30 focus:outline-none"
                  />
                </div>

                <div className="flex flex-wrap items-end gap-2 border-t border-white/[0.06] px-3 py-2.5 lg:px-4">
                  <div className="relative" ref={modelRef}>
                    <ChipButton
                      active={modelOpen}
                      onClick={() => {
                        setModelOpen((v) => !v);
                        setOpenChip(null);
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
                        selectedSlug={activeSlug}
                        onSelect={(slug) => {
                          if (mediaType === "image") setImageSlug(slug);
                          else if (mediaType === "video") setVideoSlug(slug);
                          else setAudioSlug(slug);
                          applyModelDefaults(slug, mediaType);
                          setModelOpen(false);
                        }}
                      />
                    )}
                  </div>

                  {mediaType === "video" && videoModel.chips.includes("orientation") && (
                    <div className="inline-flex gap-0.5 rounded-full border border-white/10 bg-[#1a1a1a] p-0.5">
                      {(["video", "image"] as const).map((o) => (
                        <button
                          key={o}
                          type="button"
                          onClick={() => setOrientation(o)}
                          className={cn(
                            "rounded-full px-2.5 py-1 text-[11px] font-medium",
                            orientation === o
                              ? "bg-[rgba(209,254,23,0.15)] text-[rgba(209,254,23,1)]"
                              : "text-white/45",
                          )}
                        >
                          {o === "video" ? "По видео" : "По картинке"}
                        </button>
                      ))}
                    </div>
                  )}

                  {mediaType === "video" && videoModel.chips.includes("quality") && (
                    <div className="inline-flex gap-0.5 rounded-full border border-white/10 bg-[#1a1a1a] p-0.5">
                      {chipOptions(videoSlug, "quality").map((q) => (
                        <button
                          key={q}
                          type="button"
                          onClick={() => setMotionQuality(q)}
                          className={cn(
                            "rounded-full px-2.5 py-1 font-mono text-[11px] uppercase",
                            motionQuality === q
                              ? "bg-[rgba(209,254,23,0.15)] text-[rgba(209,254,23,1)]"
                              : "text-white/45",
                          )}
                        >
                          {q}
                        </button>
                      ))}
                    </div>
                  )}

                  {dockChips.map((chip) => {
                    if (chip === "audio") {
                      return (
                        <button
                          key="audio"
                          type="button"
                          onClick={() => setGenerateAudio((v) => !v)}
                          className={cn(
                            "inline-flex h-9 items-center gap-1.5 rounded-xl border px-2.5 text-[12px] font-medium",
                            generateAudio
                              ? "border-[rgba(209,254,23,0.35)] bg-[rgba(209,254,23,0.10)]"
                              : "border-white/10 bg-[#222] text-white/70",
                          )}
                        >
                          {OUTSEE_CHIP_LABELS.audio}
                        </button>
                      );
                    }
                    if (chip === "instrumental") {
                      return (
                        <button
                          key="instrumental"
                          type="button"
                          onClick={() => setInstrumental((v) => !v)}
                          className={cn(
                            "inline-flex h-9 items-center gap-1.5 rounded-xl border px-2.5 text-[12px] font-medium",
                            !instrumental
                              ? "border-[rgba(209,254,23,0.35)] bg-[rgba(209,254,23,0.10)]"
                              : "border-white/10 bg-[#222] text-white/70",
                          )}
                          title="Вокал on = не instrumental"
                        >
                          {OUTSEE_CHIP_LABELS.instrumental}
                          <span className="font-mono text-[10px] text-white/40">
                            {instrumental ? "off" : "on"}
                          </span>
                        </button>
                      );
                    }

                    const opts = chipOptions(activeSlug, chip);
                    if (!opts.length) return null;

                    let display = aspect;
                    let onSelect = setAspect;
                    if (chip === "resolution") {
                      display = mediaType === "image" ? resolution : videoResolution;
                      onSelect = mediaType === "image" ? setResolution : setVideoResolution;
                    } else if (chip === "detail") {
                      display = detailLabel(detail);
                      onSelect = setDetail;
                    } else if (chip === "duration") {
                      display = `${duration}с`;
                      onSelect = setDuration;
                    }

                    const options =
                      chip === "detail"
                        ? OUTSEE_DETAIL_LEVELS.map((d) => ({
                            id: d.id,
                            label: d.label,
                            hint: d.hint,
                          }))
                        : chip === "duration"
                          ? opts.map((d) => ({ id: d, label: `${d}с` }))
                          : opts.map((o) => ({ id: o, label: o }));

                    return (
                      <OptionDropdown
                        key={chip}
                        label={OUTSEE_CHIP_LABELS[chip] || chip}
                        value={display}
                        open={openChip === chip}
                        onOpenChange={(v) => {
                          setOpenChip(v ? chip : null);
                          if (v) setModelOpen(false);
                        }}
                        options={options}
                        onSelect={onSelect}
                        mono={chip !== "detail"}
                      />
                    );
                  })}

                  {supportsRelax(activeSlug, mediaType === "audio" ? "image" : mediaType) &&
                    mediaType !== "audio" && (
                      <LimitToggle
                        on={mediaType === "image" ? relax : videoRelax}
                        onChange={mediaType === "image" ? setRelax : setVideoRelax}
                      />
                    )}

                  <div className="ml-auto flex flex-wrap items-center gap-2">
                    <button
                      type="button"
                      disabled={saveGlobal.isPending}
                      onClick={() => saveGlobal.mutate()}
                      className="rounded-xl border border-white/10 bg-white/[0.04] px-3 py-2.5 text-[11px] font-medium text-white/70 hover:bg-white/[0.08] disabled:opacity-40"
                    >
                      {saveGlobal.isPending ? "…" : "Сохранить"}
                    </button>
                    <button
                      type="button"
                      disabled={applyToProject.isPending || projectId == null}
                      onClick={() => applyToProject.mutate()}
                      className="rounded-xl border border-white/10 bg-white/[0.04] px-3 py-2.5 text-[11px] font-medium text-white/70 hover:bg-white/[0.08] disabled:opacity-40"
                      title="Скопировать глобальные настройки в выбранный проект"
                    >
                      В проект
                    </button>
                    <button
                      type="button"
                      disabled={runStep.isPending || projectId == null || mediaType === "audio"}
                      onClick={() => runStep.mutate()}
                      className="inline-flex min-w-[140px] items-center justify-center gap-1.5 rounded-xl px-4 py-2.5 text-[12px] font-semibold text-black transition hover:brightness-110 disabled:opacity-40"
                      style={{ backgroundColor: OUTSEE_ACCENT }}
                    >
                      {runStep.isPending ? (
                        <>
                          <Loader2 className="h-3.5 w-3.5 animate-spin" />
                          …
                        </>
                      ) : (
                        "Генерировать"
                      )}
                    </button>
                  </div>
                </div>
              </div>
            </div>
          </div>
        </section>
      </div>
    </div>
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
        "inline-flex h-9 items-center gap-2 rounded-xl border px-2.5",
        on ? "border-amber-400/35 bg-amber-500/10" : "border-white/10 bg-[#1a1a1a]",
      )}
    >
      <span className={cn("text-xs font-semibold", on ? "text-gray-200" : "text-gray-400")}>
        Безлимит
      </span>
      <span className={cn("relative h-4 w-8 rounded-full", on ? "bg-amber-500" : "bg-zinc-600")}>
        <span
          className={cn(
            "absolute top-0.5 h-3 w-3 rounded-full bg-white transition-all",
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
          className="absolute bottom-full left-0 z-[1000] mb-1 max-h-56 overflow-y-auto rounded-xl border p-1.5 shadow-2xl"
          style={{ backgroundColor: "#1a1a1a", borderColor: "rgba(255,255,255,0.1)", minWidth: 140 }}
        >
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
                className="flex w-full items-center justify-between rounded-lg px-2.5 py-2 text-left text-[12px] hover:bg-white/[0.06]"
                style={{
                  background: active ? "rgba(209,254,23,0.10)" : undefined,
                  color: active ? OUTSEE_ACCENT : "white",
                }}
              >
                <span className={cn(mono && "font-mono")}>{opt.label}</span>
                {opt.hint && <span className="text-[10px] text-white/35">{opt.hint}</span>}
              </button>
            );
          })}
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
  const title =
    mediaType === "image"
      ? "Модели изображений"
      : mediaType === "video"
        ? "Модели видео"
        : "Модели аудио";
  const models = pickerModelsForType(mediaType);

  return (
    <div
      className="absolute bottom-full left-0 z-50 mb-3.5 flex max-h-[82vh] flex-col overflow-hidden rounded-2xl border border-white/10 shadow-2xl"
      style={{
        backgroundColor: "#141414",
        width: mediaType === "video" ? 580 : mediaType === "audio" ? 420 : 460,
      }}
      role="dialog"
      aria-label={title}
      onPointerDown={(e) => e.stopPropagation()}
    >
      <div className="border-b border-white/[0.06] px-3 py-2.5">
        <span className="text-[12px] font-semibold text-white/80">{title}</span>
        <span className="ml-2 font-mono text-[10px] text-white/35">{models.length}</span>
      </div>
      <div
        className="grid gap-1.5 overflow-y-auto p-2"
        style={{
          gridTemplateColumns: mediaType === "audio" ? "1fr" : "repeat(2, minmax(0, 1fr))",
          minHeight: 0,
        }}
      >
        {models.map((m) => {
          const active = m.slug === selectedSlug;
          const badge = m.isTop
            ? { tone: "top" as const, label: "ТОП" }
            : m.isNew
              ? { tone: "new" as const, label: "НОВОЕ" }
              : null;
          return (
            <button
              key={m.slug}
              type="button"
              onClick={() => onSelect(m.slug)}
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
