"use client";

import { useEffect, useRef, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ChevronLeft, ChevronRight, Download, Loader2, Maximize2, Replace, Upload } from "lucide-react";
import { toast } from "sonner";
import { api } from "@/lib/api";
import type { NodeResultItem, NodeResultSnapshot } from "@/lib/node-result-resolver";
import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/textarea";
import { ScrollArea } from "@/components/ui/scroll-area";
import { cn } from "@/lib/utils";
import { MediaFrameGallery } from "@/components/hitl/media-frame-gallery";
import {
  pickGeneralPlanSheet,
  ROW_VOICEOVER_V8,
  SHEET_PLAN_V8,
} from "@/lib/xlsx-sheets";

export function NodeResultViewBody({
  projectId,
  nodeType,
  snapshot,
  onHeroReplaced,
}: {
  projectId: number;
  nodeType: string;
  snapshot: NodeResultSnapshot;
  onHeroReplaced?: () => void;
}) {
  switch (snapshot.viewMode) {
    case "xlsx_general_plan":
      return <GeneralPlanSheetView projectId={projectId} />;
    case "voiceover_wide":
      return <VoiceoverWideView projectId={projectId} snapshot={snapshot} />;
    case "xlsx_split_row":
      return <SplitRowView projectId={projectId} />;
    case "frame_prompts":
      return <FramePromptsView items={snapshot.items} />;
    case "frame_images":
      return (
        <FrameImagesView
          projectId={projectId}
          nodeType={nodeType}
          items={snapshot.items}
          onHeroReplaced={onHeroReplaced}
        />
      );
    case "frame_videos":
      return <FrameVideosView items={snapshot.items} />;
    case "topic_edit":
      return <TopicEditView projectId={projectId} snapshot={snapshot} />;
    default:
      return <DefaultResultView projectId={projectId} snapshot={snapshot} />;
  }
}

function LoadingBlock() {
  return (
    <div className="flex items-center justify-center py-12">
      <Loader2 className="h-6 w-6 animate-spin text-muted-foreground" />
    </div>
  );
}

function GeneralPlanSheetView({ projectId }: { projectId: number }) {
  const meta = useQuery({
    queryKey: ["xlsx-sheets", projectId],
    queryFn: () => api.previewProjectXlsx(projectId, { maxRows: 1 }),
  });
  const sheet = pickGeneralPlanSheet(meta.data?.sheets ?? []);
  const grid = useQuery({
    queryKey: ["xlsx-general-plan", projectId, sheet],
    queryFn: () =>
      api.previewProjectXlsx(projectId, {
        sheet,
        raw: true,
        maxRows: 200,
        maxCols: 30,
      }),
    enabled: Boolean(sheet),
  });

  if (meta.isLoading || grid.isLoading) return <LoadingBlock />;

  if (!sheet || !grid.data?.rows?.length) {
    return (
      <p className="py-8 text-center text-sm text-muted-foreground">
        Лист «Общий план» пока пуст или Excel ещё не создан.
      </p>
    );
  }

  return (
    <div className="min-h-0 flex-1 overflow-auto rounded-lg border border-white/10 bg-black/20">
      <table className="min-w-max border-collapse text-left text-xs">
        <tbody>
          {grid.data.rows.map((row, ri) => (
            <tr key={ri} className="border-b border-white/5 hover:bg-white/[0.02]">
              <td className="sticky left-0 z-10 border-r border-white/10 bg-card/95 px-2 py-1.5 text-[10px] text-muted-foreground">
                {ri + 1}
              </td>
              {row.map((cell, ci) => (
                <td
                  key={ci}
                  className="max-w-[320px] min-w-[80px] whitespace-pre-wrap border-r border-white/5 px-2 py-1.5 align-top"
                >
                  {cell || "\u00a0"}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function VoiceoverWideView({
  projectId,
  snapshot,
}: {
  projectId: number;
  snapshot: NodeResultSnapshot;
}) {
  const qc = useQueryClient();
  const textItem = snapshot.items.find((i) => i.kind === "text");
  const fileItem = snapshot.items.find((i) => i.downloadUrl);
  const [text, setText] = useState(textItem?.content ?? "");

  const fileText = useQuery({
    queryKey: ["voiceover-file", fileItem?.downloadUrl],
    queryFn: async () => {
      const res = await fetch(fileItem!.downloadUrl!);
      if (!res.ok) throw new Error(await res.text());
      return res.text();
    },
    enabled: Boolean(fileItem?.downloadUrl) && !textItem?.content,
  });

  useEffect(() => {
    if (textItem?.content) setText(textItem.content);
    else if (fileText.data) setText(fileText.data);
  }, [textItem?.content, fileText.data]);

  const save = useMutation({
    mutationFn: (body: string) => api.patchProject(projectId, { script_text: body }),
    onSuccess: () => {
      toast.success("Текст сохранён");
      qc.invalidateQueries({ queryKey: ["project", projectId] });
    },
    onError: (e) => toast.error(String(e)),
  });

  if (fileText.isLoading && !text) return <LoadingBlock />;

  return (
    <div className="flex min-h-0 flex-1 flex-col gap-2">
      
      <div className="flex shrink-0 flex-wrap gap-2">
        {fileItem?.downloadUrl && (
          <Button size="sm" variant="outline" asChild>
            <a href={fileItem.downloadUrl} download target="_blank" rel="noreferrer">
              <Download className="h-3.5 w-3.5" />
              Скачать voiceover.txt
            </a>
          </Button>
        )}
        <Button size="sm" disabled={save.isPending} onClick={() => save.mutate(text)}>
          {save.isPending ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Replace className="h-3.5 w-3.5" />}
          Заменить текст
        </Button>
      </div>
      <Textarea
        value={text}
        onChange={(e) => setText(e.target.value)}
        className="min-h-[65vh] flex-1 resize-none font-mono text-sm leading-relaxed"
        placeholder="Закадровый текст…"
      />
    </div>
  );
}

function SplitRowView({ projectId }: { projectId: number }) {
  const row = useQuery({
    queryKey: ["xlsx-split-row", projectId],
    queryFn: () =>
      api.previewProjectXlsx(projectId, {
        sheet: SHEET_PLAN_V8,
        row: ROW_VOICEOVER_V8,
        maxCols: 120,
      }),
  });

  if (row.isLoading) return <LoadingBlock />;

  const cells = row.data?.cells ?? [];
  const filled = cells.filter((c) => c.trim());

  if (!filled.length) {
    return (
      <p className="py-8 text-center text-sm text-muted-foreground">
        Строка {ROW_VOICEOVER_V8} листа «{SHEET_PLAN_V8}» пока пуста — сначала выполните разбивку.
      </p>
    );
  }

  return (
    <div className="min-h-0 flex-1 overflow-x-auto overflow-y-hidden rounded-lg border border-white/10 bg-black/20 p-3">
      <p className="mb-2 text-[11px] text-muted-foreground">
        Лист «{SHEET_PLAN_V8}», строка {ROW_VOICEOVER_V8} — прокрутите вправо для всех кадров
      </p>
      <div className="flex min-w-max gap-2">
        {cells.map((cell, i) => {
          if (!cell.trim()) return null;
          const frameNum = cells.slice(0, i + 1).filter((c) => c.trim()).length;
          return (
            <div
              key={i}
              className="flex w-[220px] shrink-0 flex-col rounded-lg border border-white/10 bg-black/30 p-2"
            >
              <span className="mb-1 text-[10px] font-medium text-primary">Кадр {frameNum}</span>
              <p className="max-h-40 overflow-auto whitespace-pre-wrap text-xs leading-relaxed text-foreground/90">
                {cell.trim()}
              </p>
            </div>
          );
        })}
      </div>
    </div>
  );
}

function FramePromptsView({ items }: { items: NodeResultItem[] }) {
  const [selectedId, setSelectedId] = useState(items[0]?.id ?? "");
  const selected = items.find((i) => i.id === selectedId) ?? items[0];

  useEffect(() => {
    if (items[0]?.id) setSelectedId(items[0].id);
  }, [items]);

  return (
    <div className="grid min-h-0 flex-1 gap-3 md:grid-cols-[200px_1fr] md:items-stretch">
      <ScrollArea className="h-[65vh] rounded-lg border border-white/10">
        <div className="p-1">
          {items.map((item) => (
            <button
              key={item.id}
              type="button"
              onClick={() => setSelectedId(item.id)}
              className={cn(
                "mb-1 w-full rounded-md px-2 py-2 text-left text-[11px] transition",
                selected?.id === item.id
                  ? "bg-primary/20 text-primary"
                  : "text-muted-foreground hover:bg-white/5",
              )}
            >
              {item.label}
            </button>
          ))}
        </div>
      </ScrollArea>
      <Textarea
        readOnly
        value={selected?.content ?? ""}
        className="h-[65vh] resize-none text-xs leading-relaxed"
      />
    </div>
  );
}

function FrameImagesView({
  projectId,
  nodeType,
  items,
  onHeroReplaced,
}: {
  projectId: number;
  nodeType: string;
  items: NodeResultItem[];
  onHeroReplaced?: () => void;
}) {
  if (nodeType === "images") {
    return <SceneImagesGalleryView projectId={projectId} />;
  }

  const [index, setIndex] = useState(0);
  const fileRef = useRef<HTMLInputElement>(null);
  const qc = useQueryClient();
  const isHero = nodeType === "hero" || nodeType === "hitl_hero";
  const current = items[index] ?? items[0];

  const replaceHero = useMutation({
    mutationFn: (file: File) =>
      api.replaceHeroImage(projectId, file, current?.filePath ?? undefined),
    onSuccess: () => {
      toast.success("Персонаж заменён");
      qc.invalidateQueries({ queryKey: ["project-assets", projectId] });
      onHeroReplaced?.();
    },
    onError: (e) => toast.error(String(e)),
  });

  return (
    <div className="flex min-h-0 flex-1 flex-col gap-3">
      <div className="flex flex-wrap items-center gap-2">
        {isHero && (
          <>
            <input
              ref={fileRef}
              type="file"
              accept="image/png,image/jpeg,image/webp"
              className="hidden"
              onChange={(e) => {
                const f = e.target.files?.[0];
                if (f) replaceHero.mutate(f);
                e.target.value = "";
              }}
            />
            <Button
              size="sm"
              variant="outline"
              disabled={replaceHero.isPending}
              onClick={() => fileRef.current?.click()}
            >
              {replaceHero.isPending ? (
                <Loader2 className="h-3.5 w-3.5 animate-spin" />
              ) : (
                <Replace className="h-3.5 w-3.5" />
              )}
              Заменить персонажа
            </Button>
          </>
        )}
        {current?.downloadUrl && (
          <Button size="sm" variant="outline" asChild>
            <a href={current.downloadUrl} download target="_blank" rel="noreferrer">
              <Download className="h-3.5 w-3.5" />
              Скачать
            </a>
          </Button>
        )}
        {items.length > 1 && (
          <>
            <Button
              size="icon"
              variant="ghost"
              className="h-8 w-8"
              disabled={index <= 0}
              onClick={() => setIndex((i) => Math.max(0, i - 1))}
            >
              <ChevronLeft className="h-4 w-4" />
            </Button>
            <span className="text-[11px] text-muted-foreground">
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
          </>
        )}
      </div>
      {items.length > 1 && (
        <div className="flex gap-2 overflow-x-auto pb-1">
          {items.map((item, i) => (
            <button
              key={item.id}
              type="button"
              onClick={() => setIndex(i)}
              className={cn(
                "h-16 w-12 shrink-0 overflow-hidden rounded-lg border transition",
                i === index ? "border-primary ring-1 ring-primary/40" : "border-white/10",
              )}
            >
              {item.previewUrl ? (
                <img src={item.previewUrl} alt="" className="h-full w-full object-cover" />
              ) : (
                <span className="flex h-full items-center justify-center px-1 text-[9px]">{item.label}</span>
              )}
            </button>
          ))}
        </div>
      )}
      {current?.previewUrl && (
        <div className="flex justify-center rounded-xl border border-white/10 bg-black/30 p-2">
          <img src={current.previewUrl} alt="" className="max-h-[45vh] w-full object-contain" />
        </div>
      )}
      {!isHero && (
        <div className="rounded-lg border border-white/10 bg-black/20 p-3">
          <p className="mb-1 text-[10px] font-medium uppercase tracking-wide text-muted-foreground">
            Закадровый текст
          </p>
          <p className="max-h-36 overflow-auto whitespace-pre-wrap text-sm leading-relaxed">
            {current?.content?.trim() || "—"}
          </p>
        </div>
      )}
    </div>
  );
}

function FrameVideosView({ items }: { items: NodeResultItem[] }) {
  const [index, setIndex] = useState(0);
  const videoRef = useRef<HTMLVideoElement>(null);
  const current = items[index] ?? items[0];

  const goFullscreen = async () => {
    const el = videoRef.current;
    if (!el) return;
    try {
      if (el.requestFullscreen) await el.requestFullscreen();
    } catch {
      toast.error("Полноэкранный режим недоступен");
    }
  };

  return (
    <div className="flex min-h-0 flex-1 flex-col gap-3">
      <div className="flex flex-wrap items-center gap-2">
        <Button size="sm" variant="outline" onClick={goFullscreen} disabled={!current?.previewUrl}>
          <Maximize2 className="h-3.5 w-3.5" />
          На весь экран
        </Button>
        {current?.downloadUrl && (
          <Button size="sm" variant="outline" asChild>
            <a href={current.downloadUrl} download target="_blank" rel="noreferrer">
              <Download className="h-3.5 w-3.5" />
              Скачать
            </a>
          </Button>
        )}
        {items.length > 1 && (
          <>
            <Button
              size="icon"
              variant="ghost"
              className="h-8 w-8"
              disabled={index <= 0}
              onClick={() => setIndex((i) => Math.max(0, i - 1))}
            >
              <ChevronLeft className="h-4 w-4" />
            </Button>
            <span className="text-[11px] text-muted-foreground">
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
          </>
        )}
      </div>
      <div className="flex flex-1 items-center justify-center rounded-xl border border-white/10 bg-black/40 p-2">
        {current?.previewUrl ? (
          <video
            ref={videoRef}
            src={current.previewUrl}
            controls
            className="max-h-[60vh] w-full object-contain"
          />
        ) : (
          <p className="py-12 text-sm text-muted-foreground">Видео ещё не сгенерировано</p>
        )}
      </div>
    </div>
  );
}

function SceneImagesGalleryView({ projectId }: { projectId: number }) {
  const media = useQuery({
    queryKey: ["media-review", projectId, "images"],
    queryFn: () => api.listMediaReview(projectId, "images"),
  });

  if (media.isLoading) return <LoadingBlock />;

  const items = (media.data ?? []).filter((f) => f.preview_url);

  return (
    <div className="min-h-0 flex-1 overflow-auto">
      <MediaFrameGallery
        projectId={projectId}
        kind="images"
        items={items}
        showApproveButtons={false}
      />
    </div>
  );
}

function TopicEditView({
  projectId,
  snapshot,
}: {
  projectId: number;
  snapshot: NodeResultSnapshot;
}) {
  const qc = useQueryClient();
  const [topic, setTopic] = useState(snapshot.items[0]?.content ?? "");

  useEffect(() => {
    setTopic(snapshot.items[0]?.content ?? "");
  }, [snapshot.items, projectId]);

  const save = useMutation({
    mutationFn: () => api.patchProject(projectId, { topic: topic.trim() }),
    onSuccess: () => {
      toast.success("Тема ролика сохранена");
      qc.invalidateQueries({ queryKey: ["project", projectId] });
    },
    onError: (e) => toast.error(String(e)),
  });

  return (
    <div className="flex min-h-0 flex-1 flex-col gap-4">
      <p className="text-sm text-muted-foreground">
        Тема задаёт направление всего ролика — как в боте перед шагом «Общий план».
        Для массовой генерации используйте Excel с колонкой «Название ролика».
      </p>
      <Textarea
        value={topic}
        onChange={(e) => setTopic(e.target.value)}
        rows={5}
        placeholder="Например: Почему кошки всегда приземляются на лапы"
        className="text-sm"
      />
      <Button
        size="sm"
        disabled={!topic.trim() || save.isPending}
        onClick={() => save.mutate()}
      >
        {save.isPending ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : null}
        Сохранить тему
      </Button>
    </div>
  );
}

function DefaultResultView({
  projectId,
  snapshot,
}: {
  projectId: number;
  snapshot: NodeResultSnapshot;
}) {
  const fileRef = useRef<HTMLInputElement>(null);
  const qc = useQueryClient();
  const uploadXlsx = useMutation({
    mutationFn: (file: File) => api.uploadProjectXlsx(projectId, file),
    onSuccess: () => {
      toast.success("Excel заменён");
      qc.invalidateQueries({ queryKey: ["project", projectId] });
    },
    onError: (e) => toast.error(String(e)),
  });

  const item = snapshot.items[0];
  if (!item) {
    return <p className="text-sm text-muted-foreground">Нет данных для отображения.</p>;
  }

  return (
    <div className="space-y-3">
      {item.kind === "xlsx" && (
        <div className="flex gap-2">
          <Button size="sm" variant="outline" asChild>
            <a href={api.downloadProjectXlsx(projectId)} download>
              <Download className="h-3.5 w-3.5" />
              Скачать Excel
            </a>
          </Button>
          <Button size="sm" variant="outline" onClick={() => fileRef.current?.click()}>
            <Upload className="h-3.5 w-3.5" />
            Заменить
          </Button>
          <input
            ref={fileRef}
            type="file"
            accept=".xlsx,.xls"
            className="hidden"
            onChange={(e) => {
              const f = e.target.files?.[0];
              if (f) uploadXlsx.mutate(f);
              e.target.value = "";
            }}
          />
        </div>
      )}
      {item.previewUrl && item.kind === "image" && (
        <img src={item.previewUrl} alt="" className="max-h-64 rounded-lg object-contain" />
      )}
      {item.content && (
        <pre className="max-h-64 overflow-auto whitespace-pre-wrap rounded-lg bg-black/30 p-3 text-xs">
          {item.content}
        </pre>
      )}
    </div>
  );
}

