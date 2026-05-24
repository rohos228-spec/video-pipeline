"use client";

import { useEffect, useState } from "react";
import { CLIENT_STUDIO_VERSION } from "@/lib/studio-version";
import { cn } from "@/lib/utils";

type ServerVersion = { build: number; sha: string; label: string };

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

  const stale = server != null && server.label !== CLIENT_STUDIO_VERSION;

  return (
    <span
      className={cn(
        "inline-flex items-center rounded border px-1.5 py-px font-mono text-[10px] leading-none tracking-normal normal-case",
        stale
          ? "border-amber-500/50 bg-amber-500/10 text-amber-300"
          : "border-border/60 bg-muted/40 text-muted-foreground",
      )}
      title={
        stale
          ? `UI устарел: в браузере ${CLIENT_STUDIO_VERSION}, на сервере ${server?.label}. Выполните npm run build в web/ и перезапустите Studio.`
          : `Сборка UI: ${CLIENT_STUDIO_VERSION}`
      }
    >
      {stale ? (
        <>
          {CLIENT_STUDIO_VERSION} → {server?.label}
        </>
      ) : (
        CLIENT_STUDIO_VERSION
      )}
    </span>
  );
}
