"use client";

import { useEffect, useState } from "react";
import { toast } from "sonner";
import { AppShell } from "@/components/shell/app-shell";
import { ProjectSidebar } from "@/components/sidebar/project-sidebar";
import { Inspector } from "@/components/inspector/inspector";
import { StudioWorkspace } from "@/components/studio/studio-workspace";
import { FleetPanelSheet } from "@/components/fleet/fleet-panel-sheet";
import { FleetTransferBanner } from "@/components/fleet/fleet-transfer-banner";
import { OutseeCreateWorkspace } from "@/components/outsee/outsee-create-workspace";
import { GptWorkspace } from "@/components/gpt/gpt-workspace";
import { BazaWorkspace } from "@/components/baza/baza-workspace";
import { OrchestratorPanel } from "@/components/orchestrator/orchestrator-panel";
import { useGlobalEvents } from "@/hooks/use-bus";
import { useFleetTransfer, FLEET_TRANSFER_PUSH_START, optimisticPushTransfer } from "@/hooks/use-fleet-transfer";
import { usePersistedState } from "@/hooks/use-persisted-state";
import { NARROW_QUERY, useMediaQuery } from "@/hooks/use-media-query";
import { FolderOpen, Info } from "lucide-react";
import { cn } from "@/lib/utils";
import { api } from "@/lib/api";
import { fleetPushToHub } from "@/lib/fleet-api";

export default function HomePage() {
  const [selectedProjectId, setSelectedProjectId] = usePersistedState<number | null>(
    "vp-studio-selected-project-id",
    null,
  );
  const [selectedNodeKey, setSelectedNodeKey] = useState<string | null>(null);
  const [sidebarCollapsed, setSidebarCollapsed] = usePersistedState(
    "vp-studio-sidebar-collapsed",
    false,
  );
  const [studioOpen, setStudioOpen] = useState(false);
  const [fleetOpen, setFleetOpen] = useState(false);
  const [outseeOpen, setOutseeOpen] = useState(false);
  const [gptOpen, setGptOpen] = useState(false);
  const [bazaOpen, setBazaOpen] = useState(false);
  const { transfer, dismiss } = useFleetTransfer(selectedProjectId);
  const narrow = useMediaQuery(NARROW_QUERY);
  const [projectsDrawer, setProjectsDrawer] = useState(false);
  const [inspectorDrawer, setInspectorDrawer] = useState(false);
  const closeDrawers = () => {
    setProjectsDrawer(false);
    setInspectorDrawer(false);
  };

  useGlobalEvents();

  useEffect(() => {
    const openSidebar = () => {
      setSidebarCollapsed(false);
      setProjectsDrawer(true);
      setInspectorDrawer(false);
    };
    window.addEventListener("studio-open-projects-sidebar", openSidebar);
    return () => window.removeEventListener("studio-open-projects-sidebar", openSidebar);
  }, []);

  useEffect(() => {
    const openFleet = () => setFleetOpen(true);
    window.addEventListener("studio-open-fleet", openFleet);
    return () => window.removeEventListener("studio-open-fleet", openFleet);
  }, []);

  useEffect(() => {
    const openOutsee = (ev: Event) => {
      const detail = (ev as CustomEvent<{ projectId?: number | null }>).detail;
      if (detail?.projectId != null) setSelectedProjectId(detail.projectId);
      setOutseeOpen(true);
    };
    window.addEventListener("studio-open-outsee", openOutsee);
    return () => window.removeEventListener("studio-open-outsee", openOutsee);
  }, [setSelectedProjectId]);

  useEffect(() => {
    const openGpt = () => setGptOpen(true);
    window.addEventListener("studio-open-gpt", openGpt);
    return () => window.removeEventListener("studio-open-gpt", openGpt);
  }, []);

  useEffect(() => {
    const openBaza = () => setBazaOpen(true);
    window.addEventListener("studio-open-baza", openBaza);
    return () => window.removeEventListener("studio-open-baza", openBaza);
  }, []);

  // Оркестратор создал проект → выделяем его в пайплайне.
  useEffect(() => {
    const onSelectProject = (ev: Event) => {
      const detail = (ev as CustomEvent<{ projectId?: number | null }>).detail;
      if (detail?.projectId != null) {
        setSelectedProjectId(detail.projectId);
        setSelectedNodeKey(null);
        setStudioOpen(false);
      }
    };
    window.addEventListener("studio-select-project", onSelectProject);
    return () => window.removeEventListener("studio-select-project", onSelectProject);
  }, [setSelectedProjectId]);

  const onSelectNode = (key: string | null) => {
    setSelectedNodeKey(key);
  };

  return (
    <AppShell>
      <div className="flex h-[calc(100vh-48px)] min-h-0 flex-col">
        <div
          data-testid="mobile-panels-bar"
          className="flex h-10 shrink-0 items-center gap-2 border-b border-white/[0.06] bg-black/30 px-2 md:hidden"
        >
          <button
            type="button"
            data-testid="open-projects-drawer"
            aria-expanded={projectsDrawer}
            onClick={() => { setProjectsDrawer((v) => !v); setInspectorDrawer(false); }}
            className="flex h-8 items-center gap-1.5 rounded-md border border-white/10 px-2.5 text-xs text-white/80"
          >
            <FolderOpen className="h-4 w-4" />
            Проекты
          </button>
          <button
            type="button"
            data-testid="open-inspector-drawer"
            aria-expanded={inspectorDrawer}
            onClick={() => { setInspectorDrawer((v) => !v); setProjectsDrawer(false); }}
            className="flex h-8 items-center gap-1.5 rounded-md border border-white/10 px-2.5 text-xs text-white/80"
          >
            <Info className="h-4 w-4" />
            Инспектор
          </button>
          <span className="min-w-0 flex-1 truncate text-right text-[11px] text-white/45">
            {selectedProjectId == null ? "проект не выбран" : `проект #${selectedProjectId}`}
          </span>
        </div>
        {narrow && (projectsDrawer || inspectorDrawer) && (
          <button
            type="button"
            aria-label="Закрыть боковую панель"
            className="fixed inset-x-0 bottom-0 top-12 z-[45] bg-black/60"
            onClick={closeDrawers}
          />
        )}
        <div className="flex min-h-0 flex-1">
        <div
          className={cn(
            "md:contents",
            "max-md:fixed max-md:bottom-0 max-md:left-0 max-md:top-12 max-md:z-50 max-md:w-[min(85vw,320px)] max-md:[&>aside]:w-full! max-md:[&>aside]:max-w-full",
            projectsDrawer ? "max-md:flex" : "max-md:hidden",
          )}
        >
        <ProjectSidebar
          selectedProjectId={selectedProjectId}
          onSelect={(id) => {
            setSelectedProjectId(id);
            setSelectedNodeKey(null);
            setStudioOpen(false);
            closeDrawers();
          }}
          collapsed={narrow ? false : sidebarCollapsed}
          onToggleCollapsed={() => (narrow ? closeDrawers() : setSidebarCollapsed((c) => !c))}
        />
        </div>
        <main className="relative min-w-0 flex-1 overflow-hidden">
          <StudioWorkspace
            projectId={selectedProjectId}
            selectedNodeKey={selectedNodeKey}
            onSelectNode={onSelectNode}
            studioOpen={studioOpen}
            onStudioOpenChange={setStudioOpen}
          />
          <FleetTransferBanner
            transfer={transfer}
            onPushToHub={
              (transfer?.project_id ?? selectedProjectId) != null
                ? async () => {
                    const pid = transfer?.project_id ?? selectedProjectId!;
                    window.dispatchEvent(
                      new CustomEvent(FLEET_TRANSFER_PUSH_START, {
                        detail: optimisticPushTransfer(pid, transfer?.slug),
                      }),
                    );
                    const res = await fleetPushToHub(pid);
                    if ("started" in res && res.started) {
                      toast.message("Отправка идёт — смотри полоску внизу");
                      return;
                    }
                    toast.success(
                      res.size_mb
                        ? `Отправлено на главный ПК (${res.size_mb} MB)`
                        : "Отправлено на главный ПК",
                    );
                  }
                : undefined
            }
            onCancelTransfer={
              (transfer?.project_id ?? selectedProjectId) != null
                ? async () => {
                    await api.stopProject(transfer?.project_id ?? selectedProjectId!);
                  }
                : undefined
            }
            onDismiss={dismiss}
          />
          <OrchestratorPanel projectId={selectedProjectId} />
        </main>
        <FleetPanelSheet
          open={fleetOpen}
          onOpenChange={setFleetOpen}
          onOpenProject={(projectId) => {
            setSelectedProjectId(projectId);
            setSelectedNodeKey(null);
            setStudioOpen(false);
          }}
        />
        <div
          className={cn(
            "md:contents",
            "max-md:fixed max-md:bottom-0 max-md:right-0 max-md:top-12 max-md:z-50 max-md:w-[min(85vw,320px)] max-md:[&>aside]:w-full! max-md:[&>aside]:max-w-full",
            inspectorDrawer ? "max-md:flex" : "max-md:hidden",
          )}
        >
        <Inspector
          projectId={selectedProjectId}
          selectedNodeKey={selectedNodeKey}
          onOpenNodeStudio={() => {
            if (selectedNodeKey) {
              window.dispatchEvent(
                new CustomEvent("studio-open-node-prompts", {
                  detail: { nodeKey: selectedNodeKey },
                }),
              );
            } else {
              setStudioOpen(true);
            }
          }}
        />
        </div>
        </div>
      </div>
      <OutseeCreateWorkspace
        open={outseeOpen}
        onOpenChange={setOutseeOpen}
        projectId={selectedProjectId}
      />
      <GptWorkspace
        open={gptOpen}
        onOpenChange={setGptOpen}
      />
      <BazaWorkspace
        open={bazaOpen}
        onOpenChange={setBazaOpen}
        projectId={selectedProjectId}
      />
    </AppShell>
  );
}
