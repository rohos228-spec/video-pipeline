"use client";

import { useEffect, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Plus, Trash2, Loader2, Search, FolderOpen, PanelLeftClose, PanelLeft } from "lucide-react";
import { toast } from "sonner";
import { api } from "@/lib/api";
import type { ProjectStatus, ProjectSummary } from "@/lib/types";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { ScrollArea } from "@/components/ui/scroll-area";
import { Badge } from "@/components/ui/badge";
import { cn, formatRelativeTime } from "@/lib/utils";
import { formatProjectStatus } from "@/lib/format-labels";
import { NewProjectWizard } from "@/components/sidebar/new-project-wizard";

export function ProjectSidebar({
  selectedProjectId,
  onSelect,
  collapsed,
  onToggleCollapsed,
}: {
  selectedProjectId: number | null;
  onSelect: (id: number) => void;
  collapsed: boolean;
  onToggleCollapsed: () => void;
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

  useEffect(() => {
    if (selectedProjectId != null) return;
    const list = projects.data;
    if (!list?.length) return;
    onSelect(list[0].id);
  }, [projects.data, selectedProjectId, onSelect]);

  const filtered = (projects.data ?? []).filter((p) =>
    !filter.trim()
      ? true
      : p.topic.toLowerCase().includes(filter.toLowerCase()) ||
        p.slug.toLowerCase().includes(filter.toLowerCase())
  );

  if (collapsed) {
    return (
      <aside className="flex w-11 shrink-0 flex-col items-center border-r border-border bg-card/20 py-2">
        <Button
          type="button"
          size="icon"
          variant="ghost"
          className="h-8 w-8"
          title="Показать проекты"
          onClick={onToggleCollapsed}
        >
          <PanelLeft className="h-4 w-4" />
        </Button>
        <NewProjectWizard
          trigger={
            <Button size="icon" variant="ghost" className="mt-2 h-8 w-8" title="Новый проект">
              <Plus className="h-4 w-4" />
            </Button>
          }
          onCreated={(p) => onSelect(p.id)}
        />
      </aside>
    );
  }

  return (
    <aside className="flex w-72 shrink-0 flex-col border-r border-border bg-card/20 transition-[width]">
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
        <div className="flex items-center gap-0.5">
          <Button
            type="button"
            size="icon"
            variant="ghost"
            className="h-7 w-7"
            title="Скрыть панель"
            onClick={onToggleCollapsed}
          >
            <PanelLeftClose className="h-4 w-4" />
          </Button>
          <NewProjectWizard
            trigger={
              <Button size="icon" variant="ghost" className="h-7 w-7">
                <Plus className="h-4 w-4" />
              </Button>
            }
            onCreated={(p) => onSelect(p.id)}
          />
        </div>
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
    <div
      role="button"
      tabIndex={0}
      onClick={onSelect}
      onKeyDown={(e) => {
        if (e.key === "Enter" || e.key === " ") {
          e.preventDefault();
          onSelect();
        }
      }}
      className={cn(
        "group flex cursor-pointer flex-col items-stretch gap-1.5 rounded-md border border-transparent px-2.5 py-2 text-left transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring",
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
    </div>
  );
}

function StatusPill({ status }: { status: ProjectStatus }) {
  const variant = statusVariant(status);
  return (
    <Badge variant={variant} className="h-4 px-1.5 text-[9px]">
      {formatProjectStatus(status)}
    </Badge>
  );
}

function statusVariant(s: ProjectStatus): "default" | "success" | "warning" | "destructive" | "info" | "muted" {
  if (s === "new") return "muted";
  if (s === "paused" || s === "failed") return "destructive";
  if (s === "published" || s === "assembled") return "success";
  if (s.endsWith("_ready") || s === "audio_ready" || s === "videos_ready") return "info";
  return "default";
}
