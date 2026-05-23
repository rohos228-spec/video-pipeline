"use client";

import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  CheckCircle2,
  RefreshCw,
  XCircle,
  PenLine,
  Loader2,
  AlertTriangle,
  Image as ImageIcon,
  FileText,
  Video,
} from "lucide-react";
import { toast } from "sonner";
import { api } from "@/lib/api";
import type { HITLDTO, HITLKind } from "@/lib/types";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Textarea } from "@/components/ui/input";
import { Badge } from "@/components/ui/badge";
import { useHotkeysInDialog } from "@/hooks/use-hotkeys";
import { VisualHitlGallery } from "@/components/hitl/visual-hitl-gallery";

const HITL_TITLES: Record<HITLKind, string> = {
  approve_plan: "Общий план",
  approve_script: "Сценарий",
  approve_hero: "Референс героя",
  approve_images: "Картинки кадров",
  approve_videos: "Клипы кадров",
  approve_final: "Финальный ролик",
};

const HITL_DESCRIPTIONS: Record<HITLKind, string> = {
  approve_plan: "Согласуй концепт ролика, прежде чем мы пойдём писать сценарий.",
  approve_script: "Закадровый текст, разбитый по кадрам. После approve пойдёт раскадровка.",
  approve_hero: "Reference-картинка героя. После approve пойдёт генерация всех сцен.",
  approve_images: "Все картинки кадров. Можно одобрить целиком или отклонить.",
  approve_videos: "Все клипы кадров. После approve пойдёт сборка с озвучкой.",
  approve_final: "Финальный mp4. После approve пайплайн опубликует на 5 площадок.",
};

export function HitlBanner({ projectId }: { projectId: number }) {
  const [open, setOpen] = useState(false);
  const [activeId, setActiveId] = useState<number | null>(null);

  const pending = useQuery({
    queryKey: ["hitl", projectId],
    queryFn: () => api.listProjectHitl(projectId),
    refetchInterval: 4000,
    select: (data) => data.filter((r) => r.decision === "pending"),
  });

  const items = pending.data ?? [];
  if (items.length === 0) return null;

  const first = items[0];

  const openModal = (id: number) => {
    setActiveId(id);
    setOpen(true);
  };

  return (
    <>
      <div className="pointer-events-none absolute left-1/2 top-4 z-10 flex -translate-x-1/2 items-center gap-2">
        <button
          type="button"
          className="pointer-events-auto pulse-soft group flex items-center gap-2.5 rounded-full border border-warning/50 bg-warning/15 px-4 py-2 text-xs font-medium text-warning shadow-md backdrop-blur-sm transition-all hover:scale-[1.02] hover:border-warning/80 hover:bg-warning/25"
          onClick={() => openModal(first.id)}
        >
          <span className="relative flex h-2 w-2">
            <span className="absolute inline-flex h-full w-full animate-ping rounded-full bg-warning opacity-75" />
            <span className="relative inline-flex h-2 w-2 rounded-full bg-warning" />
          </span>
          <AlertTriangle className="h-3.5 w-3.5" />
          <span>
            Нужно одобрение: {HITL_TITLES[first.kind] ?? first.kind}
            {items.length > 1 && (
              <span className="ml-1 opacity-80">
                ({1} из {items.length})
              </span>
            )}
          </span>
        </button>
      </div>
      <HitlModal
        hitlId={activeId}
        open={open}
        onOpenChange={(o) => setOpen(o)}
      />
    </>
  );
}

function HitlModal({
  hitlId,
  open,
  onOpenChange,
}: {
  hitlId: number | null;
  open: boolean;
  onOpenChange: (o: boolean) => void;
}) {
  const qc = useQueryClient();
  const [editedPrompt, setEditedPrompt] = useState("");
  const [editMode, setEditMode] = useState(false);

  // Загружаем все HITL и берём текущий — proще чем делать отдельный endpoint.
  const all = useQuery({
    queryKey: ["hitl", "pending"],
    queryFn: api.listPendingHitl,
    enabled: open && hitlId != null,
  });
  const current = (all.data ?? []).find((r) => r.id === hitlId) ?? null;

  const submit = useMutation({
    mutationFn: ({ decision, edited }: { decision: string; edited?: string }) =>
      api.submitHitlDecision(hitlId!, { decision, edited_prompt: edited }),
    onSuccess: (_, vars) => {
      qc.invalidateQueries({ queryKey: ["hitl"] });
      qc.invalidateQueries({ queryKey: ["projects"] });
      qc.invalidateQueries({ queryKey: ["project-run"] });
      const map: Record<string, string> = {
        approve: "Одобрено",
        regenerate: "Отправлено на перегенерацию",
        reject: "Отклонено",
        edit_prompt: "Отправлено на правку",
      };
      toast.success(map[vars.decision] ?? "Решение записано");
      onOpenChange(false);
      setEditMode(false);
      setEditedPrompt("");
    },
    onError: (e) => toast.error(`Ошибка: ${String(e)}`),
  });

  const decide = (decision: string, edited?: string) => {
    submit.mutate({ decision, edited });
  };

  // Hotkeys: Enter = approve (вне textarea); Cmd/Ctrl+Enter в edit mode = apply.
  useHotkeysInDialog(open, (e) => {
    if (!open || !current) return;
    const inTextarea = (e.target as HTMLElement)?.tagName === "TEXTAREA";
    if (e.key === "Enter" && !editMode && !inTextarea) {
      e.preventDefault();
      decide("approve");
    }
    if (e.key === "Enter" && editMode && (e.metaKey || e.ctrlKey)) {
      e.preventDefault();
      if (editedPrompt.trim()) decide("edit_prompt", editedPrompt);
    }
  });

  if (!current && open) {
    return (
      <Dialog open={open} onOpenChange={onOpenChange}>
        <DialogContent className="max-w-md">
          <div className="flex items-center justify-center py-12">
            <Loader2 className="h-5 w-5 animate-spin text-muted-foreground" />
          </div>
        </DialogContent>
      </Dialog>
    );
  }
  if (!current) return null;

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent
        className={
          current.kind === "approve_images" || current.kind === "approve_videos"
            ? "max-w-5xl max-h-[90vh] overflow-y-auto"
            : "max-w-2xl"
        }
      >
        <DialogHeader>
          <DialogTitle className="flex items-center gap-2">
            <span className="flex h-6 w-6 items-center justify-center rounded-md bg-warning/15 text-warning">
              <KindIcon kind={current.kind} />
            </span>
            {HITL_TITLES[current.kind] ?? current.kind}
          </DialogTitle>
          <DialogDescription>
            {HITL_DESCRIPTIONS[current.kind] ?? "Требуется решение пользователя."}
          </DialogDescription>
        </DialogHeader>

        {current.kind === "approve_images" || current.kind === "approve_videos" ? (
          <VisualHitlGallery hitl={current} onDecided={() => onOpenChange(false)} />
        ) : (
          <HitlPreview hitl={current} />
        )}

        {(current.kind === "approve_images" || current.kind === "approve_videos") ? null : editMode ? (
          <div className="flex flex-col gap-2">
            <label className="text-xs font-medium text-muted-foreground">
              Что поменять?
            </label>
            <Textarea
              autoFocus
              value={editedPrompt}
              onChange={(e) => setEditedPrompt(e.target.value)}
              rows={4}
              placeholder="Например: «Сделай героя моложе, тон более динамичный, добавь упоминание про космос»"
              className="font-mono text-xs"
            />
          </div>
        ) : null}

        {(current.kind === "approve_images" || current.kind === "approve_videos") ? null : (
        <DialogFooter className="!justify-between sm:!justify-between">
          {!editMode ? (
            <>
              <div className="flex items-center gap-3">
                <Button
                  variant="outline"
                  size="sm"
                  onClick={() => setEditMode(true)}
                  disabled={submit.isPending}
                  className="gap-1.5"
                >
                  <PenLine className="h-3.5 w-3.5" />
                  Изменить промт
                </Button>
                <span className="hidden text-[10px] text-muted-foreground md:inline">
                  Enter · одобрить
                </span>
              </div>
              <div className="flex items-center gap-2">
                <Button
                  variant="ghost"
                  size="sm"
                  onClick={() => decide("reject")}
                  disabled={submit.isPending}
                  className="gap-1.5 text-destructive hover:text-destructive"
                >
                  <XCircle className="h-3.5 w-3.5" />
                  Отклонить
                </Button>
                <Button
                  variant="outline"
                  size="sm"
                  onClick={() => decide("regenerate")}
                  disabled={submit.isPending}
                  className="gap-1.5"
                >
                  <RefreshCw className="h-3.5 w-3.5" />
                  Перегенерировать
                </Button>
                <Button
                  size="sm"
                  onClick={() => decide("approve")}
                  disabled={submit.isPending}
                  className="gap-1.5"
                >
                  {submit.isPending ? (
                    <Loader2 className="h-3.5 w-3.5 animate-spin" />
                  ) : (
                    <CheckCircle2 className="h-3.5 w-3.5" />
                  )}
                  Одобрить
                </Button>
              </div>
            </>
          ) : (
            <>
              <Button
                variant="ghost"
                size="sm"
                onClick={() => setEditMode(false)}
                disabled={submit.isPending}
              >
                Отмена
              </Button>
              <Button
                size="sm"
                onClick={() => decide("edit_prompt", editedPrompt)}
                disabled={submit.isPending || !editedPrompt.trim()}
                className="gap-1.5"
              >
                {submit.isPending && <Loader2 className="h-3.5 w-3.5 animate-spin" />}
                Применить правки
              </Button>
            </>
          )}
        </DialogFooter>
        )}
      </DialogContent>
    </Dialog>
  );
}

function HitlPreview({ hitl }: { hitl: HITLDTO }) {
  // Пытаемся понять что показать на основе payload и kind.
  const photoPath = (hitl.payload?.photo_path as string | undefined) ?? null;
  const videoPath = (hitl.payload?.video_path as string | undefined) ?? null;
  const text = (hitl.payload?.text as string | undefined) ?? null;

  // Артефакты на диске показывать через GET /api/artifacts/<uuid>/file —
  // но в payload путь файловый, не uuid. Пока показываем как preview-стаб.

  if (photoPath) {
    // Файл на диске; в SaaS-варианте — через signed URL.
    // Локально просто показываем имя файла; интеграция с GET artifact-file
    // будет когда payload будет содержать artifact_uuid.
    return (
      <div className="flex flex-col gap-2 rounded-md border border-border bg-muted/30 p-3">
        <div className="flex items-center gap-1.5 text-[10px] uppercase tracking-wider text-muted-foreground">
          <ImageIcon className="h-3 w-3" />
          Превью картинки
        </div>
        <div className="overflow-hidden rounded">
          <img
            src={`/api/files?path=${encodeURIComponent(photoPath)}`}
            alt="HITL preview"
            className="max-h-72 w-full object-contain"
            onError={(e) => {
              (e.target as HTMLImageElement).style.display = "none";
            }}
          />
        </div>
        <div className="font-mono text-[10px] text-muted-foreground">{photoPath}</div>
      </div>
    );
  }

  if (videoPath) {
    return (
      <div className="flex flex-col gap-2 rounded-md border border-border bg-muted/30 p-3">
        <div className="flex items-center gap-1.5 text-[10px] uppercase tracking-wider text-muted-foreground">
          <Video className="h-3 w-3" />
          Превью видео
        </div>
        <video
          controls
          className="max-h-72 w-full rounded"
          src={`/api/files?path=${encodeURIComponent(videoPath)}`}
        />
        <div className="font-mono text-[10px] text-muted-foreground">{videoPath}</div>
      </div>
    );
  }

  if (text) {
    return (
      <div className="flex flex-col gap-2 rounded-md border border-border bg-muted/30 p-3">
        <div className="flex items-center justify-between gap-1.5 text-[10px] uppercase tracking-wider text-muted-foreground">
          <span className="flex items-center gap-1.5">
            <FileText className="h-3 w-3" />
            Содержимое ({text.length.toLocaleString("ru-RU")} симв.)
          </span>
        </div>
        <pre className="max-h-[420px] overflow-auto whitespace-pre-wrap font-mono text-[11.5px] leading-relaxed">
          {text}
        </pre>
      </div>
    );
  }

  return (
    <div className="rounded-md border border-dashed border-border p-4 text-center text-xs text-muted-foreground">
      Превью недоступно — открой папку проекта или дождись артефакта.
    </div>
  );
}

function KindIcon({ kind }: { kind: HITLKind }) {
  const iconMap = {
    approve_plan: FileText,
    approve_script: FileText,
    approve_hero: ImageIcon,
    approve_images: ImageIcon,
    approve_videos: Video,
    approve_final: Video,
  } as const;
  const Icon = iconMap[kind] ?? AlertTriangle;
  return <Icon className="h-3.5 w-3.5" />;
}
