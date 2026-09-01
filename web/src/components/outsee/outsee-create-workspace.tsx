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
  Check,
  ChevronDown,
  Clock,
  Coins,
  Copy,
  CornerDownLeft,
  Dices,
  Download,
  ExternalLink,
  FileText,
  History,
  ImageIcon,
  Layers,
  Link2,
  Loader2,
  Maximize2,
  Music,
  Paperclip,
  Play,
  Search,
  Send,
  Trash2,
  Video,
  X,
} from "lucide-react";
import { toast } from "sonner";
import { api } from "@/lib/api";
import type { KieField, KieModelSpec } from "@/lib/api";
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
  type OutseeChip,
  type OutseeFeedKind,
  type OutseeMediaType,
} from "@/lib/outsee-catalog";
import { estimateCreatePrice } from "@/lib/create-pricing";
import {
  estimateKie,
  kieChipFields,
  kieFileFields,
  kieMainTextField,
} from "@/lib/kie-pricing";

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
  raw_url?: string | null;
  path?: string | null;
  label: string;
  project_id: number | null;
  project_slug: string | null;
  prompt: string | null;
  status?: string | null;
  job_id?: string | null;
  error?: string | null;
  model?: string | null;
  elapsed_sec?: number | null;
  elapsed_label?: string | null;
  created_at?: string | null;
  started_at?: string | null;
  finished_at?: string | null;
  mtime?: number | null;
  params?: Record<string, unknown> | null;
  reference_images?: string[] | null;
  first_frame_url?: string | null;
};

function readFileAsDataUrl(file: File): Promise<string> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(String(reader.result || ""));
    reader.onerror = () => reject(reader.error || new Error("read failed"));
    reader.readAsDataURL(file);
  });
}

function formatElapsedMinSec(totalSec: number | null | undefined): string {
  const n = Math.max(0, Math.round(Number(totalSec) || 0));
  const m = Math.floor(n / 60);
  const s = n % 60;
  return `${m} мин ${s} сек`;
}

export const STYLE_PRESETS = [
  { id: "none", label: "Без стиля", icon: "", suffix: "" },
  {
    id: "photo",
    label: "Фото",
    icon: "📸",
    suffix: ", professional 8k photography, hyperrealistic, sharp focus, natural lighting, highly detailed",
  },
  {
    id: "cinematic",
    label: "Кино",
    icon: "🎬",
    suffix: ", cinematic still, 35mm film, atmospheric lighting, dramatic depth of field, blockbuster movie aesthetic",
  },
  {
    id: "3d",
    label: "3D",
    icon: "🎨",
    suffix: ", 3d render, unreal engine 5, octane render, smooth lighting, volumetric raytracing, 8k",
  },
  {
    id: "anime",
    label: "Аниме",
    icon: "🍙",
    suffix: ", anime art style, vibrant colors, detailed line art, aesthetic masterpiece",
  },
  {
    id: "oil",
    label: "Живопись",
    icon: "🖌️",
    suffix: ", oil painting, masterwork, rich brushstrokes, expressive texture, classical fine art",
  },
  {
    id: "cyberpunk",
    label: "Киберпанк",
    icon: "🌆",
    suffix: ", cyberpunk aesthetic, neon glow, futuristic city, reflections, high-tech dark atmosphere",
  },
  {
    id: "fantasy",
    label: "Фэнтези",
    icon: "🌌",
    suffix: ", epic fantasy digital art, magical glowing atmosphere, ethereal lighting, mythical",
  },
];

export const RANDOM_PROMPTS = [
  "A majestic ancient Japanese temple surrounded by blooming pink cherry blossoms, serene koi pond with reflections of soft golden morning rays, hyperrealistic photography",
  "Futuristic cyberpunk Tokyo street at midnight, neon holographic advertisements reflecting on wet asphalt, volumetric steam, cinematic depth of field, 8k octane render",
  "Cozy warm coffee shop on a rainy autumn day in Paris, steam rising from ceramic cup, wooden table by rain-streaked window with view of street lamps, photorealistic",
  "Epic fantasy dragon perched atop a towering snowy mountain peak during a dramatic sunset, golden sunlight through clouds, intricate scales, mythological masterpiece",
  "Close-up macro photography of an iridescent hummingbird drinking nectar from a vibrant exotic flower, dewdrops, shallow depth of field, sharp focus, 8k",
  "Interior of a luxurious futuristic space station greenhouse with view of planet Earth in background, bioluminescent plants, architectural elegance, unreal engine 5",
  "Cinematic portrait of a wise old Nordic blacksmith with braided beard, glowing forge embers, sparks flying, textured leather apron, dramatic Rembrandt lighting",
  "A breathtaking turquoise alpine lake nestled inside granite mountains, wildflower meadow in foreground, crisp morning air, National Geographic award-winning photography",
  "Steampunk airship soaring through fluffy cumulus clouds at golden hour, brass gears, copper detailing, propellers spinning, adventure aesthetic",
  "An ancient library with towering mahogany bookshelves reaching into shadows, floating glowing magical dust motes, stained glass window casting colorful light",
  "Sleek modern sports car speeding along a winding coastal highway at dusk, motion blur, taillight trails, sunset reflection on metallic paint",
  "Enchanted bioluminescent forest at night, glowing mushrooms, ethereal spirits floating among giant mossy ancient trees, fantasy concept art",
  "A cyberpunk samurai warrior standing in the rain on a skyscraper rooftop, glowing katana, neon city skyline in background, cinematic wide shot",
  "Minimalist architectural desert villa with an infinity pool reflecting the starry night sky and Milky Way, warm interior ambient lights, 8k architectural render",
  "Cute fluffy baby red panda playing in fresh autumn leaves, soft natural lighting, high detail fur, adorable expression, professional wildlife photography",
  "A colossal ancient stone titan half-buried in sand dunes, ancient glyphs glowing faintly, desert wind blowing sand, epic cinematic landscape",
  "A majestic white stag with glowing crystalline antlers standing in an ethereal moonlit clearing, mist swirling around hooves, fantasy masterwork",
  "A futuristic hypercar prototype parked inside a minimalist concrete hangar, dramatic studio lighting, carbon fiber body, aerodynamic curves",
  "Makoto Shinkai aesthetic anime scene of two friends standing on a hillside overlooking a coastal Japanese town under a starry night sky with falling meteors",
  "A mysterious masked alchemist brewing glowing purple potions in a cluttered medieval apothecary filled with dried herbs, glass retorts, and ancient grimoires",
  "Dramatic ocean storm at sunset, towering turquoise waves crashing against rugged black volcanic cliffs, golden sea spray, long exposure photography",
  "A cozy Scandinavian log cabin surrounded by deep pine snowdrifts, warm amber light glowing from windows, vibrant green northern lights aurora borealis above",
  "A cybernetic geisha with delicate porcelain faceplates and intricate glowing gold circuits, wearing a high-fashion holographic kimono, studio portrait",
  "A breathtaking underwater coral reef teeming with vibrant tropical fish, sea turtles gliding through crystal clear sunlit water, wide angle photography",
  "An opulent Venetian masquerade ballroom at midnight, grand crystal chandeliers, masked dancers in elaborate baroque gowns, golden reflections on marble floor",
  "A lone astronaut discovering an ancient alien monolith glowing with violet hieroglyphs on Mars, red dust storm swirling, double moons on horizon",
  "A mystical waterfall cascading into a crystal clear hidden grotto illuminated by glowing azure crystals, lush ferns, ethereal fantasy environment",
  "Vintage 1960s Italian cafe terrace in Positano overlooking the Amalfi coastline, espresso cup on marble table, blooming bougainvillea, warm Mediterranean sunlight",
  "A fierce Viking shieldmaiden with braided blonde hair and war paint standing on the prow of a dragon longship in a misty fjord, cinematic film still",
  "A futuristic solar punk city with vertical botanical gardens covering skyscrapers, elevated glass sky trains, clean solar canals, bright optimistic daylight",
  "A macro photograph of an intricate mechanical watch movement, exposed tourbillon, polished ruby jewels, Damascus steel bridges, extreme sharp detail",
  "A whimsical treehouse village connected by glowing rope bridges nestled in colossal redwood trees at dusk, fairy lights, lanterns, magical fantasy vibe",
  "A hyperrealistic portrait of an Ethiopian woman wearing traditional beaded silver jewelry and embroidered scarf, warm golden hour sunlight, sharp eye focus",
  "A dramatic volcanic eruption at night, glowing red lava rivers flowing down black basalt slopes into the sea, thunderous ash cloud with lightning bolts",
  "A cyberpunk hacker workstation surrounded by floating transparent holographic screens, neon blue and magenta reflections, cables, coffee cup, nighttime room",
  "An enchanted crystal cave with giant luminous amethyst clusters growing from cavern walls, reflective underground river, ethereal dreamlike atmosphere",
  "A sleek luxury yacht sailing through turquoise Caribbean waters near secluded white sand island, aerial drone view, sun glinting on clear water",
  "A formidable knight in ornate black and gold armor standing guard in a gothic throne room, sunlight streaming through tall archways, cinematic dust motes",
  "A hyper-detailed slice of artisan strawberry shortcake on a vintage porcelain plate, whipped cream, glazed berries, fork, warm bakery background",
  "A futuristic mech warrior standing in a ruined battlefield covered in snow, weathered steel armor, glowing blue optics, smoke rising from vents",
  "A peaceful zen rock garden at sunrise, perfectly raked sand patterns, bonsai pine tree, dewdrops on smooth river stones, soft tranquil morning light",
  "A dark fantasy necromancer summoning green spectral flames from an ancient tomb, glowing runic circle on stone floor, cinematic shadows and mist",
  "A vibrant bustling Moroccan bazaar at twilight, hanging brass lanterns casting intricate shadows, colorful spice pyramids, woven carpets, rich textures",
  "A majestic bald eagle soaring over the Grand Canyon at sunrise, golden sunbeams piercing deep red rock canyons, crisp photographic detail",
  "A futuristic orbital space elevator extending from an ocean platform into the starry cosmos, aurora borealis curving around Earth horizon",
  "A romantic cobblestone street in old Prague at blue hour after rain, glowing streetlamps reflecting in puddles, Gothic church spires in background",
  "A stunning studio portrait of a silver-haired elf queen with intricate diamond crown, delicate ear cuffs, deep sapphire velvet gown, soft cinematic lighting",
  "A whimsical greenhouse conservatory filled with glowing giant mushrooms, miniature floating jellyfish plants, brass Victorian framing, magical realism",
  "An epic science fiction starship armada dropping out of warp speed near a ringed gas giant planet, engine plasma trails, immense cosmic scale",
  "A cute fluffy kitten sleeping curled up inside a wizard hat surrounded by glowing spell books and spilled star glitter, warm candlelight, cozy fantasy art"
];

function downloadMediaFile(
  url: string,
  filename: string,
  format: "png" | "jpg" | "webp" | "mp4" | "mp3" | string = "png",
  path?: string | null,
) {
  const params = new URLSearchParams();
  if (path) params.set("path", path);
  if (url) params.set("url", url);
  params.set("format", format);
  params.set("filename", filename);

  const downloadUrl = `/api/outsee-create/download?${params.toString()}`;
  const a = document.createElement("a");
  a.href = downloadUrl;
  a.download = `${filename.replace(/\.[^/.]+$/, "")}.${format}`;
  document.body.appendChild(a);
  a.click();
  document.body.removeChild(a);
}

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
  const [stylePreset, setStylePreset] = useState("none");
  const [negativePrompt, setNegativePrompt] = useState("");
  const [showNegativePrompt, setShowNegativePrompt] = useState(false);
  const [downloadFormat, setDownloadFormat] = useState<"png" | "jpg" | "webp">("png");
  const [batchCount, setBatchCount] = useState<1 | 2 | 4>(1);
  const [isEnhancingPrompt, setIsEnhancingPrompt] = useState(false);
  const [soraSize, setSoraSize] = useState<"small" | "large">("small");
  const [firstFrameDataUrl, setFirstFrameDataUrl] = useState<string | null>(null);
  const [lastFrameDataUrl, setLastFrameDataUrl] = useState<string | null>(null);
  const [firstFrameName, setFirstFrameName] = useState<string | null>(null);
  const [lastFrameName, setLastFrameName] = useState<string | null>(null);
  const [referenceImages, setReferenceImages] = useState<
    { id: string; url: string; name: string }[]
  >([]);
  const [modelOpen, setModelOpen] = useState(false);
  const [openChip, setOpenChip] = useState<string | null>(null);
  const [lightboxOpen, setLightboxOpen] = useState(false);
  const [kieValues, setKieValues] = useState<Record<string, unknown>>({});
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [settingsHydrated, setSettingsHydrated] = useState(false);
  const modelRef = useRef<HTMLDivElement>(null);
  const firstFrameInputRef = useRef<HTMLInputElement>(null);
  const lastFrameInputRef = useRef<HTMLInputElement>(null);
  const multiRefInputRef = useRef<HTMLInputElement>(null);

  const settingsQ = useQuery({
    queryKey: ["outsee-create-settings"],
    queryFn: api.getOutseeCreateSettings,
    enabled: open,
  });

  const outseeStatusQ = useQuery({
    queryKey: ["outsee-status"],
    queryFn: api.outseeStatus,
    enabled: open,
    staleTime: 30_000,
  });

  const createQueueQ = useQuery({
    queryKey: ["create-queue"],
    queryFn: api.createQueue,
    enabled: open,
    refetchInterval: open ? 1200 : false,
  });

  const kieCatalogQ = useQuery({
    queryKey: ["kie-catalog"],
    queryFn: api.kieCatalog,
    enabled: open,
    staleTime: 60_000,
  });
  const kieCreditsQ = useQuery({
    queryKey: ["kie-credits"],
    queryFn: api.kieCredits,
    enabled: open,
    refetchInterval: open ? 60_000 : false,
  });

  const runningJobs = createQueueQ.data?.running ?? [];
  const waitingJobs = createQueueQ.data?.waiting ?? [];
  const queueCount =
    (createQueueQ.data?.total_active ?? 0) ||
    runningJobs.length + waitingJobs.length;
  const historyBusy = queueCount > 0;

  const historyQ = useQuery({
    queryKey: ["outsee-create-history", feedKind],
    queryFn: () =>
      api.listOutseeCreateHistory(feedKind, { scope: "create", limit: 60 }),
    enabled: open,
    // Не долбим диск/сеть: часто только пока есть очередь, иначе редко.
    refetchInterval: open ? (historyBusy ? 3000 : 12_000) : false,
  });

  useEffect(() => {
    if (!open || !settingsQ.data || settingsHydrated) return;
    const s = settingsQ.data;
    const mt = (s.media_type as OutseeMediaType) || "image";
    const rawImg = String(s.image_slug || "gpt-image-2").replace("kie:", "");
    if (rawImg === "gpt-image-2" || rawImg === "nano-banana-2") {
      setImageSlug(rawImg);
    } else {
      setImageSlug(rawImg.startsWith("kie:") ? rawImg : `kie:${rawImg}`);
    }
    const rawVid = String(s.video_slug || "veo-3-1-lite").replace("kie:", "");
    if (rawVid === "veo-3-1-lite" || rawVid === "veo-3-1") {
      setVideoSlug("veo-3-1-lite");
    } else {
      setVideoSlug(rawVid.startsWith("kie:") ? rawVid : `kie:${rawVid}`);
    }
    const rawAud = String(s.audio_slug || "kie:suno-music");
    setAudioSlug(rawAud.startsWith("kie:") ? rawAud : rawAud === "suno-5-5" ? "kie:suno-music" : `kie:${rawAud}`);
    setAspect(String(s.aspect || "16:9"));
    setResolution(String(s.image_resolution || "2K"));
    setDetail(String(s.image_quality || "medium"));
    setVideoResolution(String(s.video_resolution || "1080p"));
    setDuration(String(s.duration || "5"));
    const restoredVideo = rawVid;
    setGenerateAudio(
      restoredVideo === "veo-3-1-lite" ? false : Boolean(s.generate_audio),
    );
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

  const [nowTs, setNowTs] = useState(() => Date.now());
  useEffect(() => {
    const id = window.setInterval(() => setNowTs(Date.now()), 1000);
    return () => window.clearInterval(id);
  }, []);

  const getLiveElapsed = (
    item?:
      | HistoryItem
      | {
          id?: string;
          job_id?: string | null;
          created_at?: string | null;
          started_at?: string | null;
          elapsed_sec?: number | null;
          elapsed_label?: string | null;
          status?: string | null;
        }
      | null,
  ) => {
    if (!item) return "0 мин 00 сек";
    const isPending = item.status === "queued" || item.status === "processing";
    if (!isPending) {
      if (item.elapsed_label) return item.elapsed_label;
      if (item.elapsed_sec != null) return formatElapsedMinSec(item.elapsed_sec);
    }
    // Проверяем соответствующую задачу в активной очереди
    const activeJob =
      runningJobs.find(
        (j) =>
          (item.id && j.history_id === item.id) ||
          (item.job_id && j.job_id === item.job_id),
      ) ||
      waitingJobs.find(
        (j) =>
          (item.id && j.history_id === item.id) ||
          (item.job_id && j.job_id === item.job_id),
      );
    const iso =
      activeJob?.created_at ||
      activeJob?.started_at ||
      item.created_at ||
      item.started_at;
    if (!iso) {
      if (item.elapsed_sec != null && item.elapsed_sec > 0) {
        return formatElapsedMinSec(item.elapsed_sec);
      }
      return "0 мин 01 сек";
    }
    try {
      const t0 = new Date(iso).getTime();
      const diffSec = Math.max(1, Math.floor((nowTs - t0) / 1000));
      return formatElapsedMinSec(diffSec);
    } catch {
      return item.elapsed_label || (item.elapsed_sec != null ? formatElapsedMinSec(item.elapsed_sec) : "0 мин 01 сек");
    }
  };

  const activeSlug =
    mediaType === "image" ? imageSlug : mediaType === "video" ? videoSlug : audioSlug;
  const imageModel = getImageModel(imageSlug);
  const videoModel = getVideoModel(videoSlug);
  const audioModel = getAudioModel(audioSlug);
  const dockChips = dockChipsForModel(activeSlug, mediaType);

  // ---- KIE: модель выбрана из общего пикера (slug "kie:<id>") ----
  const kieModels = useMemo(
    () =>
      (kieCatalogQ.data?.models ?? []).filter((m) => {
        const id = m.id.toLowerCase();
        // GPT Image 2, Nano Banana 2, Veo 3.1 Lite — строго Outsee
        if (id.includes("veo") || id.includes("gpt-image") || id.includes("banana")) {
          return false;
        }
        return true;
      }),
    [kieCatalogQ.data],
  );
  const kieModel = useMemo(() => {
    if (!activeSlug.startsWith("kie:")) return null;
    return kieModels.find((m) => m.id === activeSlug.slice(4)) ?? null;
  }, [activeSlug, kieModels]);
  const kieActive = kieModel != null;
  const kieTextField = kieModel ? kieMainTextField(kieModel) : null;

  const maxReferences = useMemo(() => {
    if (mediaType !== "image") return 0;
    const slug = activeSlug.toLowerCase();
    if (slug.includes("z-image")) return 0;
    if (kieActive && kieModel) {
      const refField = kieModel.fields.find(
        (f) =>
          f.kind === "images" ||
          ["image_urls", "image_input", "imageUrls", "images", "image_url", "imageUrl"].includes(f.name),
      );
      if (refField) return refField.max_items || (refField.kind === "images" ? 8 : 1);
      return 0;
    }
    return 8;
  }, [mediaType, activeSlug, kieActive, kieModel]);

  const kiePrice = useMemo(() => {
    if (!kieModel || !kieCatalogQ.data) return null;
    const vals = { ...kieValues };
    if (kieTextField) vals[kieTextField] = prompt;
    return estimateKie(kieModel, vals, kieCatalogQ.data.credit_usd);
  }, [kieModel, kieValues, prompt, kieTextField, kieCatalogQ.data]);

  useEffect(() => {
    // Сохраняем совместимые поля при смене модели KIE
    setKieValues((prev) => {
      const next: Record<string, unknown> = {};
      if (prev.aspect_ratio) next.aspect_ratio = prev.aspect_ratio;
      if (prev.resolution) next.resolution = prev.resolution;
      if (prev.quality) next.quality = prev.quality;
      return next;
    });
  }, [activeSlug]);

  // Трим каталога Create: outsee — только GPT Image 2 / Nano Banana 2 /
  // Veo 3.1 Lite; аудио — только KIE (Suno/ElevenLabs не дублируются).
  useEffect(() => {
    if (!kieCatalogQ.data) return;
    const isKie = (s: string) => kieModels.some((m) => `kie:${m.id}` === s);
    if (
      mediaType === "image" &&
      !isKie(imageSlug) &&
      !["gpt-image-2", "nano-banana-2"].includes(imageSlug)
    ) {
      setImageSlug("gpt-image-2");
    }
    if (mediaType === "video" && !isKie(videoSlug) && videoSlug !== "veo-3-1-lite") {
      setVideoSlug("veo-3-1-lite");
    }
    if (mediaType === "audio" && audioSlug === "kie:suno-sounds") {
      // Suno Sounds Task поёт / делает петли — настоящий SFX это ElevenLabs.
      setAudioSlug("kie:elevenlabs-sfx");
    } else if (mediaType === "audio" && !isKie(audioSlug)) {
      setAudioSlug("kie:suno-music");
    }
  }, [kieCatalogQ.data, kieModels, mediaType, imageSlug, videoSlug, audioSlug]);

  const currentName = kieActive
    ? (kieModel.label ?? kieModel.id)
    : mediaType === "image"
      ? imageModel.displayName
      : mediaType === "video"
        ? videoModel.displayName
        : audioModel.displayName;
  const currentWired = false;
  const outseeConfigured = Boolean(outseeStatusQ.data?.configured);
  const kieConfigured = Boolean(kieCatalogQ.data?.configured);

  const autoProvider: "outsee" | null = useMemo(() => {
    if (kieActive) return null;
    if (mediaType === "audio") return null;
    if (outseeConfigured) return "outsee";
    return null;
  }, [kieActive, mediaType, outseeConfigured]);

  const maxParallel =
    autoProvider === "outsee"
      ? (createQueueQ.data?.max_parallel_outsee ?? 5)
      : (createQueueQ.data?.max_parallel ?? 5);

  const canApiDirect = kieActive ? kieConfigured : autoProvider != null;
  const currentIcon = kieActive
    ? null
    : mediaType === "image"
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

  const basePriceLabel = kieActive
    ? kiePrice
      ? `$${kiePrice.usd.toFixed(3)} · ${kiePrice.credits} кр`
      : "—"
    : estimateCreatePrice({
        media: mediaType,
        model: activeSlug,
        resolution,
        duration: Number(duration) || 10,
        size: soraSize,
        catalogPrice: currentCatalogPrice,
      }).label;

  const priceLabel = useMemo(() => {
    if ((mediaType === "image" || mediaType === "video") && batchCount > 1) {
      if (kieActive && kiePrice) {
        return `$${(kiePrice.usd * batchCount).toFixed(3)} · ${kiePrice.credits * batchCount} кр`;
      }
      return `${basePriceLabel} (x${batchCount})`;
    }
    return basePriceLabel;
  }, [basePriceLabel, mediaType, batchCount, kieActive, kiePrice]);

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

  const applyFrameFromHistory = async (item: HistoryItem, slot: "first" | "last") => {
    // Предпочитаем публичный raw_url (Outsee CDN) — data:/local Outsee игнорит.
    const httpUrl = item.raw_url && item.raw_url.startsWith("http") ? item.raw_url : null;
    if (httpUrl) {
      if (slot === "first") {
        setFirstFrameDataUrl(httpUrl);
        setFirstFrameName(item.label || "история");
      } else {
        setLastFrameDataUrl(httpUrl);
        setLastFrameName(item.label || "история");
      }
      toast.success(slot === "first" ? "Стартовый кадр из истории" : "Конечный кадр из истории");
      return;
    }
    if (!item.preview_url) {
      toast.error("Нет URL картинки для кадра");
      return;
    }
    try {
      const res = await fetch(item.preview_url);
      const blob = await res.blob();
      const file = new File([blob], `${item.id}.png`, { type: blob.type || "image/png" });
      const dataUrl = await readFileAsDataUrl(file);
      if (slot === "first") {
        setFirstFrameDataUrl(dataUrl);
        setFirstFrameName(item.label || item.id);
      } else {
        setLastFrameDataUrl(dataUrl);
        setLastFrameName(item.label || item.id);
      }
      toast.success(slot === "first" ? "Стартовый кадр" : "Конечный кадр");
    } catch {
      toast.error("Не удалось взять кадр из истории");
    }
  };

  const addReferenceFromHistory = async (item: HistoryItem) => {
    if (maxReferences <= 0) return;
    if (referenceImages.length >= maxReferences) {
      toast.error(`Достигнут лимит референсов (${maxReferences})`);
      return;
    }
    const httpUrl = item.raw_url && item.raw_url.startsWith("http") ? item.raw_url : null;
    if (httpUrl) {
      setReferenceImages((prev) => [
        ...prev,
        {
          id: `${Date.now()}-${Math.random().toString(36).slice(2, 7)}`,
          url: httpUrl,
          name: item.label || "история",
        },
      ]);
      toast.success("Референс взят из истории");
      return;
    }
    if (!item.preview_url) {
      toast.error("Нет URL картинки для референса");
      return;
    }
    try {
      const res = await fetch(item.preview_url);
      const blob = await res.blob();
      const file = new File([blob], `${item.id}.png`, { type: blob.type || "image/png" });
      const dataUrl = await readFileAsDataUrl(file);
      setReferenceImages((prev) => [
        ...prev,
        {
          id: `${Date.now()}-${Math.random().toString(36).slice(2, 7)}`,
          url: dataUrl,
          name: item.label || item.id,
        },
      ]);
      toast.success("Референс добавлен из истории");
    } catch {
      toast.error("Не удалось взять референс из истории");
    }
  };

  const applyModelDefaults = (slug: string, kind: OutseeMediaType) => {
    if (kind === "image") {
      const m = getImageModel(slug);
      const d = m.defaults;
      const aspects = chipOptions(slug, "aspect");
      const resolutions = chipOptions(slug, "resolution");
      if (aspects.length) {
        setAspect((current) => clampToOptions(current, aspects, d.aspectRatio || "16:9"));
      }
      if (resolutions.length) {
        setResolution((current) => clampToOptions(current, resolutions, d.imageResolution || "2K"));
      }
      if (m.chips.includes("detail")) {
        setDetail((current) => current || d.detailLevel || "medium");
      }
      return;
    }
    if (kind === "audio") {
      return;
    }
    const m = getVideoModel(slug);
    const d = m.defaults;
    const aspects = chipOptions(slug, "aspect");
    const resolutions = chipOptions(slug, "resolution");
    const durations = chipOptions(slug, "duration");
    if (aspects.length) {
      setAspect((current) => clampToOptions(current, aspects, d.aspectRatio || "16:9"));
    }
    if (resolutions.length) {
      setVideoResolution((current) => clampToOptions(current, resolutions, d.resolution || resolutions[0]));
    }
    if (durations.length) {
      setDuration((current) => clampToOptions(current, durations, d.duration != null ? String(d.duration) : durations[0]));
    }
    if (m.chips.includes("quality")) {
      setMotionQuality((current) => current || d.motionQuality || "std");
    }
    // Сохраняем вложения и референсы пользователя при смене моделей!
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
    image_provider: "outsee",
    video_provider: "outsee",
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

  const deleteItem = useMutation({
    mutationFn: (item: HistoryItem) =>
      api.deleteOutseeCreateHistoryItem({ path: item.path || undefined, itemId: item.id }),
    onSuccess: (_, item) => {
      toast.success("Удалено из истории");
      if (selectedId === item.id) setSelectedId(null);
      qc.invalidateQueries({ queryKey: ["outsee-create-history"] });
    },
    onError: (e) => toast.error(errorMessageFromUnknown(e)),
  });

  const [trackingJobs, setTrackingJobs] = useState<
    { provider: "outsee" | "kie"; jobId: string; historyId: string }[]
  >([]);

  useEffect(() => {
    if (!trackingJobs.length) return;
    let cancelled = false;
    const tick = async () => {
      for (const t of [...trackingJobs]) {
        try {
          const job = await api.createJob(t.jobId);
          if (cancelled) return;
          qc.invalidateQueries({ queryKey: ["create-queue"] });
          if (job.status === "done") {
            const took =
              job.elapsed_label ||
              (job.elapsed_sec != null ? formatElapsedMinSec(job.elapsed_sec) : null);
            toast.success(
              took
                ? `Готово · ${job.model || "файл"} · ${took}`
                : `Готово · ${job.model || "файл"}`,
            );
            if (job.history_id) setSelectedId(job.history_id);
            setTrackingJobs((prev) => prev.filter((x) => x.jobId !== t.jobId));
            qc.invalidateQueries({ queryKey: ["outsee-create-history"] });
          } else if (job.status === "failed") {
            const took =
              job.elapsed_label ||
              (job.elapsed_sec != null ? formatElapsedMinSec(job.elapsed_sec) : null);
            toast.error(
              took
                ? `${job.error || "Генерация не удалась"} · ${took}`
                : job.error || "Генерация не удалась",
            );
            setTrackingJobs((prev) => prev.filter((x) => x.jobId !== t.jobId));
            qc.invalidateQueries({ queryKey: ["outsee-create-history"] });
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

  const handleRandomPrompt = () => {
    const available = RANDOM_PROMPTS.filter((p) => p !== prompt);
    const chosen = available[Math.floor(Math.random() * available.length)];
    setPrompt(chosen);
    toast.success("Случайный промпт подставлен 🎲");
  };

  const handleEnhancePrompt = async () => {
    const text = prompt.trim();
    if (!text) {
      toast.error("Сначала напишите краткую идею в поле ввода");
      return;
    }
    setIsEnhancingPrompt(true);
    try {
      const res = await api.enhanceOutseeCreatePrompt({
        prompt: text,
        style: stylePreset !== "none" ? stylePreset : undefined,
      });
      if (res?.enhanced_prompt) {
        setPrompt(res.enhanced_prompt);
        toast.success("Промпт улучшен ИИ ✨");
      }
    } catch {
      toast.error("Не удалось улучшить промпт");
    } finally {
      setIsEnhancingPrompt(false);
    }
  };

  const createGenerate = useMutation({
    mutationFn: async () => {
      const preset = STYLE_PRESETS.find((p) => p.id === stylePreset);
      let text = prompt.trim();
      if (text && mediaType === "image" && preset?.suffix) {
        text += preset.suffix;
      }
      if (text && mediaType === "image" && negativePrompt.trim()) {
        text += `\nAvoid: ${negativePrompt.trim()}`;
      }

      const executeSingle = async (index: number) => {
        const nonce = `${Date.now()}-${index}-${Math.random().toString(36).slice(2, 7)}`;
        // ---- KIE: динамическая модель из каталога kie.ai ----
        if (kieActive && kieModel) {
          if (!kieConfigured) {
            throw new Error("KIE_API_KEY не задан в .env");
          }
          const vals: Record<string, unknown> = { ...kieValues, _nonce: nonce };
          if (kieTextField) vals[kieTextField] = text;
          if (negativePrompt.trim()) {
            const negField = kieModel.fields.find((f) => f.name.toLowerCase().includes("neg"));
            if (negField) vals[negField.name] = negativePrompt.trim();
          }
          // Автоматическая передача референсов и стартовых кадров в поля модели KIE
          const refUrls =
            referenceImages.length > 0
              ? referenceImages.map((r) => r.url)
              : firstFrameDataUrl
                ? [firstFrameDataUrl]
                : [];
          if (refUrls.length > 0) {
            const imageField = kieModel.fields.find(
              (f) =>
                f.kind === "images" ||
                ["image_urls", "image_input", "imageUrls", "images", "image_url", "imageUrl"].includes(f.name),
            );
            if (imageField) {
              if (
                imageField.kind === "images" ||
                imageField.name.endsWith("s") ||
                imageField.name === "image_input" ||
                (imageField.max_items && imageField.max_items > 1)
              ) {
                vals[imageField.name] = refUrls.slice(0, imageField.max_items || 8);
              } else {
                vals[imageField.name] = refUrls[0];
              }
            }
          }
          const missing = kieModel.fields
            .filter((f) => f.required)
            .filter((f) => {
              const v = vals[f.name] ?? f.default;
              if (v === undefined || v === null) return true;
              if (typeof v === "string") return v.trim() === "";
              if (Array.isArray(v)) return v.length === 0;
              return false;
            });
          if (missing.length) {
            throw new Error(`Заполни: ${missing.map((f) => f.label).join(", ")}`);
          }
          const res = await api.kieGenerate({ model_id: kieModel.id, values: vals });
          return {
            job_id: res.job.job_id,
            history_id: res.job.history_id,
            status: res.job.status,
            queue_position: res.job.queue_position,
            provider: "kie" as const,
          };
        }
        if (!text) throw new Error("Введите промпт");
        if (mediaType === "audio") {
          if (projectId == null) {
            throw new Error("Аудио — через шаг пайплайна: выберите проект");
          }
          await api.putOutseeCreateSettings(settingsPayload());
          await applyToProject.mutateAsync();
          // Suno (Create «АУДИО») → music; иначе TTS/voice → audio.
          const step =
            String(audioSlug || "").toLowerCase().includes("suno") ? "music" : "audio";
          return api.runProjectStep(projectId, step);
        }
        if (!outseeConfigured) {
          throw new Error("OUTSEE_API_KEY не задан в .env");
        }
        // Settings не блокируют enqueue: параллельные клики иначе ломаются
        // на гонке записи outsee_create_settings.json.
        void api.putOutseeCreateSettings(settingsPayload()).catch(() => undefined);
        const enqueued =
          mediaType === "video"
            ? await api.outseeGenerate({
                prompt: text,
                media: "video",
                model: videoSlug,
                aspect,
                resolution: videoResolution,
                duration: Number(duration) || 5,
                generate_audio: videoModel.chips.includes("audio") ? generateAudio : null,
                first_frame_url: firstFrameDataUrl,
                last_frame_url: lastFrameDataUrl,
                project_id: projectId,
                nonce,
                batch_index: index,
              })
            : await api.outseeGenerate({
                prompt: text,
                media: "image",
                model: imageSlug,
                aspect,
                resolution,
                detail_level: imageModel.chips.includes("detail") ? detail : undefined,
                first_frame_url: referenceImages.length > 0 ? referenceImages[0].url : firstFrameDataUrl,
                reference_images:
                  referenceImages.length > 0
                    ? referenceImages.map((r) => r.url)
                    : firstFrameDataUrl
                      ? [firstFrameDataUrl]
                      : undefined,
                project_id: projectId,
                nonce,
                batch_index: index,
              });
        return { ...enqueued, provider: "outsee" as const };
      };

      const count = (mediaType === "image" || mediaType === "video") ? batchCount : 1;
      if (count > 1) {
        const results = await Promise.all(
          Array.from({ length: count }, (_, i) => executeSingle(i))
        );
        return { batch: true, count, results };
      }
      return executeSingle(0);
    },
    onSuccess: (res) => {
      if (res && typeof res === "object" && "batch" in res && Array.isArray((res as any).results)) {
        const batchRes = (res as any).results as any[];
        const newTrackers: { provider: "outsee" | "kie"; jobId: string; historyId: string }[] = [];
        let firstHistId: string | null = null;
        for (const r of batchRes) {
          if (r && typeof r === "object" && "job_id" in r && r.job_id) {
            if (!firstHistId && r.history_id) firstHistId = r.history_id;
            newTrackers.push({ provider: r.provider, jobId: r.job_id, historyId: r.history_id });
          }
        }
        if (firstHistId) setSelectedId(firstHistId);
        setTrackingJobs((prev) => [
          ...prev.filter((x) => !newTrackers.some((n) => n.jobId === x.jobId)),
          ...newTrackers,
        ]);
        qc.invalidateQueries({ queryKey: ["outsee-create-history"] });
        qc.invalidateQueries({ queryKey: ["create-queue"] });
        toast.success(`Запущено ${batchRes.length} генерации 🚀`);
        return;
      }
      if (res && typeof res === "object" && "job_id" in res && res.job_id) {
        const r = res as {
          job_id: string;
          history_id: string;
          queue?: number;
          waiting_count?: number;
          running_count?: number;
          status?: string;
          queue_position?: number | null;
          provider: "outsee" | "kie";
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
    let item: HistoryItem | null = null;
    if (!historyItems.length) return null;
    if (selectedId) {
      item = historyItems.find((h) => h.id === selectedId) ?? historyItems[0] ?? null;
    } else {
      item = historyItems[0] ?? null;
    }
    if (!item) return null;
    const activeJob =
      runningJobs.find((j) => j.history_id === item?.id || (item?.job_id && j.job_id === item.job_id)) ||
      waitingJobs.find((j) => j.history_id === item?.id || (item?.job_id && j.job_id === item.job_id));
    if (activeJob) {
      return {
        ...item,
        status: activeJob.status || item.status,
        created_at: activeJob.created_at || item.created_at,
        started_at: activeJob.started_at || item.started_at,
      };
    }
    return item;
  }, [historyItems, selectedId, runningJobs, waitingJobs]);

  if (!open) return null;

  const TypeIcon = ({ id }: { id: OutseeMediaType }) => {
    if (id === "video") return <Video className="h-4 w-4" strokeWidth={1.7} />;
    if (id === "audio") return <Music className="h-4 w-4" strokeWidth={1.7} />;
    return <ImageIcon className="h-4 w-4" strokeWidth={1.7} />;
  };

  return (
    <div className="fixed inset-0 z-[80] flex flex-col bg-[#0a0a0a] text-white">
      <header className="flex h-[52px] shrink-0 items-center justify-between border-b border-white/10 bg-[#0d0d11]/90 px-4 backdrop-blur-xl">
        <div className="flex items-center gap-3">
          <button
            type="button"
            onClick={() => onOpenChange(false)}
            className="inline-flex h-8 w-8 items-center justify-center rounded-lg border border-white/10 bg-white/[0.03] text-white/70 transition hover:bg-white/[0.08] hover:text-white"
          >
            <X className="h-4 w-4" />
          </button>
          <div className="flex items-center gap-2">
            <Layers className="h-4 w-4" style={{ color: OUTSEE_ACCENT }} />
            <div className="leading-tight">
              <div className="text-sm font-bold tracking-tight text-white/95">Генерация</div>
              <div className="text-[10px] uppercase tracking-[0.16em] text-white/40">
                outsee create · глобально
              </div>
            </div>
          </div>
        </div>
        <div className="flex items-center gap-2.5">
          <span className="hidden text-[11px] text-white/40 sm:inline">
            настройки и история общие для Studio
          </span>
          {projectId != null && (
            <span className="rounded-full border border-[#22d3ee]/30 bg-[#22d3ee]/10 px-2.5 py-0.5 font-mono text-[10px] font-semibold text-[#22d3ee]">
              проект #{projectId}
            </span>
          )}
          <a
            href={outseeCreateUrl(mediaType, activeSlug)}
            target="_blank"
            rel="noreferrer"
            className="inline-flex items-center gap-1.5 rounded-lg border border-white/10 bg-white/[0.03] px-2.5 py-1.5 text-[11px] text-white/60 transition hover:border-white/25 hover:bg-white/[0.07] hover:text-white"
          >
            outsee.io
            <ExternalLink className="h-3 w-3" />
          </a>
        </div>
      </header>

      <div className="flex min-h-0 flex-1">
        {/* History + feed filter */}
        <aside className="flex w-[250px] shrink-0 flex-col border-r border-white/10 bg-[#0a0a0d]/95 backdrop-blur-xl lg:w-[290px]">
          <div className="flex items-center gap-2 border-b border-white/[0.08] px-3.5 py-2.5">
            <History className="h-3.5 w-3.5 text-white/50" />
            <span className="text-[11px] font-bold uppercase tracking-[0.14em] text-white/50">
              История
            </span>
            {queueCount > 0 && (
              <span
                className="inline-flex items-center gap-1 rounded-md px-1.5 py-0.5 text-[9px] font-extrabold uppercase tracking-wider text-black shadow-sm"
                style={{ backgroundColor: OUTSEE_ACCENT }}
                title={`В работе ${runningJobs.length}/${maxParallel}, ожидание ${waitingJobs.length}`}
              >
                <Loader2 className="h-2.5 w-2.5 animate-spin" />
                {runningJobs.length}·{waitingJobs.length}
              </span>
            )}
            <span className="ml-auto font-mono text-[10px] text-white/40 font-semibold">
              {historyItems.length}
            </span>
          </div>
          <div className="space-y-2 border-b border-white/[0.08] px-2.5 py-2.5">
            <div>
              <div className="mb-1.5 px-1 text-[9px] font-bold uppercase tracking-wider text-white/40">
                В работе · {runningJobs.length}/{maxParallel}
              </div>
              {runningJobs.length === 0 ? (
                <div className="rounded-lg border border-dashed border-white/10 px-2 py-2 text-[9px] text-white/30">
                  нет активных
                </div>
              ) : (
                <div className="space-y-1.5">
                  {runningJobs.map((j) => (
                    <button
                      key={j.job_id}
                      type="button"
                      onClick={() => j.history_id && setSelectedId(j.history_id)}
                      className="flex w-full items-center gap-2 rounded-xl border border-[#22d3ee]/40 bg-[#22d3ee]/10 px-2.5 py-2 text-left shadow-[0_0_15px_rgba(34,211,238,0.15)] transition"
                    >
                      <Loader2
                        className="h-3.5 w-3.5 shrink-0 animate-spin text-[#22d3ee]"
                      />
                      <div className="min-w-0 flex-1">
                        <div className="flex items-center justify-between gap-1">
                          <div className="truncate font-mono text-[10px] font-semibold text-white/90">
                            {j.model || j.media}
                          </div>
                          <span className="shrink-0 font-mono text-[9px] font-semibold text-[#22d3ee]">
                            {getLiveElapsed(j)}
                          </span>
                        </div>
                        <div className="truncate text-[9px] text-white/50">
                          {j.prompt_preview || "генерация…"}
                        </div>
                      </div>
                    </button>
                  ))}
                </div>
              )}
            </div>
            <div>
              <div className="mb-1.5 px-1 text-[9px] font-bold uppercase tracking-wider text-white/40">
                Ожидание · {waitingJobs.length}
              </div>
              {waitingJobs.length === 0 ? (
                <div className="rounded-lg border border-dashed border-white/10 px-2 py-2 text-[9px] text-white/30">
                  очередь пуста
                </div>
              ) : (
                <div className="space-y-1.5">
                  {waitingJobs.map((j) => (
                    <button
                      key={j.job_id}
                      type="button"
                      onClick={() => j.history_id && setSelectedId(j.history_id)}
                      className="flex w-full items-center gap-2 rounded-xl border border-white/10 bg-white/[0.03] px-2.5 py-2 text-left transition hover:border-white/20 hover:bg-white/[0.06]"
                    >
                      <span className="inline-flex h-5 w-5 shrink-0 items-center justify-center rounded-md bg-white/10 font-mono text-[9px] font-bold text-white/70">
                        #{j.queue_position ?? "—"}
                      </span>
                      <div className="min-w-0 flex-1">
                        <div className="flex items-center justify-between gap-1">
                          <div className="truncate font-mono text-[10px] font-semibold text-white/80">
                            {j.model || j.media}
                          </div>
                          <span className="shrink-0 font-mono text-[9px] font-medium text-white/50">
                            {getLiveElapsed(j)}
                          </span>
                        </div>
                        <div className="truncate text-[9px] text-white/40">
                          {j.prompt_preview || "в очереди"}
                        </div>
                      </div>
                    </button>
                  ))}
                </div>
              )}
            </div>
          </div>
          <div className="flex flex-wrap gap-1 border-b border-white/[0.08] p-2">
            {OUTSEE_FEED_TABS.map((t) => (
              <button
                key={t.id}
                type="button"
                onClick={() => setFeedKind(t.id)}
                className={cn(
                  "rounded-lg px-2.5 py-1 text-[10px] font-bold uppercase tracking-wider transition-all duration-150",
                  feedKind === t.id
                    ? "bg-[#22d3ee] text-black font-extrabold shadow-[0_0_15px_rgba(34,211,238,0.3)]"
                    : "bg-white/[0.04] text-white/50 hover:bg-white/[0.08] hover:text-white",
                )}
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
              <div className="grid grid-cols-2 gap-2">
                {historyItems.map((item) => {
                  const active = selected?.id === item.id;
                  const isVideo = item.kind === "video";
                  const isAudio = item.kind === "audio";
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
                        "group relative aspect-square overflow-hidden rounded-xl border bg-[#121216] transition-all duration-200",
                        active
                          ? "border-[#22d3ee] ring-2 ring-[#22d3ee]/40 shadow-[0_0_20px_rgba(34,211,238,0.25)]"
                          : "border-white/[0.08] hover:border-white/25 hover:bg-[#18181f]",
                      )}
                      title={`${item.label}${item.project_slug ? ` · ${item.project_slug}` : ""}`}
                    >
                      {item.preview_url && !pending ? (
                        isVideo ? (
                          <video
                            src={item.preview_url}
                            muted
                            playsInline
                            preload="metadata"
                            className="h-full w-full object-cover"
                          />
                        ) : isAudio ? (
                          <div className="flex h-full flex-col items-center justify-center gap-1 bg-white/[0.03]">
                            <Music className="h-6 w-6 text-white/40" />
                          </div>
                        ) : (
                          // eslint-disable-next-line @next/next/no-img-element
                          <img
                            src={item.preview_url}
                            alt=""
                            loading="lazy"
                            decoding="async"
                            className="h-full w-full object-cover"
                          />
                        )
                      ) : (
                        <div className="flex h-full flex-col items-center justify-center gap-1.5 px-2 text-center">
                          {pending ? (
                            <Loader2
                              className="h-5 w-5 animate-spin text-[#22d3ee]"
                            />
                          ) : failed ? (
                            <span className="text-[10px] font-semibold text-red-400">ошибка</span>
                          ) : (
                            <span className="text-[9px] text-white/25">{item.kind}</span>
                          )}
                          {pending && (
                            <div className="flex flex-col items-center gap-0.5">
                              <span className="text-[9px] font-semibold uppercase tracking-wider text-white/55">
                                {pendingLabel}
                              </span>
                              <span className="font-mono text-[9px] font-bold text-[#22d3ee]">
                                {getLiveElapsed(item)}
                              </span>
                            </div>
                          )}
                        </div>
                      )}
                      {((item.reference_images && item.reference_images.length > 0) || item.first_frame_url) && !pending && (
                        <div className="absolute top-1.5 left-1.5 z-20 flex items-center gap-0.5 rounded-md bg-black/75 px-1.5 py-0.5 text-[8px] font-bold text-[#22d3ee] backdrop-blur">
                          <Paperclip className="h-2.5 w-2.5" />
                          <span>{item.reference_images?.length || 1}</span>
                        </div>
                      )}
                      <div className="absolute inset-x-0 bottom-0 bg-gradient-to-t from-black/90 via-black/50 to-transparent px-2 py-1.5">
                        <div className="truncate font-mono text-[9px] font-semibold text-white/80">{item.label}</div>
                        {item.project_slug && (
                          <div className="truncate text-[8px] text-white/45">{item.project_slug}</div>
                        )}
                      </div>
                      {!pending && (
                        <div className="absolute top-1.5 right-1.5 z-20 flex items-center gap-1 opacity-0 transition group-hover:opacity-100">
                          <button
                            type="button"
                            onClick={(e) => {
                              e.stopPropagation();
                              deleteItem.mutate(item);
                            }}
                            className="flex h-6 w-6 items-center justify-center rounded-md bg-black/75 text-white/70 backdrop-blur transition hover:bg-red-600 hover:text-white shadow-md"
                            title="Удалить из истории"
                          >
                            <Trash2 className="h-3.5 w-3.5" />
                          </button>
                          {(item.preview_url || item.raw_url) && (
                            <button
                              type="button"
                              onClick={(e) => {
                                e.stopPropagation();
                                downloadMediaFile(
                                  item.preview_url || item.raw_url || "",
                                  item.label || "generation",
                                  item.kind === "video" ? "mp4" : item.kind === "audio" ? "mp3" : "png",
                                  item.path,
                                );
                              }}
                              className="flex h-6 w-6 items-center justify-center rounded-md bg-black/75 text-white/80 backdrop-blur transition hover:bg-[#22d3ee] hover:text-black shadow-md"
                              title="Скачать файл"
                            >
                              <Download className="h-3.5 w-3.5" />
                            </button>
                          )}
                        </div>
                      )}
                    </button>
                  );
                })}
              </div>
            )}
          </div>
        </aside>

        {/* Result + dock */}
        <section className="relative flex min-w-0 flex-1 flex-col">
          {/* Ambient glow backlight */}
          <div className="pointer-events-none absolute inset-0 flex items-center justify-center -z-0">
            <div className="h-80 w-80 rounded-full bg-[#22d3ee]/10 blur-[110px]" />
            <div className="h-60 w-60 rounded-full bg-purple-500/10 blur-[90px]" />
          </div>

          {/* Header - float top-left so media starts at the very top */}
          <div className="pointer-events-none absolute top-3 left-4 z-20 flex flex-col gap-0.5 lg:left-6">
            <h2 className="text-sm font-bold text-white lg:text-base">
              Результат генерации
            </h2>
            {selected && (
              <div className="flex flex-col text-[11px] font-medium text-white/50">
                {selected.elapsed_label ||
                (selected.elapsed_sec != null && selected.elapsed_sec >= 0) ? (
                  <div>
                    Время генерации:{" "}
                    <span className="font-mono font-semibold text-[#22d3ee]">
                      {getLiveElapsed(selected)}
                    </span>
                  </div>
                ) : null}
                {selected.model && (
                  <div className="text-white/45">
                    Модель:{" "}
                    <span className="font-mono font-semibold text-white/75">{selected.model}</span>
                  </div>
                )}
              </div>
            )}
          </div>

          <div className="relative z-10 flex min-h-0 flex-1 flex-col items-center justify-start px-4 pt-2 pb-[260px] lg:px-6">
            {selected?.preview_url &&
            selected.status !== "queued" &&
            selected.status !== "processing" ? (
              <div className="flex flex-col items-center">
                <div className="group relative flex max-h-[calc(100vh-360px)] max-w-full items-center justify-center">
                  {selected.kind === "video" ? (
                    <video
                      src={selected.preview_url}
                      controls
                      className="max-h-[calc(100vh-360px)] max-w-full rounded-2xl border border-white/15 bg-black/80 shadow-[0_20px_50px_rgba(0,0,0,0.8)]"
                    />
                  ) : selected.kind === "audio" ? (
                    <div className="flex w-full max-w-md flex-col items-center gap-4 rounded-2xl border border-white/15 bg-[#121216]/90 p-8 backdrop-blur-2xl shadow-[0_20px_50px_rgba(0,0,0,0.8)]">
                      <Music className="h-8 w-8 text-[#22d3ee]" />
                      <div className="text-sm font-semibold text-white/85">{selected.label}</div>
                      <audio src={selected.preview_url} controls className="w-full" />
                    </div>
                  ) : (
                    // eslint-disable-next-line @next/next/no-img-element
                    <img
                      src={selected.preview_url}
                      alt=""
                      onClick={() => setLightboxOpen(true)}
                      className="max-h-[calc(100vh-360px)] max-w-full cursor-zoom-in rounded-2xl border border-white/15 bg-black/80 object-contain shadow-[0_20px_50px_rgba(0,0,0,0.8)] transition hover:brightness-105"
                    />
                  )}
                  {selected.kind !== "audio" && (
                    <button
                      type="button"
                      onClick={() => setLightboxOpen(true)}
                      className="absolute top-3 right-3 z-30 flex items-center gap-1.5 rounded-xl border border-white/20 bg-black/70 px-3 py-1.5 text-[11px] font-medium text-white/90 opacity-0 backdrop-blur-md transition hover:scale-105 hover:border-[#22d3ee]/60 hover:bg-[#22d3ee]/20 hover:text-white group-hover:opacity-100 shadow-2xl"
                      title="Инспектор промпта / Во весь экран"
                    >
                      <Maximize2 className="h-3.5 w-3.5" />
                      <span>Инспектор</span>
                    </button>
                  )}
                </div>

                {/* Отображение использованных референсов (если были) */}
                {((selected.reference_images && selected.reference_images.length > 0) || selected.first_frame_url) && (
                  <div className="mt-2.5 flex flex-wrap items-center gap-2 rounded-xl border border-white/10 bg-black/50 px-3 py-1.5 backdrop-blur-md shadow-md">
                    <span className="text-[10px] font-semibold uppercase tracking-wider text-white/50">
                      Использованные референсы ({selected.reference_images?.length || 1}):
                    </span>
                    <div className="flex items-center gap-1.5">
                      {(selected.reference_images && selected.reference_images.length > 0
                        ? selected.reference_images
                        : [selected.first_frame_url!]
                      ).map((u, i) => (
                        <a
                          key={i}
                          href={u}
                          target="_blank"
                          rel="noreferrer"
                          className="group/ref relative block h-8 w-8 overflow-hidden rounded-lg border border-white/20 transition hover:scale-110 hover:border-[#22d3ee]"
                          title="Открыть референс в новой вкладке"
                        >
                          {/* eslint-disable-next-line @next/next/no-img-element */}
                          <img src={u} alt="" className="h-full w-full object-cover" />
                        </a>
                      ))}
                    </div>
                  </div>
                )}
              </div>
            ) : selected &&
              (selected.status === "queued" || selected.status === "processing") ? (
              <div className="flex w-full max-w-sm flex-col items-center gap-3.5 rounded-2xl border border-white/15 bg-[#121216]/90 px-6 py-10 text-center backdrop-blur-2xl shadow-[0_20px_50px_rgba(0,0,0,0.8)]">
                <Loader2
                  className="h-9 w-9 animate-spin text-[#22d3ee]"
                />
                <div className="text-sm font-bold text-white/90">
                  {selected.status === "queued" ? "В очереди ожидания" : "Идёт генерация…"}
                </div>
                <div className="inline-flex items-center gap-1.5 rounded-full border border-[#22d3ee]/30 bg-[#22d3ee]/10 px-3 py-1 font-mono text-[12px] font-bold text-[#22d3ee]">
                  <Clock className="h-3.5 w-3.5" />
                  <span>{getLiveElapsed(selected)}</span>
                </div>
                <div className="text-[12px] text-white/50">
                  {selected.model || selected.label}
                  {queueCount > 1 ? ` · очередь ${queueCount}` : ""}
                </div>
                {selected.prompt && (
                  <div className="line-clamp-3 max-w-full text-[11px] text-white/40">
                    {selected.prompt}
                  </div>
                )}
              </div>
            ) : selected?.status === "failed" ? (
              <div className="flex w-full max-w-sm flex-col items-center gap-3 rounded-2xl border border-red-500/30 bg-red-500/10 px-6 py-8 text-center backdrop-blur-2xl shadow-[0_20px_50px_rgba(0,0,0,0.8)]">
                <div className="text-sm font-bold text-red-300">Ошибка генерации</div>
                <div className="text-[12px] text-white/60">
                  {selected.error || "Не удалось получить файл"}
                </div>
                <div className="text-[12px] font-medium text-white/55">
                  Результат ·{" "}
                  {selected.elapsed_label ||
                    formatElapsedMinSec(selected.elapsed_sec)}
                </div>
                <button
                  type="button"
                  onClick={() => deleteItem.mutate(selected)}
                  className="mt-2 inline-flex items-center gap-1.5 rounded-xl border border-red-500/30 bg-red-500/20 px-3.5 py-1.5 text-[11px] font-semibold text-red-300 backdrop-blur transition hover:border-red-500/50 hover:bg-red-500/30 hover:text-white shadow-lg"
                  title="Удалить ошибочную запись из истории"
                >
                  <Trash2 className="h-3.5 w-3.5" />
                  <span>Удалить из истории</span>
                </button>
              </div>
            ) : (
              <div className="flex w-full max-w-xs flex-col items-center gap-4 rounded-2xl border border-white/10 bg-[#121216]/70 px-6 py-10 text-center backdrop-blur-xl">
                <ImageIcon className="h-8 w-8 text-white/30" />
                <div className="text-sm font-medium text-white/70">Нет результата</div>
                <div className="text-[12px] text-white/40">
                  Файлы пишутся в{" "}
                  <span className="font-mono text-white/60">data/generations/</span> на этом
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
                        "flex min-w-[76px] flex-col items-center gap-1.5 rounded-xl border px-3 py-2.5 transition-all duration-200",
                        active
                          ? "border-[#22d3ee] bg-[#22d3ee]/15 text-[#22d3ee] shadow-[0_0_18px_rgba(34,211,238,0.25)]"
                          : "border-white/10 bg-[#16161b]/90 text-white/45 hover:border-white/20 hover:bg-[#1e1e24] hover:text-white",
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
                className="min-w-0 flex-1 rounded-2xl border border-white/15 bg-[#121216]/95 backdrop-blur-2xl shadow-[0_20px_60px_rgba(0,0,0,0.85)] ring-1 ring-white/10"
              >
                {/* KIE: вложения для аудио/видео (голос, донор движения, аудиофайл) */}
                {kieActive &&
                  kieModel &&
                  kieFileFields(kieModel).filter((f) => f.kind !== "images").length > 0 && (
                  <div className="flex flex-wrap items-center gap-2 border-b border-white/[0.08] px-3 py-2.5 lg:px-4">
                    {kieFileFields(kieModel)
                      .filter((f) => f.kind !== "images")
                      .map((f) => (
                        <KieAttachButton
                          key={f.name}
                          field={f}
                          values={kieValues}
                          onChange={(name, items) =>
                            setKieValues((prev) => ({ ...prev, [name]: items }))
                          }
                        />
                      ))}
                    <span className="text-[10px] text-white/35">
                      файл грузится в kie → публичный URL; или вставь свой URL в поле
                    </span>
                  </div>
                )}
                {/* Унифицированная зона вложений и референсов (Outsee + KIE) */}
                {((mediaType === "video" &&
                  (videoModel.chips.includes("image-input") ||
                    (kieActive &&
                      Boolean(
                        kieModel?.fields.some(
                          (f) => f.kind === "images" || f.name.toLowerCase().includes("image"),
                        ),
                      )))) ||
                  (mediaType === "image" && maxReferences > 0)) && (
                  <div className="flex flex-wrap items-center gap-2 border-b border-white/[0.08] px-3 py-2.5 lg:px-4">
                    {mediaType === "image" ? (
                      <>
                        <input
                          ref={multiRefInputRef}
                          type="file"
                          multiple
                          accept="image/png,image/jpeg,image/webp"
                          className="hidden"
                          onChange={async (e) => {
                            const files = Array.from(e.target.files || []);
                            if (!files.length) return;
                            const remaining = maxReferences - referenceImages.length;
                            if (remaining <= 0) {
                              toast.error(`Достигнут лимит референсов (${maxReferences})`);
                              return;
                            }
                            const toAdd = files.slice(0, remaining);
                            const newRefs: { id: string; url: string; name: string }[] = [];
                            for (const f of toAdd) {
                              const dataUrl = await readFileAsDataUrl(f);
                              newRefs.push({
                                id: `${Date.now()}-${Math.random().toString(36).slice(2, 7)}`,
                                url: dataUrl,
                                name: f.name,
                              });
                            }
                            setReferenceImages((prev) => [...prev, ...newRefs]);
                            toast.success(`Добавлено ${newRefs.length} референс(ов)`);
                            e.target.value = "";
                          }}
                        />
                        <button
                          type="button"
                          onClick={() => multiRefInputRef.current?.click()}
                          disabled={referenceImages.length >= maxReferences}
                          className={cn(
                            "inline-flex h-9 items-center gap-2 rounded-xl border px-3 text-[11px] font-bold uppercase tracking-wider transition",
                            referenceImages.length > 0
                              ? "border-[#22d3ee]/40 bg-[#22d3ee]/10 text-[#22d3ee]"
                              : "border-dashed border-white/20 bg-white/[0.03] text-white/70 hover:border-white/40 hover:text-white",
                          )}
                        >
                          <Paperclip className="h-3.5 w-3.5" />
                          <span>+ Референс</span>
                          <span className="rounded-md bg-white/10 px-1.5 py-0.5 font-mono text-[10px]">
                            {referenceImages.length}/{maxReferences}
                          </span>
                        </button>
                        {referenceImages.map((ref, idx) => (
                          <div
                            key={ref.id}
                            className="group flex items-center gap-1.5 rounded-xl border border-white/15 bg-white/[0.05] py-1 pl-1 pr-2 text-[11px] text-white/90"
                          >
                            {/* eslint-disable-next-line @next/next/no-img-element */}
                            <img
                              src={ref.url}
                              alt=""
                              className="h-6 w-6 rounded-lg object-cover ring-1 ring-white/15"
                            />
                            <span className="max-w-[90px] truncate font-mono text-[10px] text-white/75">
                              {ref.name || `Реф #${idx + 1}`}
                            </span>
                            <button
                              type="button"
                              onClick={() => setReferenceImages((prev) => prev.filter((r) => r.id !== ref.id))}
                              className="ml-0.5 text-white/40 transition hover:text-red-400"
                              title="Удалить"
                            >
                              <X className="h-3 w-3" />
                            </button>
                          </div>
                        ))}
                        {maxReferences > 0 &&
                          selected?.kind === "image" &&
                          selected.status === "done" &&
                          referenceImages.length < maxReferences && (
                            <button
                              type="button"
                              onClick={() => void addReferenceFromHistory(selected)}
                              className="inline-flex h-9 items-center gap-1.5 rounded-xl border border-[#22d3ee]/40 bg-[#22d3ee]/10 px-3 text-[11px] font-semibold text-[#22d3ee] transition hover:bg-[#22d3ee]/20"
                              title="Добавить текущий результат в референсы"
                            >
                              + В референсы ({referenceImages.length}/{maxReferences})
                            </button>
                          )}
                      </>
                    ) : (
                      <>
                        <input
                          ref={firstFrameInputRef}
                          type="file"
                          accept="image/png,image/jpeg,image/webp"
                          className="hidden"
                          onChange={(e) => {
                            const f = e.target.files?.[0];
                            if (!f) return;
                            void readFileAsDataUrl(f).then((dataUrl) => {
                              setFirstFrameDataUrl(dataUrl);
                              setFirstFrameName(f.name);
                              toast.success("Стартовый кадр добавлен");
                            });
                            e.target.value = "";
                          }}
                        />
                        <input
                          ref={lastFrameInputRef}
                          type="file"
                          accept="image/png,image/jpeg,image/webp"
                          className="hidden"
                          onChange={(e) => {
                            const f = e.target.files?.[0];
                            if (!f) return;
                            void readFileAsDataUrl(f).then((dataUrl) => {
                              setLastFrameDataUrl(dataUrl);
                              setLastFrameName(f.name);
                              toast.success("Конечный кадр добавлен");
                            });
                            e.target.value = "";
                          }}
                        />
                        <button
                          type="button"
                          onClick={() => firstFrameInputRef.current?.click()}
                          className={cn(
                            "inline-flex h-9 items-center gap-2 rounded-xl border px-3 text-[11px] font-bold uppercase tracking-wider transition",
                            firstFrameDataUrl
                              ? "border-[#22d3ee]/40 bg-[#22d3ee]/10 text-[#22d3ee]"
                              : "border-dashed border-white/20 bg-white/[0.03] text-white/70 hover:border-white/40 hover:text-white",
                          )}
                        >
                          {firstFrameDataUrl ? (
                            // eslint-disable-next-line @next/next/no-img-element
                            <img
                              src={firstFrameDataUrl}
                              alt=""
                              className="h-6 w-6 rounded-lg object-cover ring-1 ring-white/15"
                            />
                          ) : (
                            <Paperclip className="h-3.5 w-3.5" />
                          )}
                          <span>Стартовый кадр</span>
                          {firstFrameDataUrl && (
                            <span
                              className="text-white/45 hover:text-white"
                              onClick={(ev) => {
                                ev.stopPropagation();
                                setFirstFrameDataUrl(null);
                                setFirstFrameName(null);
                              }}
                            >
                              <X className="h-3.5 w-3.5" />
                            </span>
                          )}
                        </button>
                        <button
                          type="button"
                          onClick={() => lastFrameInputRef.current?.click()}
                          className={cn(
                            "inline-flex h-9 items-center gap-2 rounded-xl border px-3 text-[11px] font-bold uppercase tracking-wider transition",
                            lastFrameDataUrl
                              ? "border-[#22d3ee]/40 bg-[#22d3ee]/10 text-[#22d3ee]"
                              : "border-dashed border-white/20 bg-white/[0.03] text-white/70 hover:border-white/40 hover:text-white",
                          )}
                        >
                          {lastFrameDataUrl ? (
                            // eslint-disable-next-line @next/next/no-img-element
                            <img
                              src={lastFrameDataUrl}
                              alt=""
                              className="h-6 w-6 rounded-lg object-cover ring-1 ring-white/15"
                            />
                          ) : (
                            <Paperclip className="h-3.5 w-3.5" />
                          )}
                          <span>Конечный кадр</span>
                          {lastFrameDataUrl && (
                            <span
                              className="text-white/45 hover:text-white"
                              onClick={(ev) => {
                                ev.stopPropagation();
                                setLastFrameDataUrl(null);
                                setLastFrameName(null);
                              }}
                            >
                              <X className="h-3.5 w-3.5" />
                            </span>
                          )}
                        </button>
                        {mediaType === "video" &&
                          videoModel.chips.includes("image-input") &&
                          selected?.kind === "image" &&
                          selected.status === "done" && (
                            <>
                              <button
                                type="button"
                                onClick={() => void applyFrameFromHistory(selected, "first")}
                                className="inline-flex h-9 items-center gap-1 rounded-xl border border-[#22d3ee]/40 bg-[#22d3ee]/10 px-2.5 text-[11px] font-semibold text-[#22d3ee] transition hover:bg-[#22d3ee]/20"
                                title="Текущее фото → Стартовый кадр"
                              >
                                → В старт
                              </button>
                              <button
                                type="button"
                                onClick={() => void applyFrameFromHistory(selected, "last")}
                                className="inline-flex h-9 items-center gap-1 rounded-xl border border-white/20 bg-white/[0.04] px-2.5 text-[11px] font-medium text-white/75 transition hover:border-white/30 hover:bg-white/[0.08]"
                                title="Текущее фото → Конечный кадр"
                              >
                                → В финиш
                              </button>
                            </>
                          )}
                      </>
                    )}
                    <span className="text-[10px] text-white/35">
                      файл с диска или выбор из истории слева
                    </span>
                  </div>
                )}
                {/* Style Presets and Negative Prompt for image mode */}
                {mediaType === "image" && (
                  <div className="flex flex-wrap items-center justify-between gap-1.5 border-b border-white/[0.06] bg-white/[0.015] px-3 py-1.5 lg:px-4">
                    <div className="flex items-center gap-1 overflow-x-auto py-0.5 no-scrollbar">
                      <span className="mr-1 shrink-0 font-mono text-[10px] font-semibold uppercase text-white/40">
                        Стиль:
                      </span>
                      {STYLE_PRESETS.map((p) => {
                        const active = stylePreset === p.id;
                        return (
                          <button
                            key={p.id}
                            type="button"
                            onClick={() => setStylePreset(p.id)}
                            className={cn(
                              "inline-flex shrink-0 items-center gap-1 rounded-lg px-2 py-0.5 text-[11px] font-medium transition",
                              active
                                ? "bg-[#22d3ee]/20 font-semibold text-[#22d3ee] ring-1 ring-[#22d3ee]/40"
                                : "bg-white/[0.04] text-white/60 hover:bg-white/[0.08] hover:text-white",
                            )}
                          >
                            <span>{p.icon}</span>
                            <span>{p.label}</span>
                          </button>
                        );
                      })}
                    </div>
                    <div className="flex items-center gap-1.5 shrink-0">
                      <button
                        type="button"
                        onClick={handleRandomPrompt}
                        className="inline-flex shrink-0 items-center gap-1 rounded-lg border border-white/10 bg-white/[0.04] px-2 py-0.5 text-[11px] font-medium text-white/70 transition hover:border-[#22d3ee]/40 hover:bg-[#22d3ee]/10 hover:text-white"
                        title="Подставить готовый красивый пример промпта"
                      >
                        <Dices className="h-3 w-3 text-[#22d3ee]" />
                        <span>Случайный</span>
                      </button>
                      <button
                        type="button"
                        disabled={isEnhancingPrompt}
                        onClick={handleEnhancePrompt}
                        className="inline-flex shrink-0 items-center gap-1 rounded-lg border border-[#22d3ee]/30 bg-[#22d3ee]/10 px-2 py-0.5 text-[11px] font-semibold text-[#22d3ee] transition hover:bg-[#22d3ee]/20 disabled:opacity-50"
                        title="Улучшить и детализировать текущий промпт с помощью ИИ"
                      >
                        {isEnhancingPrompt ? (
                          <Loader2 className="h-3 w-3 animate-spin text-[#22d3ee]" />
                        ) : (
                          <FileText className="h-3 w-3 text-[#22d3ee]" />
                        )}
                        <span>{isEnhancingPrompt ? "Улучшаем…" : "Улучшить"}</span>
                      </button>
                      <button
                        type="button"
                        onClick={() => setShowNegativePrompt((v) => !v)}
                        className={cn(
                          "inline-flex shrink-0 items-center gap-1 rounded-lg px-2 py-0.5 font-mono text-[10px] transition",
                          showNegativePrompt || negativePrompt
                            ? "bg-purple-500/20 text-purple-300 ring-1 ring-purple-500/30"
                            : "bg-white/[0.04] text-white/45 hover:text-white",
                        )}
                      >
                        <span>⛔ Негативный</span>
                        {negativePrompt && <span className="h-1.5 w-1.5 rounded-full bg-purple-400" />}
                      </button>
                    </div>
                  </div>
                )}

                {/* Expandable Negative Prompt input */}
                {mediaType === "image" && showNegativePrompt && (
                  <div className="border-b border-white/[0.06] bg-black/20 px-3 py-2 lg:px-4">
                    <input
                      type="text"
                      value={negativePrompt}
                      onChange={(e) => setNegativePrompt(e.target.value)}
                      placeholder="Отрицательный промпт: чего НЕ должно быть на картинке (напр. размытие, лишние пальцы, текст, мусор)..."
                      className="w-full rounded-lg border border-white/10 bg-[#16161b] px-3 py-1.5 text-[12px] text-white placeholder-white/30 focus:border-purple-400 focus:outline-none"
                    />
                  </div>
                )}
                {(!kieActive || kieTextField) && (
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
                      style={{ outline: "none" }}
                      className="w-full resize-none bg-transparent text-[13px] leading-relaxed text-white/90 placeholder:text-white/30 border-0 outline-none ring-0 focus:border-0 focus:outline-none focus:ring-0"
                    />
                    {mediaType === "image" && stylePreset !== "none" && (
                      <div className="mt-1 flex items-center gap-1.5 rounded-lg border border-[#22d3ee]/25 bg-[#22d3ee]/5 px-2.5 py-1 text-[11px] text-[#22d3ee]/90">
                        <span className="font-semibold">
                          Стиль «{STYLE_PRESETS.find((p) => p.id === stylePreset)?.label}»:
                        </span>
                        <span className="truncate font-mono text-[10px] text-white/60">
                          {STYLE_PRESETS.find((p) => p.id === stylePreset)?.suffix}
                        </span>
                      </div>
                    )}
                  </div>
                )}
                {kieActive && kieModel && !kieTextField && (
                  <div className="px-3 pt-3 lg:px-4">
                    <div className="rounded-xl border border-white/[0.08] bg-white/[0.02] px-3 py-2.5 text-[12px] leading-relaxed text-white/60">
                      {kieModel.hint || kieModel.desc}
                    </div>
                  </div>
                )}

                <div className="flex flex-wrap items-end gap-2 border-t border-white/[0.08] px-3 py-2.5 lg:px-4">
                  <div className="relative" ref={modelRef}>
                    <ChipButton
                      active={modelOpen}
                      onClick={() => {
                        setModelOpen((v) => !v);
                        setOpenChip(null);
                      }}
                    >
                      {currentIcon ? (
                        // eslint-disable-next-line @next/next/no-img-element
                        <img
                          src={currentIcon}
                          alt=""
                          width={18}
                          height={18}
                          className="h-[18px] w-[18px] shrink-0 rounded-md object-cover ring-1 ring-white/10"
                        />
                      ) : (
                        <span className="inline-flex h-[18px] w-[18px] shrink-0 items-center justify-center rounded-md bg-[#38bdf8]/20 font-mono text-[10px] font-bold text-[#38bdf8] ring-1 ring-white/10">
                          K
                        </span>
                      )}
                      <span className="font-medium">
                        {currentWired ? (
                          <span className="mr-1 font-mono text-[#22d3ee]">+</span>
                        ) : null}
                        {currentName}
                      </span>
                      <ChevronDown className="h-3 w-3 opacity-60" />
                    </ChipButton>
                    {modelOpen && (
                      <ModelPickerPopover
                        mediaType={mediaType}
                        selectedSlug={activeSlug}
                        kieModels={kieModels}
                        creditUsd={kieCatalogQ.data?.credit_usd ?? 0.005}
                        onSelect={(slug) => {
                          if (mediaType === "image") setImageSlug(slug);
                          else if (mediaType === "video") setVideoSlug(slug);
                          else setAudioSlug(slug);
                          if (!slug.startsWith("kie:")) applyModelDefaults(slug, mediaType);
                          setModelOpen(false);
                        }}
                      />
                    )}
                  </div>

                  {!kieActive && mediaType === "video" && videoModel.chips.includes("orientation") && (
                    <div className="inline-flex gap-0.5 rounded-full border border-white/10 bg-[#16161b] p-0.5">
                      {(["video", "image"] as const).map((o) => (
                        <button
                          key={o}
                          type="button"
                          onClick={() => setOrientation(o)}
                          className={cn(
                            "rounded-full px-2.5 py-1 text-[11px] font-medium transition",
                            orientation === o
                              ? "bg-[#22d3ee]/20 text-[#22d3ee] font-semibold"
                              : "text-white/45 hover:text-white",
                          )}
                        >
                          {o === "video" ? "По видео" : "По картинке"}
                        </button>
                      ))}
                    </div>
                  )}

                  {!kieActive && mediaType === "video" && videoModel.chips.includes("quality") && (
                    <div className="inline-flex gap-0.5 rounded-full border border-white/10 bg-[#16161b] p-0.5">
                      {chipOptions(videoSlug, "quality").map((q) => (
                        <button
                          key={q}
                          type="button"
                          onClick={() => setMotionQuality(q)}
                          className={cn(
                            "rounded-full px-2.5 py-1 font-mono text-[11px] uppercase transition",
                            motionQuality === q
                              ? "bg-[#22d3ee]/20 text-[#22d3ee] font-semibold"
                              : "text-white/45 hover:text-white",
                          )}
                        >
                          {q}
                        </button>
                      ))}
                    </div>
                  )}

                  {!kieActive && dockChips.map((chip) => {
                    if (chip === "audio") {
                      return (
                        <button
                          key="audio"
                          type="button"
                          onClick={() => setGenerateAudio((v) => !v)}
                          className={cn(
                            "inline-flex h-9 items-center gap-1.5 rounded-xl border px-3 text-[12px] font-medium transition",
                            generateAudio
                              ? "border-[#22d3ee]/40 bg-[#22d3ee]/15 text-[#22d3ee]"
                              : "border-white/10 bg-[#16161b] text-white/70 hover:border-white/20 hover:text-white",
                          )}
                          title={generateAudio ? "Со звуком" : "Без звука"}
                        >
                          {OUTSEE_CHIP_LABELS.audio}
                          <span className="font-mono text-[10px] text-white/45">
                            {generateAudio ? "on" : "off"}
                          </span>
                        </button>
                      );
                    }
                    if (chip === "image-input") {
                      return null;
                    }
                    if (chip === "instrumental") {
                      return (
                        <button
                          key="instrumental"
                          type="button"
                          onClick={() => setInstrumental((v) => !v)}
                          className={cn(
                            "inline-flex h-9 items-center gap-1.5 rounded-xl border px-3 text-[12px] font-medium transition",
                            !instrumental
                              ? "border-[#22d3ee]/40 bg-[#22d3ee]/15 text-[#22d3ee]"
                              : "border-white/10 bg-[#16161b] text-white/70 hover:border-white/20 hover:text-white",
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
                    let selectedVal = aspect;
                    if (chip === "resolution") {
                      display = mediaType === "image" ? resolution : videoResolution;
                      onSelect = mediaType === "image" ? setResolution : setVideoResolution;
                      selectedVal = display;
                    } else if (chip === "detail") {
                      display = `Детализация: ${detailLabel(detail)}`;
                      onSelect = setDetail;
                      selectedVal = detail;
                    } else if (chip === "duration") {
                      display = `${duration}с`;
                      onSelect = setDuration;
                      selectedVal = duration;
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
                        selectedValue={selectedVal}
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

                  {/* KIE: динамические настройки модели из каталога */}
                  {kieActive &&
                    kieModel &&
                    kieChipFields(kieModel)
                      .filter((f) => kieFieldVisible(f, kieValues))
                      .map((f) => (
                        <KieFieldChip
                          key={f.name}
                          field={f}
                          values={kieValues}
                          openChip={openChip}
                          setOpenChip={setOpenChip}
                          setModelOpen={setModelOpen}
                          onChange={(name, v) =>
                            setKieValues((prev) => ({ ...prev, [name]: v }))
                          }
                        />
                      ))}

                  <div className="ml-auto flex flex-wrap items-center gap-2">
                    {!kieActive && mediaType === "video" &&
                      (videoSlug === "sora-2" ||
                        videoSlug === "sora2-portrait" ||
                        videoSlug === "sora2-landscape") && (
                        <div className="inline-flex gap-0.5 rounded-xl border border-white/10 bg-[#16161b] p-0.5">
                          {(["small", "large"] as const).map((sz) => (
                            <button
                              key={sz}
                              type="button"
                              onClick={() => setSoraSize(sz)}
                              className={cn(
                                "rounded-lg px-2.5 py-1.5 font-mono text-[10px] uppercase transition",
                                soraSize === sz
                                  ? "bg-[#22d3ee]/20 text-[#22d3ee] font-semibold"
                                  : "text-white/40 hover:text-white",
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
                      className="rounded-xl border border-white/10 bg-white/[0.04] px-3 py-2 text-[11px] font-medium text-white/70 transition hover:border-white/25 hover:bg-white/[0.08] hover:text-white disabled:opacity-40"
                    >
                      {saveGlobal.isPending ? "…" : "Сохранить"}
                    </button>
                    {!kieActive && (
                      <button
                        type="button"
                        disabled={applyToProject.isPending || projectId == null}
                        onClick={() => applyToProject.mutate()}
                        className="rounded-xl border border-white/10 bg-white/[0.04] px-3 py-2 text-[11px] font-medium text-white/70 transition hover:border-white/25 hover:bg-white/[0.08] hover:text-white disabled:opacity-40"
                        title="Скопировать глобальные настройки в выбранный проект"
                      >
                        В проект
                      </button>
                    )}
                    {kieActive && kieCreditsQ.data?.credits != null && (
                      <div
                        className="inline-flex h-9 items-center gap-1.5 rounded-xl border border-white/10 bg-[#16161b] px-2.5 font-mono text-[11px] text-white/60"
                        title="Баланс kie.ai"
                      >
                        {kieCreditsQ.data.credits.toFixed(0)} кр
                      </div>
                    )}
                    <div
                      className="inline-flex h-9 items-center gap-1.5 rounded-xl border border-white/10 bg-[#16161b] px-2.5 font-mono text-[11px] text-white/80"
                      title={
                        kieActive
                          ? "kie.ai: 1 кр = $0.005. Цена за выбранные параметры."
                          : "1 токен = $0.10 (10¢). Цена за выбранные параметры."
                      }
                    >
                      <Coins className="h-3 w-3 text-[#22d3ee]" strokeWidth={2.5} />
                      <span>{priceLabel}</span>
                    </div>
                    {(mediaType === "image" || mediaType === "video") && (
                      <div
                        className="inline-flex h-9 items-center gap-0.5 rounded-xl border border-white/10 bg-[#16161b] p-0.5"
                        title={`Пакетная генерация: ${mediaType === "image" ? "1, 2 или 4 фото" : "1 или 2 видео"}`}
                      >
                        {(mediaType === "image" ? ([1, 2, 4] as const) : ([1, 2] as const)).map(
                          (cnt) => (
                            <button
                              key={cnt}
                              type="button"
                              onClick={() => setBatchCount(cnt)}
                              className={cn(
                                "rounded-lg px-2 py-1 font-mono text-[11px] font-bold transition",
                                batchCount === cnt
                                  ? "bg-[#22d3ee]/20 text-[#22d3ee] ring-1 ring-[#22d3ee]/40"
                                  : "text-white/45 hover:text-white",
                              )}
                            >
                              {cnt}x
                            </button>
                          ),
                        )}
                      </div>
                    )}
                    <button
                      type="button"
                      disabled={
                        createGenerate.isPending ||
                        (kieActive
                          ? (kieTextField && !prompt.trim()) || !kieConfigured
                          : !prompt.trim() ||
                            (mediaType === "audio" && projectId == null) ||
                            (mediaType !== "audio" && !canApiDirect))
                      }
                      onClick={() => {
                        if (createGenerate.isPending) return;
                        createGenerate.mutate();
                      }}
                      className={cn(
                        "inline-flex min-w-[145px] items-center justify-center gap-2 rounded-xl bg-gradient-to-r from-[#22d3ee] to-[#0ea5e9] px-4 py-2 text-[12px] font-extrabold uppercase tracking-wider text-black shadow-[0_0_20px_rgba(34,211,238,0.3)] transition-all duration-200 hover:brightness-110 hover:shadow-[0_0_25px_rgba(34,211,238,0.45)] disabled:opacity-40 disabled:pointer-events-none",
                      )}
                      title={
                        createGenerate.isPending
                          ? "Уже ставится в очередь…"
                          : !canApiDirect && mediaType !== "audio"
                            ? "Нужен OUTSEE_API_KEY или KIE_API_KEY в .env"
                            : `Сгенерировать (${batchCount > 1 ? `${batchCount} шт` : "1 шт"}, лимит ${maxParallel}) · ${priceLabel}`
                      }
                    >
                      {createGenerate.isPending ? (
                        <>
                          <Loader2 className="h-3.5 w-3.5 animate-spin" />
                          <span>Запуск…</span>
                        </>
                      ) : (
                        <>
                          <Play className="h-3.5 w-3.5 fill-current" />
                          <span>
                            Генерировать
                            {batchCount > 1 ? ` (${batchCount}x)` : ""}
                          </span>
                        </>
                      )}
                    </button>
                  </div>
                </div>
              </div>
            </div>
          </div>
        </section>
      </div>

      {/* Lightbox full screen modal & Prompt Inspector */}
      {lightboxOpen && selected?.preview_url && (
        <div
          className="fixed inset-0 z-50 flex items-center justify-center bg-black/95 p-3 md:p-6 backdrop-blur-2xl animate-in fade-in duration-200"
          onClick={() => setLightboxOpen(false)}
        >
          <div
            className="relative flex flex-col md:flex-row items-center justify-center max-h-[96vh] max-w-[98vw] gap-4 w-full"
            onClick={(e) => e.stopPropagation()}
          >
            {/* Close & Action floating buttons */}
            <div className="absolute top-2 right-2 z-50 flex items-center gap-2">
              <div className="inline-flex items-center rounded-xl border border-white/20 bg-black/80 p-0.5 backdrop-blur-md shadow-2xl">
                <button
                  type="button"
                  onClick={() =>
                    void downloadMediaFile(
                      selected.preview_url || selected.raw_url || "",
                      selected.label || "generation",
                      downloadFormat,
                      selected.path,
                    )
                  }
                  className="inline-flex items-center gap-1.5 rounded-lg px-3 py-1.5 text-[12px] font-semibold text-white/90 transition hover:bg-white/[0.12] hover:text-white"
                >
                  <Download className="h-4 w-4 text-[#22d3ee]" />
                  Скачать
                </button>
                {selected.kind === "image" && (
                  <div className="flex items-center border-l border-white/20 pl-1 pr-1 font-mono text-[11px]">
                    {(["png", "jpg", "webp"] as const).map((fmt) => (
                      <button
                        key={fmt}
                        type="button"
                        onClick={() => setDownloadFormat(fmt)}
                        className={cn(
                          "rounded px-2 py-0.5 uppercase transition",
                          downloadFormat === fmt
                            ? "bg-[#22d3ee]/25 font-bold text-[#22d3ee]"
                            : "text-white/50 hover:text-white",
                        )}
                      >
                        {fmt}
                      </button>
                    ))}
                  </div>
                )}
              </div>
              <button
                type="button"
                onClick={() => {
                  setLightboxOpen(false);
                  deleteItem.mutate(selected);
                }}
                className="inline-flex h-9 items-center gap-1.5 rounded-xl border border-red-500/30 bg-black/80 px-3 text-[12px] font-medium text-red-400 backdrop-blur transition hover:border-red-500/50 hover:bg-red-500/20 hover:text-red-300 shadow-2xl"
                title="Удалить из истории"
              >
                <Trash2 className="h-3.5 w-3.5" />
                <span>Удалить</span>
              </button>
              <button
                type="button"
                onClick={() => setLightboxOpen(false)}
                className="flex h-9 w-9 items-center justify-center rounded-xl border border-white/20 bg-black/80 text-white/80 backdrop-blur transition hover:bg-white/20 hover:text-white shadow-2xl"
                title="Закрыть"
              >
                <X className="h-5 w-5" />
              </button>
            </div>

            {/* Media Area */}
            <div className="flex flex-1 items-center justify-center max-h-[88vh] max-w-full min-w-0">
              {selected.kind === "video" ? (
                <video
                  src={selected.preview_url}
                  controls
                  autoPlay
                  className="max-h-[88vh] max-w-full rounded-2xl border border-white/15 bg-black object-contain shadow-[0_0_80px_rgba(0,0,0,0.9)]"
                />
              ) : selected.kind === "audio" ? (
                <div className="flex w-full max-w-md flex-col items-center gap-4 rounded-2xl border border-white/15 bg-[#121216]/90 p-8 backdrop-blur-2xl shadow-[0_20px_50px_rgba(0,0,0,0.8)]">
                  <Music className="h-10 w-10 text-[#22d3ee]" />
                  <div className="text-base font-semibold text-white/90">{selected.label}</div>
                  <audio src={selected.preview_url} controls className="w-full" />
                </div>
              ) : (
                // eslint-disable-next-line @next/next/no-img-element
                <img
                  src={selected.preview_url}
                  alt=""
                  className="max-h-[88vh] max-w-full rounded-2xl border border-white/15 bg-black object-contain shadow-[0_0_80px_rgba(0,0,0,0.9)]"
                />
              )}
            </div>

            {/* Prompt Inspector Panel */}
            <div className="flex w-full md:w-84 shrink-0 flex-col gap-3 rounded-2xl border border-white/15 bg-[#121216]/95 p-4 backdrop-blur-2xl shadow-[0_20px_60px_rgba(0,0,0,0.9)] max-h-[88vh] overflow-y-auto ring-1 ring-white/10">
              <div className="flex items-center justify-between border-b border-white/10 pb-2.5">
                <div className="flex items-center gap-1.5 text-xs font-bold uppercase tracking-wider text-[#22d3ee]">
                  <FileText className="h-4 w-4" />
                  <span>Инспектор</span>
                </div>
                {selected.model && (
                  <span className="rounded-md border border-white/10 bg-white/[0.04] px-2 py-0.5 font-mono text-[10px] text-white/70">
                    {selected.model}
                  </span>
                )}
              </div>

              {/* Prompt Text Box */}
              <div>
                <div className="mb-1 text-[11px] font-semibold text-white/50">Промпт:</div>
                <div className="max-h-52 overflow-y-auto rounded-xl border border-white/10 bg-black/40 p-3 text-[12px] leading-relaxed text-white/90 select-text">
                  {selected.prompt || "Без текстового описания"}
                </div>
              </div>

              {/* Quick Actions: Copy & Insert into Prompt Dock */}
              <div className="grid grid-cols-2 gap-2">
                <button
                  type="button"
                  onClick={() => {
                    if (selected.prompt) {
                      void navigator.clipboard.writeText(selected.prompt);
                      toast.success("Промпт скопирован в буфер 📋");
                    }
                  }}
                  disabled={!selected.prompt}
                  className="inline-flex h-9 items-center justify-center gap-1.5 rounded-xl border border-white/15 bg-white/[0.05] px-3 text-[11px] font-semibold text-white/85 transition hover:border-white/30 hover:bg-white/[0.1] hover:text-white disabled:opacity-40"
                >
                  <Copy className="h-3.5 w-3.5" />
                  <span>Скопировать</span>
                </button>
                <button
                  type="button"
                  onClick={() => {
                    if (selected.prompt) {
                      setPrompt(selected.prompt);
                      setLightboxOpen(false);
                      toast.success("Промпт подставлен в поле ввода ✍️");
                    }
                  }}
                  disabled={!selected.prompt}
                  className="inline-flex h-9 items-center justify-center gap-1.5 rounded-xl border border-[#22d3ee]/40 bg-[#22d3ee]/15 px-3 text-[11px] font-bold text-[#22d3ee] transition hover:bg-[#22d3ee]/25 disabled:opacity-40"
                >
                  <CornerDownLeft className="h-3.5 w-3.5" />
                  <span>Вставить в чат</span>
                </button>
              </div>

              {/* References Strip (if any) */}
              {((selected.reference_images && selected.reference_images.length > 0) || selected.first_frame_url) && (
                <div className="border-t border-white/10 pt-2.5">
                  <div className="mb-1.5 text-[11px] font-semibold text-white/50">
                    Использованные референсы ({selected.reference_images?.length || 1}):
                  </div>
                  <div className="flex flex-wrap gap-1.5">
                    {(selected.reference_images && selected.reference_images.length > 0
                      ? selected.reference_images
                      : [selected.first_frame_url!]
                    ).map((u, i) => (
                      <a
                        key={i}
                        href={u}
                        target="_blank"
                        rel="noreferrer"
                        className="group/ref relative block h-12 w-12 overflow-hidden rounded-lg border border-white/20 transition hover:scale-105 hover:border-[#22d3ee]"
                        title="Открыть референс в новой вкладке"
                      >
                        {/* eslint-disable-next-line @next/next/no-img-element */}
                        <img src={u} alt="" className="h-full w-full object-cover" />
                      </a>
                    ))}
                  </div>
                </div>
              )}

              {/* Meta details */}
              <div className="space-y-1 border-t border-white/10 pt-2.5 font-mono text-[10px] text-white/45">
                {selected.elapsed_label || selected.elapsed_sec != null ? (
                  <div>
                    Время генерации:{" "}
                    <span className="font-semibold text-white/70">
                      {selected.elapsed_label || formatElapsedMinSec(selected.elapsed_sec)}
                    </span>
                  </div>
                ) : null}
                <div>
                  ID: <span className="text-white/60">{selected.id}</span>
                </div>
              </div>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

function kieFieldVisible(f: KieField, values: Record<string, unknown>): boolean {
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

/** KIE: одна настройка модели в виде чипа (select/toggle/number/text). */
function KieFieldChip({
  field,
  values,
  openChip,
  setOpenChip,
  setModelOpen,
  onChange,
}: {
  field: KieField;
  values: Record<string, unknown>;
  openChip?: string | null;
  setOpenChip?: (v: string | null) => void;
  setModelOpen?: (v: boolean) => void;
  onChange: (name: string, v: unknown) => void;
}) {
  const v = values[field.name] ?? field.default;

  if (field.kind === "toggle") {
    const on = v === true || String(v).toLowerCase() === "true";
    return (
      <button
        type="button"
        onClick={() => onChange(field.name, !on)}
        title={field.desc || field.label}
        className={cn(
          "inline-flex h-9 items-center gap-1.5 rounded-xl border px-3 text-[12px] font-medium transition",
          on
            ? "border-[#22d3ee]/40 bg-[#22d3ee]/15 text-[#22d3ee]"
            : "border-white/10 bg-[#16161b] text-white/70 hover:border-white/20 hover:text-white",
        )}
      >
        {field.label}
        <span className="font-mono text-[10px] text-white/45">{on ? "on" : "off"}</span>
      </button>
    );
  }

  if (field.kind === "select") {
    const formatOption = (opt: string) => {
      const fn = field.name.toLowerCase();
      const o = opt.toLowerCase();
      if (fn === "quality") {
        if (o === "basic") return { label: "1K", hint: "Базовое" };
        if (o === "high") return { label: "2K", hint: "Высокое" };
        if (o === "std" || o === "standard") return { label: "Standard", hint: "720p" };
        if (o === "pro") return { label: "Pro", hint: "1080p" };
      }
      if (fn === "output_format" || fn === "format") {
        return { label: opt.toUpperCase() };
      }
      return { label: opt };
    };

    const options = (field.options || []).map((o) => {
      const meta = formatOption(o);
      return { id: o, label: meta.label, hint: meta.hint };
    });
    const currentVal = String(v ?? field.options?.[0] ?? "—");
    const selectedOpt = options.find((o) => o.id === currentVal);
    const displayVal = selectedOpt ? selectedOpt.label : currentVal;

    return (
      <OptionDropdown
        label={field.label}
        value={displayVal}
        selectedValue={currentVal}
        open={openChip === field.name}
        onOpenChange={(isOpen) => {
          if (setOpenChip) setOpenChip(isOpen ? field.name : null);
          if (isOpen && setModelOpen) setModelOpen(false);
        }}
        options={options}
        onSelect={(optId) => onChange(field.name, optId)}
        mono
      />
    );
  }

  if (field.kind === "number") {
    return (
      <div
        className="inline-flex h-9 items-center gap-1.5 rounded-xl border border-white/10 bg-[#16161b] px-2.5"
        title={field.desc || field.label}
      >
        <span className="text-[11px] text-white/55">{field.label}</span>
        <input
          type="number"
          value={v === undefined || v === null ? "" : String(v)}
          min={field.min}
          max={field.max}
          step={field.step ?? 1}
          onChange={(e) =>
            onChange(field.name, e.target.value === "" ? undefined : Number(e.target.value))
          }
          className="w-16 bg-transparent font-mono text-[12px] text-white/90 outline-none"
        />
      </div>
    );
  }

  // text
  return (
    <div
      className="inline-flex h-9 items-center gap-1.5 rounded-xl border border-white/10 bg-[#16161b] px-2.5"
      title={field.desc || field.label}
    >
      <span className="text-[11px] text-white/55">{field.label}</span>
      <input
        value={String(v ?? "")}
        onChange={(e) => onChange(field.name, e.target.value)}
        placeholder="—"
        className="w-28 bg-transparent text-[12px] text-white/90 outline-none placeholder:text-white/25"
      />
    </div>
  );
}

const KIE_FILE_ACCEPT: Record<string, string> = {
  images: "image/png,image/jpeg,image/webp,image/bmp,image/gif",
  videos: "video/mp4,video/quicktime,video/webm",
  audios: "audio/mpeg,audio/wav,audio/mp4,audio/x-m4a",
};

/** KIE: кнопка вложения в стиле «Стартовый кадр» — загрузка в kie или URL. */
function KieAttachButton({
  field,
  values,
  onChange,
}: {
  field: KieField;
  values: Record<string, unknown>;
  onChange: (name: string, items: string[]) => void;
}) {
  const inputRef = useRef<HTMLInputElement>(null);
  const [busy, setBusy] = useState(false);
  const [urlMode, setUrlMode] = useState(false);
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
    setUrlMode(false);
  };

  return (
    <>
      {items.map((u, i) => (
        <span
          key={`${u}-${i}`}
          className="inline-flex h-10 items-center gap-2 rounded-xl border border-[#22d3ee]/40 bg-[#22d3ee]/10 px-3 text-[12px] font-medium text-[#22d3ee]"
        >
          {field.kind === "images" ? (
            // eslint-disable-next-line @next/next/no-img-element
            <img src={u} alt="" className="h-7 w-7 rounded-md object-cover ring-1 ring-white/15" />
          ) : (
            <Paperclip className="h-4 w-4" />
          )}
          <span className="max-w-[120px] truncate font-mono text-[10px] text-white/70">
            {u.split("/").pop()}
          </span>
          <span
            className="cursor-pointer text-white/45 hover:text-white"
            onClick={() => onChange(field.name, items.filter((_, j) => j !== i))}
          >
            <X className="h-3.5 w-3.5" />
          </span>
        </span>
      ))}
      {canAdd && (
        <span className="inline-flex items-center gap-1">
          <input
            ref={inputRef}
            type="file"
            accept={KIE_FILE_ACCEPT[field.kind] || "*/*"}
            className="hidden"
            onChange={(e) => {
              const f = e.target.files?.[0];
              if (!f) return;
              if (f.size === 0) {
                toast.error("Выбран пустой файл (0 байт)");
                e.target.value = "";
                return;
              }
              if (field.kind === "audios" && f.size < 1000) {
                toast.error("Файл слишком мал или не содержит аудиоданных (минимум 1 КБ)");
                e.target.value = "";
                return;
              }
              if (field.kind === "videos" && f.size < 2000) {
                toast.error("Файл слишком мал или не содержит видеоданных");
                e.target.value = "";
                return;
              }
              setBusy(true);
              api
                .kieUpload(f)
                .then((r) => {
                  onChange(field.name, [...items, r.url]);
                  toast.success(`${field.label}: файл загружен`);
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
            title={field.desc || field.label}
            className="inline-flex h-10 items-center gap-2 rounded-xl border border-dashed border-white/25 bg-white/[0.03] px-3 text-[12px] font-medium text-white/70 transition hover:border-white/40 hover:text-white disabled:opacity-50"
          >
            {busy ? (
              <Loader2 className="h-4 w-4 animate-spin text-[#22d3ee]" />
            ) : (
              <Paperclip className="h-4 w-4" />
            )}
            {field.label}
            {field.required ? <span className="text-red-400">*</span> : null}
          </button>
          <button
            type="button"
            onClick={() => setUrlMode((v) => !v)}
            title="Вставить URL вместо загрузки"
            className="inline-flex h-10 w-8 items-center justify-center rounded-xl border border-white/10 bg-white/[0.03] text-white/50 transition hover:border-white/25 hover:text-white"
          >
            <Link2 className="h-3.5 w-3.5" />
          </button>
          {urlMode && (
            <span className="inline-flex items-center gap-1">
              <input
                value={urlDraft}
                onChange={(e) => setUrlDraft(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === "Enter") {
                    e.preventDefault();
                    addUrl();
                  }
                }}
                placeholder="https://…"
                className="h-10 w-48 rounded-xl border border-white/10 bg-black/40 px-2.5 text-[11px] text-white/80 outline-none placeholder:text-white/25 focus:border-[#22d3ee]/50"
              />
              <button
                type="button"
                onClick={addUrl}
                className="h-10 rounded-xl border border-white/10 px-2.5 text-[11px] text-white/60 transition hover:border-white/25 hover:text-white"
              >
                +
              </button>
            </span>
          )}
        </span>
      )}
    </>
  );
}

function ChipButton({
  children,
  onClick,
  active,
  title,
}: {
  children: React.ReactNode;
  onClick: () => void;
  active?: boolean;
  title?: string;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      title={title}
      className={cn(
        "inline-flex h-9 items-center gap-1.5 rounded-xl border px-3 text-[12px] font-medium transition-all duration-200",
        active
          ? "border-[#22d3ee] bg-[#22d3ee]/15 text-[#22d3ee] shadow-[0_0_15px_rgba(34,211,238,0.2)]"
          : "border-white/10 bg-[#16161b] text-white/80 hover:border-white/20 hover:bg-[#1f1f26] hover:text-white",
      )}
    >
      {children}
    </button>
  );
}

function OptionDropdown({
  label,
  value,
  selectedValue,
  open,
  onOpenChange,
  options,
  onSelect,
  mono,
}: {
  label: string;
  value: string;
  selectedValue?: string;
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

  const currentVal = selectedValue ?? value;

  return (
    <div className="relative" ref={ref}>
      <ChipButton active={open} onClick={() => onOpenChange(!open)} title={label}>
        <span className={cn(mono && "font-mono tabular-nums")}>{value}</span>
        <ChevronDown className="h-3 w-3 opacity-60" />
      </ChipButton>
      {open && (
        <div
          className="absolute bottom-full left-0 z-[1000] mb-2 max-h-60 overflow-y-auto rounded-xl border border-white/15 bg-[#121216]/95 backdrop-blur-2xl p-1.5 shadow-[0_15px_40px_rgba(0,0,0,0.85)] ring-1 ring-white/10"
          style={{ minWidth: 140 }}
        >
          {options.map((opt) => {
            const active = opt.id === currentVal || opt.label === currentVal || opt.id === value || opt.label === value;
            return (
              <button
                key={opt.id}
                type="button"
                onClick={() => {
                  onSelect(opt.id);
                  onOpenChange(false);
                }}
                className={cn(
                  "flex w-full items-center justify-between rounded-lg px-2.5 py-2 text-left text-[12px] transition",
                  active
                    ? "bg-[#22d3ee]/15 text-[#22d3ee] font-semibold"
                    : "text-white/80 hover:bg-white/[0.08] hover:text-white",
                )}
              >
                <span className={cn(mono && "font-mono")}>{opt.label}</span>
                {opt.hint && <span className="text-[10px] text-white/40">{opt.hint}</span>}
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
  kieModels = [],
  creditUsd = 0.005,
  onSelect,
}: {
  mediaType: OutseeMediaType;
  selectedSlug: string;
  kieModels?: KieModelSpec[];
  creditUsd?: number;
  onSelect: (slug: string) => void;
}) {
  const [search, setSearch] = useState("");
  const title =
    mediaType === "image"
      ? "Модели изображений"
      : mediaType === "video"
        ? "Модели видео"
        : "Модели аудио";
  const models = pickerModelsForType(mediaType);
  const kieForType = kieModels.filter((m) => {
    const id = m.id.toLowerCase();
    // GPT Image 2, Nano Banana 2, Veo 3.1 Lite — строго через Outsee!
    if (id.includes("veo") || id.includes("gpt-image") || id.includes("banana")) {
      return false;
    }
    const media =
      m.media || (m.category === "video" ? "video" : m.category === "image" ? "image" : "audio");
    return media === mediaType;
  });

  const q = search.trim().toLowerCase();

  const filteredModels = useMemo(() => {
    if (!q) return models;
    return models.filter(
      (m) =>
        m.displayName.toLowerCase().includes(q) ||
        m.description.toLowerCase().includes(q) ||
        m.slug.toLowerCase().includes(q),
    );
  }, [models, q]);

  const filteredKie = useMemo(() => {
    if (!q) return kieForType;
    return kieForType.filter(
      (m) =>
        m.label.toLowerCase().includes(q) ||
        (m.desc || "").toLowerCase().includes(q) ||
        (m.hint || "").toLowerCase().includes(q) ||
        m.id.toLowerCase().includes(q),
    );
  }, [kieForType, q]);

  const unifiedItems = useMemo(() => {
    // Collect icons from base models for quick lookup
    const iconBySlug = new Map<string, string>();
    for (const m of filteredModels) {
      if (m.icon) {
        iconBySlug.set(m.slug.toLowerCase(), m.icon);
        iconBySlug.set(m.slug.toLowerCase().replace(/[-_]/g, ""), m.icon);
      }
    }

    // Build authoritative KIE items first
    const kieModelIds = new Set<string>();
    const kieItems = filteredKie.map((m) => {
      const rawId = m.id.toLowerCase();
      const cleanId = rawId.replace(/[-_]/g, "");
      kieModelIds.add(rawId);
      kieModelIds.add(cleanId);
      const est = estimateKie(m, {}, creditUsd);

      const icon =
        iconBySlug.get(rawId) ||
        iconBySlug.get(cleanId) ||
        (rawId.includes("nano-banana") ? iconBySlug.get("nano-banana-2") : null) ||
        (rawId.includes("gpt-image") ? iconBySlug.get("gpt-image-2") : null) ||
        (rawId.includes("seedream") ? iconBySlug.get("seedream-5-pro") : null) ||
        (rawId.includes("veo") ? iconBySlug.get("veo-3-1-lite") || iconBySlug.get("veo-3-1") : null) ||
        (rawId.includes("kling") ? iconBySlug.get("kling-3-0") : null) ||
        (rawId.includes("hailuo") ? iconBySlug.get("hailuo-02") : null) ||
        (rawId.includes("topaz") ? iconBySlug.get("topaz-video-upscale") || iconBySlug.get("topaz-image-upscale") : null) ||
        (rawId.includes("suno") ? iconBySlug.get("suno-5-5") : null) ||
        null;

      return {
        slug: `kie:${m.id}`,
        label: m.label,
        desc: m.hint || m.desc,
        icon,
        letter: m.label.slice(0, 1),
        isKie: true,
        isTop: Boolean(m.is_top || m.isTop),
        badge: m.badge || (m.is_top || m.isTop ? "ТОП" : undefined),
        priceLabel: null as string | null,
        priceUsd: est.usd > 0 ? est.usd : null,
      };
    });

    // Only include remaining base models that are NOT present in KIE
    const remainingBaseItems = filteredModels
      .filter((m) => {
        const s = m.slug.toLowerCase();
        const c = s.replace(/[-_]/g, "");
        return !kieModelIds.has(s) && !kieModelIds.has(c);
      })
      .map((m) => ({
        slug: m.slug,
        label: m.displayName,
        desc: m.description,
        icon: m.icon,
        letter: null as string | null,
        isKie: false,
        isTop: Boolean(m.isTop || m.slug === "veo-3-1-lite"),
        badge: m.isTop ? "ТОП" : m.isNew ? "НОВОЕ" : undefined,
        priceLabel: m.price ? m.price : null,
        priceUsd: null as number | null,
      }));

    return [...kieItems, ...remainingBaseItems];
  }, [filteredModels, filteredKie, creditUsd]);

  const topItems = useMemo(() => unifiedItems.filter((i) => i.isTop), [unifiedItems]);
  const otherItems = useMemo(() => unifiedItems.filter((i) => !i.isTop), [unifiedItems]);

  const totalCount = unifiedItems.length;

  const renderCard = (item: (typeof unifiedItems)[0]) => {
    const active = item.slug === selectedSlug;
    const isTopCard = item.isTop;
    return (
      <button
        key={item.slug}
        type="button"
        onClick={() => onSelect(item.slug)}
        className={cn(
          "group relative flex items-start gap-2.5 rounded-xl border p-2.5 text-left transition-all duration-200",
          active
            ? isTopCard
              ? "border-[#22d3ee] bg-[#22d3ee]/15 text-white shadow-[0_0_22px_rgba(34,211,238,0.25)] ring-1 ring-[#22d3ee]/50"
              : "border-[#38bdf8] bg-[#38bdf8]/10 text-white shadow-[0_0_20px_rgba(56,189,248,0.2)]"
            : isTopCard
              ? "border-[#22d3ee]/25 bg-[#22d3ee]/[0.03] hover:border-[#22d3ee]/50 hover:bg-[#22d3ee]/[0.08]"
              : "border-white/[0.08] bg-white/[0.03] hover:border-white/20 hover:bg-white/[0.06]",
        )}
      >
        {item.badge && (
          <span
            className={cn(
              "absolute top-2 right-2 rounded-md px-1.5 py-0.5 font-mono text-[9px] font-bold tracking-wider shadow-sm",
              item.badge === "ТОП"
                ? "bg-[#22d3ee] text-black font-extrabold"
                : item.badge === "1080p" || item.badge === "2K/4K"
                  ? "bg-indigo-500 text-white"
                  : item.badge.includes("Звук")
                    ? "bg-purple-500 text-white"
                    : item.badge.includes("с")
                      ? "bg-emerald-500/20 text-emerald-300 border border-emerald-500/30"
                      : "bg-white/10 text-white/60",
            )}
          >
            {item.badge}
          </span>
        )}
        <div className="flex shrink-0 flex-col items-center">
          {item.icon ? (
            // eslint-disable-next-line @next/next/no-img-element
            <img
              src={item.icon}
              alt={item.label}
              className="h-10 w-10 rounded-lg object-cover ring-1 ring-white/10 transition group-hover:ring-[#22d3ee]/40"
            />
          ) : (
            <span
              className={cn(
                "inline-flex h-10 w-10 items-center justify-center rounded-lg font-mono text-[14px] font-bold ring-1 transition",
                isTopCard
                  ? "bg-[#22d3ee]/15 text-[#22d3ee] ring-[#22d3ee]/30 group-hover:ring-[#22d3ee]/60"
                  : "bg-[#38bdf8]/15 text-[#38bdf8] ring-white/10 group-hover:ring-[#38bdf8]/40",
              )}
            >
              {item.letter || item.label.slice(0, 1)}
            </span>
          )}
          {item.priceLabel && (
            <span className="mt-1 inline-flex items-center gap-0.5 font-mono text-[10px] text-white/60">
              <Coins className="h-2.5 w-2.5 text-[#22d3ee]" strokeWidth={2.5} />
              {item.priceLabel}
            </span>
          )}
          {item.priceUsd !== null && item.priceUsd !== undefined && (
            <span className="mt-1 inline-flex items-center gap-0.5 font-mono text-[10px] text-white/60">
              <Coins className="h-2.5 w-2.5 text-[#38bdf8]" strokeWidth={2.5} />
              {`$${item.priceUsd.toFixed(3)}`}
            </span>
          )}
        </div>
        <div className="min-w-0 flex-1 pr-7">
          <p
            className={cn(
              "truncate text-[12px] font-semibold",
              active
                ? "text-[#22d3ee]"
                : isTopCard
                  ? "text-white font-medium group-hover:text-[#22d3ee]"
                  : "text-white/90 group-hover:text-white",
            )}
          >
            {item.label}
          </p>
          <p className="mt-0.5 line-clamp-2 text-[10px] leading-snug text-white/45 transition group-hover:text-white/70">
            {item.desc}
          </p>
        </div>
      </button>
    );
  };

  return (
    <div
      className="absolute bottom-full left-0 z-50 mb-3 flex max-h-[76vh] flex-col overflow-hidden rounded-2xl border border-white/15 bg-[#121216]/95 backdrop-blur-2xl shadow-[0_25px_70px_rgba(0,0,0,0.85)] ring-1 ring-white/10"
      style={{
        width: mediaType === "video" ? 620 : mediaType === "audio" ? 450 : 520,
      }}
      role="dialog"
      aria-label={title}
      onPointerDown={(e) => e.stopPropagation()}
    >
      {/* Sticky Header with Search */}
      <div className="shrink-0 border-b border-white/[0.08] bg-[#16161b]/95 p-3 space-y-2.5">
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-2">
            <span className="text-[13px] font-bold tracking-tight text-white/90">{title}</span>
            <span className="rounded-full bg-white/10 px-2 py-0.5 font-mono text-[10px] font-semibold text-[#22d3ee]">
              {totalCount}
            </span>
          </div>
        </div>
        <div className="relative flex items-center">
          <Search className="absolute left-2.5 h-3.5 w-3.5 text-white/40" />
          <input
            type="text"
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            placeholder="Быстрый поиск модели (Kling, Nano, Flux, Veo, Sora...)"
            className="h-8 w-full rounded-xl border border-white/10 bg-black/40 pl-8 pr-7 text-[11px] text-white/90 placeholder:text-white/30 transition focus:border-[#22d3ee]/60 focus:outline-none focus:ring-1 focus:ring-[#22d3ee]/30"
          />
          {search && (
            <button
              type="button"
              onClick={() => setSearch("")}
              className="absolute right-2 text-white/40 hover:text-white"
            >
              <X className="h-3 w-3" />
            </button>
          )}
        </div>
      </div>

      {/* Unified single scrollable body */}
      <div className="flex-1 overflow-y-auto p-3 space-y-4">
        {/* Section 1: TOP Models */}
        {topItems.length > 0 && (
          <div>
            <div className="mb-2 flex items-center gap-1.5 px-1 text-[11px] font-bold uppercase tracking-[0.14em] text-[#22d3ee]">
              <span className="flex items-center gap-1">
                <span>🔥</span>
                <span>ТОП МОДЕЛИ</span>
              </span>
              <span className="rounded-full bg-[#22d3ee]/15 px-1.5 py-0.5 font-mono text-[9px] font-bold text-[#22d3ee]">
                {topItems.length}
              </span>
            </div>
            <div
              className="grid gap-2"
              style={{
                gridTemplateColumns: mediaType === "audio" ? "1fr" : "repeat(2, minmax(0, 1fr))",
              }}
            >
              {topItems.map((item) => renderCard(item))}
            </div>
          </div>
        )}

        {/* Section 2: Other Models */}
        {otherItems.length > 0 && (
          <div>
            <div className="mb-2 flex items-center gap-1.5 px-1 text-[10px] font-bold uppercase tracking-[0.14em] text-white/45">
              <span>Другие и специальные модели</span>
              <span className="font-mono text-white/25">({otherItems.length})</span>
            </div>
            <div
              className="grid gap-2"
              style={{
                gridTemplateColumns: mediaType === "audio" ? "1fr" : "repeat(2, minmax(0, 1fr))",
              }}
            >
              {otherItems.map((item) => renderCard(item))}
            </div>
          </div>
        )}

        {unifiedItems.length === 0 && (
          <div className="py-12 text-center text-[12px] text-white/40">
            Модели по запросу «<span className="text-white/70">{search}</span>» не найдены
          </div>
        )}
      </div>
    </div>
  );
}
