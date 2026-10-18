"use client";

import { useCallback, useEffect, useMemo, useReducer, useRef, useState } from "react";
import {
  consumeOrchStream,
  isAbortError,
  orchTaskModeEnabled,
  OrchApiError,
  postOrchDecision,
  postOrchKill,
  postOrchActionMessage,
  postOrchMessage,
  readOrchLiveVersion,
  readOrchProjectId,
  probeOrchAuth,
  readOrchBoot,
  readOrchToken,
  sleepWithSignal,
  writeOrchToken,
  type OrchClientConfig,
} from "@/lib/orchestratorApi";
import { conflictRetryBody, reportEditBody, restoreBody } from "@/lib/orchestratorActionCards";
import {
  initialOrchScreen,
  reduceOrchScreen,
  type OrchScreenState,
} from "@/lib/orchestratorSession";

export type ConnPhase = "idle" | "live" | "reconnecting" | "restored";

/** Сколько ждём подтверждение kill. Дольше — ошибка и «Повторить». */
const KILL_CONFIRM_MS = 12_000;

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
  const conflictRefresh = useRef(new Set<string>());

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

  const sendReportEdit = useCallback(
    async (reportId: number, instruction: string) => {
      const body = reportEditBody(reportId, instruction);
      if (!body.text || !token || blocked) return false;
      dispatch({
        type: "user-message",
        id: `user-report-${reportId}-${state.messages.length}`,
        text: body.text,
        taskId: null,
      });
      dispatch({ type: "user-action", name: body.action.name, baseVersion: null });
      try {
        await postOrchActionMessage(cfg, body);
        setSendError("");
        return true;
      } catch (error) {
        if (!noteAuth(error)) {
          setSendError(error instanceof Error ? error.message : "правка отчёта не ушла");
        }
        return false;
      }
    },
    [blocked, cfg, noteAuth, state.messages.length, token],
  );

  const sendRestore = useCallback(
    async (restoreAction: string, snapshotId: string | null, archiveRef: string | null) => {
      const body = restoreBody(restoreAction, snapshotId, archiveRef);
      if (!body.text || !token || blocked) return false;
      dispatch({
        type: "user-message",
        id: `user-restore-${snapshotId ?? archiveRef ?? restoreAction}-${state.messages.length}`,
        text: body.text,
        taskId: null,
      });
      dispatch({ type: "user-action", name: body.action.name, baseVersion: null });
      try {
        await postOrchActionMessage(cfg, body);
        setSendError("");
        return true;
      } catch (error) {
        if (!noteAuth(error)) {
          setSendError(error instanceof Error ? error.message : "восстановление не ушло");
        }
        return false;
      }
    },
    [blocked, cfg, noteAuth, state.messages.length, token],
  );

  const sendConflictRetry = useCallback(
    async (actionName: string, baseVersion: number | null, staleBase: number | null) => {
      const body = conflictRetryBody(actionName, baseVersion, staleBase);
      if (!body || !token || blocked) return false;
      dispatch({
        type: "user-message",
        id: `user-conflict-${body.action.name}-${state.messages.length}`,
        text: body.text,
        taskId: null,
      });
      dispatch({ type: "user-action", name: body.action.name, baseVersion: body.action.base_version ?? null });
      try {
        await postOrchActionMessage(cfg, body);
        setSendError("");
        return true;
      } catch (error) {
        if (!noteAuth(error)) {
          setSendError(error instanceof Error ? error.message : "повтор правки не ушёл");
        }
        return false;
      }
    },
    [blocked, cfg, noteAuth, state.messages.length, token],
  );

  useEffect(() => {
    if (!token || blocked) return;
    const cards = state.actionResults.filter(
      (card) => card.conflict && card.kind !== "invalid" && card.retryBaseVersion == null && !conflictRefresh.current.has(card.id),
    );
    if (!cards.length) return;
    const ids = cards.map((card) => card.id);
    for (const id of ids) conflictRefresh.current.add(id);
    const snapshotUrl = cards.find((card) => card.snapshotUrl)?.snapshotUrl ?? null;
    const ac = new AbortController();
    void (async () => {
      try {
        const version = await readOrchLiveVersion(cfg, snapshotUrl, ac.signal);
        if (ac.signal.aborted) {
          for (const id of ids) conflictRefresh.current.delete(id);
          return;
        }
        if (version == null) return;
        dispatch({ type: "conflict-refresh", ids, version });
      } catch {
        if (ac.signal.aborted) {
          for (const id of ids) conflictRefresh.current.delete(id);
        }
      }
    })();
    return () => {
      ac.abort();
      for (const id of ids) conflictRefresh.current.delete(id);
    };
  }, [blocked, cfg, state.actionResults, token]);

  const decide = useCallback(
    async (approvalId: string, _taskId: string | null, decision: "approved" | "rejected") => {
      if (decisionLock.current) return;
      const card = state.approvals.find((item) => item.id === approvalId);
      if (!card?.changeId || !/^orch-appr-[0-9]+-[0-9]+$/.test(card.changeId)) {
        setDecisionError({ id: approvalId, message: "нет change_id формата orch-appr" });
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
    sendReportEdit,
    sendRestore,
    sendConflictRetry,
    decide,
    dismissBlock,
    kill,
  };
}
