"use client";

import { useState } from "react";
import { Sparkles, Activity, KeyRound } from "lucide-react";
import { Button } from "@/components/ui/button";
import { LogPanel } from "@/components/logs/log-panel";

export function Topbar() {
  const [logsOpen, setLogsOpen] = useState(false);
  return (
    <>
      <header className="flex h-12 shrink-0 items-center justify-between border-b border-border bg-card/30 px-4 backdrop-blur-sm">
        <div className="flex items-center gap-2.5">
          <div className="flex h-7 w-7 items-center justify-center rounded-md bg-primary/15 text-primary">
            <Sparkles className="h-4 w-4" />
          </div>
          <div className="flex flex-col leading-tight">
            <span className="text-sm font-semibold tracking-tight">video-pipeline</span>
            <span className="text-[10px] uppercase tracking-[0.18em] text-muted-foreground">
              node studio
            </span>
          </div>
        </div>
        <div className="flex items-center gap-2">
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
              <KeyRound className="h-3.5 w-3.5" />
              API
            </a>
          </Button>
        </div>
      </header>
      <LogPanel open={logsOpen} onOpenChange={setLogsOpen} />
    </>
  );
}
