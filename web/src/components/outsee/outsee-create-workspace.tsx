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
  isGrsaiWiredSlug,
  outseeCreateUrl,
  pickerModelsForType,
  slugToStudioId,
  toGrsaiVideoModel,
  type OutseeChip,
  type OutseeFeedKind,
  type OutseeMediaType,
} from "@/lib/outsee-catalog";
import { estimateCreatePrice } from "@/lib/create-pricing";

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
  path?: string | null;
  label: string;
  project_id: number | null;
  project_slug: string | null;
  prompt: string | null;
  status?: string | null;
  job_id?: string | null;
  error?: string | null;
  model?: string | null;
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
  const [videoResolution, setVideoResolution] = useState("1080p");
  const [duration, setDuration] = useState("5");
  const [generateAudio, setGenerateAudio] = useState(false);
  const [orientation, setOrientation] = useState<"video" | "image">("video");
  const [motionQuality, setMotionQuality] = useState("std");
  const [instrumental, setInstrumental] = useState(false);
  const [prompt, setPrompt] = useState("");
  const [soraSize, setSoraSize] = useState<"small" | "large">("small");
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

  const grsaiStatusQ = useQuery({
    queryKey: ["grsai-status"],
    queryFn: api.getGrsaiStatus,
    enabled: open,
    staleTime: 30_000,
  });

  const outseeStatusQ = useQuery({
    queryKey: ["outsee-status"],
    queryFn: api.outseeStatus,
    enabled: open,
    staleTime: 30_000,
  });

  const historyQ = useQuery({
    queryKey: ["outsee-create-history", feedKind],
    queryFn: () => api.listOutseeCreateHistory(feedKind),
    enabled: open,
    refetchInterval: open ? 2500 : false,
  });

  const createQueueQ = useQuery({
    queryKey: ["create-queue"],
    queryFn: api.createQueue,
    enabled: open,
    refetchInterval: open ? 1200 : false,
  });

  const runningJobs = createQueueQ.data?.running ?? [];
  const waitingJobs = createQueueQ.data?.waiting ?? [];
  const queueCount =
    (createQueueQ.data?.total_active ?? 0) ||
    runningJobs.length + waitingJobs.length;

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
    setVideoResolution(String(s.video_resolution || "1080p"));
    setDuration(String(s.duration || "5"));
    setGenerateAudio(Boolean(s.generate_audio));
    setOrientation(s.orientation === "image" ? "image" : "video");
    setMotionQuality(String(s.motion_quality || "std"));
    setInstrumental(Boolean(s.instrumental));
    setPrompt(String(s.prompt || ""));
    setSoraSize(s.sora_size === "large" ? "large" : "small");
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
  const currentWired = isGrsaiWiredSlug(activeSlug, mediaType);
  const outseeConfigured = Boolean(outseeStatusQ.data?.configured);
  const grsaiConfigured = Boolean(grsaiStatusQ.data?.configured);

  /** Без UI-переключателя: ключ Outsee → Outsee; Sora/Kling → Grsai; иначе Grsai. */
  const autoProvider: "outsee" | "grsai" | null = useMemo(() => {
    if (mediaType === "audio") return null;
    const slug = activeSlug.toLowerCase();
    if (mediaType === "image") {
      if (outseeConfigured) return "outsee";
      if (grsaiConfigured) return "grsai";
      return null;
    }
    // video
    if (slug.includes("sora") || slug.includes("kling")) {
      if (grsaiConfigured) return "grsai";
      return null;
    }
    if (outseeConfigured && slug.includes("veo")) return "outsee";
    if (grsaiConfigured) return "grsai";
    if (outseeConfigured) return "outsee";
    return null;
  }, [mediaType, activeSlug, outseeConfigured, grsaiConfigured]);

  const maxParallel =
    autoProvider === "outsee"
      ? (createQueueQ.data?.max_parallel_outsee ?? 5)
      : autoProvider === "grsai"
        ? (createQueueQ.data?.max_parallel_grsai ?? 10)
        : (createQueueQ.data?.max_parallel ?? 5);

  const canApiDirect = autoProvider != null;
  const currentIcon =
    mediaType === "image"
      ? imageModel.icon
      : mediaType === "video"
        ? videoModel.icon
        : audioModel.icon;
  const currentCatalogPrice =
    mediaType === "image"
      ? imageModel.price
      : mediaType === "video"
        ? videoModel.price
        : audioModel.price;

  const quoteModel =
    mediaType === "video" && autoProvider === "grsai"
      ? toGrsaiVideoModel(videoSlug)
      : activeSlug;

  const quoteQ = useQuery({
    queryKey: [
      "grsai-quote",
      mediaType,
      quoteModel,
      resolution,
      duration,
      soraSize,
      currentCatalogPrice,
    ],
    queryFn: () =>
      api.grsaiQuote({
        media: mediaType,
        model: quoteModel,
        resolution,
        duration: Number(duration) || 10,
        size: soraSize,
        catalog_price: currentCatalogPrice,
      }),
    enabled: open,
    staleTime: 5_000,
  });

  const priceLabel =
    quoteQ.data?.label ||
    estimateCreatePrice({
      media: mediaType,
      model: quoteModel,
      resolution,
      duration: Number(duration) || 10,
      size: soraSize,
      catalogPrice: currentCatalogPrice,
    }).label;

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
    image_relax: false,
    video_resolution: videoResolution,
    video_relax: false,
    duration,
    generate_audio: generateAudio,
    orientation,
    motion_quality: motionQuality,
    instrumental,
    prompt,
    // провайдер выбирается автоматически при Generate — в UI не показываем
    image_provider: autoProvider === "outsee" ? "outsee" : "grsai",
    video_provider: autoProvider === "outsee" ? "outsee" : "grsai",
    sora_size: soraSize,
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
        body.image_relax = false;
      }
      if (vidStudio) {
        body.video_generator = vidStudio;
        const vr = videoResolution.toLowerCase();
        if (vr === "720p" || vr === "1080p") body.video_resolution = vr;
        body.video_relax = false;
      }
      return api.patchProject(projectId, body);
    },
    onSuccess: () => {
      if (projectId != null) qc.invalidateQueries({ queryKey: ["project", projectId] });
      toast.success("Настройки применены к проекту");
    },
    onError: (e) => toast.error(errorMessageFromUnknown(e)),
  });

  const [trackingJobs, setTrackingJobs] = useState<
    { provider: "grsai" | "outsee"; jobId: string; historyId: string }[]
  >([]);

  useEffect(() => {
    if (!trackingJobs.length) return;
    let cancelled = false;
    const tick = async () => {
      for (const t of [...trackingJobs]) {
        try {
          const job = await api.createJob(t.jobId);
          if (cancelled) return;
          qc.invalidateQueries({ queryKey: ["outsee-create-history"] });
          qc.invalidateQueries({ queryKey: ["create-queue"] });
          if (job.status === "done") {
            toast.success(`Готово · ${job.model || "файл"}`);
            if (job.history_id) setSelectedId(job.history_id);
            setTrackingJobs((prev) => prev.filter((x) => x.jobId !== t.jobId));
          } else if (job.status === "failed") {
            toast.error(job.error || "Генерация не удалась");
            setTrackingJobs((prev) => prev.filter((x) => x.jobId !== t.jobId));
          }
        } catch {
          /* job may not be ready yet */
        }
      }
    };
    void tick();
    const id = window.setInterval(() => void tick(), 1500);
    return () => {
      cancelled = true;
      window.clearInterval(id);
    };
  }, [trackingJobs, qc]);

  const createGenerate = useMutation({
    mutationFn: async () => {
      const text = prompt.trim();
      if (!text) throw new Error("Введите промпт");
      if (mediaType === "audio") {
        if (projectId == null) {
          throw new Error("Аудио — через шаг пайплайна: выберите проект");
        }
        await api.putOutseeCreateSettings(settingsPayload());
        await applyToProject.mutateAsync();
        return api.runProjectStep(projectId, "audio");
      }
      const provider = autoProvider;
      if (!provider) {
        throw new Error(
          "Нет API-ключа: задайте OUTSEE_API_KEY или GRSAI_API_KEY в .env и перезапустите Studio",
        );
      }
      // Settings не блокируют enqueue: параллельные клики иначе ломаются
      // на гонке записи outsee_create_settings.json.
      void api.putOutseeCreateSettings(settingsPayload()).catch(() => undefined);
      if (provider === "grsai") {
        if (!grsaiConfigured) {
          throw new Error("GRSAI_API_KEY не задан в .env");
        }
        const enqueued =
          mediaType === "video"
            ? await api.grsaiGenerate({
                prompt: text,
                model: toGrsaiVideoModel(videoSlug),
                aspect,
                media: "video",
                duration: Number(duration) || 10,
                size: soraSize,
              })
            : await api.grsaiGenerate({
                prompt: text,
                model: imageSlug,
                aspect,
                resolution,
                media: "image",
              });
        return { ...enqueued, provider: "grsai" as const };
      }
      if (!outseeConfigured) {
        throw new Error("OUTSEE_API_KEY не задан в .env");
      }
      const enqueued =
        mediaType === "video"
          ? await api.outseeGenerate({
              prompt: text,
              media: "video",
              model: videoSlug,
              aspect,
              resolution: videoResolution,
              duration: Number(duration) || 5,
              project_id: projectId,
            })
          : await api.outseeGenerate({
              prompt: text,
              media: "image",
              model: imageSlug,
              aspect,
              resolution,
              project_id: projectId,
            });
      return { ...enqueued, provider: "outsee" as const };
    },
    onSuccess: (res) => {
      if (res && typeof res === "object" && "job_id" in res && res.job_id) {
        const r = res as {
          job_id: string;
          history_id: string;
          queue?: number;
          waiting_count?: number;
          running_count?: number;
          status?: string;
          queue_position?: number | null;
          provider: "grsai" | "outsee";
        };
        if (r.history_id) setSelectedId(r.history_id);
        setTrackingJobs((prev) => [
          ...prev.filter((x) => x.jobId !== r.job_id),
          { provider: r.provider, jobId: r.job_id, historyId: r.history_id },
        ]);
        qc.invalidateQueries({ queryKey: ["outsee-create-history"] });
        qc.invalidateQueries({ queryKey: ["create-queue"] });
        const wait = r.waiting_count ?? 0;
        const run = r.running_count ?? 0;
        if (r.status === "queued" || (r.queue_position != null && r.queue_position > 0)) {
          toast.message(
            `Ожидание #${r.queue_position ?? wait} · в работе ${run}/${maxParallel}`,
          );
        } else {
          toast.message(`В работе · ${run}/${maxParallel}`);
        }
        return;
      }
      toast.success("Шаг запущен");
      qc.invalidateQueries({ queryKey: ["outsee-create-history"] });
    },
    onError: (e) => {
      toast.error(errorMessageFromUnknown(e));
    },
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
            {queueCount > 0 && (
              <span
                className="inline-flex items-center gap-1 rounded-md px-1.5 py-0.5 text-[9px] font-bold uppercase tracking-wider text-black"
                style={{ backgroundColor: OUTSEE_ACCENT }}
                title={`В работе ${runningJobs.length}/${maxParallel}, ожидание ${waitingJobs.length}`}
              >
                <Loader2 className="h-2.5 w-2.5 animate-spin" />
                {runningJobs.length}·{waitingJobs.length}
              </span>
            )}
            <span className="ml-auto font-mono text-[10px] text-white/30">
              {historyItems.length}
            </span>
          </div>
          <div className="space-y-2 border-b border-white/[0.06] px-2 py-2">
            <div>
              <div className="mb-1 px-1 text-[9px] font-semibold uppercase tracking-wider text-white/40">
                В работе · {runningJobs.length}/{maxParallel}
              </div>
              {runningJobs.length === 0 ? (
                <div className="rounded-lg border border-dashed border-white/10 px-2 py-2 text-[9px] text-white/30">
                  нет активных
                </div>
              ) : (
                <div className="space-y-1">
                  {runningJobs.map((j) => (
                    <button
                      key={j.job_id}
                      type="button"
                      onClick={() => j.history_id && setSelectedId(j.history_id)}
                      className="flex w-full items-center gap-2 rounded-lg border border-[rgba(209,254,23,0.25)] bg-[rgba(209,254,23,0.06)] px-2 py-1.5 text-left"
                    >
                      <Loader2
                        className="h-3 w-3 shrink-0 animate-spin"
                        style={{ color: OUTSEE_ACCENT }}
                      />
                      <div className="min-w-0 flex-1">
                        <div className="truncate font-mono text-[10px] text-white/80">
                          {j.model || j.media}
                        </div>
                        <div className="truncate text-[9px] text-white/40">
                          {j.prompt_preview || "генерация…"}
                        </div>
                      </div>
                    </button>
                  ))}
                </div>
              )}
            </div>
            <div>
              <div className="mb-1 px-1 text-[9px] font-semibold uppercase tracking-wider text-white/40">
                Ожидание · {waitingJobs.length}
              </div>
              {waitingJobs.length === 0 ? (
                <div className="rounded-lg border border-dashed border-white/10 px-2 py-2 text-[9px] text-white/30">
                  очередь пуста
                </div>
              ) : (
                <div className="space-y-1">
                  {waitingJobs.map((j) => (
                    <button
                      key={j.job_id}
                      type="button"
                      onClick={() => j.history_id && setSelectedId(j.history_id)}
                      className="flex w-full items-center gap-2 rounded-lg border border-white/10 bg-white/[0.03] px-2 py-1.5 text-left"
                    >
                      <span className="inline-flex h-5 w-5 shrink-0 items-center justify-center rounded-md bg-white/10 font-mono text-[9px] text-white/60">
                        #{j.queue_position ?? "—"}
                      </span>
                      <div className="min-w-0 flex-1">
                        <div className="truncate font-mono text-[10px] text-white/70">
                          {j.model || j.media}
                        </div>
                        <div className="truncate text-[9px] text-white/35">
                          {j.prompt_preview || "в очереди"}
                        </div>
                      </div>
                    </button>
                  ))}
                </div>
              )}
            </div>
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
                Пока нет файлов. Результаты сохраняются в{" "}
                <span className="font-mono text-white/50">data/generations/</span>
              </div>
            ) : (
              <div className="grid grid-cols-2 gap-1.5">
                {historyItems.map((item) => {
                  const active = selected?.id === item.id;
                  const isVideo = item.kind === "video";
                  const waitJob = waitingJobs.find((j) => j.history_id === item.id);
                  const runJob = runningJobs.find((j) => j.history_id === item.id);
                  const pending =
                    item.status === "queued" ||
                    item.status === "processing" ||
                    Boolean(waitJob || runJob);
                  const failed = item.status === "failed";
                  const pendingLabel = waitJob
                    ? `ожидание #${waitJob.queue_position ?? "—"}`
                    : runJob || item.status === "processing"
                      ? "в работе"
                      : item.status === "queued"
                        ? "в очереди"
                        : "генерация";
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
                      {item.preview_url && !pending ? (
                        isVideo ? (
                          <video src={item.preview_url} muted playsInline className="h-full w-full object-cover" />
                        ) : (
                          // eslint-disable-next-line @next/next/no-img-element
                          <img src={item.preview_url} alt="" className="h-full w-full object-cover" />
                        )
                      ) : (
                        <div className="flex h-full flex-col items-center justify-center gap-1.5 px-2 text-center">
                          {pending ? (
                            <Loader2
                              className="h-5 w-5 animate-spin"
                              style={{ color: OUTSEE_ACCENT }}
                            />
                          ) : failed ? (
                            <span className="text-[10px] font-semibold text-red-400">ошибка</span>
                          ) : (
                            <span className="text-[9px] text-white/25">{item.kind}</span>
                          )}
                          {pending && (
                            <span className="text-[9px] font-semibold uppercase tracking-wider text-white/55">
                              {pendingLabel}
                            </span>
                          )}
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
          <div className="flex min-h-0 flex-1 flex-col items-center justify-center gap-2 px-4 pb-[230px] lg:px-6">
            {selected?.preview_url &&
            selected.status !== "queued" &&
            selected.status !== "processing" ? (
              <>
                {selected.kind === "video" ? (
                  <video
                    src={selected.preview_url}
                    controls
                    className="max-h-[calc(100vh-320px)] max-w-full rounded-xl border border-white/[0.06] bg-black"
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
                    className="max-h-[calc(100vh-320px)] max-w-full rounded-xl border border-white/[0.06] object-contain"
                  />
                )}
                {selected.path && (
                  <div
                    className="max-w-full truncate px-2 font-mono text-[10px] text-white/35"
                    title={selected.path}
                  >
                    {selected.path}
                  </div>
                )}
              </>
            ) : selected &&
              (selected.status === "queued" || selected.status === "processing") ? (
              <div className="flex w-full max-w-sm flex-col items-center gap-4 rounded-2xl border border-white/[0.08] bg-white/[0.03] px-6 py-12 text-center">
                <Loader2
                  className="h-9 w-9 animate-spin"
                  style={{ color: OUTSEE_ACCENT }}
                />
                <div className="text-sm font-semibold text-white/85">
                  {selected.status === "queued" ? "В очереди" : "Генерация…"}
                </div>
                <div className="text-[12px] text-white/45">
                  {selected.model || selected.label}
                  {queueCount > 1 ? ` · очередь ${queueCount}` : ""}
                </div>
                {selected.prompt && (
                  <div className="line-clamp-3 max-w-full text-[11px] text-white/35">
                    {selected.prompt}
                  </div>
                )}
              </div>
            ) : selected?.status === "failed" ? (
              <div className="flex w-full max-w-sm flex-col items-center gap-3 rounded-2xl border border-red-500/30 bg-red-500/5 px-6 py-10 text-center">
                <div className="text-sm font-semibold text-red-300">Ошибка генерации</div>
                <div className="text-[12px] text-white/50">
                  {selected.error || "Не удалось получить файл"}
                </div>
              </div>
            ) : (
              <div className="flex w-full max-w-xs flex-col items-center gap-4 rounded-2xl border border-white/[0.06] bg-white/[0.02] px-6 py-10 text-center">
                <ImageIcon className="h-8 w-8 text-white/30" />
                <div className="text-sm text-white/70">Нет результата</div>
                <div className="text-[12px] text-white/40">
                  Файлы пишутся в{" "}
                  <span className="font-mono text-white/55">data/generations/</span> на этом
                  компьютере.
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
                      <span className="font-medium">
                        {currentWired ? (
                          <span className="mr-1 font-mono text-[rgba(209,254,23,1)]">+</span>
                        ) : null}
                        {currentName}
                      </span>
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

                  <div className="ml-auto flex flex-wrap items-center gap-2">
                    {mediaType === "video" &&
                      (videoSlug === "sora-2" ||
                        videoSlug === "sora2-portrait" ||
                        videoSlug === "sora2-landscape") && (
                        <div className="inline-flex gap-0.5 rounded-xl border border-white/10 bg-[#1a1a1a] p-0.5">
                          {(["small", "large"] as const).map((sz) => (
                            <button
                              key={sz}
                              type="button"
                              onClick={() => setSoraSize(sz)}
                              className={cn(
                                "rounded-lg px-2.5 py-1.5 font-mono text-[10px] uppercase",
                                soraSize === sz
                                  ? "bg-[rgba(209,254,23,0.15)] text-[rgba(209,254,23,1)]"
                                  : "text-white/40",
                              )}
                            >
                              {sz}
                            </button>
                          ))}
                        </div>
                      )}
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
                    <div
                      className="inline-flex h-9 items-center gap-1.5 rounded-xl border border-white/10 bg-[#1a1a1a] px-2.5 font-mono text-[11px] text-white/75"
                      title="1 токен = $0.10 (10¢). Цена за выбранные параметры."
                    >
                      <Coins className="h-3 w-3 text-[rgba(209,254,23,0.85)]" strokeWidth={2.5} />
                      <span>{priceLabel}</span>
                    </div>
                    <button
                      type="button"
                      disabled={
                        !prompt.trim() ||
                        (mediaType === "audio" && projectId == null) ||
                        (mediaType !== "audio" && !canApiDirect)
                      }
                      onClick={() => createGenerate.mutate()}
                      className="inline-flex min-w-[140px] items-center justify-center gap-1.5 rounded-xl px-4 py-2.5 text-[12px] font-semibold text-black transition hover:brightness-110 disabled:opacity-40"
                      style={{ backgroundColor: OUTSEE_ACCENT }}
                      title={
                        !canApiDirect && mediaType !== "audio"
                          ? "Нужен OUTSEE_API_KEY или GRSAI_API_KEY в .env"
                          : `Сгенерировать (можно несколько параллельно, лимит ${maxParallel}) · ${priceLabel}`
                      }
                    >
                      {createGenerate.isPending ? (
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
          const wired = "grsaiWired" in m && Boolean(m.grsaiWired);
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
              {wired && (
                <span
                  className="absolute -top-1.5 left-2 z-10 rounded-md px-1.5 py-0.5 font-mono text-[11px] font-bold leading-none text-black"
                  style={{ backgroundColor: OUTSEE_ACCENT }}
                  title="Временно подключено через Grsai"
                >
                  +
                </span>
              )}
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
                  {wired ? (
                    <span className="mr-1 font-mono text-[rgba(209,254,23,1)]">+</span>
                  ) : null}
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
