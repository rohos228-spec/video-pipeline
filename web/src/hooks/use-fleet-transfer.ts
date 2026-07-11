"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { subscribeWS } from "@/lib/api";
import { getAuthToken } from "@/lib/fleet-api";
import type { FleetTransferState } from "@/lib/types";

export const FLEET_TRANSFER_PUSH_START = "fleet-transfer-push-start";

function fleetAuthHeaders(): Record<string, string> {
  const token = getAuthToken();
  return token ? { Authorization: `Bearer ${token}` } : {};
}

function isFleetTransferEvent(raw: unknown): raw is FleetTransferState & { type: string } {
  return (
    typeof raw === "object" &&
    raw !== null &&
    (raw as { type?: string }).type === "fleet_transfer" &&
    typeof (raw as { project_id?: unknown }).project_id === "number"
  );
}

function pickDisplayTransfer(
  active: FleetTransferState | null,
  projectId: number | null,
  dismissed: boolean,
): FleetTransferState | null {
  if (dismissed || !active) return null;
  if (active.phase === "cancelled") return null;
  if (active.status === "active") return active;
  if (projectId != null && active.project_id === projectId) {
    return active.status === "error" && RUNNING_PHASES.has(active.phase)
      ? active
      : active.status === "done"
        ? active
        : null;
  }
  return active.status === "error" ? active : null;
}

const RUNNING_PHASES = new Set(["packing", "upload", "download", "receive", "send"]);

export function isFleetTransferRunning(t: FleetTransferState | null | undefined): boolean {
  if (!t || t.status !== "active") return false;
  return RUNNING_PHASES.has(t.phase) || t.phase === "waiting";
}

export function optimisticPushTransfer(projectId: number, slug?: string): FleetTransferState {
  return {
    project_id: projectId,
    slug,
    job: "handoff",
    phase: "packing",
    direction: "to_hub",
    percent: 0,
    sent_mb: 0,
    total_mb: 0,
    message: "Запуск отправки…",
    status: "active",
  };
}

/** Активная передача bundle (push/pull) — WebSocket + polling fallback. */
export function useFleetTransfer(projectId: number | null) {
  const [active, setActive] = useState<FleetTransferState | null>(null);
  const [dismissed, setDismissed] = useState(false);
  const doneTimer = useRef<ReturnType<typeof setTimeout> | null>(null);

  useEffect(() => {
    setDismissed(false);
    setActive((cur) => {
      if (cur?.status === "active" && RUNNING_PHASES.has(cur.phase)) return cur;
      return null;
    });
  }, [projectId]);

  const dismiss = useCallback(() => {
    setDismissed(true);
    if (doneTimer.current) clearTimeout(doneTimer.current);
  }, []);

  const applyEvent = useCallback((evt: FleetTransferState) => {
    if (evt.phase === "cancelled") {
      setActive(null);
      return;
    }
    if (evt.status === "active") {
      setDismissed(false);
    }
    setActive(evt);
    if (doneTimer.current) clearTimeout(doneTimer.current);
    if (evt.status === "done" || evt.status === "error") {
      doneTimer.current = setTimeout(() => {
        setActive((cur) =>
          cur?.project_id === evt.project_id && cur?.phase === evt.phase ? null : cur,
        );
      }, 8000);
    }
  }, []);

  const handleWs = useCallback(
    (raw: unknown) => {
      if (!isFleetTransferEvent(raw)) return;
      const { type: _t, ...rest } = raw;
      applyEvent(rest as FleetTransferState);
    },
    [applyEvent],
  );

  useEffect(() => {
    const unsubs = [subscribeWS("global", handleWs)];
    if (projectId != null) {
      unsubs.push(subscribeWS(`projects.${projectId}`, handleWs));
    }
    return () => {
      for (const u of unsubs) u();
      if (doneTimer.current) clearTimeout(doneTimer.current);
    };
  }, [projectId, handleWs]);

  useEffect(() => {
    const onPushStart = (ev: Event) => {
      const detail = (ev as CustomEvent<FleetTransferState>).detail;
      if (detail?.project_id) applyEvent(detail);
    };
    window.addEventListener(FLEET_TRANSFER_PUSH_START, onPushStart);
    return () => window.removeEventListener(FLEET_TRANSFER_PUSH_START, onPushStart);
  }, [applyEvent]);

  useEffect(() => {
    let cancelled = false;
    const poll = async () => {
      try {
        const res = await fetch("/api/fleet/transfers/active", {
          headers: fleetAuthHeaders(),
        });
        if (!res.ok || cancelled) return;
        const data = (await res.json()) as { transfers?: FleetTransferState[] };
        const list = data.transfers ?? [];
        const pick =
          projectId == null
            ? list[0]
            : list.find((t) => t.project_id === projectId) ?? list[0];
        if (pick) applyEvent(pick);
      } catch {
        /* ignore */
      }
    };
    void poll();
    const ms = active?.status === "active" ? 1000 : 3000;
    const id = setInterval(poll, ms);
    return () => {
      cancelled = true;
      clearInterval(id);
    };
  }, [applyEvent, projectId, active?.status]);

  const display = pickDisplayTransfer(active, projectId, dismissed);
  return {
    transfer: display,
    isActive: display?.status === "active",
    dismiss,
    applyEvent,
  };
}
