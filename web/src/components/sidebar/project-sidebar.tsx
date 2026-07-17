"use client";

import { useCallback, useEffect, useMemo, useRef, useState, Fragment } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  Plus,
  Trash2,
  Loader2,
  Search,
  FolderOpen,
  FolderPlus,
  PanelLeftClose,
  PanelLeft,
  ChevronDown,
  ChevronRight,
  ListVideo,
  GripVertical,
} from "lucide-react";
import { toast } from "sonner";
import { errorMessageFromUnknown } from "@/lib/error-message";
import { api } from "@/lib/api";
import type { GenQueueIdleInfo, ProjectStatus, ProjectSummary, SidebarFolder } from "@/lib/types";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { ScrollArea } from "@/components/ui/scroll-area";
import { Badge } from "@/components/ui/badge";
import { cn, formatRelativeTime } from "@/lib/utils";
import { formatProjectStatus } from "@/lib/format-labels";
import { NewProjectWizard } from "@/components/sidebar/new-project-wizard";
import { GenQueueDialog } from "@/components/sidebar/gen-queue-dialog";
import { SidebarResizeHandle } from "@/components/sidebar/sidebar-resize-handle";
import { usePersistedState } from "@/hooks/use-persisted-state";
import { useSidebarWidth } from "@/hooks/use-sidebar-width";

type DragPayload =
  | { kind: "project"; id: number; folderId: string | null }
  | { kind: "folder"; id: string };

function queueIdleShortLabel(idle: GenQueueIdleInfo | null, projectId: number): string | null {
  if (!idle || idle.project_id !== projectId) return null;
  const byReason: Record<string, string> = {
    paused: "пауза",
    user_stop: "стоп",
    failed: "ошибка",
    auto_mode: "нет ИИ",
    waiting: "ждёт",
  };
  return byReason[idle.reason] ?? idle.detail.slice(0, 12);
}

function queueIdleTooltip(idle: GenQueueIdleInfo | null, projectId: number): string | null {
  if (!idle || idle.project_id !== projectId) return null;
  return idle.detail;
}

function parseDragPayload(raw: string): DragPayload | null {
  try {
    return JSON.parse(raw) as DragPayload;
  } catch {
    return null;
  }
}

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
  // Свёрнутые папки / mass — в localStorage, не сбрасывать на F5 и refetch.
  const [expanded, setExpanded] = usePersistedState<Record<string, boolean>>(
    "vp-studio-sidebar-expanded",
    {},
  );
  const [activeFolderId, setActiveFolderId] = useState<string | null>(null);
  const lastExpandedForProject = useRef<number | null>(null);
  const [newFolderOpen, setNewFolderOpen] = useState(false);
  const [newFolderName, setNewFolderName] = useState("");
  const [dragOverKey, setDragOverKey] = useState<string | null>(null);
  const [queueDialogProject, setQueueDialogProject] = useState<ProjectSummary | null>(null);
  const { width: sidebarWidth, setWidth: setSidebarWidth, minWidth, maxWidth } =
    useSidebarWidth();

  const projects = useQuery({
    queryKey: ["projects"],
    queryFn: api.listProjects,
    refetchInterval: 5000,
  });
  const layout = useQuery({
    queryKey: ["sidebar-layout"],
    queryFn: api.getSidebarLayout,
    refetchInterval: 5000,
  });
  const qc = useQueryClient();

  const deleteMutation = useMutation({
    mutationFn: (id: number) => api.deleteProject(id),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["projects"] });
      qc.invalidateQueries({ queryKey: ["sidebar-layout"] });
      toast.success("Проект удалён");
    },
    onError: (e) => toast.error(`Не получилось удалить: ${String(e)}`),
  });

  const createChildMutation = useMutation({
    mutationFn: (parentId: number) => api.createChildProject(parentId),
    onSuccess: (child, parentId) => {
      qc.invalidateQueries({ queryKey: ["projects"] });
      setExpanded((e) => ({ ...e, [`mass:${parentId}`]: true }));
      onSelect(child.id);
      toast.success(`Дочерний проект создан: ${child.topic}`);
    },
    onError: (e) => toast.error(errorMessageFromUnknown(e)),
  });

  const saveLayoutMutation = useMutation({
    mutationFn: (body: {
      folders?: SidebarFolder[];
      project_layout?: Record<string, { folder_id: string | null; order: number }>;
    }) => api.updateSidebarLayout(body),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["sidebar-layout"] });
      qc.invalidateQueries({ queryKey: ["projects"] });
    },
    onError: (e) => toast.error(`Не удалось сохранить порядок: ${String(e)}`),
  });

  const createFolderMutation = useMutation({
    mutationFn: (name: string) => api.createSidebarFolder(name),
    onSuccess: (folder) => {
      qc.invalidateQueries({ queryKey: ["sidebar-layout"] });
      setNewFolderOpen(false);
      setNewFolderName("");
      setActiveFolderId(folder.id);
      setExpanded((e) => ({ ...e, [`folder:${folder.id}`]: true }));
      toast.success(`Папка «${folder.name}» создана`);
    },
    onError: (e) => toast.error(errorMessageFromUnknown(e)),
  });

  const deleteFolderMutation = useMutation({
    mutationFn: (folderId: string) => api.deleteSidebarFolder(folderId),
    onSuccess: (_data, folderId) => {
      qc.invalidateQueries({ queryKey: ["sidebar-layout"] });
      qc.invalidateQueries({ queryKey: ["projects"] });
      setActiveFolderId((id) => (id === folderId ? null : id));
      toast.success("Папка удалена");
    },
    onError: (e) => toast.error(`Не получилось удалить папку: ${String(e)}`),
  });

  const queueToggleMutation = useMutation({
    mutationFn: (projectId: number) => api.toggleGenQueue(projectId),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["projects"] });
      qc.invalidateQueries({ queryKey: ["sidebar-layout"] });
      toast.success("Проект снят с очереди");
    },
    onError: (e) => toast.error(errorMessageFromUnknown(e)),
  });

  const handleQueueClick = useCallback(
    (project: ProjectSummary) => {
      if (project.gen_queue_position != null) {
        queueToggleMutation.mutate(project.id);
        return;
      }
      setQueueDialogProject(project);
    },
    [queueToggleMutation],
  );

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

  const folders = useMemo(
    () =>
      [...(layout.data?.folders ?? [])].sort(
        (a, b) => (a.order ?? 0) - (b.order ?? 0) || a.name.localeCompare(b.name),
      ),
    [layout.data?.folders],
  );

  const projectsByFolder = useMemo(() => {
    const map = new Map<string | null, ProjectSummary[]>();
    for (const p of roots) {
      const fid = p.sidebar_folder_id ?? null;
      const arr = map.get(fid) ?? [];
      arr.push(p);
      map.set(fid, arr);
    }
    for (const arr of map.values()) {
      arr.sort((a, b) => (a.sidebar_order ?? 999) - (b.sidebar_order ?? 999));
    }
    return map;
  }, [roots]);

  const rootProjects = projectsByFolder.get(null) ?? [];
  const genQueueIdle = layout.data?.gen_queue_idle ?? null;

  const persistProjectLayout = useCallback(
    (
      updates: Array<{
        id: number;
        folder_id: string | null;
        order: number;
      }>,
    ) => {
      const base = { ...(layout.data?.project_layout ?? {}) };
      for (const u of updates) {
        base[String(u.id)] = { folder_id: u.folder_id, order: u.order };
      }
      saveLayoutMutation.mutate({ project_layout: base });
    },
    [layout.data?.project_layout, saveLayoutMutation],
  );

  const moveProject = useCallback(
    (
      projectId: number,
      targetFolderId: string | null,
      targetIndex: number,
    ) => {
      const pool = [...(projectsByFolder.get(targetFolderId) ?? [])].filter(
        (p) => p.id !== projectId,
      );
      const moving = roots.find((p) => p.id === projectId);
      if (!moving) return;
      const clamped = Math.max(0, Math.min(targetIndex, pool.length));
      pool.splice(clamped, 0, { ...moving, sidebar_folder_id: targetFolderId });
      const updates = pool.map((p, idx) => ({
        id: p.id,
        folder_id: targetFolderId,
        order: idx,
      }));
      persistProjectLayout(updates);
    },
    [persistProjectLayout, projectsByFolder, roots],
  );

  const moveFolder = useCallback(
    (folderId: string, targetIndex: number) => {
      const list = folders.filter((f) => f.id !== folderId);
      const moving = folders.find((f) => f.id === folderId);
      if (!moving) return;
      const clamped = Math.max(0, Math.min(targetIndex, list.length));
      list.splice(clamped, 0, moving);
      const updated = list.map((f, idx) => ({ ...f, order: idx }));
      saveLayoutMutation.mutate({ folders: updated });
    },
    [folders, saveLayoutMutation],
  );

  const folderIndexById = useCallback(
    (folderId: string) => folders.findIndex((f) => f.id === folderId),
    [folders],
  );

  const handleDropOnProjects = (
    targetFolderId: string | null,
    targetIndex: number,
    payload: DragPayload,
  ) => {
    setDragOverKey(null);
    if (payload.kind === "project") {
      moveProject(payload.id, targetFolderId, targetIndex);
      return;
    }
    if (payload.kind === "folder" && targetFolderId === null) {
      moveFolder(payload.id, targetIndex);
    }
  };

  useEffect(() => {
    if (selectedProjectId != null) return;
    const list = projects.data;
    if (!list?.length) return;
    onSelect(list[0].id);
  }, [projects.data, selectedProjectId, onSelect]);

  // Раскрывать папку только при СМЕНЕ выбранного проекта — не на каждом
  // refetch projects (иначе свёрнутая папка снова открывается каждые 5с).
  useEffect(() => {
    if (selectedProjectId == null) return;
    if (lastExpandedForProject.current === selectedProjectId) return;
    const sel = projects.data?.find((p) => p.id === selectedProjectId);
    if (!sel) return;
    lastExpandedForProject.current = selectedProjectId;
    if (sel.mass_parent_id != null) {
      setExpanded((e) => ({ ...e, [`mass:${sel.mass_parent_id}`]: true }));
    }
    if (sel.sidebar_folder_id) {
      setExpanded((e) => ({ ...e, [`folder:${sel.sidebar_folder_id}`]: true }));
    }
  }, [selectedProjectId, projects.data, setExpanded]);

  const q = filter.trim().toLowerCase();
  const matchProject = (p: ProjectSummary) =>
    !q ||
    p.topic.toLowerCase().includes(q) ||
    p.slug.toLowerCase().includes(q);

  const renderProject = (
    p: ProjectSummary,
    opts?: { compact?: boolean; badge?: string; draggable?: boolean },
  ) => {
    const children = childrenByParent.get(p.id) ?? [];
    const isFactory = p.mass_factory || children.length > 0;
    const isOpen = expanded[`mass:${p.id}`] ?? isFactory;
    const parentBadge = opts?.badge
      ?? (p.mass_factory ? "шаблон" : children.length > 0 ? `${children.length} доч.` : undefined);
    const queueIdleShort = queueIdleShortLabel(genQueueIdle, p.id);
    const queueIdleTip = queueIdleTooltip(genQueueIdle, p.id);
    return (
      <div key={p.id} className="flex flex-col">
        <div
          className={cn(
            "flex items-stretch gap-0.5",
            dragOverKey === `project-slot:${p.sidebar_folder_id ?? "root"}:${p.id}` &&
              "rounded-xl ring-1 ring-primary/25",
          )}
          onDragOver={(e) => {
            if (!opts?.draggable) return;
            e.preventDefault();
            setDragOverKey(`project-slot:${p.sidebar_folder_id ?? "root"}:${p.id}`);
          }}
          onDragLeave={() => setDragOverKey(null)}
          onDrop={(e) => {
            e.preventDefault();
            e.stopPropagation();
            const payload = parseDragPayload(e.dataTransfer.getData("application/x-sidebar-item"));
            if (!payload) return;
            const pool = projectsByFolder.get(p.sidebar_folder_id ?? null) ?? [];
            const idx = pool.findIndex((x) => x.id === p.id);
            handleDropOnProjects(p.sidebar_folder_id ?? null, Math.max(0, idx), payload);
          }}
        >
          {isFactory ? (
            <button
              type="button"
              className="mt-2 flex h-6 w-5 shrink-0 items-center justify-center rounded text-muted-foreground/45 transition-colors hover:bg-white/[0.05] hover:text-foreground/75"
              onClick={() =>
                setExpanded((e) => ({ ...e, [`mass:${p.id}`]: !isOpen }))
              }
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
              badge={parentBadge}
              compact={opts?.compact}
              draggable={opts?.draggable}
              canCreateChild={p.mass_parent_id == null}
              creatingChild={createChildMutation.isPending && createChildMutation.variables === p.id}
              queuePosition={p.gen_queue_position ?? null}
              queueIdleShort={queueIdleShort}
              queueIdleDetail={queueIdleTip}
              onToggleQueue={() => handleQueueClick(p)}
              onSelect={() => onSelect(p.id)}
              onDelete={() => deleteMutation.mutate(p.id)}
              onCreateChild={() => createChildMutation.mutate(p.id)}
            />
          </div>
        </div>
        {isFactory && isOpen && children.length > 0 && (
          <div className="ml-4 space-y-1 border-l border-violet-400/15 pl-2">
            {children.map((c) =>
              matchProject(c) || matchProject(p) ? (
                <ProjectRow
                  key={c.id}
                  project={c}
                  selected={c.id === selectedProjectId}
                  compact
                  badge={p.mass_factory ? `#${c.mass_lane_position ?? "?"}` : `доч. ${c.mass_lane_position ?? "?"}`}
                  queuePosition={c.gen_queue_position ?? null}
                  queueIdleShort={queueIdleShortLabel(genQueueIdle, c.id)}
                  queueIdleDetail={queueIdleTooltip(genQueueIdle, c.id)}
                  onToggleQueue={() => handleQueueClick(c)}
                  onSelect={() => onSelect(c.id)}
                  onDelete={() => deleteMutation.mutate(c.id)}
                />
              ) : null,
            )}
          </div>
        )}
      </div>
    );
  };

  if (collapsed) {
    return (
      <aside className="flex w-11 shrink-0 flex-col items-center border-r border-white/[0.06] bg-gradient-to-b from-card/40 to-card/10 py-2 font-light backdrop-blur-xl">
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

  const visibleFolders = folders.filter(
    (f) =>
      !q ||
      f.name.toLowerCase().includes(q) ||
      (projectsByFolder.get(f.id) ?? []).some(matchProject),
  );
  const visibleRootProjects = rootProjects.filter(matchProject);

  return (
    <aside
      style={{ width: sidebarWidth }}
      className="relative flex min-h-0 shrink-0 flex-col border-r border-white/[0.06] bg-gradient-to-b from-card/50 via-card/25 to-background/80 font-light shadow-[inset_-1px_0_0_rgba(255,255,255,0.04)] backdrop-blur-xl"
    >
      <SidebarResizeHandle
        width={sidebarWidth}
        onWidthChange={setSidebarWidth}
        minWidth={minWidth}
        maxWidth={maxWidth}
      />
      <div className="flex items-center justify-between gap-2 border-b border-white/[0.06] px-3.5 py-3.5">
        <div className="flex min-w-0 items-center gap-2.5">
          <div className="flex h-7 w-7 items-center justify-center rounded-lg border border-white/[0.08] bg-white/[0.03] shadow-sm">
            <FolderOpen className="h-3.5 w-3.5 text-muted-foreground/80" strokeWidth={1.5} />
          </div>
          <div className="min-w-0">
            <span className="block text-[11px] font-normal tracking-[0.14em] text-muted-foreground/90 uppercase">
              Проекты
            </span>
            {projects.data && (
              <span className="text-[10px] text-muted-foreground/55">
                {roots.length} {roots.length === 1 ? "проект" : "проектов"}
              </span>
            )}
          </div>
        </div>
        <div className="flex items-center gap-0.5 rounded-lg border border-white/[0.06] bg-white/[0.02] p-0.5">
          <Button
            type="button"
            size="icon"
            variant="ghost"
            className="h-7 w-7 rounded-md text-muted-foreground hover:bg-white/[0.06] hover:text-foreground"
            title="Новая папка"
            onClick={() => setNewFolderOpen(true)}
          >
            <FolderPlus className="h-3.5 w-3.5" strokeWidth={1.5} />
          </Button>
          <Button
            type="button"
            size="icon"
            variant="ghost"
            className="h-7 w-7 rounded-md text-muted-foreground hover:bg-white/[0.06] hover:text-foreground"
            title="Скрыть панель"
            onClick={onToggleCollapsed}
          >
            <PanelLeftClose className="h-3.5 w-3.5" strokeWidth={1.5} />
          </Button>
          <NewProjectWizard
            folderId={activeFolderId}
            trigger={
              <Button
                size="icon"
                variant="ghost"
                className="h-7 w-7 rounded-md text-muted-foreground hover:bg-white/[0.06] hover:text-foreground"
                title="Новый проект"
              >
                <Plus className="h-3.5 w-3.5" strokeWidth={1.5} />
              </Button>
            }
            onCreated={(p) => onSelect(p.id)}
          />
        </div>
      </div>

      {newFolderOpen && (
        <div className="flex items-center gap-1.5 border-b border-white/[0.06] bg-white/[0.02] px-3 py-2.5">
          <Input
            autoFocus
            placeholder="Название папки…"
            value={newFolderName}
            onChange={(e) => setNewFolderName(e.target.value)}
            className="h-8 border-white/[0.08] bg-background/40 text-xs font-light placeholder:text-muted-foreground/50"
            onKeyDown={(e) => {
              if (e.key === "Enter" && newFolderName.trim()) {
                createFolderMutation.mutate(newFolderName.trim());
              }
              if (e.key === "Escape") setNewFolderOpen(false);
            }}
          />
          <Button
            size="sm"
            className="h-8 shrink-0 px-3 text-xs font-normal"
            disabled={!newFolderName.trim() || createFolderMutation.isPending}
            onClick={() => createFolderMutation.mutate(newFolderName.trim())}
          >
            OK
          </Button>
        </div>
      )}

      <div className="border-b border-white/[0.06] px-3 py-2.5">
        <div className="relative">
          <Search
            className="pointer-events-none absolute left-3 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-muted-foreground/45"
            strokeWidth={1.5}
          />
          <Input
            placeholder="Поиск проектов…"
            value={filter}
            onChange={(e) => setFilter(e.target.value)}
            className="h-9 rounded-lg border-white/[0.08] bg-white/[0.03] pl-9 text-xs font-light placeholder:text-muted-foreground/45 focus-visible:ring-1 focus-visible:ring-white/10"
          />
        </div>
      </div>

      <ScrollArea className="min-h-0 flex-1">
        <div
          className="flex flex-col gap-0.5 px-2.5 py-2"
          onDragOver={(e) => e.preventDefault()}
        >
          {projects.isLoading && (
            <div className="flex items-center justify-center py-8">
              <Loader2 className="h-4 w-4 animate-spin text-muted-foreground" />
            </div>
          )}

          {!projects.isLoading && visibleFolders.length === 0 && visibleRootProjects.length === 0 && (
            <div className="px-3 py-10 text-center text-xs font-light text-muted-foreground/60">
              {filter ? "Ничего не найдено." : "Пока ни одного проекта."}
            </div>
          )}

          {(visibleFolders.length > 0 || visibleRootProjects.length > 0) && (
            <DropZone
              zoneKey="top"
              activeKey={dragOverKey}
              onActivate={() => setDragOverKey("top")}
              onDeactivate={() => setDragOverKey(null)}
              onDrop={(payload) => {
                if (payload.kind === "folder") moveFolder(payload.id, 0);
                else moveProject(payload.id, null, 0);
              }}
            />
          )}

          {visibleFolders.map((folder) => {
            const fKey = `folder:${folder.id}`;
            const isOpen = expanded[fKey] ?? true;
            const folderProjects = (projectsByFolder.get(folder.id) ?? []).filter(matchProject);
            const isActiveFolder = activeFolderId === folder.id;
            return (
              <Fragment key={folder.id}>
              <div
                className={cn(
                  "group mb-0.5 overflow-hidden rounded-xl border transition-all duration-200",
                  isActiveFolder
                    ? "border-amber-400/20 bg-amber-500/[0.04] shadow-[0_0_0_1px_rgba(251,191,36,0.08)]"
                    : "border-white/[0.05] bg-white/[0.02]",
                  dragOverKey === `folder:${folder.id}` &&
                    "border-primary/30 bg-primary/[0.04] ring-1 ring-primary/20",
                )}
                onDragOver={(e) => {
                  e.preventDefault();
                  setDragOverKey(`folder:${folder.id}`);
                }}
                onDragLeave={() => setDragOverKey(null)}
                onDrop={(e) => {
                  e.preventDefault();
                  e.stopPropagation();
                  const payload = parseDragPayload(
                    e.dataTransfer.getData("application/x-sidebar-item"),
                  );
                  if (!payload) return;
                  if (payload.kind === "project") {
                    handleDropOnProjects(folder.id, folderProjects.length, payload);
                  }
                }}
              >
                <div className="flex items-center gap-1 px-2 py-2">
                  <span
                    draggable
                    onDragStart={(e) => {
                      e.dataTransfer.setData(
                        "application/x-sidebar-item",
                        JSON.stringify({ kind: "folder", id: folder.id }),
                      );
                      e.dataTransfer.effectAllowed = "move";
                    }}
                    className="cursor-grab rounded p-0.5 text-muted-foreground/35 opacity-0 transition-opacity hover:text-muted-foreground/70 active:cursor-grabbing group-hover:opacity-100"
                    title="Перетащить папку"
                  >
                    <GripVertical className="h-3 w-3" strokeWidth={1.5} />
                  </span>
                  <button
                    type="button"
                    className="flex h-6 w-5 items-center justify-center rounded text-muted-foreground/50 transition-colors hover:bg-white/[0.05] hover:text-foreground/80"
                    onClick={() => setExpanded((e) => ({ ...e, [fKey]: !isOpen }))}
                  >
                    {isOpen ? (
                      <ChevronDown className="h-3.5 w-3.5" strokeWidth={1.5} />
                    ) : (
                      <ChevronRight className="h-3.5 w-3.5" strokeWidth={1.5} />
                    )}
                  </button>
                  <button
                    type="button"
                    className={cn(
                      "group flex min-w-0 flex-1 items-center gap-2 rounded-lg px-2 py-1.5 text-left transition-colors",
                      isActiveFolder
                        ? "bg-amber-500/[0.08] text-foreground/90"
                        : "hover:bg-white/[0.04] text-foreground/80",
                    )}
                    onClick={() => {
                      setActiveFolderId(folder.id);
                      setExpanded((e) => ({ ...e, [fKey]: true }));
                    }}
                    title="Выбрать папку для новых проектов"
                  >
                    <FolderOpen
                      className={cn(
                        "h-3.5 w-3.5 shrink-0",
                        isActiveFolder ? "text-amber-300/90" : "text-amber-400/55",
                      )}
                      strokeWidth={1.5}
                    />
                    <span className="break-words text-[12px] font-normal leading-snug tracking-[0.01em]">
                      {folder.name}
                    </span>
                    <span className="ml-auto rounded-full border border-white/[0.08] bg-white/[0.03] px-1.5 py-px text-[9px] font-normal tabular-nums text-muted-foreground/70">
                      {folderProjects.length}
                    </span>
                  </button>
                  <button
                    type="button"
                    onClick={(e) => {
                      e.stopPropagation();
                      const count = folderProjects.length;
                      const detail =
                        count > 0
                          ? `\n\n${count} ${count === 1 ? "проект" : "проектов"} переместится в корень списка (папка не удаляет проекты).`
                          : "";
                      if (
                        confirm(`Удалить папку «${folder.name}»?${detail}`)
                      ) {
                        deleteFolderMutation.mutate(folder.id);
                      }
                    }}
                    disabled={deleteFolderMutation.isPending}
                    className="invisible h-6 w-6 shrink-0 rounded-md text-muted-foreground/50 transition-colors hover:bg-destructive/10 hover:text-destructive/90 group-hover:visible"
                    aria-label="Удалить папку"
                    title="Удалить папку"
                  >
                    <Trash2 className="h-3 w-3" strokeWidth={1.5} />
                  </button>
                </div>
                {isOpen && (
                  <div className="space-y-1 border-t border-white/[0.05] px-1.5 py-1.5">
                    {folderProjects.map((p) => renderProject(p, { draggable: true }))}
                    {folderProjects.length === 0 && (
                      <div className="px-3 py-3 text-center text-[10px] font-light text-muted-foreground/45">
                        Перетащите проект сюда
                      </div>
                    )}
                  </div>
                )}
              </div>
              <DropZone
                zoneKey={`after-folder:${folder.id}`}
                activeKey={dragOverKey}
                onActivate={() => setDragOverKey(`after-folder:${folder.id}`)}
                onDeactivate={() => setDragOverKey(null)}
                onDrop={(payload) => {
                  const afterIdx = folderIndexById(folder.id) + 1;
                  if (payload.kind === "folder") moveFolder(payload.id, afterIdx);
                  else moveProject(payload.id, null, 0);
                }}
              />
              </Fragment>
            );
          })}

          {visibleFolders.length > 0 && visibleRootProjects.length > 0 && (
            <DropZone
              zoneKey="folders-root-gap"
              activeKey={dragOverKey}
              onActivate={() => setDragOverKey("folders-root-gap")}
              onDeactivate={() => setDragOverKey(null)}
              onDrop={(payload) => {
                if (payload.kind === "folder") moveFolder(payload.id, folders.length);
                else moveProject(payload.id, null, 0);
              }}
            />
          )}

          {visibleRootProjects.map((p, idx) => (
            <div key={p.id}>
              {renderProject(p, { draggable: true })}
              <DropZone
                zoneKey={`after-root:${p.id}`}
                activeKey={dragOverKey}
                onActivate={() => setDragOverKey(`after-root:${p.id}`)}
                onDeactivate={() => setDragOverKey(null)}
                onDrop={(payload) => {
                  if (payload.kind === "project") {
                    handleDropOnProjects(null, idx + 1, payload);
                  } else if (payload.kind === "folder") {
                    moveFolder(payload.id, folders.length);
                  }
                }}
              />
            </div>
          ))}

          {(visibleFolders.length > 0 || visibleRootProjects.length > 0) && (
            <DropZone
              zoneKey="bottom"
              activeKey={dragOverKey}
              onActivate={() => setDragOverKey("bottom")}
              onDeactivate={() => setDragOverKey(null)}
              className="min-h-6"
              onDrop={(payload) => {
                if (payload.kind === "folder") moveFolder(payload.id, folders.length);
                else handleDropOnProjects(null, rootProjects.length, payload);
              }}
            />
          )}
        </div>
      </ScrollArea>
      <GenQueueDialog
        open={queueDialogProject != null}
        onOpenChange={(o) => {
          if (!o) setQueueDialogProject(null);
        }}
        project={queueDialogProject}
      />
    </aside>
  );
}

function DropZone({
  zoneKey,
  activeKey,
  onActivate,
  onDeactivate,
  onDrop,
  className,
}: {
  zoneKey: string;
  activeKey: string | null;
  onActivate: () => void;
  onDeactivate: () => void;
  onDrop: (payload: DragPayload) => void;
  className?: string;
}) {
  const active = activeKey === zoneKey;
  return (
    <div
      className={cn(
        "shrink-0 rounded-lg transition-all duration-150",
        active ? "my-0.5 min-h-7 bg-primary/10 ring-1 ring-primary/25" : "min-h-3",
        className,
      )}
      onDragOver={(e) => {
        e.preventDefault();
        e.stopPropagation();
        onActivate();
      }}
      onDragLeave={(e) => {
        const related = e.relatedTarget as Node | null;
        if (related && e.currentTarget.contains(related)) return;
        onDeactivate();
      }}
      onDrop={(e) => {
        e.preventDefault();
        e.stopPropagation();
        onDeactivate();
        const payload = parseDragPayload(e.dataTransfer.getData("application/x-sidebar-item"));
        if (payload) onDrop(payload);
      }}
    />
  );
}

function ProjectRow({
  project,
  selected,
  compact,
  badge,
  draggable,
  canCreateChild,
  creatingChild,
  queuePosition,
  queueIdleShort,
  queueIdleDetail,
  onToggleQueue,
  onSelect,
  onDelete,
  onCreateChild,
}: {
  project: ProjectSummary;
  selected: boolean;
  compact?: boolean;
  badge?: string;
  draggable?: boolean;
  canCreateChild?: boolean;
  creatingChild?: boolean;
  queuePosition?: number | null;
  queueIdleShort?: string | null;
  queueIdleDetail?: string | null;
  onToggleQueue?: () => void;
  onSelect: () => void;
  onDelete: () => void;
  onCreateChild?: () => void;
}) {
  return (
    <div
      role="button"
      tabIndex={0}
      data-project-id={project.id}
      onClick={onSelect}
      onKeyDown={(e) => {
        if (e.key === "Enter" || e.key === " ") {
          e.preventDefault();
          onSelect();
        }
      }}
      className={cn(
        "group relative cursor-pointer rounded-xl border text-left transition-all duration-200 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-white/15",
        compact ? "px-2.5 py-2" : "px-3 py-2.5",
        selected
          ? "border-white/[0.12] bg-gradient-to-br from-white/[0.08] to-white/[0.03] shadow-[0_1px_0_rgba(255,255,255,0.06),inset_0_1px_0_rgba(255,255,255,0.04)]"
          : "border-transparent bg-transparent hover:border-white/[0.06] hover:bg-white/[0.03]",
      )}
    >
      <button
        type="button"
        onClick={(e) => {
          e.stopPropagation();
          onToggleQueue?.();
        }}
        className={cn(
          "absolute top-1.5 right-1.5 z-10 flex h-[18px] min-w-[18px] max-w-[72px] items-center justify-center gap-0.5 rounded-full border px-1 text-[9px] font-normal tabular-nums leading-none transition-all",
          queuePosition
            ? queueIdleShort
              ? "border-amber-300/40 bg-amber-400/15 text-amber-100/95 shadow-[0_0_12px_rgba(251,191,36,0.12)]"
              : "border-sky-300/35 bg-sky-400/15 text-sky-100/95 shadow-[0_0_12px_rgba(56,189,248,0.15)]"
            : "border-white/[0.08] bg-white/[0.02] text-transparent opacity-0 hover:border-sky-300/30 hover:text-muted-foreground/50 group-hover:opacity-100",
          queuePosition && "opacity-100",
        )}
        title={
          queuePosition
            ? `Очередь генерации: ${queuePosition}${
                queueIdleDetail ? ` — ${queueIdleDetail}` : ""
              }. Нажмите, чтобы снять.`
            : "Добавить в очередь генерации"
        }
      >
        <span>{queuePosition ?? "·"}</span>
        {queuePosition && queueIdleShort ? (
          <span className="max-w-[40px] truncate text-[7px] font-normal normal-case opacity-90">
            {queueIdleShort}
          </span>
        ) : null}
      </button>

      <div className="flex items-start gap-1.5 pr-7">
        {draggable && (
          <span
            draggable
            onDragStart={(e) => {
              e.stopPropagation();
              e.dataTransfer.setData(
                "application/x-sidebar-item",
                JSON.stringify({
                  kind: "project",
                  id: project.id,
                  folderId: project.sidebar_folder_id ?? null,
                }),
              );
              e.dataTransfer.effectAllowed = "move";
            }}
            onClick={(e) => e.stopPropagation()}
            className="mt-0.5 cursor-grab rounded p-0.5 text-muted-foreground/30 opacity-0 transition-opacity hover:text-muted-foreground/65 active:cursor-grabbing group-hover:opacity-100"
            title="Перетащить"
          >
            <GripVertical className="h-3 w-3" strokeWidth={1.5} />
          </span>
        )}
        <div className="min-w-0 flex-1">
          <div className="flex items-start justify-between gap-2">
            <span
              className={cn(
                "min-w-0 break-words font-normal leading-snug tracking-[0.01em] text-foreground/90",
                compact ? "line-clamp-3 text-[11px]" : "line-clamp-3 text-[13px]",
              )}
            >
              {compact && (
                <ListVideo
                  className="mr-1 inline h-3 w-3 translate-y-[-1px] text-violet-300/70"
                  strokeWidth={1.5}
                />
              )}
              {project.topic}
            </span>
            <div className="flex shrink-0 flex-col items-center gap-0.5">
              {canCreateChild && onCreateChild ? (
                <button
                  type="button"
                  onClick={(e) => {
                    e.stopPropagation();
                    onCreateChild();
                  }}
                  disabled={creatingChild}
                  className="invisible h-5 w-5 rounded-md text-muted-foreground/50 transition-colors hover:bg-emerald-500/10 hover:text-emerald-400/90 group-hover:visible disabled:opacity-50"
                  aria-label="Создать дочерний проект"
                  title="Дочерний проект (настройки, промты и текст для GPT — без закадрового, Excel и результатов)"
                >
                  {creatingChild ? (
                    <Loader2 className="h-3 w-3 animate-spin" />
                  ) : (
                    <Plus className="h-3 w-3" strokeWidth={1.5} />
                  )}
                </button>
              ) : null}
              <button
                type="button"
                onClick={(e) => {
                  e.stopPropagation();
                  if (confirm(`Удалить проект «${project.topic}»?`)) onDelete();
                }}
                className="invisible mt-0.5 h-5 w-5 shrink-0 rounded-md text-muted-foreground/50 transition-colors hover:bg-destructive/10 hover:text-destructive/90 group-hover:visible"
                aria-label="Удалить"
              >
                <Trash2 className="h-3 w-3" strokeWidth={1.5} />
              </button>
            </div>
          </div>
          <div className="mt-2 flex items-center justify-between gap-2">
            <div className="flex min-w-0 items-center gap-1.5">
              <StatusPill status={project.status} />
              {badge && (
                <span className="rounded-full border border-white/[0.08] bg-white/[0.03] px-1.5 py-px text-[9px] font-normal text-muted-foreground/70">
                  {badge}
                </span>
              )}
            </div>
            <span className="shrink-0 text-[10px] font-light text-muted-foreground/50 tabular-nums">
              {formatRelativeTime(project.updated_at)}
            </span>
          </div>
        </div>
      </div>
    </div>
  );
}

function StatusPill({ status }: { status: ProjectStatus }) {
  const variant = statusVariant(status);
  return (
    <Badge
      variant={variant}
      className="h-[18px] border-white/[0.06] px-1.5 text-[9px] font-normal tracking-normal normal-case shadow-none"
    >
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
