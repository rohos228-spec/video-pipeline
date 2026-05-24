"use client";

import { useCallback, useEffect, useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { Loader2, Square } from "lucide-react";
import { toast } from "sonner";
import { api } from "@/lib/api";
import { cn } from "@/lib/utils";

const STOP_RETRY_MS = 800;
const STOP_RETRY_MAX = 12;

/**
 * ⏹ → POST /api/projects/{id}/stop (task.cancel + откат статуса).
 * Повторяет запрос, пока бэкенд сообщает generation_still_active.
 */
export function StopGenerationBar({
  projectId,
  visible,
  hint,
  className,
}: {
  projectId: number;
  visible: boolean;
  hint?: string;
  className?: string;
}) {
  const [busy, setBusy] = useState(false);
  const [stopping, setStopping] = useState(false);
  const qc = useQueryClient();

  const runStop = useCallback(async () => {
    let lastMsg = "";
    for (let i = 0; i < STOP_RETRY_MAX; i += 1) {
      const r = await api.stopProject(projectId);
      lastMsg = r.message;
      await Promise.all([
        qc.invalidateQueries({ queryKey: ["project", projectId] }),
        qc.invalidateQueries({ queryKey: ["project-run", projectId] }),
        qc.invalidateQueries({ queryKey: ["runs"] }),
        qc.invalidateQueries({ queryKey: ["hitl", projectId] }),
      ]);
      if (!r.generation_still_active) {
        return { ok: true, message: lastMsg };
      }
      await new Promise((resolve) => setTimeout(resolve, STOP_RETRY_MS));
    }
    return { ok: false, message: lastMsg };
  }, [projectId, qc]);

  useEffect(() => {
    if (!stopping) return;
    let cancelled = false;
    void (async () => {
      try {
        const r = await runStop();
        if (cancelled) return;
        if (r.ok) {
          toast.success(r.message || "⏹ Шаг остановлен");
        } else {
          toast.warning(
            "Stop отправлен, но цикл ещё жив. Перезапустите бэкенд: python -m app.main",
          );
        }
      } catch (e) {
        if (!cancelled) toast.error(String(e));
      } finally {
        if (!cancelled) {
          setBusy(false);
          setStopping(false);
        }
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [stopping, runStop]);

  if (!visible && !stopping) return null;

  return (
    <motionlessBar
      busy={busy}
      className={className}
      hint={hint}
      onStop={() => {
        if (busy || stopping) return;
        setBusy(true);
        setStopping(true);
      }}
      stopping={stopping}
    />
  );
}

function motionlessBar({
  busy,
  className,
  hint,
  onStop,
  stopping,
}: {
  busy: boolean;
  className?: string;
  hint?: string;
  onStop: () => void;
  stopping: boolean;
}) {
  return (
    <div
      className={cn(
        "pointer-events-auto fixed bottom-6 left-1/2 z-[120] w-[min(480px,calc(100vw-1.5rem))] -translate-x-1/2",
        className,
      )}
    >
      <button
        type="button"
        disabled={busy}
        onClick={onStop}
        title="Прервать asyncio-task воркера (как ⏹ в Telegram)"
        className={cn(
          "flex w-full items-center justify-center gap-3 rounded-2xl border-2 border-red-400/90",
          "bg-gradient-to-r from-red-600 via-red-600 to-red-700 px-6 py-4",
          "text-lg font-bold tracking-tight text-white shadow-2xl shadow-red-950/50",
          "transition hover:brightness-110 active:scale-[0.99] disabled:opacity-70",
        )}
      >
        {busy ? (
          <Loader2 className="h-7 w-7 shrink-0 animate-spin" aria-hidden />
        ) : (
          <Square className="h-7 w-7 shrink-0 fill-current" aria-hidden />
        )}
        {stopping ? "⏹ Прерываем цикл…" : "⏹ Остановить текущий шаг"}
      </button>
      {hint ? (
        <p className="mt-2 text-center text-[11px] text-muted-foreground">{hint}</p>
      ) : null}
    </div>
  );
}
