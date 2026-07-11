"use client";

import { useEffect, useState } from "react";
import { toast } from "sonner";
import { AppShell } from "@/components/shell/app-shell";
import { ProjectSidebar } from "@/components/sidebar/project-sidebar";
import { Inspector } from "@/components/inspector/inspector";
import { StudioWorkspace } from "@/components/studio/studio-workspace";
import { FleetPanelSheet } from "@/components/fleet/fleet-panel-sheet";
import { FleetTransferBanner } from "@/components/fleet/fleet-transfer-banner";
import { ProjectMaterialsSheet } from "@/components/project/project-materials-sheet";
import { useGlobalEvents } from "@/hooks/use-bus";
import { useFleetTransfer, FLEET_TRANSFER_PUSH_START, optimisticPushTransfer } from "@/hooks/use-fleet-transfer";
import { api } from "@/lib/api";
import { fleetPushToHub } from "@/lib/fleet-api";

export default function HomePage() {
  const [selectedProjectId, setSelectedProjectId] = useState<number | null>(null);
  const [selectedNodeKey, setSelectedNodeKey] = useState<string | null>(null);
  const [sidebarCollapsed, setSidebarCollapsed] = useState(false);
  const [studioOpen, setStudioOpen] = useState(false);
  const [fleetOpen, setFleetOpen] = useState(false);
  const [materialsOpen, setMaterialsOpen] = useState(false);
  const { transfer, dismiss } = useFleetTransfer(selectedProjectId);

  useGlobalEvents();

  useEffect(() => {
    const openSidebar = () => setSidebarCollapsed(false);
    window.addEventListener("studio-open-projects-sidebar", openSidebar);
    return () => window.removeEventListener("studio-open-projects-sidebar", openSidebar);
  }, []);

  useEffect(() => {
    const openFleet = () => setFleetOpen(true);
    window.addEventListener("studio-open-fleet", openFleet);
    return () => window.removeEventListener("studio-open-fleet", openFleet);
  }, []);

  useEffect(() => {
    const openMaterials = () => {
      if (selectedProjectId == null) {
        toast.error("Выбери проект слева");
        return;
      }
      setMaterialsOpen(true);
    };
    window.addEventListener("studio-open-materials", openMaterials);
    return () => window.removeEventListener("studio-open-materials", openMaterials);
  }, [selectedProjectId]);

  useEffect(() => {
    const openFrames = () => {
      if (selectedProjectId == null) {
        toast.error("Выбери проект слева");
        return;
      }
      window.dispatchEvent(
        new CustomEvent("studio-open-frames", { detail: { projectId: selectedProjectId } }),
      );
    };
    window.addEventListener("studio-open-frames-topbar", openFrames);
    return () => window.removeEventListener("studio-open-frames-topbar", openFrames);
  }, [selectedProjectId]);

  const onSelectNode = (key: string | null) => {
    setSelectedNodeKey(key);
  };

  return (
    <AppShell>
      <div className="flex h-[calc(100vh-48px)] min-h-0">
        <ProjectSidebar
          selectedProjectId={selectedProjectId}
          onSelect={(id) => {
            setSelectedProjectId(id);
            setSelectedNodeKey(null);
            setStudioOpen(false);
          }}
          collapsed={sidebarCollapsed}
          onToggleCollapsed={() => setSidebarCollapsed((c) => !c)}
        />
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
        <ProjectMaterialsSheet
          projectId={selectedProjectId}
          open={materialsOpen}
          onOpenChange={setMaterialsOpen}
        />
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
    </AppShell>
  );
}
