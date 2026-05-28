"use client";

import { useEffect, useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  Plus,
  Trash2,
  Loader2,
  Search,
  FolderOpen,
  PanelLeftClose,
  PanelLeft,
  ChevronDown,
  ChevronRight,
  ListVideo,
} from "lucide-react";
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
  const [expanded, setExpanded] = useState<Record<number, boolean>>({});
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

  const { roots, childrenByParent } = useMemo(() => {
    const list = projects.data ?? [];
    const childrenMap = new Map<number, ProjectSummary[]>();
    const rootList: ProjectSummary[] = [];
    for (const p of list) {
      const pid = p.mass_parent_id;
      if (pid != null) {
        const arr = childrenMap.get(pid) ?? [];
        arr.push(p);
        childrenMap.set(pid, arr);
      } else {
        rootList.push(p);
      }
    }
    for (const arr of childrenMap.values()) {
      arr.sort((a, b) => (a.mass_lane_position ?? 999) - (b.mass_lane_position ?? 999));
    }
    return { roots: rootList, childrenByParent: childrenMap };
  }, [projects.data]);

  useEffect(() => {
    if (selectedProjectId != null) return;
    const list = projects.data;
    if (!list?.length) return;
    onSelect(list[0].id);
  }, [projects.data, selectedProjectId, onSelect]);

  useEffect(() => {
    const sel = projects.data?.find((p) => p.id === selectedProjectId);
    if (sel?.mass_parent_id != null) {
      setExpanded((e) => ({ ...e, [sel.mass_parent_id!]: true }));
    }
  }, [selectedProjectId, projects.data]);

  const q = filter.trim().toLowerCase();
  const filteredRoots = roots.filter((p) => {
    if (!q) return true;
    if (p.topic.toLowerCase().includes(q) || p.slug.toLowerCase().includes(q)) return true;
    const kids = childrenByParent.get(p.id) ?? [];
    return kids.some(
      (c) => c.topic.toLowerCase().includes(q) || c.slug.toLowerCase().includes(q),
    );
  });

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
              {roots.length}
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
          {!projects.isLoading && filteredRoots.length === 0 && (
            <div className="px-3 py-8 text-center text-xs text-muted-foreground">
              {filter ? "Ничего не найдено." : "Пока ни одного проекта."}
            </div>
          )}
          {filteredRoots.map((p) => {
            const children = (childrenByParent.get(p.id) ?? []).filter((c) =>
              !q
                ? true
                : c.topic.toLowerCase().includes(q) ||
                  c.slug.toLowerCase().includes(q) ||
                  p.topic.toLowerCase().includes(q),
            );
            const isFactory = p.mass_factory || children.length > 0;
            const isOpen = expanded[p.id] ?? isFactory;
            return (
              <div key={p.id} className="flex flex-col">
                <div className="flex items-stretch gap-0.5">
                  {isFactory ? (
                    <button
                      type="button"
                      className="mt-2 flex h-6 w-5 shrink-0 items-center justify-center text-muted-foreground hover:text-foreground"
                      onClick={() =>
                        setExpanded((e) => ({ ...e, [p.id]: !isOpen }))
                      }
                      aria-label={isOpen ? "Свернуть" : "Показать дочерние"}
                    >
                      {isOpen ? (
                        <ChevronDown className="h-3.5 w-3.5" />
                      ) : (
                        <ChevronRight className="h-3.5 w-3.5" />
                      )}
                    </button>
                  ) : (
                    <span className="w-5 shrink-0" />
                  )}
                  <div className="min-w-0 flex-1">
                    <ProjectRow
                      project={p}
                      selected={p.id === selectedProjectId}
                      badge={isFactory ? "шаблон" : undefined}
                      onSelect={() => onSelect(p.id)}
                      onDelete={() => deleteMutation.mutate(p.id)}
                    />
                  </div>
                </div>
                {isFactory && isOpen && children.length > 0 && (
                  <div className="ml-5 border-l border-violet-500/20 pl-1">
                    {children.map((c) => (
                      <ProjectRow
                        key={c.id}
                        project={c}
                        selected={c.id === selectedProjectId}
                        compact
                        badge={`#${c.mass_lane_position ?? "?"}`}
                        onSelect={() => onSelect(c.id)}
                        onDelete={() => deleteMutation.mutate(c.id)}
                      />
                    ))}
                  </div>
                )}
              </div>
            );
          })}
        </div>
      </ScrollArea>
    </aside>
  );
}

function ProjectRow({
  project,
  selected,
  compact,
  badge,
  onSelect,
  onDelete,
}: {
  project: ProjectSummary;
  selected: boolean;
  compact?: boolean;
  badge?: string;
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
        compact && "py-1.5",
        selected
          ? "border-primary/30 bg-primary/5 text-foreground"
          : "hover:border-border hover:bg-accent/50",
      )}
    >
      <div className="flex items-center justify-between gap-2">
        <span
          className={cn(
            "line-clamp-1 font-medium leading-tight",
            compact ? "text-xs" : "text-sm",
          )}
        >
          {compact && (
            <ListVideo className="mr-1 inline h-3 w-3 text-violet-400" />
          )}
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
        <div className="flex items-center gap-1">
          <StatusPill status={project.status} />
          {badge && (
            <Badge variant="muted" className="h-4 px-1 text-[9px]">
              {badge}
            </Badge>
          )}
        </div>
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

function statusVariant(
  s: ProjectStatus,
): "default" | "success" | "warning" | "destructive" | "info" | "muted" {
  if (s === "new") return "muted";
  if (s === "paused" || s === "failed") return "destructive";
  if (s === "published" || s === "assembled") return "success";
  if (s.endsWith("_ready") || s === "audio_ready" || s === "videos_ready") return "info";
  return "default";
}
