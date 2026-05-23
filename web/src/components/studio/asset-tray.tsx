"use client";

import { useEffect, useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  ChevronLeft,
  ChevronRight,
  Check,
  Edit3,
  Loader2,
  Save,
  Trash2,
  X,
} from "lucide-react";
import { toast } from "sonner";
import { api, type ProjectAsset } from "@/lib/api";
import type { AssetTrayKind } from "@/components/canvas/canvas-actions-context";
import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/textarea";
import { cn } from "@/lib/utils";

const KIND_LABELS: Record<AssetTrayKind, string> = {
  hero: "Персонажи",
  items: "Предметы",
  images: "Картинки",
  videos: "Видео",
  project: "Проект",
};

const HITL_KIND_BY_TRAY: Partial<Record<AssetTrayKind, string>> = {
  hero: "approve_hero",
  items: "approve_hero",
  images: "approve_images",
  videos: "approve_videos",
};

export function AssetTray({
  projectId,
  kind,
  onClose,
}: {
  projectId: number;
  kind: AssetTrayKind;
  onClose: () => void;
}) {
  const [index, setIndex] = useState(0);
  const [editing, setEditing] = useState(false);
  const [editText, setEditText] = useState("");
  const qc = useQueryClient();

  const assetsQ = useQuery({
    queryKey: ["project-assets", projectId, kind],
    queryFn: () => api.listProjectAssets(projectId, kind === "project" ? "all" : kind),
    enabled: projectId > 0,
  });

  const mediaReview = useQuery({
    queryKey: ["media-review", projectId, kind],
    queryFn: () =>
      api.listMediaReview(projectId, kind === "videos" ? "videos" : "images"),
    enabled: projectId > 0 && (kind === "images" || kind === "videos"),
  });

  const items = useMemo(() => {
    const fromApi = assetsQ.data ?? [];
    if (kind === "images" || kind === "videos") {
      const review = (mediaReview.data ?? []).map((r) => ({
        source: "frame" as const,
        id: String(r.frame_id),
        kind,
        path: r.file_path,
        preview_url: r.preview_url,
        label: `Кадр ${r.number}`,
        frame_id: r.frame_id,
        voiceover: r.voiceover_text,
        description: r.image_prompt || r.animation_prompt,
      }));
      if (review.length) return review;
    }
    return fromApi.filter((a) => kind === "project" || a.kind === kind);
  }, [assetsQ.data, mediaReview.data, kind]);

  const current = items[index];
  const previewSrc = current?.preview_url || null;

  useEffect(() => {
    setIndex(0);
    setEditing(false);
  }, [kind, projectId]);

  useEffect(() => {
    const text =
      (current as { voiceover?: string })?.voiceover ||
      (current as { description?: string })?.description ||
      current?.label ||
      "";
    setEditText(text);
    setEditing(false);
  }, [current, index]);

  const saveEdit = useMutation({
    mutationFn: async () => {
      const raw = (current as { frame_id?: number; id?: string }).frame_id ?? current?.id;
      const frameId = typeof raw === "number" ? raw : Number(raw);
      if (!Number.isFinite(frameId)) {
        throw new Error("Редактирование доступно для кадров с привязкой к БД");
      }
      if (kind === "images" || kind === "videos") {
        return api.patchFrame(projectId, frameId, { image_prompt: editText });
      }
      return api.patchFrame(projectId, frameId, { voiceover_text: editText });
    },
    onSuccess: () => {
      toast.success("Сохранено");
      setEditing(false);
      qc.invalidateQueries({ queryKey: ["project-assets", projectId, kind] });
      qc.invalidateQueries({ queryKey: ["media-review", projectId, kind] });
    },
    onError: (e) => toast.error(String(e)),
  });

  const hitlApprove = useMutation({
    mutationFn: async () => {
      const pending = await api.listProjectHitl(projectId);
      const want = HITL_KIND_BY_TRAY[kind];
      const match = want
        ? pending.find((h) => h.kind === want) ?? pending[0]
        : pending[0];
      if (!match) throw new Error("Нет ожидающих проверок для этого типа");
      return api.submitHitlDecision(match.id, { decision: "approve" });
    },
    onSuccess: () => {
      toast.success("Одобрено");
      qc.invalidateQueries({ queryKey: ["project", projectId] });
    },
    onError: (e) => toast.error(String(e)),
  });

  return (
    <div className="premium-tray absolute inset-x-0 bottom-0 z-20 flex max-h-[42vh] flex-col border-t border-white/10 bg-gradient-to-t from-[hsl(240_8%_4%/0.98)] via-[hsl(240_8%_6%/0.96)] to-transparent shadow-[0_-12px_48px_rgba(0,0,0,0.45)] backdrop-blur-xl">
      <div className="flex items-center justify-between gap-2 px-4 py-2">
        <div className="flex items-center gap-2">
          <span className="text-xs font-semibold tracking-wide text-foreground">
            {KIND_LABELS[kind]}
          </span>
          <span className="rounded-full bg-white/5 px-2 py-0.5 text-[10px] text-muted-foreground">
            {items.length} файлов
          </span>
        </div>
        <Button size="icon" variant="ghost" className="h-7 w-7" onClick={onClose}>
          <X className="h-4 w-4" />
        </Button>
      </div>

      {assetsQ.isLoading ? (
        <div className="flex flex-1 items-center justify-center py-8">
          <Loader2 className="h-5 w-5 animate-spin text-muted-foreground" />
        </div>
      ) : items.length === 0 ? (
        <p className="px-4 pb-4 text-xs text-muted-foreground">
          Файлов пока нет — запустите соответствующий шаг пайплайна.
        </p>
      ) : (
        <div className="flex min-h-0 flex-1 gap-0">
          <div className="flex min-w-0 flex-1 flex-col px-3 pb-3">
            <div className="flex gap-2 overflow-x-auto pb-2">
              {items.map((item, i) => (
                <button
                  key={`${item.id}-${i}`}
                  type="button"
                  onClick={() => setIndex(i)}
                  className={cn(
                    "flex w-28 shrink-0 flex-col overflow-hidden rounded-xl border text-left transition-all",
                    i === index
                      ? "border-amber-400/50 bg-amber-400/10 shadow-lg shadow-amber-500/10"
                      : "border-white/10 bg-white/5 hover:border-white/20",
                  )}
                >
                  <div className="relative aspect-[9/12] w-full bg-black/30">
                    {item.preview_url || previewFor(item) ? (
                      isVideo(item) ? (
                        <video
                          src={item.preview_url || previewFor(item) || ""}
                          className="h-full w-full object-cover"
                          muted
                        />
                      ) : (
                        <img
                          src={item.preview_url || previewFor(item) || ""}
                          alt=""
                          className="h-full w-full object-cover"
                        />
                      )
                    ) : (
                      <div className="flex h-full items-center justify-center text-[9px] text-muted-foreground">
                        {item.label || item.id}
                      </div>
                    )}
                  </div>
                  <span className="truncate px-1.5 py-1 text-[9px] text-muted-foreground">
                    {item.label || item.id}
                  </span>
                </button>
              ))}
            </div>
            {current && (
              <p className="line-clamp-2 text-[11px] text-muted-foreground">
                {(current as { voiceover?: string }).voiceover ||
                  (current as { description?: string }).description ||
                  current.label ||
                  ""}
              </p>
            )}
          </div>

          <div className="flex w-[min(380px,40%)] shrink-0 flex-col border-l border-white/10 bg-black/20 p-3">
            <div className="mb-2 flex items-center justify-between">
              <Button
                size="icon"
                variant="ghost"
                className="h-8 w-8"
                disabled={index <= 0}
                onClick={() => setIndex((i) => Math.max(0, i - 1))}
              >
                <ChevronLeft className="h-4 w-4" />
              </Button>
              <span className="text-[10px] text-muted-foreground">
                {index + 1} / {items.length}
              </span>
              <Button
                size="icon"
                variant="ghost"
                className="h-8 w-8"
                disabled={index >= items.length - 1}
                onClick={() => setIndex((i) => Math.min(items.length - 1, i + 1))}
              >
                <ChevronRight className="h-4 w-4" />
              </Button>
            </div>
            <div className="relative mb-3 flex-1 overflow-hidden rounded-xl border border-white/10 bg-black/40">
              {previewSrc ? (
                isVideo(current) ? (
                  <video src={previewSrc} controls className="h-full max-h-48 w-full object-contain" />
                ) : (
                  <img src={previewSrc} alt="" className="h-full max-h-48 w-full object-contain" />
                )
              ) : (
                <div className="flex h-40 items-center justify-center p-4 text-center text-xs text-muted-foreground">
                  {(current as { voiceover?: string })?.voiceover ||
                    (current as { description?: string })?.description ||
                    "Нет превью"}
                </div>
              )}
            </div>
            <div className="mb-3 flex-1">
              {editing ? (
                <Textarea
                  value={editText}
                  onChange={(e) => setEditText(e.target.value)}
                  rows={5}
                  className="text-[11px]"
                  placeholder="Закадровый текст или описание"
                />
              ) : (
                <p className="line-clamp-4 text-[11px] leading-relaxed text-muted-foreground">
                  {editText || "Нет описания"}
                </p>
              )}
            </div>
            <div className="flex flex-wrap gap-1.5">
              {editing ? (
                <Button
                  size="sm"
                  variant="default"
                  className="h-8 gap-1 text-xs"
                  disabled={saveEdit.isPending}
                  onClick={() => saveEdit.mutate()}
                >
                  {saveEdit.isPending ? (
                    <Loader2 className="h-3 w-3 animate-spin" />
                  ) : (
                    <Save className="h-3 w-3" />
                  )}
                  Сохранить
                </Button>
              ) : (
                <Button
                  size="sm"
                  variant="outline"
                  className="h-8 gap-1 text-xs"
                  onClick={() => setEditing(true)}
                >
                  <Edit3 className="h-3 w-3" />
                  Редактировать
                </Button>
              )}
              <Button
                size="sm"
                variant="default"
                className="h-8 gap-1 text-xs"
                disabled={hitlApprove.isPending}
                onClick={() => hitlApprove.mutate()}
              >
                {hitlApprove.isPending ? (
                  <Loader2 className="h-3 w-3 animate-spin" />
                ) : (
                  <Check className="h-3 w-3" />
                )}
                Одобрить
              </Button>
              <Button
                size="sm"
                variant="ghost"
                className="h-8 gap-1 text-xs text-destructive"
                onClick={() => toast.message("Удаление файла — через папку проекта")}
              >
                <Trash2 className="h-3 w-3" />
                Удалить
              </Button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

function isVideo(item: ProjectAsset | { path?: string | null; kind?: string }): boolean {
  const p = item.path || "";
  return item.kind === "videos" || /\.(mp4|webm)$/i.test(p);
}

function previewFor(item: ProjectAsset): string | null {
  if (item.preview_url) return item.preview_url;
  return null;
}
