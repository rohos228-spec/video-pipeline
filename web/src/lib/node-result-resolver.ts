import { api, type ProjectAsset } from "@/lib/api";
import type { ArtifactDTO, FrameDTO, NodeRunStatus, ProjectDetail } from "@/lib/types";
import { MIN_GENERAL_PLAN_CHARS } from "@/lib/node-run-status";
import { isEnrichNode } from "@/lib/node-prompts";
import {
  pickGeneralPlanSheet,
  projectHasXlsx,
  ROW_VOICEOVER_V8,
  SHEET_PLAN_V8,
} from "@/lib/xlsx-sheets";

export type NodeResultItemKind = "text" | "image" | "video" | "file" | "xlsx" | "frames";

export interface NodeResultItem {
  id: string;
  label: string;
  kind: NodeResultItemKind;
  previewUrl?: string | null;
  downloadUrl?: string | null;
  content?: string | null;
  /** Путь на диске — для замены hero-файла. */
  filePath?: string | null;
  frameNumber?: number;
}

export type NodeResultReplaceMode = "text" | "xlsx" | "assets" | "studio" | "none";

export type NodeResultViewMode =
  | "default"
  | "xlsx_general_plan"
  | "voiceover_wide"
  | "xlsx_split_row"
  | "frame_prompts"
  | "frame_images"
  | "frame_videos"
  | "topic_edit";

export interface NodeResultSnapshot {
  hasResult: boolean;
  itemCount: number;
  summary: string;
  items: NodeResultItem[];
  replaceMode: NodeResultReplaceMode;
  viewMode: NodeResultViewMode;
  /** Поле проекта для текстовой замены (plan / script). */
  textField?: "general_plan" | "script_text";
}

export interface NodeResultContext {
  project: ProjectDetail | null | undefined;
  artifacts: ArtifactDTO[];
  assets: ProjectAsset[];
  frames: FrameDTO[];
  mediaImages: ProjectAsset[];
  mediaVideos: ProjectAsset[];
}

function meaningfulGeneralPlan(project: ProjectDetail | null | undefined): string | null {
  const text = project?.general_plan?.trim();
  if (!text || text.length < MIN_GENERAL_PLAN_CHARS) return null;
  return text;
}

function filterArtifacts(list: ArtifactDTO[], nodeType: string): ArtifactDTO[] {
  if (nodeType.includes("image") || nodeType === "images" || nodeType === "hitl_images") {
    return list.filter((a) => a.kind.includes("image") || a.kind.includes("scene"));
  }
  if (nodeType.includes("video") || nodeType === "videos" || nodeType === "hitl_videos") {
    return list.filter((a) => a.kind.includes("video"));
  }
  if (nodeType === "hero" || nodeType === "items" || nodeType === "hitl_hero") {
    return list.filter((a) => a.kind.includes("hero") || a.kind.includes("item"));
  }
  if (nodeType === "audio") {
    return list.filter((a) => a.kind.includes("audio") || a.kind.includes("subtitle"));
  }
  if (nodeType === "assemble" || nodeType === "hitl_final") {
    return list.filter((a) => a.kind.includes("final"));
  }
  return list;
}

function artifactItems(arts: ArtifactDTO[]): NodeResultItem[] {
  return arts.map((a) => {
    const path = a.path || "";
    const isVideo = /\.(mp4|webm)$/i.test(path) || a.kind.includes("video");
    const url = api.artifactFileUrl(a.uuid);
    const metaMtime = (a.meta as { file_mtime?: number } | undefined)?.file_mtime;
    const bust = metaMtime
      ? `?v=${metaMtime}`
      : a.created_at
        ? `?v=${encodeURIComponent(String(a.created_at))}`
        : "";
    return {
      id: a.uuid,
      label: a.kind,
      kind: isVideo ? "video" : path.match(/\.(png|jpe?g|webp|gif)$/i) ? "image" : "file",
      previewUrl: `${url}${bust}`,
      downloadUrl: `${url}${bust}`,
      filePath: path || null,
    };
  });
}

function assetItems(assets: ProjectAsset[]): NodeResultItem[] {
  return assets.map((a) => {
    const path = a.path || "";
    const isVideo = a.kind === "videos" || /\.(mp4|webm)$/i.test(path);
    const voiceover = (a as { voiceover?: string }).voiceover;
    return {
      id: String(a.id),
      label: a.label || a.kind || a.id,
      kind: a.kind === "xlsx" ? "xlsx" : isVideo ? "video" : a.preview_url ? "image" : "file",
      previewUrl: a.preview_url,
      downloadUrl: a.preview_url || undefined,
      content: voiceover ?? undefined,
      filePath: path || null,
      frameNumber: a.frame_id ?? undefined,
    };
  });
}

function xlsxAsset(assets: ProjectAsset[]): ProjectAsset | undefined {
  return assets.find((a) => a.id === "project.xlsx" || a.kind === "xlsx");
}

function textAsset(assets: ProjectAsset[], name: string): ProjectAsset | undefined {
  return assets.find((a) => a.id === name || a.label === name);
}

/** Зелёный кубик — только когда шаг реально завершён (не из общего xlsx). */
export function gateNodeResultVisibility(
  snapshot: NodeResultSnapshot,
  nodeType: string,
  nodeStatus?: NodeRunStatus,
): NodeResultSnapshot {
  if (nodeStatus === "done" || nodeStatus === "waiting_hitl") {
    return snapshot;
  }
  if (nodeType === "topic") {
    return snapshot;
  }
  if (
    !nodeStatus ||
    nodeStatus === "pending" ||
    nodeStatus === "skipped" ||
    nodeStatus === "running" ||
    nodeStatus === "failed"
  ) {
    return { ...snapshot, hasResult: false, itemCount: 0 };
  }
  return snapshot;
}

export function resolveNodeResult(
  nodeType: string,
  ctx: NodeResultContext,
  nodeStatus?: NodeRunStatus,
): NodeResultSnapshot {
  const snapshot = computeNodeResult(nodeType, ctx);
  return gateNodeResultVisibility(snapshot, nodeType, nodeStatus);
}

function computeNodeResult(nodeType: string, ctx: NodeResultContext): NodeResultSnapshot {
  const empty = (
    summary: string,
    replaceMode: NodeResultReplaceMode = "none",
    viewMode: NodeResultViewMode = "default",
  ): NodeResultSnapshot => ({
    hasResult: false,
    itemCount: 0,
    summary,
    items: [],
    replaceMode,
    viewMode,
  });

  const ready = (
    items: NodeResultItem[],
    summary: string,
    replaceMode: NodeResultReplaceMode,
    viewMode: NodeResultViewMode = "default",
    textField?: NodeResultSnapshot["textField"],
  ): NodeResultSnapshot => ({
    hasResult: items.length > 0,
    itemCount: items.length,
    summary,
    items,
    replaceMode,
    viewMode,
    textField,
  });

  const project = ctx.project;
  const arts = filterArtifacts(ctx.artifacts, nodeType);

  switch (nodeType) {
    case "topic": {
      const text = project?.topic?.trim() ?? "";
      return {
        hasResult: Boolean(text),
        itemCount: 1,
        summary: text ? "Тема ролика задана" : "Укажите тему ролика",
        items: [
          {
            id: "topic",
            label: "Тема ролика",
            kind: "text",
            content: text,
          },
        ],
        replaceMode: "text",
        viewMode: "topic_edit",
      };
    }

    case "plan":
    case "hitl_gate": {
      const planText = meaningfulGeneralPlan(project);
      if (planText && projectHasXlsx(ctx.assets)) {
        return {
          hasResult: true,
          itemCount: 1,
          summary: "Лист «Общий план» в Excel",
          items: [{ id: "xlsx_general", label: "Сценарий (Excel)", kind: "xlsx" }],
          replaceMode: "xlsx",
          viewMode: "xlsx_general_plan",
        };
      }
      if (planText) {
        return ready(
          [{ id: "general_plan", label: "Сценарий", kind: "text", content: planText }],
          "Текст плана готов",
          "text",
          "xlsx_general_plan",
          "general_plan",
        );
      }
      return empty("Сценарий ещё не сгенерирован", "text", "xlsx_general_plan");
    }

    case "script": {
      const text = project?.script_text?.trim();
      if (text) {
        return ready(
          [{ id: "script_text", label: "Закадровый текст", kind: "text", content: text }],
          "Закадровый текст готов",
          "text",
          "voiceover_wide",
          "script_text",
        );
      }
      const voiceFile = textAsset(ctx.assets, "voiceover.txt");
      if (voiceFile) {
        return {
          hasResult: true,
          itemCount: 1,
          summary: "voiceover.txt",
          items: [
            {
              id: voiceFile.id,
              label: "voiceover.txt",
              kind: "file",
              downloadUrl: voiceFile.preview_url,
            },
          ],
          replaceMode: "text",
          viewMode: "voiceover_wide",
          textField: "script_text",
        };
      }
      return empty("Закадровый текст ещё не готов", "text", "voiceover_wide");
    }

    case "split": {
      if (projectHasXlsx(ctx.assets) || ctx.frames.length > 0) {
        return {
          hasResult: true,
          itemCount: ctx.frames.length || 1,
          summary: "Разбивка по ячейкам (лист «план», строка 49)",
          items: [{ id: "split_row", label: "Разбивка", kind: "frames" }],
          replaceMode: "studio",
          viewMode: "xlsx_split_row",
        };
      }
      return empty("Раскадровка ещё не создана", "studio", "xlsx_split_row");
    }

    case "hero":
    case "hitl_hero": {
      const heroAssets = ctx.assets.filter(
        (a) =>
          a.kind.includes("hero") ||
          a.path?.includes("/hero") ||
          a.path?.includes("\\hero") ||
          a.path?.includes("/characters") ||
          a.path?.includes("\\characters"),
      );
      // Диск (c01.png) важнее артефактов: у file-asset есть filePath для «Заменить».
      const fromDisk = assetItems(heroAssets);
      const fromArts = artifactItems(arts.filter((a) => a.kind.includes("hero")));
      const seenPaths = new Set(
        fromDisk.map((i) => (i.filePath || "").replace(/\\/g, "/").toLowerCase()).filter(Boolean),
      );
      const dedupedArts = fromArts.filter((i) => {
        const key = (i.filePath || "").replace(/\\/g, "/").toLowerCase();
        if (!key) return true;
        if (seenPaths.has(key)) return false;
        seenPaths.add(key);
        return true;
      });
      const items = [...fromDisk, ...dedupedArts];
      if (items.length) {
        const descriptions = project?.hero_descriptions ?? [];
        const enriched = items.map((item, i) => ({
          ...item,
          content: item.content?.trim() || descriptions[i] || descriptions[0] || item.label,
        }));
        return ready(enriched, `${items.length} reference персонажей`, "assets", "frame_images");
      }
      if ((project?.hero_descriptions?.length ?? 0) > 0) {
        return ready(
          (project?.hero_descriptions ?? []).map((d, i) => ({
            id: `hero_desc_${i}`,
            label: `Персонаж ${i + 1}`,
            kind: "text" as const,
            content: d,
          })),
          "Описания персонажей без картинок",
          "assets",
        );
      }
      return empty("Персонажи ещё не сгенерированы", "assets");
    }

    case "items": {
      const itemAssets = ctx.assets.filter((a) => a.kind.includes("item") || a.path?.includes("item"));
      const items = [
        ...assetItems(itemAssets),
        ...artifactItems(arts.filter((a) => a.kind.includes("item"))),
      ];
      if (items.length) return ready(items, `${items.length} reference предметов`, "assets");
      if ((project?.item_descriptions?.length ?? 0) > 0) {
        return ready(
          (project?.item_descriptions ?? []).map((d, i) => ({
            id: `item_desc_${i}`,
            label: `Предмет ${i + 1}`,
            kind: "text" as const,
            content: d,
          })),
          "Описания предметов без картинок",
          "assets",
        );
      }
      return empty("Предметы ещё не сгенерированы", "assets");
    }

    case "enrich_1":
    case "enrich_2":
    case "enrich_3":
    case "enrich_4":
    case "enrich_5":
    case "enrich": {
      const xlsx = xlsxAsset(ctx.assets);
      if (xlsx && project?.id) {
        return ready(
          [
            {
              id: xlsx.id,
              label: "project.xlsx",
              kind: "xlsx",
              downloadUrl: api.downloadProjectXlsx(project.id),
            },
          ],
          "Таблица Excel загружена",
          "xlsx",
        );
      }
      return empty("Таблица Excel ещё не создана", "xlsx");
    }

    case "image_prompts": {
      const withPrompt = ctx.frames.filter((f) => f.image_prompt?.trim());
      if (withPrompt.length) {
        return ready(
          withPrompt.map((f) => ({
            id: `frame_${f.id}`,
            label: `Кадр ${f.number}`,
            kind: "text" as const,
            content: f.image_prompt,
            frameNumber: f.number,
          })),
          `Промты картинок: ${withPrompt.length} кадров`,
          "studio",
          "frame_prompts",
        );
      }
      return empty("Промты картинок ещё не готовы", "studio", "frame_prompts");
    }

    case "images":
    case "hitl_images": {
      const frameById = new Map(ctx.frames.map((f) => [f.id, f]));
      const fromMedia = ctx.mediaImages.map((a) => {
        const fr = a.frame_id != null ? frameById.get(a.frame_id) : undefined;
        const base = assetItems([a])[0];
        return {
          ...base,
          content: (a as { voiceover?: string }).voiceover || fr?.voiceover_text || base.content,
        };
      });
      const fromArts = artifactItems(arts);
      const items = (fromMedia.length ? fromMedia : fromArts).filter((i) => i.previewUrl);
      if (items.length) {
        return ready(items, `${items.length} картинок`, "assets", "frame_images");
      }
      return empty("Картинки ещё не сгенерированы", "assets", "frame_images");
    }

    case "animation_prompts": {
      const withPrompt = ctx.frames.filter((f) => f.animation_prompt?.trim());
      if (withPrompt.length) {
        return ready(
          withPrompt.slice(0, 12).map((f) => ({
            id: `frame_${f.id}`,
            label: `Кадр ${f.number}`,
            kind: "text" as const,
            content: f.animation_prompt,
          })),
          `Промты анимации: ${withPrompt.length} кадров`,
          "studio",
        );
      }
      return empty("Промты анимации ещё не готовы", "studio");
    }

    case "videos":
    case "hitl_videos": {
      const fromMedia = assetItems(ctx.mediaVideos).filter((i) => i.previewUrl);
      const fromArts = artifactItems(arts);
      const items = fromMedia.length ? fromMedia : fromArts;
      if (items.length) {
        return ready(items, `${items.length} видео`, "assets", "frame_videos");
      }
      return empty("Видео ещё не сгенерированы", "assets", "frame_videos");
    }

    case "audio": {
      const audioAssets = ctx.assets.filter((a) => a.kind.includes("audio") || a.path?.includes("audio"));
      const items = [...assetItems(audioAssets), ...artifactItems(arts)];
      if (items.length) return ready(items, `${items.length} аудио-файлов`, "assets");
      return empty("Аудио ещё не сгенерировано", "assets");
    }

    case "assemble":
    case "hitl_final": {
      const finalArts = artifactItems(arts);
      const finalAssets = ctx.assets.filter((a) => a.kind.includes("final"));
      const items = [...assetItems(finalAssets), ...finalArts];
      if (items.length) return ready(items, "Финальный ролик готов", "assets");
      if (project?.status === "assembled") {
        return {
          hasResult: true,
          itemCount: 0,
          summary: "Сборка завершена",
          items: [],
          replaceMode: "assets",
          viewMode: "default",
        };
      }
      return empty("Финальный ролик ещё не собран", "assets");
    }

    case "publish": {
      if (project?.status === "published") {
        return {
          hasResult: true,
          itemCount: 0,
          summary: "Опубликовано",
          items: [],
          replaceMode: "none",
          viewMode: "default",
        };
      }
      const final = ctx.assets.filter((a) => a.kind.includes("final"));
      if (final.length) return ready(assetItems(final), "Готово к публикации", "assets");
      return empty("Публикация ещё не выполнена", "none");
    }

    default: {
      if (isEnrichNode(nodeType)) {
        return computeNodeResult("enrich_1", ctx);
      }
      const generic = artifactItems(filterArtifacts(ctx.artifacts, nodeType)).slice(0, 8);
      if (generic.length) return ready(generic, `${generic.length} артефактов`, "studio");
      return empty("Результата пока нет", "none");
    }
  }
}
