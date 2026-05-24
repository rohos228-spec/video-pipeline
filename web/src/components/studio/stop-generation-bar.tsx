"use client";

import { useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { Loader2, Square } from "lucide-react";
import { toast } from "sonner";
import { api } from "@/lib/api";
import { cn } from "@/lib/utils";

/**
 * Большая кнопка ⏹ как отдельная строка в Telegram (`⏹ Остановить текущий шаг`).
 * Вызывает тот же API, что бот: request_stop + откат running-статуса.
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
  const qc = useQueryClient();

  if (!visible) return null;

  const handleStop = async () => {
    setBusy(true);
    try {
      const r = await api.stopProject(projectId);
      toast.success(r.message || "⏹ Шаг остановлен");
      await Promise.all([
        qc.invalidateQueries({ queryKey: ["project", projectId] }),
        qc.invalidateQueries({ queryKey: ["project-run", projectId] }),
        qc.invalidateQueries({ queryKey: ["runs"] }),
        qc.invalidateQueries({ queryKey: ["hitl", projectId] }),
      ]);
    } catch (e) {
      toast.error(String(e));
    } finally {
      setBusy(false);
    }
  };

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
        onClick={() => void handleStop()}
        title="Как ⏹ в Telegram: прервать текущий шаг и цикл генерации"
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
        ⏹ Остановить текущий шаг
      </button>
      {hint ? (
        <p className="mt-2 text-center text-[11px] text-muted-foreground">{hint}</p>
      ) : null}
    </div>
  );
}
