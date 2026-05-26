"use client";

import { useEffect, useState } from "react";
import { CLIENT_STUDIO_VERSION } from "@/lib/studio-version";
import { cn } from "@/lib/utils";

type ServerVersion = {
  build: number;
  sha: string;
  label: string;
  ui_baked_build?: number;
  ui_stale?: boolean;
  attach_expected?: string;
  backend_attach?: string;
  backend_ok?: boolean;
  orchestrator_expected?: string;
  backend_orchestrator?: string;
  orchestrator_ok?: boolean;
  pipeline_ok?: boolean;
};

export function StudioVersionBadge() {
  const [server, setServer] = useState<ServerVersion | null>(null);

  useEffect(() => {
    let cancelled = false;
    fetch("/api/studio-version", { cache: "no-store" })
      .then((r) => (r.ok ? r.json() : null))
      .then((data: ServerVersion | null) => {
        if (!cancelled && data?.label) setServer(data);
      })
      .catch(() => undefined);
    return () => {
      cancelled = true;
    };
  }, []);

  const displayLabel = server?.label ?? CLIENT_STUDIO_VERSION;
  const uiStale =
    server != null &&
    (server.ui_stale === true || server.label !== CLIENT_STUDIO_VERSION);
  const backendStale =
    server != null && (server.pipeline_ok === false || server.backend_ok === false);

  return (
    <span
      className={cn(
        "inline-flex items-center rounded border px-1.5 py-px font-mono text-[10px] leading-none tracking-normal normal-case",
        uiStale || backendStale
          ? "border-amber-500/50 bg-amber-500/10 text-amber-300"
          : "border-border/60 bg-muted/40 text-muted-foreground",
      )}
      title={
        uiStale
          ? `Старый UI в кэше (${CLIENT_STUDIO_VERSION}). Сервер: ${server?.label}. Ctrl+F5 или FIX-VERSION.cmd`
          : server && server.pipeline_ok === false
            ? `Python устарел: attach=${server.backend_attach}`
            : `Studio ${displayLabel}`
      }
    >
      {displayLabel}
      {uiStale ? " !" : null}
    </span>
  );
}
