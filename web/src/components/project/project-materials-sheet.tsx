"use client";

import { useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Film, ImageIcon, Loader2, Mic } from "lucide-react";
import { api } from "@/lib/api";
import { NodeResultViewBody } from "@/components/canvas/node-result-views";
import { buildVoiceoverSnapshot } from "@/lib/voiceover-snapshot";
import type { NodeResultItem } from "@/lib/node-result-resolver";
import {
  Sheet,
  SheetContent,
  SheetDescription,
  SheetHeader,
  SheetTitle,
} from "@/components/ui/sheet";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";

type MaterialsTab = "voiceover" | "images" | "videos";

export function ProjectMaterialsSheet({
  projectId,
  open,
  onOpenChange,
}: {
  projectId: number | null;
  open: boolean;
  onOpenChange: (open: boolean) => void;
}) {
  const [tab, setTab] = useState<MaterialsTab>("images");

  const assets = useQuery({
    queryKey: ["project-assets", projectId, "all"],
    queryFn: () => api.listProjectAssets(projectId!, "all"),
    enabled: open && projectId != null,
    refetchInterval: open ? 8000 : false,
  });

  const mediaImages = useQuery({
    queryKey: ["media-review", projectId, "images"],
    queryFn: () => api.listMediaReview(projectId!, "images"),
    enabled: open && projectId != null,
    refetchInterval: open ? 8000 : false,
  });

  const mediaVideos = useQuery({
    queryKey: ["media-review", projectId, "videos"],
    queryFn: () => api.listMediaReview(projectId!, "videos"),
    enabled: open && projectId != null,
    refetchInterval: open ? 8000 : false,
  });

  const voiceoverSnapshot = useMemo(
    () => buildVoiceoverSnapshot(assets.data ?? []),
    [assets.data],
  );

  const imageItems = useMemo((): NodeResultItem[] => {
    return (mediaImages.data ?? [])
      .filter((r) => r.preview_url)
      .map((r) => ({
        id: String(r.frame_id),
        label: `Кадр ${r.number}`,
        kind: "image" as const,
        previewUrl: r.preview_url!,
        downloadUrl: r.preview_url!,
        content: r.voiceover_text,
        frameId: r.frame_id,
      }));
  }, [mediaImages.data]);

  const videoItems = useMemo((): NodeResultItem[] => {
    return (mediaVideos.data ?? [])
      .filter((r) => r.preview_url)
      .map((r) => ({
        id: String(r.frame_id),
        label: `Кадр ${r.number}`,
        kind: "video" as const,
        previewUrl: r.preview_url!,
        downloadUrl: r.preview_url!,
        content: r.voiceover_text,
        frameId: r.frame_id,
      }));
  }, [mediaVideos.data]);

  const loading =
    assets.isLoading || mediaImages.isLoading || mediaVideos.isLoading;

  const tabs: { id: MaterialsTab; label: string; icon: typeof ImageIcon; count: number }[] = [
    {
      id: "images",
      label: "Картинки",
      icon: ImageIcon,
      count: imageItems.length,
    },
    {
      id: "videos",
      label: "Видео",
      icon: Film,
      count: videoItems.length,
    },
    {
      id: "voiceover",
      label: "Озвучка",
      icon: Mic,
      count: voiceoverSnapshot.hasResult ? 1 : 0,
    },
  ];

  return (
    <Sheet open={open} onOpenChange={onOpenChange}>
      <SheetContent side="right" className="flex !max-w-5xl flex-col gap-0 p-0 sm:!max-w-5xl">
        <SheetHeader className="border-b border-border px-4 py-3">
          <SheetTitle>Материалы проекта</SheetTitle>
          <SheetDescription>
            Картинки, видео и voiceover выбранного проекта — всё в одном месте.
          </SheetDescription>
        </SheetHeader>

        <div className="flex min-h-0 flex-1 flex-col">
          <div className="flex shrink-0 gap-1 border-b border-border px-3 py-2">
            {tabs.map((t) => {
              const Icon = t.icon;
              return (
                <Button
                  key={t.id}
                  size="sm"
                  variant={tab === t.id ? "secondary" : "ghost"}
                  className="h-8 gap-1.5 text-xs"
                  onClick={() => setTab(t.id)}
                >
                  <Icon className="h-3.5 w-3.5" />
                  {t.label}
                  <span className="tabular-nums text-muted-foreground">({t.count})</span>
                </Button>
              );
            })}
          </div>

          <div className="min-h-0 flex-1 overflow-auto p-4">
            {projectId == null ? (
              <p className="text-sm text-muted-foreground">Выбери проект слева.</p>
            ) : loading ? (
              <div className="flex h-40 items-center justify-center">
                <Loader2 className="h-6 w-6 animate-spin text-muted-foreground" />
              </div>
            ) : tab === "voiceover" ? (
              voiceoverSnapshot.hasResult ? (
                <NodeResultViewBody
                  projectId={projectId}
                  nodeType="music"
                  snapshot={voiceoverSnapshot}
                />
              ) : (
                <EmptyTab text="voiceover.txt ещё не создан — пройди шаг «Закадровый текст»." />
              )
            ) : tab === "images" ? (
              imageItems.length > 0 ? (
                <NodeResultViewBody
                  projectId={projectId}
                  nodeType="images"
                  snapshot={{
                    hasResult: true,
                    itemCount: imageItems.length,
                    summary: `${imageItems.length} картинок`,
                    items: imageItems,
                    replaceMode: "assets",
                    viewMode: "frame_images",
                  }}
                />
              ) : (
                <EmptyTab text="Картинки ещё не сгенерированы." />
              )
            ) : videoItems.length > 0 ? (
              <NodeResultViewBody
                projectId={projectId}
                nodeType="videos"
                snapshot={{
                  hasResult: true,
                  itemCount: videoItems.length,
                  summary: `${videoItems.length} видео`,
                  items: videoItems,
                  replaceMode: "assets",
                  viewMode: "frame_videos",
                }}
              />
            ) : (
              <EmptyTab text="Видео ещё не сгенерированы." />
            )}
          </div>
        </div>
      </SheetContent>
    </Sheet>
  );
}

function EmptyTab({ text }: { text: string }) {
  return (
    <p className={cn("rounded-lg border border-dashed border-border px-4 py-8 text-center text-sm text-muted-foreground")}>
      {text}
    </p>
  );
}
