"use client";

import { useEffect, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Loader2, Save, X } from "lucide-react";
import { toast } from "sonner";
import { api } from "@/lib/api";
import type { FrameDTO } from "@/lib/types";
import { Button } from "@/components/ui/button";
import { Sheet, SheetContent, SheetDescription, SheetHeader, SheetTitle } from "@/components/ui/sheet";
import { Badge } from "@/components/ui/badge";
import { cn } from "@/lib/utils";

export function FramesGrid({
  projectId,
  open,
  onOpenChange,
}: {
  projectId: number | null;
  open: boolean;
  onOpenChange: (o: boolean) => void;
}) {
  const frames = useQuery({
    queryKey: ["frames", projectId],
    queryFn: () => api.listFrames(projectId!),
    enabled: open && projectId != null,
  });

  return (
    <Sheet open={open} onOpenChange={onOpenChange}>
      <SheetContent side="right" className="!max-w-5xl">
        <SheetHeader>
          <SheetTitle>
            Кадры проекта{" "}
            {frames.data && (
              <Badge variant="muted" className="ml-2 h-5 px-2">
                {frames.data.length}
              </Badge>
            )}
          </SheetTitle>
          <SheetDescription>
            Все кадры с озвучкой, image-prompts и animation-prompts. Все поля
            редактируются inline — изменения сохраняются по Cmd/Ctrl+Enter или
            кнопке.
          </SheetDescription>
        </SheetHeader>

        <div className="flex-1 overflow-auto">
          {!projectId ? (
            <EmptyMessage text="Выбери проект сначала." />
          ) : frames.isLoading ? (
            <div className="flex h-full items-center justify-center">
              <Loader2 className="h-5 w-5 animate-spin text-muted-foreground" />
            </div>
          ) : !frames.data || frames.data.length === 0 ? (
            <EmptyMessage text="Кадров пока нет — пройди шаги «План → Сценарий → Разбивка»." />
          ) : (
            <div className="flex flex-col divide-y divide-border">
              {frames.data.map((frame) => (
                <FrameRow key={frame.id} frame={frame} />
              ))}
            </div>
          )}
        </div>
      </SheetContent>
    </Sheet>
  );
}

function FrameRow({ frame }: { frame: FrameDTO }) {
  const qc = useQueryClient();
  const [voiceover, setVoiceover] = useState(frame.voiceover_text);
  const [imagePrompt, setImagePrompt] = useState(frame.image_prompt ?? "");
  const [animPrompt, setAnimPrompt] = useState(frame.animation_prompt ?? "");

  // Сбрасываем локальное состояние при смене props (когда фрейм пришёл с сервера обновлённым).
  useEffect(() => {
    setVoiceover(frame.voiceover_text);
    setImagePrompt(frame.image_prompt ?? "");
    setAnimPrompt(frame.animation_prompt ?? "");
  }, [frame.voiceover_text, frame.image_prompt, frame.animation_prompt]);

  const dirty =
    voiceover !== frame.voiceover_text ||
    imagePrompt !== (frame.image_prompt ?? "") ||
    animPrompt !== (frame.animation_prompt ?? "");

  const save = useMutation({
    mutationFn: () =>
      api.patchFrame(frame.project_id, frame.id, {
        voiceover_text: voiceover,
        image_prompt: imagePrompt || null,
        animation_prompt: animPrompt || null,
      }),
    onSuccess: () => {
      toast.success(`Кадр #${frame.number} сохранён`);
      qc.invalidateQueries({ queryKey: ["frames", frame.project_id] });
    },
    onError: (e) => toast.error(`Не удалось сохранить: ${String(e)}`),
  });

  const handleSave = () => {
    if (dirty && !save.isPending) save.mutate();
  };

  const onKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) {
      e.preventDefault();
      handleSave();
    }
  };

  return (
    <div
      className={cn(
        "grid grid-cols-[60px_1fr_1fr_1fr_auto] gap-3 px-4 py-3 transition-colors",
        dirty && "bg-warning/5",
      )}
    >
      <div className="flex flex-col items-start gap-1.5">
        <span className="font-mono text-xs font-medium">#{frame.number}</span>
        <Badge variant={statusVariant(frame.status)} className="h-4 px-1.5 text-[9px]">
          {frame.status}
        </Badge>
        {frame.duration_seconds != null && (
          <span className="font-mono text-[10px] text-muted-foreground">
            {frame.duration_seconds.toFixed(1)}s
          </span>
        )}
      </div>

      <Field
        label="Озвучка"
        value={voiceover}
        onChange={setVoiceover}
        placeholder="Закадровый текст..."
        rows={3}
        onKeyDown={onKeyDown}
      />
      <Field
        label="Image prompt"
        value={imagePrompt}
        onChange={setImagePrompt}
        placeholder="Промт для генерации картинки..."
        rows={3}
        onKeyDown={onKeyDown}
      />
      <Field
        label="Animation prompt"
        value={animPrompt}
        onChange={setAnimPrompt}
        placeholder="Промт для оживления (Veo)..."
        rows={3}
        onKeyDown={onKeyDown}
      />

      <div className="flex flex-col items-end gap-1">
        {dirty && (
          <Button
            size="sm"
            variant="default"
            onClick={handleSave}
            disabled={save.isPending}
            className="h-7 gap-1 px-2 text-xs"
          >
            {save.isPending ? (
              <Loader2 className="h-3 w-3 animate-spin" />
            ) : (
              <Save className="h-3 w-3" />
            )}
            Save
          </Button>
        )}
        {dirty && (
          <Button
            size="sm"
            variant="ghost"
            className="h-6 gap-1 px-1.5 text-[10px] text-muted-foreground"
            onClick={() => {
              setVoiceover(frame.voiceover_text);
              setImagePrompt(frame.image_prompt ?? "");
              setAnimPrompt(frame.animation_prompt ?? "");
            }}
          >
            <X className="h-3 w-3" />
            Reset
          </Button>
        )}
      </div>
    </div>
  );
}

function Field({
  label,
  value,
  onChange,
  placeholder,
  rows,
  onKeyDown,
}: {
  label: string;
  value: string;
  onChange: (s: string) => void;
  placeholder?: string;
  rows: number;
  onKeyDown?: (e: React.KeyboardEvent) => void;
}) {
  return (
    <div className="flex flex-col gap-1">
      <label className="text-[9px] font-medium uppercase tracking-wider text-muted-foreground">
        {label}
      </label>
      <textarea
        className="resize-none rounded border border-border bg-background/50 px-2 py-1.5 font-mono text-[11px] leading-relaxed shadow-sm focus:border-primary/50 focus:outline-none focus:ring-1 focus:ring-primary/30"
        value={value}
        rows={rows}
        onChange={(e) => onChange(e.target.value)}
        onKeyDown={onKeyDown}
        placeholder={placeholder}
      />
    </div>
  );
}

function EmptyMessage({ text }: { text: string }) {
  return (
    <div className="flex h-full items-center justify-center px-6 text-center text-xs text-muted-foreground">
      {text}
    </div>
  );
}

function statusVariant(s: string): "default" | "success" | "warning" | "destructive" | "info" | "muted" {
  if (s === "done") return "success";
  if (s === "failed") return "destructive";
  if (s === "image_approved" || s === "video_approved") return "success";
  if (s === "image_generated" || s === "video_generated") return "info";
  return "default";
}
