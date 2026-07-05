"use client";

import { useEffect, useState } from "react";
import { AppShell } from "@/components/shell/app-shell";
import { ProjectSidebar } from "@/components/sidebar/project-sidebar";
import { Inspector } from "@/components/inspector/inspector";
import { StudioWorkspace } from "@/components/studio/studio-workspace";
import { useGlobalEvents } from "@/hooks/use-bus";

export default function HomePage() {
  const [selectedProjectId, setSelectedProjectId] = useState<number | null>(null);
  const [selectedNodeKey, setSelectedNodeKey] = useState<string | null>(null);
  const [sidebarCollapsed, setSidebarCollapsed] = useState(false);
  const [studioOpen, setStudioOpen] = useState(false);

  useGlobalEvents();

  useEffect(() => {
    const openSidebar = () => setSidebarCollapsed(false);
    window.addEventListener("studio-open-projects-sidebar", openSidebar);
    return () => window.removeEventListener("studio-open-projects-sidebar", openSidebar);
  }, []);

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
    </AppShell>
  );
}
