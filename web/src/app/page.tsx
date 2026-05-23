"use client";

import { useState } from "react";
import { AppShell } from "@/components/shell/app-shell";
import { ProjectSidebar } from "@/components/sidebar/project-sidebar";
import { FlowCanvas } from "@/components/canvas/flow-canvas";
import { Inspector } from "@/components/inspector/inspector";
import { NodeStudio } from "@/components/studio/node-studio";
import { useGlobalEvents } from "@/hooks/use-bus";

export default function HomePage() {
  const [selectedProjectId, setSelectedProjectId] = useState<number | null>(null);
  const [selectedNodeKey, setSelectedNodeKey] = useState<string | null>(null);
  const [sidebarCollapsed, setSidebarCollapsed] = useState(false);
  const [studioOpen, setStudioOpen] = useState(false);

  useGlobalEvents();

  const onSelectNode = (key: string | null) => {
    setSelectedNodeKey(key);
    if (key) setStudioOpen(true);
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
          <FlowCanvas
            projectId={selectedProjectId}
            selectedNodeKey={selectedNodeKey}
            onSelectNode={onSelectNode}
          />
        </main>
        <Inspector
          projectId={selectedProjectId}
          selectedNodeKey={selectedNodeKey}
          onOpenNodeStudio={() => setStudioOpen(true)}
        />
      </div>
      <NodeStudio
        open={studioOpen && selectedNodeKey != null}
        onOpenChange={setStudioOpen}
        projectId={selectedProjectId}
        nodeKey={selectedNodeKey}
      />
    </AppShell>
  );
}
