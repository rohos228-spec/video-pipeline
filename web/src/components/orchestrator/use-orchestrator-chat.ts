"use client";

import { useCallback, useEffect, useMemo, useReducer, useRef, useState } from "react";
import {
  consumeOrchStream,
  isAbortError,
  orchContractIsR34,
  orchKillMessageBody,
  orchKillUsesMessageField,
  orchTaskModeEnabled,
  parseOrchProjectId,
  parseOrchTaskId,
  bindTaskId,
  projectBindWireBody,
  postOrchProject,
  PROJECT_BIND_CONFLICT,
  PROJECT_BIND_FOREIGN,
  isForeignTaskBindError,
  PROJECT_BIND_INVALID,
  PROJECT_BIND_TASK_INVALID,
  PROJECT_BIND_VALIDATION,
  OrchApiError,
  postOrchDecision,
  postOrchKill,
  postOrchMessage,
  postPaidOutcomeDecision,
  postPaidOutcomeRestartGrant,
  readOrchProjectId,
  probeOrchAuth,
  readOrchBoot,
  readOrchToken,
  sleepWithSignal,
  writeOrchToken,
  type ApprovalHttpResult,
  type OrchClientConfig,
} from "@/lib/orchestratorApi";
import {
  PAID_OUTCOME_DECISION,
  paidOutcomeDecisionBody,
  paidOutcomeGrantBody,
  paidOutcomeGrantNotice,
  type PaidOutcomeDecision,
} from "@/lib/orchestratorPaidOutcome";
import {
  initialOrchScreen,
  reduceOrchScreen,
  type OrchScreenState,
} from "@/lib/orchestratorSession";
import type { PaidOutcomeDecisionResult } from "@/lib/orchestratorApi";

export type ConnPhase = "idle" | "live" | "reconnecting" | "restored";

/** Сколько ждём подтверждение kill. Дольше — ошибка и «Повторить». */
const KILL_CONFIRM_MS = 12_000;

function approvalPostAccepted(result: ApprovalHttpResult, approvalId: string): boolean {
  if (result.status === 204) return true;
  if (result.status < 200 || result.status >= 300) return false;
  const body = result.body;
  if (!body) return false;
  if (body.ok === false || body.matched === false) return false;
  if (typeof body.approval_id === "string" && body.approval_id !== approvalId) return false;
  return true;
}

function paidPostAccepted(result: PaidOutcomeDecisionResult, changeId: string): boolean {
  if (!result.ok) return false;
  if (result.changeId != null && result.changeId !== changeId) return false;
  return true;
}

export function useOrchestratorChat() {
  const boot = useMemo(() => readOrchBoot(), []);
  const [token, setToken] = useState("");
  const [ready, setReady] = useState(false);
  const [contractR34, setContractR34] = useState(false);
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
    setContractR34(orchContractIsR34());
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
    async (approvalId: string, _taskId: string | null, decision: "approved" | "rejected") => {
      if (decisionLock.current) return;
      const card = state.approvals.find((item) => item.id === approvalId);
      if (!card?.changeId) {
        setDecisionError({ id: approvalId, message: "нет change_id от сервера" });
        return;
      }
      decisionLock.current = true;
      setDecisionPendingId(approvalId);
      setDecisionError(null);
      try {
        const result = await postOrchDecision(cfg, {
          change_id: card.changeId,
          decision: decision === "approved" ? "approve" : "deny",
        });
        if (!approvalPostAccepted(result, card.changeId)) {
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

  const decidePaidOutcome = useCallback(
    async (approvalId: string, decision: PaidOutcomeDecision): Promise<PaidOutcomeDecisionResult> => {
      const rejected: PaidOutcomeDecisionResult = {
        ok: false,
        changeId: null,
        decision: null,
        forecast: null,
        executed: false,
        reason: null,
        retryArmed: false,
        retryReady: false,
      };
      if (decisionLock.current) return rejected;
      const card = state.approvals.find((item) => item.id === approvalId);
      if (!card?.changeId) {
        setDecisionError({ id: approvalId, message: "Решение недоступно: сервер не передал id" });
        return rejected;
      }
      decisionLock.current = true;
      setDecisionPendingId(approvalId);
      setDecisionError(null);
      try {
        const result = await postPaidOutcomeDecision(cfg, paidOutcomeDecisionBody(card.changeId, decision));
        if (!paidPostAccepted(result, card.changeId)) {
          setDecisionError({
            id: approvalId,
            message: paidOutcomeGrantNotice(result.reason) || result.reason || "сервер не подтвердил это решение",
          });
          return { ...result, ok: false, retryReady: false };
        }
        if (decision !== PAID_OUTCOME_DECISION.retryStep) {
          dispatch({
            type: "approval-resolved",
            id: approvalId,
            decision: decision === PAID_OUTCOME_DECISION.approve ? "approved" : "rejected",
          });
        }
        setSendError("");
        return result;
      } catch (error) {
        if (!noteAuth(error)) {
          setDecisionError({
            id: approvalId,
            message: error instanceof Error ? error.message : "не записалось решение",
          });
        }
        throw error;
      } finally {
        decisionLock.current = false;
        setDecisionPendingId(null);
      }
    },
    [cfg, noteAuth, state.approvals],
  );

  const confirmPaidRestart = useCallback(
    async (approvalId: string): Promise<PaidOutcomeDecisionResult | null> => {
      if (decisionLock.current) return null;
      const card = state.approvals.find((item) => item.id === approvalId);
      if (!card?.changeId) {
        setDecisionError({ id: approvalId, message: "Решение недоступно: сервер не передал id" });
        return null;
      }
      decisionLock.current = true;
      setDecisionPendingId(approvalId);
      setDecisionError(null);
      try {
        const result = await postPaidOutcomeRestartGrant(cfg, paidOutcomeGrantBody(card.changeId));
        if (!paidPostAccepted(result, card.changeId)) {
          setDecisionError({
            id: approvalId,
            message: paidOutcomeGrantNotice(result.reason) || result.reason || "сервер не подтвердил это решение",
          });
          return { ...result, ok: false, retryReady: false, retryArmed: false };
        }
        dispatch({ type: "approval-resolved", id: approvalId, decision: "approved" });
        setSendError("");
        return result;
      } catch (error) {
        if (!noteAuth(error)) {
          setDecisionError({
            id: approvalId,
            message: error instanceof Error ? error.message : "не записалось решение",
          });
        }
        throw error;
      } finally {
        decisionLock.current = false;
        setDecisionPendingId(null);
      }
    },
    [cfg, noteAuth, state.approvals],
  );

  const killTaskId = state.killNotice?.taskId ?? null;
  const killSessionId = state.killNotice?.sessionId ?? null;
  const killPhase = state.killNotice?.phase;
  useEffect(() => {
    if (killPhase !== "stopping") return;
    const timer = window.setTimeout(() => {
      dispatch({
        type: "kill-notice",
        detail: "Нет подтверждения остановки",
        paidBoundary: false,
        phase: "error",
        taskId: killTaskId,
        sessionId: killSessionId,
      });
    }, KILL_CONFIRM_MS);
    return () => window.clearTimeout(timer);
  }, [killPhase, killSessionId, killTaskId]);

  const bindProject = useCallback(
    async (approvalId: string, taskId: string | null, raw: string) => {
      const projectId = parseOrchProjectId(raw);
      if (projectId == null) {
        setDecisionError({ id: approvalId, message: PROJECT_BIND_INVALID });
        return;
      }
      let taskNum: number | null;
      try {
        taskNum = bindTaskId(taskId);
      } catch (error) {
        setDecisionError({
          id: approvalId,
          message: error instanceof Error ? error.message : PROJECT_BIND_TASK_INVALID,
        });
        return;
      }
      if (decisionLock.current) return;
      decisionLock.current = true;
      setDecisionPendingId(approvalId);
      setDecisionError(null);
      try {
        await postOrchProject(cfg, projectBindWireBody(projectId, taskNum));
        dispatch({ type: "approval-resolved", id: approvalId, decision: "closed" });
        setSendError("");
      } catch (error) {
        if (error instanceof OrchApiError && error.status === 409) {
          const foreign = isForeignTaskBindError(error.message);
          setDecisionError({
            id: approvalId,
            message: foreign ? PROJECT_BIND_FOREIGN : PROJECT_BIND_CONFLICT,
          });
          return;
        }
        if (error instanceof OrchApiError && error.status === 422) {
          setDecisionError({ id: approvalId, message: PROJECT_BIND_VALIDATION });
          return;
        }
        if (!noteAuth(error)) {
          setDecisionError({
            id: approvalId,
            message: error instanceof Error ? error.message : "проект не записался",
          });
        }
      } finally {
        decisionLock.current = false;
        setDecisionPendingId(null);
      }
    },
    [cfg, noteAuth],
  );

  const kill = useCallback(async () => {
    const taskId = state.tasks.at(-1)?.id ?? null;
    const sessionId = boot.session;
    const notice = { taskId, sessionId };
    dispatch({ type: "kill-notice", detail: "", paidBoundary: true, phase: "stopping", ...notice });
    const ac = new AbortController();
    const timer = window.setTimeout(() => ac.abort(), KILL_CONFIRM_MS);
    const fail = (detail: string) => {
      dispatch({ type: "kill-notice", detail, paidBoundary: false, phase: "error", ...notice });
    };
    try {
      if (orchKillUsesMessageField()) {
        await postOrchMessage(cfg, orchKillMessageBody(), ac.signal);
        // Ack {ok, session, ack_seq} без stop_key_set. Фазу решает событие kill_switch.
        setSendError("");
        return;
      }
      const result = (await postOrchKill(
        cfg,
        { session_id: boot.session, reason: "kill switch из чата" },
        ac.signal,
      )) as { ok?: boolean; detail?: string; stop_key_set?: boolean } | undefined;
      const body = result ?? {};
      if (body.stop_key_set === true && body.ok !== false) {
        dispatch({
          type: "kill-notice",
          detail: body.detail || "",
          paidBoundary: true,
          phase: "stopped",
          ...notice,
        });
        setSendError("");
        return;
      }
      const missing = body.stop_key_set !== true && body.stop_key_set !== false;
      fail(body.detail || (missing ? "В ответе нет stop_key_set" : "Стоп-ключ не установлен"));
    } catch (error) {
      const message = error instanceof Error ? error.message : "kill switch не прошёл";
      if (isAbortError(error)) {
        fail("Нет подтверждения остановки");
        return;
      }
      noteAuth(error);
      fail(message);
    } finally {
      window.clearTimeout(timer);
    }
  }, [boot.session, cfg, noteAuth, state.tasks]);

  return {
    boot,
    ready,
    contractR34,
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
    decidePaidOutcome,
    confirmPaidRestart,
    bindProject,
    dismissBlock,
    kill,
  };
}
