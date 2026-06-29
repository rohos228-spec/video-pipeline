"use client";

import { useEffect, useState } from "react";
import { AppShell } from "@/components/shell/app-shell";
import { ProjectSidebar } from "@/components/sidebar/project-sidebar";
import { Inspector } from "@/components/inspector/inspector";
import { StudioWorkspace } from "@/components/studio/studio-workspace";
import { FleetPanel } from "@/components/fleet/fleet-panel";
import { ElevenLabsLabPanel } from "@/components/elevenlabs/elevenlabs-lab-panel";
import { useGlobalEvents } from "@/hooks/use-bus";
import type { AppTab } from "@/lib/app-tabs";

export default function HomePage() {
  const [selectedProjectId, setSelectedProjectId] = useState<number | null>(null);
  const [selectedNodeKey, setSelectedNodeKey] = useState<string | null>(null);
  const [sidebarCollapsed, setSidebarCollapsed] = useState(false);
  const [studioOpen, setStudioOpen] = useState(false);
  const [activeTab, setActiveTab] = useState<AppTab>("studio");

  useGlobalEvents();

  useEffect(() => {
    const handler = (event: Event) => {
      const detail = (event as CustomEvent<{ projectId?: number }>).detail;
      if (detail?.projectId != null) {
        setActiveTab("studio");
        setSelectedProjectId(detail.projectId);
        setSelectedNodeKey(null);
        setStudioOpen(false);
      }
    };
    window.addEventListener("fleet-open-project", handler);
    return () => window.removeEventListener("fleet-open-project", handler);
  }, []);

  const onSelectNode = (key: string | null) => {
    setSelectedNodeKey(key);
  };

  return (
    <AppShell activeTab={activeTab} onTabChange={setActiveTab}>
      {activeTab === "fleet" ? (
        <div className="min-h-0 flex-1 overflow-hidden">
          <FleetPanel
            onOpenProject={(projectId) => {
              setActiveTab("studio");
              setSelectedProjectId(projectId);
              setSelectedNodeKey(null);
              setStudioOpen(false);
            }}
          />
        </div>
      ) : activeTab === "elevenlabs" ? (
        <div className="min-h-0 flex-1 overflow-hidden">
          <ElevenLabsLabPanel />
        </div>
      ) : (
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
          </main>
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
      )}
    </AppShell>
  );
}
