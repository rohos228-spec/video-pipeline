"use client";

import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Plus, Trash2, Loader2, Search, FolderOpen } from "lucide-react";
import { toast } from "sonner";
import { api } from "@/lib/api";
import type { ProjectStatus, ProjectSummary } from "@/lib/types";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { ScrollArea } from "@/components/ui/scroll-area";
import { Badge } from "@/components/ui/badge";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from "@/components/ui/dialog";
import { cn, formatRelativeTime } from "@/lib/utils";

export function ProjectSidebar({
  selectedProjectId,
  onSelect,
}: {
  selectedProjectId: number | null;
  onSelect: (id: number) => void;
}) {
  const [filter, setFilter] = useState("");
  const projects = useQuery({
    queryKey: ["projects"],
    queryFn: api.listProjects,
    refetchInterval: 5000,
  });
  const qc = useQueryClient();

  const deleteMutation = useMutation({
    mutationFn: (id: number) => api.deleteProject(id),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["projects"] });
      toast.success("Проект удалён");
    },
    onError: (e) => toast.error(`Не получилось удалить: ${String(e)}`),
  });

  const filtered = (projects.data ?? []).filter((p) =>
    !filter.trim()
      ? true
      : p.topic.toLowerCase().includes(filter.toLowerCase()) ||
        p.slug.toLowerCase().includes(filter.toLowerCase())
  );

  return (
    <aside className="flex w-72 shrink-0 flex-col border-r border-border bg-card/20">
      <div className="flex items-center justify-between gap-2 border-b border-border p-3">
        <div className="flex items-center gap-2">
          <FolderOpen className="h-3.5 w-3.5 text-muted-foreground" />
          <span className="text-xs font-medium uppercase tracking-wider text-muted-foreground">
            Проекты
          </span>
          {projects.data && (
            <Badge variant="muted" className="h-4 px-1.5 text-[10px]">
              {projects.data.length}
            </Badge>
          )}
        </div>
        <NewProjectDialog
          trigger={
            <Button size="icon" variant="ghost" className="h-7 w-7">
              <Plus className="h-4 w-4" />
            </Button>
          }
          onCreated={(p) => onSelect(p.id)}
        />
      </div>
      <div className="border-b border-border p-2">
        <div className="relative">
          <Search className="pointer-events-none absolute left-2.5 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-muted-foreground" />
          <Input
            placeholder="Поиск..."
            value={filter}
            onChange={(e) => setFilter(e.target.value)}
            className="h-8 pl-7 text-xs"
          />
        </div>
      </div>
      <ScrollArea className="flex-1">
        <div className="flex flex-col p-1.5">
          {projects.isLoading && (
            <div className="flex items-center justify-center py-8">
              <Loader2 className="h-4 w-4 animate-spin text-muted-foreground" />
            </div>
          )}
          {projects.isError && (
            <div className="px-3 py-4 text-xs text-destructive">
              Не получилось загрузить проекты. Проверь, что бэкенд запущен на :8765.
            </div>
          )}
          {!projects.isLoading && filtered.length === 0 && (
            <div className="px-3 py-8 text-center text-xs text-muted-foreground">
              {filter ? "Ничего не найдено." : "Пока ни одного проекта."}
            </div>
          )}
          {filtered.map((p) => (
            <ProjectRow
              key={p.id}
              project={p}
              selected={p.id === selectedProjectId}
              onSelect={() => onSelect(p.id)}
              onDelete={() => deleteMutation.mutate(p.id)}
            />
          ))}
        </div>
      </ScrollArea>
    </aside>
  );
}

function ProjectRow({
  project,
  selected,
  onSelect,
  onDelete,
}: {
  project: ProjectSummary;
  selected: boolean;
  onSelect: () => void;
  onDelete: () => void;
}) {
  return (
    <button
      type="button"
      onClick={onSelect}
      className={cn(
        "group flex flex-col items-stretch gap-1.5 rounded-md border border-transparent px-2.5 py-2 text-left transition-colors",
        selected
          ? "border-primary/30 bg-primary/5 text-foreground"
          : "hover:border-border hover:bg-accent/50"
      )}
    >
      <div className="flex items-center justify-between gap-2">
        <span className="line-clamp-1 text-sm font-medium leading-tight">
          {project.topic}
        </span>
        <button
          type="button"
          onClick={(e) => {
            e.stopPropagation();
            if (confirm(`Удалить проект «${project.topic}»?`)) onDelete();
          }}
          className="invisible h-5 w-5 shrink-0 rounded text-muted-foreground hover:bg-destructive/15 hover:text-destructive group-hover:visible"
          aria-label="Удалить"
        >
          <Trash2 className="h-3 w-3" />
        </button>
      </div>
      <div className="flex items-center justify-between gap-2 text-[10px]">
        <StatusPill status={project.status} />
        <span className="text-muted-foreground">{formatRelativeTime(project.updated_at)}</span>
      </div>
    </button>
  );
}

function StatusPill({ status }: { status: ProjectStatus }) {
  const variant = statusVariant(status);
  return (
    <Badge variant={variant} className="h-4 px-1.5 text-[9px]">
      {statusLabel(status)}
    </Badge>
  );
}

function NewProjectDialog({
  trigger,
  onCreated,
}: {
  trigger: React.ReactNode;
  onCreated: (p: ProjectSummary) => void;
}) {
  const [open, setOpen] = useState(false);
  const [topic, setTopic] = useState("");
  const [heroMode, setHeroMode] = useState<"hero" | "no_hero" | "auto">("auto");
  const qc = useQueryClient();
  const create = useMutation({
    mutationFn: () => api.createProject({ topic, hero_mode: heroMode }),
    onSuccess: (p) => {
      qc.invalidateQueries({ queryKey: ["projects"] });
      onCreated(p);
      setOpen(false);
      setTopic("");
      toast.success(`Проект «${p.topic}» создан`);
    },
    onError: (e) => toast.error(`Не получилось: ${String(e)}`),
  });
  return (
    <Dialog open={open} onOpenChange={setOpen}>
      <DialogTrigger asChild>{trigger}</DialogTrigger>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>Новый проект</DialogTitle>
          <DialogDescription>
            Короткий вертикальный ролик 60–75 сек. Дальше пайплайн пройдёт по нодам автоматически.
          </DialogDescription>
        </DialogHeader>
        <div className="flex flex-col gap-3">
          <div className="flex flex-col gap-1.5">
            <label className="text-xs font-medium text-muted-foreground">Тема ролика</label>
            <Input
              value={topic}
              onChange={(e) => setTopic(e.target.value)}
              placeholder="Например: 5 фактов о рачках в стиле киберпанк"
              autoFocus
            />
          </div>
          <div className="flex flex-col gap-1.5">
            <label className="text-xs font-medium text-muted-foreground">Главный герой</label>
            <div className="flex gap-1">
              {(["auto", "hero", "no_hero"] as const).map((mode) => (
                <Button
                  key={mode}
                  type="button"
                  variant={heroMode === mode ? "default" : "outline"}
                  size="sm"
                  onClick={() => setHeroMode(mode)}
                  className="flex-1 text-xs"
                >
                  {mode === "auto" ? "Авто" : mode === "hero" ? "Есть герой" : "Без героя"}
                </Button>
              ))}
            </div>
          </div>
        </div>
        <DialogFooter>
          <Button variant="ghost" onClick={() => setOpen(false)}>
            Отмена
          </Button>
          <Button
            onClick={() => create.mutate()}
            disabled={!topic.trim() || create.isPending}
          >
            {create.isPending && <Loader2 className="h-3.5 w-3.5 animate-spin" />}
            Создать
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

function statusVariant(s: ProjectStatus): "default" | "success" | "warning" | "destructive" | "info" | "muted" {
  if (s === "new") return "muted";
  if (s === "paused" || s === "failed") return "destructive";
  if (s === "published" || s === "assembled") return "success";
  if (s.endsWith("_ready") || s === "audio_ready" || s === "videos_ready") return "info";
  return "default";
}

function statusLabel(s: ProjectStatus): string {
  const ru: Partial<Record<ProjectStatus, string>> = {
    new: "новый",
    planning: "план",
    plan_ready: "план готов",
    scripting: "сценарий",
    script_ready: "сценарий готов",
    splitting: "разбивка",
    frames_ready: "кадры готовы",
    generating_hero: "герои",
    hero_ready: "герои готовы",
    generating_items: "предметы",
    items_ready: "предметы готовы",
    generating_image_prompts: "img промты",
    image_prompts_ready: "img промты готовы",
    generating_images: "картинки",
    images_ready: "картинки готовы",
    generating_animation_prompts: "anim промты",
    animation_prompts_ready: "anim промты готовы",
    generating_videos: "видео",
    videos_ready: "видео готово",
    generating_audio: "аудио",
    audio_ready: "аудио готово",
    assembling: "сборка",
    assembled: "собрано",
    publishing: "публикация",
    published: "опубликовано",
    paused: "пауза",
    failed: "ошибка",
  };
  return ru[s] ?? s;
}
