"use client";

import { useEffect, useState } from "react";
import { CLIENT_STUDIO_VERSION } from "@/lib/studio-version";
import { cn } from "@/lib/utils";

type ServerVersion = {
  build: number;
  sha: string;
  label: string;
  backend_git?: string;
  ui_baked_build?: number;
  ui_stale?: boolean;
  attach_expected?: string;
  backend_attach?: string;
  backend_ok?: boolean;
  orchestrator_expected?: string;
  backend_orchestrator?: string;
  orchestrator_ok?: boolean;
  pipeline_ok?: boolean;
  text_llm_provider?: string;
  text_llm_label?: string;
  text_llm_model?: string;
  text_llm_enabled?: boolean;
};

export function StudioVersionBadge() {
  const [server, setServer] = useState<ServerVersion | null>(null);

  useEffect(() => {
    let cancelled = false;
    const load = () => {
      fetch("/api/studio-version", { cache: "no-store" })
        .then((r) => (r.ok ? r.json() : null))
        .then((data: ServerVersion | null) => {
          if (!cancelled && data?.label) setServer(data);
        })
        .catch(() => undefined);
    };
    load();
    // Бейдж — индикатор «UI/бэкенд устарели»: опрашиваем периодически и на
    // возврат фокуса, иначе открытая вкладка держит старый sha после рестарта.
    const onFocus = () => load();
    window.addEventListener("focus", onFocus);
    const timer = window.setInterval(load, 30_000);
    return () => {
      cancelled = true;
      window.removeEventListener("focus", onFocus);
      window.clearInterval(timer);
    };
  }, []);

  const backendGit = (server?.backend_git || "").slice(0, 7);
  // Показываем git бэкенда — это то, что реально крутит генерацию.
  // UI sha в label путает: «не обновляется», хотя Python уже новый.
  const displayLabel = backendGit
    ? `v${server?.build ?? "?"} · ${backendGit}`
    : (server?.label ?? CLIENT_STUDIO_VERSION);
  const uiStale = server != null && server.ui_stale === true;
  const backendStale =
    server != null && (server.pipeline_ok === false || server.backend_ok === false);
  const llmLabel = server?.text_llm_label?.trim() || "";
  const isKimi = (server?.text_llm_provider || "") === "tokenrouter";

  return (
    <span className="inline-flex items-center gap-1.5">
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
              : `backend git=${backendGit || "?"} · UI ${server?.label ?? CLIENT_STUDIO_VERSION}`
        }
      >
        {displayLabel}
        {uiStale ? " !" : null}
      </span>
      {llmLabel ? (
        <span
          className={cn(
            "inline-flex max-w-[220px] truncate items-center rounded border px-1.5 py-px text-[10px] leading-none tracking-normal normal-case",
            isKimi
              ? "border-emerald-500/40 bg-emerald-500/10 text-emerald-200"
              : "border-border/60 bg-muted/40 text-muted-foreground",
            server?.text_llm_enabled === false && "opacity-50",
          )}
          title={
            server?.text_llm_enabled === false
              ? `${llmLabel} — ключ не задан (TOKENROUTER_API_KEY / GPT_API_KEY)`
              : `${llmLabel} · model=${server?.text_llm_model || "?"}`
          }
        >
          {isKimi ? "Kimi K3" : "GPT"}
        </span>
      ) : null}
    </span>
  );
}
