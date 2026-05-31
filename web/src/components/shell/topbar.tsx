"use client";

import { useState, createContext, useContext } from "react";
import { Sparkles, Activity } from "lucide-react";
import { Button } from "@/components/ui/button";
import { LogPanel } from "@/components/logs/log-panel";
import { FramesGrid } from "@/components/frames/frames-grid";
import { StudioVersionBadge } from "@/components/shell/studio-version-badge";

interface UiState {
  framesProjectId: number | null;
  openFrames: (projectId: number) => void;
}

const UiContext = createContext<UiState | null>(null);

export function useUi(): UiState {
  const ctx = useContext(UiContext);
  if (!ctx) throw new Error("useUi must be used within AppShell / Topbar");
  return ctx;
}

export function Topbar({ children }: { children?: React.ReactNode }) {
  const [logsOpen, setLogsOpen] = useState(false);
  const [framesOpen, setFramesOpen] = useState(false);
  const [framesProjectId, setFramesProjectId] = useState<number | null>(null);

  const openFrames = (id: number) => {
    setFramesProjectId(id);
    setFramesOpen(true);
  };

  return (
    <UiContext.Provider value={{ framesProjectId, openFrames }}>
      <div className="flex h-full min-h-0 w-full flex-1 flex-col">
      <header className="flex h-12 shrink-0 items-center justify-between border-b border-border bg-card/30 px-4 backdrop-blur-sm">
        <div className="flex items-center gap-2.5">
          <div className="flex h-7 w-7 items-center justify-center rounded-md bg-primary/15 text-primary">
            <Sparkles className="h-4 w-4" />
          </div>
          <div className="flex flex-col gap-0.5 leading-tight">
            <span className="text-sm font-semibold tracking-tight">Видео студия</span>
            <div className="flex items-center gap-2">
              <span className="text-[10px] uppercase tracking-[0.18em] text-muted-foreground">
                автономный режим
              </span>
              <StudioVersionBadge />
            </div>
          </div>
        </div>
        <div className="flex items-center gap-2">
          <Button
            variant="ghost"
            size="sm"
            onClick={() =>
              window.dispatchEvent(
                new CustomEvent("studio-open-node-prompts", { detail: {} }),
              )
            }
            className="gap-2 text-xs"
            title="Промты выбранной ноды на канвасе"
          >
            <Sparkles className="h-3.5 w-3.5" />
            Промты
          </Button>
          <Button
            variant="ghost"
            size="sm"
            onClick={() => setLogsOpen(true)}
            className="gap-2 text-xs"
          >
            <Activity className="h-3.5 w-3.5" />
            Логи
          </Button>
          <Button variant="ghost" size="sm" className="gap-2 text-xs" asChild>
            <a href="/api/docs" target="_blank" rel="noreferrer">
              API
            </a>
          </Button>
        </div>
      </header>
      <LogPanel open={logsOpen} onOpenChange={setLogsOpen} />
      <FramesGrid
        projectId={framesProjectId}
        open={framesOpen}
        onOpenChange={setFramesOpen}
      />
      {children != null ? (
        <div className="flex min-h-0 flex-1 flex-col overflow-hidden">{children}</div>
      ) : null}
      </div>
    </UiContext.Provider>
  );
}
