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
    <div
      className={cn(
        "pointer-events-none absolute bottom-2 left-2 z-[120] rounded-md border px-2 py-0.5 font-mono text-[10px] shadow-sm backdrop-blur",
        stale
          ? "border-amber-500/50 bg-amber-500/10 text-amber-300"
          : "border-border/60 bg-background/85 text-muted-foreground",
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
    </div>
  );
}
