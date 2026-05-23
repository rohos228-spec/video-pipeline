"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { Pause, Play, Trash2 } from "lucide-react";
import { subscribeWS } from "@/lib/api";
import type { BusEvent } from "@/lib/types";
import { Button } from "@/components/ui/button";
import {
  Sheet,
  SheetContent,
  SheetDescription,
  SheetHeader,
  SheetTitle,
} from "@/components/ui/sheet";
import { cn } from "@/lib/utils";

const MAX_LINES = 500;

type LogLevel = "info" | "success" | "warning" | "error" | "debug";

interface LogLine {
  id: string;
  ts: Date;
  level: LogLevel;
  text: string;
}

function formatBusEvent(raw: BusEvent): { level: LogLevel; text: string } | null {
  if (!raw || typeof raw !== "object" || !("type" in raw)) return null;
  const type = String((raw as { type: string }).type);
  if (type === "subscribed") return null;

  if (type === "log") {
    const e = raw as { level?: string; line?: string; run_id?: number };
    const lvl = (e.level ?? "info").toLowerCase();
    const level: LogLevel =
      lvl === "error" || lvl === "err"
        ? "error"
        : lvl === "warn" || lvl === "warning"
          ? "warning"
          : lvl === "debug"
            ? "debug"
            : lvl === "success"
              ? "success"
              : "info";
    const prefix = e.run_id != null ? `[run ${e.run_id}] ` : "";
    return { level, text: `${prefix}${e.line ?? ""}` };
  }

  if (type === "node_status_changed") {
    const e = raw as {
      node_key?: string;
      node_type?: string;
      from?: string;
      to?: string;
      project_id?: number;
      run_id?: number;
    };
    const level: LogLevel =
      e.to === "failed" ? "error" : e.to === "done" ? "success" : "info";
    const proj = e.project_id != null ? ` project #${e.project_id}` : "";
    const run = e.run_id != null ? ` run #${e.run_id}` : "";
    return {
      level,
      text: `Нода ${e.node_type ?? e.node_key ?? "?"}:${proj}${run} → ${e.from ?? "?"} → ${e.to ?? "?"}`,
    };
  }

  if (type === "run_created") {
    const e = raw as { run_id?: number; project_id?: number };
    return {
      level: "success",
      text: `Запуск создан: run #${e.run_id ?? "?"}, project #${e.project_id ?? "?"}`,
    };
  }

  if (type === "run_cancelled") {
    const e = raw as { run_id?: number };
    return { level: "warning", text: `Запуск отменён: run #${e.run_id ?? "?"}` };
  }

  if (type === "project_created") {
    const e = raw as { project_id?: number; topic?: string; slug?: string };
    return {
      level: "success",
      text: `Проект создан #${e.project_id ?? "?"}: ${e.topic ?? e.slug ?? ""}`,
    };
  }

  if (type === "project_updated") {
    const e = raw as { project_id?: number };
    return { level: "info", text: `Проект обновлён #${e.project_id ?? "?"}` };
  }

  if (type === "project_deleted") {
    const e = raw as { project_id?: number };
    return { level: "warning", text: `Проект удалён #${e.project_id ?? "?"}` };
  }

  if (type === "hitl_pending") {
    const e = raw as { project_id?: number; hitl_id?: number; kind?: string };
    return {
      level: "warning",
      text: `HITL ожидает: project #${e.project_id ?? "?"}, ${e.kind ?? "approve"} (#${e.hitl_id ?? "?"})`,
    };
  }

  if (type === "hitl_decided") {
    const e = raw as { project_id?: number; decision?: string; kind?: string };
    return {
      level: "success",
      text: `HITL решение «${e.decision ?? "?"}»: project #${e.project_id ?? "?"}, ${e.kind ?? ""}`,
    };
  }

  return { level: "debug", text: JSON.stringify(raw) };
}

const levelClass: Record<LogLevel, string> = {
  info: "text-[hsl(var(--info))]",
  success: "text-[hsl(var(--success))]",
  warning: "text-[hsl(var(--warning))]",
  error: "text-[hsl(var(--destructive))]",
  debug: "text-muted-foreground",
};

export function LogPanel({
  open,
  onOpenChange,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
}) {
  const [lines, setLines] = useState<LogLine[]>([]);
  const [paused, setPaused] = useState(false);
  const [connected, setConnected] = useState(false);
  const scrollRef = useRef<HTMLDivElement>(null);
  const pausedRef = useRef(paused);
  const pendingRef = useRef<LogLine[]>([]);
  const seqRef = useRef(0);

  pausedRef.current = paused;

  const pushLine = useCallback((level: LogLevel, text: string) => {
    const line: LogLine = {
      id: `${Date.now()}-${++seqRef.current}`,
      ts: new Date(),
      level,
      text,
    };
    if (pausedRef.current) {
      pendingRef.current = [...pendingRef.current, line].slice(-MAX_LINES);
      return;
    }
    setLines((prev) => [...prev, line].slice(-MAX_LINES));
  }, []);

  useEffect(() => {
    if (!open) return;
    setConnected(false);
    const unsub = subscribeWS(
      "global",
      (raw) => {
        setConnected(true);
        const formatted = formatBusEvent(raw as BusEvent);
        if (formatted) pushLine(formatted.level, formatted.text);
      },
      () => setConnected(false),
    );
    pushLine("info", "Подписка на канал global…");
    return unsub;
  }, [open, pushLine]);

  useEffect(() => {
    if (paused || !scrollRef.current) return;
    scrollRef.current.scrollTop = scrollRef.current.scrollHeight;
  }, [lines, paused]);

  const handleClear = () => {
    pendingRef.current = [];
    setLines([]);
    pushLine("info", "Лог очищен");
  };

  const handleResume = () => {
    setPaused(false);
    if (pendingRef.current.length) {
      setLines((prev) => [...prev, ...pendingRef.current].slice(-MAX_LINES));
      pendingRef.current = [];
    }
  };

  return (
    <Sheet open={open} onOpenChange={onOpenChange}>
      <SheetContent side="bottom" className="flex flex-col gap-0 p-0">
        <SheetHeader className="shrink-0 px-5 py-3">
          <div className="flex items-center justify-between gap-4 pr-8">
            <div>
              <SheetTitle>Логи пайплайна</SheetTitle>
              <SheetDescription>
                События в реальном времени (WebSocket /ws/global).{" "}
                <span
                  className={cn(
                    "font-medium",
                    connected ? "text-[hsl(var(--success))]" : "text-muted-foreground",
                  )}
                >
                  {connected ? "подключено" : "ожидание…"}
                </span>
              </SheetDescription>
            </div>
            <div className="flex items-center gap-2">
              <Button
                variant="ghost"
                size="sm"
                className="gap-1.5 text-xs"
                onClick={() => (paused ? handleResume() : setPaused(true))}
              >
                {paused ? (
                  <>
                    <Play className="h-3.5 w-3.5" />
                    Продолжить
                    {pendingRef.current.length > 0 && (
                      <span className="text-warning">({pendingRef.current.length})</span>
                    )}
                  </>
                ) : (
                  <>
                    <Pause className="h-3.5 w-3.5" />
                    Пауза
                  </>
                )}
              </Button>
              <Button
                variant="ghost"
                size="sm"
                className="gap-1.5 text-xs"
                onClick={handleClear}
              >
                <Trash2 className="h-3.5 w-3.5" />
                Очистить
              </Button>
            </div>
          </div>
        </SheetHeader>

        <div
          ref={scrollRef}
          className="min-h-0 flex-1 overflow-auto border-t border-border bg-background/50 px-4 py-2 font-mono text-[11px] leading-relaxed"
        >
          {lines.length === 0 ? (
            <p className="py-8 text-center text-muted-foreground">
              Событий пока нет. Создай проект или запусти Run.
            </p>
          ) : (
            lines.map((line) => (
              <div key={line.id} className="flex gap-2 py-0.5">
                <span className="shrink-0 tabular-nums text-muted-foreground">
                  {line.ts.toLocaleTimeString("ru-RU", {
                    hour: "2-digit",
                    minute: "2-digit",
                    second: "2-digit",
                  })}
                </span>
                <span className={cn("shrink-0 uppercase w-14", levelClass[line.level])}>
                  {line.level}
                </span>
                <span className="break-all text-foreground/90">{line.text}</span>
              </div>
            ))
          )}
        </div>
      </SheetContent>
    </Sheet>
  );
}
