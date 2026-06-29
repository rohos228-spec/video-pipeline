"use client";

import { Sparkles } from "lucide-react";
import { Topbar } from "./topbar";

import type { AppTab } from "@/lib/app-tabs";

export function AppShell({
  children,
  activeTab,
  onTabChange,
}: {
  children: React.ReactNode;
  activeTab?: AppTab;
  onTabChange?: (tab: AppTab) => void;
}) {
  return (
    <div className="flex h-screen w-screen flex-col overflow-hidden bg-background text-foreground">
      <Topbar activeTab={activeTab} onTabChange={onTabChange}>{children}</Topbar>
    </div>
  );
}

export { Sparkles };
