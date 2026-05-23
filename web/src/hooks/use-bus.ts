"use client";

import { useEffect, useRef } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { subscribeWS } from "@/lib/api";
import type { BusEvent } from "@/lib/types";

/**
 * Подписка на канал глобальных событий. Инвалидирует TanStack-кэши.
 */
export function useGlobalEvents() {
  const qc = useQueryClient();
  useEffect(() => {
    const unsubscribe = subscribeWS("global", (raw) => {
      const evt = raw as BusEvent;
      if (!evt || typeof (evt as { type?: unknown }).type !== "string") return;
      const type = (evt as { type: string }).type;
      if (type === "project_created" || type === "project_deleted" || type === "project_updated") {
        qc.invalidateQueries({ queryKey: ["projects"] });
      }
      if (type === "node_status_changed" || type === "run_created" || type === "run_cancelled") {
        qc.invalidateQueries({ queryKey: ["runs"] });
        if ((evt as { project_id?: number }).project_id != null) {
          qc.invalidateQueries({
            queryKey: ["project-run", (evt as { project_id: number }).project_id],
          });
        }
      }
      if (type === "hitl_pending" || type === "hitl_decided") {
        qc.invalidateQueries({ queryKey: ["hitl"] });
      }
    });
    return unsubscribe;
  }, [qc]);
}

/**
 * Подписка на конкретный run-канал.
 */
export function useRunEvents(runId: number | null, handler: (e: BusEvent) => void) {
  const handlerRef = useRef(handler);
  handlerRef.current = handler;
  useEffect(() => {
    if (runId == null) return;
    return subscribeWS(`runs.${runId}`, (raw) => handlerRef.current(raw as BusEvent));
  }, [runId]);
}
