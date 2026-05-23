"use client";

import { useState } from "react";
import { AppShell } from "@/components/shell/app-shell";
import { ProjectSidebar } from "@/components/sidebar/project-sidebar";
import { FlowCanvas } from "@/components/canvas/flow-canvas";
import { Inspector } from "@/components/inspector/inspector";
import { useGlobalEvents } from "@/hooks/use-bus";

export default function HomePage() {
  const [selectedProjectId, setSelectedProjectId] = useState<number | null>(null);
  const [selectedNodeKey, setSelectedNodeKey] = useState<string | null>(null);

  // Глобальная подписка на события — нужна для invalidation TanStack-кэшей.
  useGlobalEvents();

  return (
    <AppShell>
      <div className="flex h-[calc(100vh-48px)]">
        <ProjectSidebar
          selectedProjectId={selectedProjectId}
          onSelect={(id) => {
            setSelectedProjectId(id);
            setSelectedNodeKey(null);
          }}
        />
        <main className="relative flex-1 overflow-hidden">
          <FlowCanvas
            projectId={selectedProjectId}
            selectedNodeKey={selectedNodeKey}
            onSelectNode={setSelectedNodeKey}
          />
        </main>
        <Inspector
          projectId={selectedProjectId}
          selectedNodeKey={selectedNodeKey}
        />
      </div>
    </AppShell>
  );
}
