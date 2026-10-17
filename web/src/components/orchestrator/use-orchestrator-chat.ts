"use client";

import { useCallback, useEffect, useMemo, useReducer, useRef, useState } from "react";
import { loadActionRegistry, resolveKillHonesty } from "@/lib/orchestratorActionMetadata";
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

    const boot = async () => {
      let actions: Awaited<ReturnType<typeof loadActionRegistry>> = null;
      try {
        actions = await loadActionRegistry(cfg);
      } catch (error) {
        if (stopped || ac.signal.aborted) return;
        if (error instanceof OrchApiError && error.status === 401) {
          setAuthError(error.message);
          setBlocked(true);
          setConn("idle");
          return;
        }
        actions = null;
      }
      if (stopped) return;
      dispatch({
        type: "registry",
        registry: actions ? { status: "ready", actions } : { status: "missing" },
      });
      await loop();
    };

    void boot();
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
      decisionLock.current = true;
      setDecisionPendingId(approvalId);
      setDecisionError(null);
      try {
        const body = { approval_id: approvalId, task_id: taskId };
        if (decision === "approved") await postOrchApprove(cfg, body);
        else await postOrchReject(cfg, { ...body, reason: "отклонено в чате" });
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
    [cfg, noteAuth],
  );

  const dismissBlock = useCallback((approvalId: string) => {
    dispatch({ type: "approval-resolved", id: approvalId, decision: "closed" });
  }, []);

  const kill = useCallback(async () => {
    try {
      const [result, actions] = await Promise.all([
        postOrchKill(cfg, {
          session_id: boot.session,
          reason: "kill switch из чата",
        }),
        loadActionRegistry(cfg).catch((error: unknown) => {
          if (error instanceof OrchApiError && error.status === 401) throw error;
          return null;
        }),
      ]);
      const steps = state.tasks.flatMap((task) => task.steps);
      const honesty = resolveKillHonesty(
        steps,
        actions ? { status: "ready", actions } : { status: "missing" },
      );
      dispatch({
        type: "kill-notice",
        detail: result.detail || "Платный шаг остановится на границе.",
        paidBoundary: honesty.paidBoundary,
        destructive: honesty.destructive,
      });
      setSendError("");
    } catch (error) {
      if (!noteAuth(error)) {
        setSendError(error instanceof Error ? error.message : "kill switch не прошёл");
      }
    }
  }, [boot.session, cfg, noteAuth, state.tasks]);

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
