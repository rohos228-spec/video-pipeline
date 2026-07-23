"use client";

import { useCallback, useEffect, useState } from "react";
import { api, subscribeWS } from "@/lib/api";
import type { ProjectStatus } from "@/lib/types";

/** Монтаж идёт: шаг assemble в пайплайне или фоновый remount с доски. */
export function useMontageBusy(
  projectId: number | null,
  projectStatus: ProjectStatus | string | undefined | null,
): boolean {
  const assembling = projectStatus === "assembling";
  const [jobRunning, setJobRunning] = useState(false);

  const syncFromStatus = useCallback(async () => {
    if (projectId == null) {
      setJobRunning(false);
      return;
    }
    try {
      const st = await api.getMontageBoardStatus(projectId);
      setJobRunning(st.job?.status === "running");
    } catch {
      // Сеть — не сбрасываем running.
    }
  }, [projectId]);

  useEffect(() => {
    if (projectId == null) {
      setJobRunning(false);
      return;
    }
    void syncFromStatus();
  }, [projectId, syncFromStatus]);

  useEffect(() => {
    if (projectId == null) return;
    return subscribeWS(`projects.${projectId}`, (raw) => {
      const evt = raw as {
        payload?: {
          stopped?: boolean;
          montage_board_montage?: boolean;
          status?: string;
        };
      };
      if (evt.payload?.stopped) {
        setJobRunning(false);
        return;
      }
      if (!evt.payload?.montage_board_montage) return;
      const status = evt.payload.status;
      if (status === "running") {
        setJobRunning(true);
      } else if (status === "done" || status === "error" || status === "cancelled") {
        setJobRunning(false);
      }
    });
  }, [projectId]);

  useEffect(() => {
    if (projectId == null || (!jobRunning && !assembling)) return;
    const id = window.setInterval(() => void syncFromStatus(), 2500);
    return () => window.clearInterval(id);
  }, [projectId, jobRunning, assembling, syncFromStatus]);

  return assembling || jobRunning;
}
