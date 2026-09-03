"use client";

import { useState, createContext, useContext } from "react";
import { Activity, Network, Film, Bot, Database } from "lucide-react";
import { Button } from "@/components/ui/button";
import { LogPanel } from "@/components/logs/log-panel";
import { FramesGrid } from "@/components/frames/frames-grid";
import { StudioVersionBadge } from "@/components/shell/studio-version-badge";
import { TextLlmPicker } from "@/components/shell/text-llm-picker";
import { BugReportButton } from "@/components/shell/bug-report-button";

interface UiState {
  framesProjectId: number | null;
  openFrames: (projectId: number) => void;
  openOutsee: (projectId?: number | null) => void;
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

  const openOutsee = (projectId?: number | null) => {
    window.dispatchEvent(
      new CustomEvent("studio-open-outsee", {
        detail: { projectId: projectId ?? null },
      }),
    );
  };

  return (
    <UiContext.Provider value={{ framesProjectId, openFrames, openOutsee }}>
      <div className="flex h-full min-h-0 w-full flex-1 flex-col">
      <header className="flex h-12 shrink-0 items-center justify-between border-b border-white/[0.06] bg-black/40 px-4 backdrop-blur-xl">
        <div className="flex items-center gap-2.5">
          <img src="/icon.svg" alt="Studio" className="h-6 w-6 shrink-0 rounded-md shadow-sm" />
          <div className="flex flex-col gap-0.5 leading-tight">
            <span className="text-sm font-semibold tracking-tight text-white">Видео студия</span>
            <div className="flex items-center gap-2">
              <span className="text-[10px] uppercase tracking-[0.18em] text-zinc-400 font-medium">
                автономный режим
              </span>
              <StudioVersionBadge />
            </div>
          </div>
        </div>
        <div className="flex items-center gap-1.5">
          <TextLlmPicker />
          <Button
            variant="outline"
            size="sm"
            onClick={() => openOutsee()}
            className="gap-2 text-xs font-semibold border-emerald-500/40 bg-emerald-950/40 text-emerald-300 hover:bg-emerald-900/50 hover:text-emerald-200 hover:border-emerald-400/60 shadow-sm shadow-emerald-950/50"
            title="Полный интерфейс генерации outsee"
          >
            <Film className="h-3.5 w-3.5 text-emerald-400" />
            Генерация
          </Button>
          <Button
            variant="outline"
            size="sm"
            onClick={() => window.dispatchEvent(new CustomEvent("studio-open-gpt"))}
            className="gap-2 text-xs font-semibold"
            title="Свободный чат с активной текстовой моделью (GPT или Kimi)"
          >
            <Bot className="h-3.5 w-3.5" />
            Чат
          </Button>
          <Button
            variant="outline"
            size="sm"
            onClick={() => window.dispatchEvent(new CustomEvent("studio-open-baza"))}
            className="gap-2 text-xs"
            title="Визуализация базы данных: карточки кадров, связи, версии промтов"
          >
            <Database className="h-3.5 w-3.5" />
            База
          </Button>
          <Button
            variant="outline"
            size="sm"
            onClick={() => window.dispatchEvent(new CustomEvent("studio-open-fleet"))}
            className="gap-2 text-xs"
            title="Станции Tailscale и очередь монтажа"
          >
            <Network className="h-3.5 w-3.5" />
            Сеть
          </Button>
          <Button
            variant="outline"
            size="sm"
            onClick={() => setLogsOpen(true)}
            className="gap-2 text-xs"
          >
            <Activity className="h-3.5 w-3.5" />
            Логи
          </Button>
          <BugReportButton />
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
