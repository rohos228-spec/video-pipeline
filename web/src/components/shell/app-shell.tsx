"use client";

import { Sparkles } from "lucide-react";
import { Topbar } from "./topbar";

export function AppShell({ children }: { children: React.ReactNode }) {
  return (
    <div className="flex h-screen w-screen flex-col overflow-hidden bg-background text-foreground">
      <Topbar />
      {children}
    </div>
  );
}

export { Sparkles };
