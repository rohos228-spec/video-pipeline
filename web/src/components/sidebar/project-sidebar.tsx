"use client";

import type React from "react";
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
  GripVertical,
  Pencil,
  ListOrdered,
} from "lucide-react";
import { toast } from "sonner";
import { errorMessageFromUnknown } from "@/lib/error-message";
import { api } from "@/lib/api";
import type { GenQueueIdleInfo, ProjectStatus, ProjectSummary, SidebarFolder } from "@/lib/types";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { ScrollArea } from "@/components/ui/scroll-area";
import { cn, formatRelativeTime } from "@/lib/utils";
import { formatProjectStatus } from "@/lib/format-labels";
import { projectDisplayName } from "@/lib/project-display";
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

const SLIDE_MS = 350;
const SLIDE_EASE = "cubic-bezier(0.65, 0, 0.35, 1)";

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
  const [slideAnimating, setSlideAnimating] = useState(false);
  const firstCollapsedRef = useRef(true);
  useEffect(() => {
    if (firstCollapsedRef.current) {
      firstCollapsedRef.current = false;
      return;
    }
    setSlideAnimating(true);
    const t = window.setTimeout(() => setSlideAnimating(false), SLIDE_MS + 50);
    return () => window.clearTimeout(t);
  }, [collapsed]);
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
    // Не чаще 10с; не стакуем poll, пока предыдущий ещё в полёте.
    refetchInterval: (q) => (q.state.fetchStatus === "fetching" ? false : 10_000),
    staleTime: 5_000,
  });
  const layout = useQuery({
    queryKey: ["sidebar-layout"],
    queryFn: api.getSidebarLayout,
    refetchInterval: (q) => (q.state.fetchStatus === "fetching" ? false : 10_000),
    staleTime: 5_000,
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
      toast.success(`Дочерний проект создан: ${projectDisplayName(child)}`);
    },
    onError: (e) => toast.error(errorMessageFromUnknown(e)),
  });

  const renameMutation = useMutation({
    mutationFn: ({ id, title }: { id: number; title: string }) =>
      api.patchProject(id, { title }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["projects"] });
      qc.invalidateQueries({ queryKey: ["project"] });
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
    onSuccess: (data) => {
      const positions = (data.gen_queue_positions || {}) as Record<string, number>;
      qc.setQueryData<ProjectSummary[]>(["projects"], (old) => {
        if (!old) return old;
        return old.map((p) => {
          const raw = positions[p.id] ?? positions[String(p.id)];
          return {
            ...p,
            gen_queue_position: typeof raw === "number" ? raw : null,
          };
        });
      });
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

  useEffect(() => {
    const onChanged = () => {
      void qc.invalidateQueries({ queryKey: ["projects"] });
      void qc.invalidateQueries({ queryKey: ["sidebar-layout"] });
    };
    window.addEventListener("studio-projects-changed", onChanged);
    return () => window.removeEventListener("studio-projects-changed", onChanged);
  }, [qc]);

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
    projectDisplayName(p).toLowerCase().includes(q) ||
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
              "ring-1 ring-white/30",
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
            <StackWrap on={isFactory}>
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
              onRename={(title) => renameMutation.mutate({ id: p.id, title })}
            />
            </StackWrap>
          </div>
        </div>
        {isFactory && isOpen && children.length > 0 && (
          <div className="ml-3 mt-px space-y-px pl-2">
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
                  onRename={(title) => renameMutation.mutate({ id: c.id, title })}
                />
              ) : null,
            )}
          </div>
        )}
      </div>
    );
  };


  const visibleFolders = folders.filter(
    (f) =>
      !q ||
      f.name.toLowerCase().includes(q) ||
      (projectsByFolder.get(f.id) ?? []).some(matchProject),
  );
  const visibleRootProjects = rootProjects.filter(matchProject);

  return (
    <div
      aria-hidden={collapsed}
      className="relative flex min-h-0 shrink-0 overflow-hidden"
      style={{
        width: collapsed ? 0 : sidebarWidth,
        transition: slideAnimating ? `width ${SLIDE_MS}ms ${SLIDE_EASE}` : undefined,
      }}
    >
    <aside
      style={{
        width: sidebarWidth,
        transform: collapsed ? "translateX(-100%)" : "translateX(0)",
        transition: slideAnimating ? `transform ${SLIDE_MS}ms ${SLIDE_EASE}` : undefined,
      }}
      className="relative flex h-full min-h-0 shrink-0 flex-col border-r border-[#232327] bg-[#111114] font-normal"
    >
      <SidebarResizeHandle
        width={sidebarWidth}
        onWidthChange={setSidebarWidth}
        minWidth={minWidth}
        maxWidth={maxWidth}
      />
      <div className="flex items-center justify-between gap-2 px-3.5 py-3.5">
        <div className="flex min-w-0 items-center gap-2.5">
          <div className="flex h-8 w-8 items-center justify-center rounded-none bg-[#1a1a1e]">
            <FolderOpen className="h-4 w-4 text-zinc-300" strokeWidth={1.75} />
          </div>
          <div className="min-w-0">
            <span className="block text-[13px] font-bold tracking-wider text-zinc-200 uppercase">
              Проекты
            </span>
            {projects.data && (
              <span className="text-[13px] text-zinc-400 font-medium">
                {roots.length} {roots.length === 1 ? "проект" : roots.length > 1 && roots.length < 5 ? "проекта" : "проектов"}
              </span>
            )}
          </div>
        </div>
        <div className="flex items-center gap-1">
          <Button
            type="button"
            size="icon"
            variant="ghost"
            className="h-8 w-8 rounded-none bg-transparent text-zinc-400 hover:bg-[#1a1a1e] hover:text-white"
            title="Новая папка"
            onClick={() => setNewFolderOpen(true)}
          >
            <FolderPlus className="h-4 w-4" strokeWidth={1.75} />
          </Button>
          <Button
            type="button"
            size="icon"
            variant="ghost"
            className="h-8 w-8 rounded-none bg-transparent text-zinc-400 hover:bg-[#1a1a1e] hover:text-white"
            title="Скрыть панель"
            onClick={onToggleCollapsed}
          >
            <PanelLeftClose className="h-4 w-4" strokeWidth={1.75} />
          </Button>
        </div>
      </div>

      {newFolderOpen && (
        <div className="flex items-center gap-1.5 border-b border-white/[0.06] bg-zinc-900/60 px-3 py-2.5">
          <Input
            autoFocus
            placeholder="Название папки…"
            value={newFolderName}
            onChange={(e) => setNewFolderName(e.target.value)}
            className="h-8.5 border-zinc-700 bg-zinc-950/80 text-[13px] font-medium text-zinc-100 placeholder:text-zinc-500"
            onKeyDown={(e) => {
              if (e.key === "Enter" && newFolderName.trim()) {
                createFolderMutation.mutate(newFolderName.trim());
              }
              if (e.key === "Escape") setNewFolderOpen(false);
            }}
          />
          <Button
            size="sm"
            className="h-8.5 shrink-0 px-3 text-[13px] font-semibold bg-emerald-600 text-white hover:bg-emerald-500"
            disabled={!newFolderName.trim() || createFolderMutation.isPending}
            onClick={() => createFolderMutation.mutate(newFolderName.trim())}
          >
            OK
          </Button>
        </div>
      )}

      <div className="px-3 pb-2.5">
        <div className="relative">
          <Search
            className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-zinc-400"
            strokeWidth={1.75}
          />
          <Input
            placeholder="Поиск проектов…"
            value={filter}
            onChange={(e) => setFilter(e.target.value)}
            className="h-9 rounded-none border-0 bg-[#18181c] pl-9.5 text-[13px] font-medium text-zinc-100 placeholder:text-zinc-400 focus-visible:ring-1 focus-visible:ring-zinc-600"
          />
        </div>
      </div>

      <ScrollArea className="min-h-0 flex-1">
        <div
          className="flex flex-col gap-[3px] px-2.5 py-2"
          onDragOver={(e) => e.preventDefault()}
        >
          {projects.isLoading && (
            <div className="flex items-center justify-center py-8">
              <Loader2 className="h-4 w-4 animate-spin text-muted-foreground" />
            </div>
          )}

          {!projects.isLoading && visibleFolders.length === 0 && visibleRootProjects.length === 0 && (
            <div className="px-3 py-10 text-center text-[13px] font-light text-muted-foreground/60">
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
            const coverIds: number[] = [];
            const walk = (list: ProjectSummary[]) => {
              for (const fp of list) {
                coverIds.push(fp.id);
                walk(childrenByParent.get(fp.id) ?? []);
              }
            };
            walk(projectsByFolder.get(folder.id) ?? []);
            return (
              <Fragment key={folder.id}>
              <div
                className={cn(
                  "group mb-0.5 overflow-hidden rounded-none border-0 transition-colors",
                  isActiveFolder
                    ? "bg-[#1a1a1e]"
                    : "bg-[#151518]",
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
                <StackWrap on>
                <div className="relative overflow-hidden border border-[#2a2a30] bg-[#151518]">
                  <FolderCover ids={coverIds.slice(0, 12)} />
                  <div className="absolute inset-0 bg-gradient-to-t from-black/90 via-black/45 to-black/10" />
                <div className="relative flex aspect-[16/9] w-full items-end gap-1 px-2 pb-2 text-white">
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
                      "group flex min-w-0 flex-1 items-center gap-2 rounded-none px-2 py-1.5 text-left transition-colors",
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
                    <span className="break-words text-[14px] font-normal leading-snug tracking-[0.01em]">
                      {folder.name}
                    </span>
                    <span className="ml-auto rounded-full border border-white/[0.08] bg-white/[0.03] px-1.5 py-px text-[13px] font-normal tabular-nums text-muted-foreground/70">
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
                </div>
                </StackWrap>
                {isOpen && (
                  <div className="space-y-0.5 border-t border-white/[0.05] px-1.5 py-1.5">
                    {folderProjects.map((p) => renderProject(p, { draggable: true }))}
                    {folderProjects.length === 0 && (
                      <div className="px-3 py-3 text-center text-[13px] font-light text-muted-foreground/45">
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
    </div>
  );
}

function FolderCover({ ids }: { ids: number[] }) {
  const [idx, setIdx] = useState(0);
  const key = ids.join(",");
  useEffect(() => setIdx(0), [key]);
  if (idx >= ids.length) return null;
  return (
    // eslint-disable-next-line @next/next/no-img-element
    <img
      key={ids[idx]}
      src={`/api/artifacts/project-cover/${ids[idx]}`}
      alt=""
      loading="lazy"
      onError={() => setIdx((i) => i + 1)}
      className="absolute inset-0 h-full w-full object-cover"
    />
  );
}

function StackWrap({ on, children }: { on: boolean; children: React.ReactNode }) {
  if (!on) return <>{children}</>;
  return (
    <div className="relative mt-1.5 ml-1.5">
      <div className="pointer-events-none absolute -top-1.5 -left-1.5 h-full w-full border border-[#2a2a30] bg-[#18181c]" />
      <div className="relative">{children}</div>
    </div>
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
        "shrink-0 rounded-none transition-all duration-150",
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
  onRename,
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
  onRename?: (title: string) => void;
}) {
  const [editing, setEditing] = useState(false);
  const [draftTitle, setDraftTitle] = useState(projectDisplayName(project));
  const displayName = projectDisplayName(project);

  useEffect(() => {
    if (!editing) setDraftTitle(displayName);
  }, [displayName, editing]);

  const commitRename = () => {
    const next = draftTitle.trim();
    setEditing(false);
    if (!next || next === displayName) return;
    onRename?.(next);
  };

  const [coverOk, setCoverOk] = useState(true);
  const coverSrc = `/api/artifacts/project-cover/${project.id}?v=${encodeURIComponent(String(project.updated_at))}`;
  const showCover = !compact && coverOk;

  const titleNode = editing ? (
    <Input
      value={draftTitle}
      onChange={(e) => setDraftTitle(e.target.value)}
      onClick={(e) => e.stopPropagation()}
      onKeyDown={(e) => {
        e.stopPropagation();
        if (e.key === "Enter") {
          e.preventDefault();
          commitRename();
        }
        if (e.key === "Escape") {
          e.preventDefault();
          setDraftTitle(displayName);
          setEditing(false);
        }
      }}
      onBlur={commitRename}
      className="h-7 min-w-0 flex-1 rounded-none border-0 bg-black/60 px-2 py-0 text-[14px]"
      autoFocus
    />
  ) : (
    <span
      className={cn(
        "min-w-0 flex-1 break-words leading-snug tracking-[0.01em]",
        compact ? "line-clamp-2 text-[13px] text-zinc-300" : "line-clamp-2 text-[15px] font-medium text-zinc-100",
        showCover && "text-white [text-shadow:0_1px_2px_rgba(0,0,0,0.9)]",
      )}
    >
      {displayName}
    </span>
  );

  const fmtDate = (v: string | null | undefined) => {
    if (!v) return "—";
    const d = new Date(v);
    if (Number.isNaN(d.getTime())) return "—";
    return d.toLocaleDateString("ru-RU", { day: "2-digit", month: "short" }).replace(".", "");
  };
  const stepChip = (
    <span
      className={cn(
        "inline-flex max-w-full items-center gap-1.5 px-1.5 py-0.5 text-[13px]",
        showCover ? "bg-black/65 text-zinc-200" : "bg-[#1d1d22] text-zinc-300",
      )}
      title="Текущий шаг"
    >
      <span className={cn("h-1.5 w-1.5 shrink-0", statusDot(project.status))} />
      <span className="truncate">{formatProjectStatus(project.status)}</span>
    </span>
  );
  const menu = (
    <div className="absolute top-1.5 right-1.5 z-20" onClick={(e) => e.stopPropagation()}>
      <DropdownMenu>
        <DropdownMenuTrigger asChild>
          <button
            type="button"
            className={cn(
              "flex h-6 items-center gap-1 rounded-none bg-black/65 px-2 text-[13px] text-zinc-200 hover:bg-black/85 hover:text-white",
              "opacity-0 group-hover:opacity-100 data-[state=open]:opacity-100",
              queuePosition && "opacity-100",
            )}
            title="Действия с проектом"
          >
            {queuePosition ? `Очередь ${queuePosition}${queueIdleShort ? ` · ${queueIdleShort}` : ""}` : "Действия"}
            <ChevronDown className="h-3 w-3" />
          </button>
        </DropdownMenuTrigger>
        <DropdownMenuContent align="end" className="min-w-[200px] rounded-none border-[#2a2a30] bg-[#151518] p-0 text-[14px]">
          {onRename ? (
            <DropdownMenuItem
              className="rounded-none px-3 py-2"
              onSelect={() => {
                setDraftTitle(displayName);
                setEditing(true);
              }}
            >
              <Pencil className="mr-2 h-3.5 w-3.5" /> Переименовать
            </DropdownMenuItem>
          ) : null}
          {canCreateChild && onCreateChild ? (
            <DropdownMenuItem className="rounded-none px-3 py-2" disabled={creatingChild} onSelect={() => onCreateChild()}>
              <Plus className="mr-2 h-3.5 w-3.5" /> Дочерний проект
            </DropdownMenuItem>
          ) : null}
          <DropdownMenuItem className="rounded-none px-3 py-2" onSelect={() => onToggleQueue?.()}>
            <ListOrdered className="mr-2 h-3.5 w-3.5" />
            {queuePosition
              ? `Снять из очереди (${queuePosition}${queueIdleDetail ? ` — ${queueIdleDetail}` : ""})`
              : "Добавить в очередь генерации"}
          </DropdownMenuItem>
          <DropdownMenuItem
            className="rounded-none px-3 py-2 text-red-300 focus:text-red-200"
            onSelect={() => {
              if (confirm(`Удалить проект «${displayName}»?`)) onDelete();
            }}
          >
            <Trash2 className="mr-2 h-3.5 w-3.5" /> Удалить
          </DropdownMenuItem>
        </DropdownMenuContent>
      </DropdownMenu>
    </div>
  );

  return (
    <div
      role="button"
      tabIndex={0}
      data-project-id={project.id}
      onClick={onSelect}
      draggable={draggable && !editing}
      onDragStart={
        draggable
          ? (e) => {
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
            }
          : undefined
      }
      onKeyDown={(e) => {
        if (e.key === "Enter" || e.key === " ") {
          e.preventDefault();
          onSelect();
        }
      }}
      className={cn(
        "group relative cursor-pointer rounded-none border text-left transition-colors focus-visible:outline-none",
        selected
          ? "border-white/60 bg-[#1d1d22]"
          : "border-[#2a2a30] bg-[#151518] hover:border-[#3a3a42] hover:bg-[#1a1a1e]",
      )}
    >
      {menu}
      <div
        className={cn(
          "relative w-full overflow-hidden",
          compact ? "min-h-[108px]" : "aspect-[16/9]",
          showCover ? "bg-[#101013]" : "bg-[#151518]",
        )}
      >
        {showCover ? (
          <>
            {/* eslint-disable-next-line @next/next/no-img-element */}
            <img
              src={coverSrc}
              alt=""
              loading="lazy"
              onError={() => setCoverOk(false)}
              className="absolute inset-0 h-full w-full object-cover"
            />
            <div className="absolute inset-0 bg-gradient-to-t from-black/85 via-black/25 to-transparent" />
          </>
        ) : null}
        <div className="absolute top-1.5 left-1.5 z-[1] max-w-[calc(100%-100px)]">{stepChip}</div>
        <div className="absolute inset-x-0 bottom-0 z-[1] flex items-end justify-between gap-2 px-2.5 pb-2.5">
          <div className="min-w-0 flex-1">
            <div className="flex items-end gap-1">{titleNode}</div>
            {badge ? <div className="mt-0.5 truncate text-[13px] text-zinc-400">{badge}</div> : null}
          </div>
          <div className="mb-0.5 grid shrink-0 grid-cols-[auto_auto] gap-x-1.5 text-right text-[13px] leading-[1.4] tabular-nums [text-shadow:0_1px_2px_rgba(0,0,0,0.9)]">
            <span className="text-zinc-400">Создан</span>
            <span className="text-zinc-200">{fmtDate(project.created_at)}</span>
            <span className="text-zinc-400">Использован</span>
            <span className="text-zinc-200" title={formatRelativeTime(project.updated_at)}>
              {fmtDate(project.updated_at)}
            </span>
          </div>
        </div>
      </div>
    </div>
  );
}

function statusDot(s: ProjectStatus): string {
  if (s === "paused" || s === "failed") return "bg-amber-400";
  if (s === "published" || s === "assembled") return "bg-emerald-400";
  if (s === "new") return "bg-zinc-600";
  if (s.endsWith("_ready")) return "bg-zinc-300";
  return "bg-sky-400";
}
