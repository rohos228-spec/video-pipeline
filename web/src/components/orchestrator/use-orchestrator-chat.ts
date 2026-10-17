"use client";

import { useCallback, useEffect, useMemo, useReducer, useRef, useState } from "react";
import {
  consumeOrchStream,
  isAbortError,
  orchTaskModeEnabled,
  OrchApiError,
  postOrchApprove,
  postOrchKill,
  postOrchMessage,
  readOrchProjectId,
  postOrchReject,
  probeOrchAuth,
  readOrchBoot,
  readOrchToken,
  sleepWithSignal,
  writeOrchToken,
  type OrchClientConfig,
} from "@/lib/orchestratorApi";
import {
  initialOrchScreen,
  reduceOrchScreen,
  type OrchScreenState,
} from "@/lib/orchestratorSession";

export type ConnPhase = "idle" | "live" | "reconnecting" | "restored";

function approvalPostAccepted(
  result: { ok?: boolean; matched?: boolean; approval_id?: string } | undefined,
  approvalId: string,
): boolean {
  if (!result || result.ok === false || result.matched === false) return false;
  if (typeof result.approval_id === "string" && result.approval_id !== approvalId) return false;
  return true;
}

export function useOrchestratorChat() {
  const boot = useMemo(() => readOrchBoot(), []);
  const [token, setToken] = useState("");
  const [ready, setReady] = useState(false);
  const [authError, setAuthError] = useState("");
  const [blocked, setBlocked] = useState(false);
  const [conn, setConn] = useState<ConnPhase>("idle");
  const [sendError, setSendError] = useState("");
  const [pending, setPending] = useState(false);
  const [decisionPendingId, setDecisionPendingId] = useState<string | null>(null);
  const [decisionError, setDecisionError] = useState<{ id: string; message: string } | null>(null);
  const [ackMs, setAckMs] = useState<number | null>(null);
  const [state, dispatch] = useReducer(reduceOrchScreen, undefined, initialOrchScreen);
  const lastSeq = useRef(0);
  const seen = useRef(new Set<number>());
  const sendStartedAt = useRef<number | null>(null);
  const decisionLock = useRef(false);

  useEffect(() => {
    setToken(readOrchToken());
    setReady(true);
  }, []);

  const cfg = useMemo<OrchClientConfig>(
    () => ({
      apiBase: boot.apiBase,
      session: boot.session,
      token,
      scenario: boot.scenario,
    }),
    [boot.apiBase, boot.scenario, boot.session, token],
  );

  useEffect(() => {
    if (!ready || !token || blocked) return;
    const ac = new AbortController();
    let stopped = false;
    let attempt = 0;

    const loop = async () => {
      while (!stopped) {
        try {
          await consumeOrchStream(cfg, {
            signal: ac.signal,
            lastEventId: lastSeq.current,
            onOpen: () => {
              if (stopped) return;
              setConn(attempt > 0 ? "restored" : "live");
            },
            onEvent: (event) => {
              if (stopped) return;
              if (seen.current.has(event.seq)) return;
              seen.current.add(event.seq);
              if (event.seq > lastSeq.current) lastSeq.current = event.seq;
              if (event.type === "ack" && sendStartedAt.current != null) {
                setAckMs(performance.now() - sendStartedAt.current);
              }
              dispatch({ type: "event", event });
            },
          });
          if (stopped || ac.signal.aborted) return;
          attempt += 1;
          setConn("reconnecting");
          await sleepWithSignal(Math.min(300 * attempt, 2000), ac.signal);
        } catch (error) {
          if (stopped || ac.signal.aborted || isAbortError(error)) return;
          if (error instanceof OrchApiError && error.status === 401) {
            setAuthError(error.message);
            setBlocked(true);
            setConn("idle");
            return;
          }
          attempt += 1;
          setConn("reconnecting");
          try {
            await sleepWithSignal(Math.min(300 * attempt, 2000), ac.signal);
          } catch {
            return;
          }
        }
      }
    };

    void loop();
    return () => {
      stopped = true;
      ac.abort();
    };
  }, [blocked, cfg, ready, token]);

  const noteAuth = useCallback((error: unknown): boolean => {
    if (error instanceof OrchApiError && error.status === 401) {
      setAuthError(error.message);
      setBlocked(true);
      return true;
    }
    return false;
  }, []);

  const submitToken = useCallback(
    async (value: string) => {
      const next = value.trim();
      if (!next) return;
      try {
        await probeOrchAuth({ ...cfg, token: next });
      } catch (error) {
        if (error instanceof OrchApiError && error.status === 401) {
          setAuthError(error.message);
        } else if (!noteAuth(error)) {
          setAuthError(error instanceof Error ? error.message : "токен не проверен");
        }
        return;
      }
      writeOrchToken(next);
      lastSeq.current = 0;
      seen.current = new Set();
      sendStartedAt.current = null;
      setAckMs(null);
      dispatch({ type: "reset" });
      setAuthError("");
      setSendError("");
      setDecisionError(null);
      setDecisionPendingId(null);
      setConn("idle");
      setBlocked(false);
      setToken(next);
    },
    [cfg, noteAuth],
  );

  const sendMessage = useCallback(
    async (text: string, mode?: "task") => {
      const trimmed = text.trim();
      if (!trimmed || !token || blocked) return;
      dispatch({
        type: "user-message",
        id: `user-${Date.now()}-${state.messages.length}`,
        text: trimmed,
        taskId: null,
      });
      sendStartedAt.current = performance.now();
      setAckMs(null);
      setPending(true);
      const asTask = orchTaskModeEnabled() && mode === "task";
      const projectId = readOrchProjectId();
      const body = {
        text: trimmed,
        ...(asTask ? { mode: "task" as const } : {}),
        ...(projectId != null ? { project_id: projectId } : {}),
      };
      try {
        await postOrchMessage(cfg, body);
        setSendError("");
      } catch (error) {
        if (!noteAuth(error)) {
          setSendError(error instanceof Error ? error.message : "не отправилось");
        }
      } finally {
        setPending(false);
      }
    },
    [blocked, cfg, noteAuth, state.messages.length, token],
  );

  const decide = useCallback(
    async (approvalId: string, taskId: string | null, decision: "approved" | "rejected") => {
      if (decisionLock.current) return;
      const card = state.approvals.find((item) => item.id === approvalId);
      if (!card?.serverId) {
        setDecisionError({ id: approvalId, message: "нет approval_id от сервера" });
        return;
      }
      decisionLock.current = true;
      setDecisionPendingId(approvalId);
      setDecisionError(null);
      try {
        const body = { approval_id: approvalId, task_id: taskId };
        const result =
          decision === "approved"
            ? await postOrchApprove(cfg, body)
            : await postOrchReject(cfg, { ...body, reason: "отклонено в чате" });
        if (!approvalPostAccepted(result, approvalId)) {
          setDecisionError({ id: approvalId, message: "сервер не подтвердил это решение" });
          return;
        }
        dispatch({ type: "approval-resolved", id: approvalId, decision });
        setSendError("");
      } catch (error) {
        if (!noteAuth(error)) {
          setDecisionError({
            id: approvalId,
            message: error instanceof Error ? error.message : "не записалось решение",
          });
        }
      } finally {
        decisionLock.current = false;
        setDecisionPendingId(null);
      }
    },
    [cfg, noteAuth, state.approvals],
  );

  const dismissBlock = useCallback((approvalId: string) => {
    dispatch({ type: "approval-resolved", id: approvalId, decision: "closed" });
  }, []);

  const kill = useCallback(async () => {
    try {
      const result = await postOrchKill(cfg, {
        session_id: boot.session,
        reason: "kill switch из чата",
      });
      dispatch({
        type: "kill-notice",
        detail: result.detail || "Платный шаг остановится на границе.",
        paidBoundary: true,
      });
      setSendError("");
    } catch (error) {
      if (!noteAuth(error)) {
        setSendError(error instanceof Error ? error.message : "kill switch не прошёл");
      }
    }
  }, [boot.session, cfg, noteAuth]);

  return {
    boot,
    ready,
    token,
    authError,
    needsToken: !ready || !token || blocked,
    conn,
    sendError,
    pending,
    decisionPendingId,
    decisionError,
    ackMs,
    state: state as OrchScreenState,
    submitToken,
    sendMessage,
    decide,
    dismissBlock,
    kill,
  };
}
