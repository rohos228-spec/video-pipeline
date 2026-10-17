"use client";

import { useCallback, useEffect, useMemo, useReducer, useRef, useState } from "react";
import {
  consumeOrchStream,
  isAbortError,
  OrchApiError,
  postOrchApprove,
  postOrchKill,
  postOrchMessage,
  postOrchReject,
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
  const [state, dispatch] = useReducer(reduceOrchScreen, undefined, initialOrchScreen);
  const lastSeq = useRef(0);
  const seen = useRef(new Set<number>());

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

  const submitToken = useCallback((value: string) => {
    const next = value.trim();
    if (!next) return;
    writeOrchToken(next);
    lastSeq.current = 0;
    seen.current = new Set();
    dispatch({ type: "reset" });
    setAuthError("");
    setSendError("");
    setConn("idle");
    setBlocked(false);
    setToken(next);
  }, []);

  const sendMessage = useCallback(
    async (text: string) => {
      const trimmed = text.trim();
      if (!trimmed || !token || blocked) return;
      const taskId = state.tasks.at(-1)?.id ?? null;
      dispatch({
        type: "user-message",
        id: `user-${Date.now()}-${state.messages.length}`,
        text: trimmed,
        taskId: null,
      });
      setPending(true);
      try {
        await postOrchMessage(cfg, { text: trimmed, task_id: taskId });
        setSendError("");
      } catch (error) {
        if (!noteAuth(error)) {
          setSendError(error instanceof Error ? error.message : "не отправилось");
        }
      } finally {
        setPending(false);
      }
    },
    [blocked, cfg, noteAuth, state.messages.length, state.tasks, token],
  );

  const decide = useCallback(
    async (approvalId: string, taskId: string | null, decision: "approved" | "rejected") => {
      dispatch({ type: "approval-resolved", id: approvalId, decision });
      try {
        const body = { approval_id: approvalId, task_id: taskId };
        if (decision === "approved") await postOrchApprove(cfg, body);
        else await postOrchReject(cfg, { ...body, reason: "отклонено в чате" });
        setSendError("");
      } catch (error) {
        if (!noteAuth(error)) {
          setSendError(error instanceof Error ? error.message : "не записалось решение");
        }
      }
    },
    [cfg, noteAuth],
  );

  const kill = useCallback(async () => {
    try {
      const result = await postOrchKill(cfg, {
        session_id: boot.session,
        reason: "kill switch из чата",
      });
      dispatch({
        type: "kill-notice",
        detail: result.detail || "Платный шаг остановится на границе.",
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
    state: state as OrchScreenState,
    submitToken,
    sendMessage,
    decide,
    kill,
  };
}
