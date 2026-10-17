"use client";

import { AppShell } from "@/components/shell/app-shell";
import { ReportEditor } from "@/components/orchestrator/report-editor";

export default function OrchestratorReportsPage() {
  return (
    <AppShell>
      <ReportEditor />
    </AppShell>
  );
}
