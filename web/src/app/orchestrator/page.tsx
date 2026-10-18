"use client";

import { AppShell } from "@/components/shell/app-shell";
import { OrchestratorWorkspace } from "@/components/orchestrator/orchestrator-workspace";

export default function OrchestratorPage() {
  return (
    <AppShell>
      <OrchestratorWorkspace />
    </AppShell>
  );
}
