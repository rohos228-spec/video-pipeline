"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import {
  Bug,
  ChevronDown,
  ChevronUp,
  GitBranch,
  Maximize2,
  MessageSquare,
  Minimize2,
  Send,
  Square,
  Trash2,
} from "lucide-react";
import { Button } from "@/components/ui/button";
import { api } from "@/lib/api";
import { usePersistedState } from "@/hooks/use-persisted-state";
import { cn } from "@/lib/utils";

interface Props {
  projectId: number | null;
}

interface PendingConfirm {
  kind: string;
  node_key?: string | null;
  node_type?: string | null;
  only?: string | null;
  count: number;
  nodes: string[];
  message?: string;
  files?: string[];
  resolved?: boolean;
}

interface ChatMsg {
  role: string;
  content: string;
  confirm?: PendingConfirm;
}

const FIX_BUGS_PROMPT =
  "Режим «Фикс багов»: по диагностике/ошибкам этого проекта найди баг в app/. " +
  "Сначала кратко по-русски: что сломано. Потом точечный edit_files (1–3 файла). " +
  "run_tests только короткий smoke (tests/test_code_autofix_allowlist.py), без полного suite. " +
  "git_commit_push с auto=false — я подтвержу push. Ветка = ORCHESTRATOR_GIT_BRANCH.";

export function OrchestratorPanel({ projectId }: Props) {
  const [open, setOpen] = usePersistedState("vp-orchestrator-open", true);
  const [expanded, setExpanded] = usePersistedState("vp-orchestrator-expanded", true);
  const storageKey = `vp-orchestrator-log-${projectId ?? "none"}`;
  const [chatLog, setChatLog] = usePersistedState<ChatMsg[]>(storageKey, []);
  const [chatInput, setChatInput] = useState("");
  const [busy, setBusy] = useState(false);
  const abortRef = useRef<AbortController | null>(null);

  useEffect(() => {
    try {
      const raw = localStorage.getItem(storageKey);
      setChatLog(raw ? JSON.parse(raw) : []);
    } catch {
      setChatLog([]);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [storageKey]);

  useEffect(() => {
    return () => {
      abortRef.current?.abort();
    };
  }, []);

  const cancelBusy = useCallback(() => {
    abortRef.current?.abort();
    abortRef.current = null;
    setBusy(false);
  }, []);

  const sendMessage = useCallback(
    async (msg: string) => {
      if (projectId == null || !msg.trim() || busy) return;
      abortRef.current?.abort();
      const ac = new AbortController();
      abortRef.current = ac;
      setBusy(true);
      setOpen(true);
      const next: ChatMsg[] = [...chatLog, { role: "user", content: msg.trim() }];
      setChatLog(next);
      try {
        const r = await api.dbOrchestratorChat(
          projectId,
          msg.trim(),
          next.slice(-9, -1).map((m) => ({ role: m.role, content: m.content })),
          { signal: ac.signal },
        );
        if (ac.signal.aborted) return;
        const notes: string[] = [];
        if (r.applied) {
          notes.push(
            `записано операций: ${r.applied.updated}${
              r.applied.exported ? `, в Excel ячеек: ${r.applied.exported.cells}` : ""
            }`,
          );
        }
        for (const a of r.actions_run ?? []) {
          if (a.run_step) notes.push(`запущен шаг «${a.run_step}» → ${a.status}`);
          else if (a.stop_step) notes.push("генерация остановлена");
          else if (a.set_option) notes.push(`настройка: ${a.set_option}`);
          else if (a.set_prompt) notes.push(`промт: ${a.set_prompt}`);
          else if (a.set_text_llm) notes.push(`LLM: ${a.set_text_llm}`);
          else if (a.run_harness) notes.push(`проверки: ${a.run_harness}`);
          else if (a.edit_files) notes.push(`код: ${a.edit_files}`);
          else if (a.run_tests) notes.push(`pytest: ${a.run_tests}`);
          else if (a.git_commit_push) notes.push(`git: ${a.git_commit_push}`);
        }
        for (const u of r.ui_actions ?? []) {
          const nodeKey = u.node_key ?? (u.node_type ? `n_${u.node_type}` : null);
          if (u.kind === "step_prompts" && nodeKey) {
            window.dispatchEvent(
              new CustomEvent("studio-open-node-prompts", { detail: { nodeKey } }),
            );
            notes.push(`открыл окно промтов шага «${u.step}»`);
          } else if (u.kind === "node_studio" && nodeKey) {
            window.dispatchEvent(
              new CustomEvent("studio-open-node-prompts", { detail: { nodeKey } }),
            );
            notes.push(`открыл студию ноды «${u.node_type}»`);
          } else if (u.kind === "prompt_builder" && nodeKey) {
            window.dispatchEvent(
              new CustomEvent("studio-open-prompt-builder", {
                detail: { nodeKey, nodeType: u.node_type },
              }),
            );
            notes.push(`открыл конструктор промтов «${u.node_type}»`);
          } else if (u.kind === "hitl" && u.hitl_id != null) {
            window.dispatchEvent(
              new CustomEvent("canvas-open-hitl-modal", { detail: { hitlId: u.hitl_id } }),
            );
            notes.push("открыл окно аппрува");
          } else if (u.kind === "topic") {
            window.dispatchEvent(
              new CustomEvent("canvas-select-node", { detail: { nodeKey: "n_topic" } }),
            );
            notes.push("тема проекта — в инспекторе справа");
          } else if (u.kind === "settings") {
            window.dispatchEvent(
              new CustomEvent("canvas-select-node", { detail: { nodeKey: null } }),
            );
            notes.push("настройки проекта — в инспекторе справа");
          } else if (u.kind === "baza") {
            window.dispatchEvent(new CustomEvent("studio-open-baza"));
            notes.push("открыл «Базу»");
          } else if (u.kind === "gpt_chat") {
            window.dispatchEvent(new CustomEvent("studio-open-gpt"));
            notes.push("открыл общий чат");
          } else if (u.kind === "open_project" && u.project_id != null) {
            window.dispatchEvent(
              new CustomEvent("studio-select-project", {
                detail: { projectId: u.project_id },
              }),
            );
            notes.push(`создал и открыл проект #${u.project_id}`);
          } else if (u.kind === "fleet") {
            window.dispatchEvent(new CustomEvent("studio-open-fleet"));
            notes.push("открыл «Сеть»");
          }
        }
        if (r.error) notes.push(`ошибка: ${r.error}`);
        const pendRemove = (r.pending_confirm ?? []).find((p) => p.kind === "remove_node");
        const pendGit = (r.pending_confirm ?? []).find((p) => p.kind === "git_commit_push");
        const pend = pendGit ?? pendRemove;
        if (pendRemove) {
          notes.push(`к удалению: ${pendRemove.count} нод — кнопки ниже`);
        }
        if (pendGit) {
          notes.push(`к push: подтверди кнопку ниже`);
        }
        setChatLog([
          ...next,
          {
            role: "assistant",
            content:
              (r.reply || "(пустой ответ)") + (notes.length ? `\n\n— ${notes.join("; ")}` : ""),
            confirm: pend ? { ...pend, resolved: false } : undefined,
          },
        ]);
      } catch (e) {
        if (ac.signal.aborted) {
          setChatLog([
            ...next,
            { role: "assistant", content: "Отменено — Studio снова отвечает." },
          ]);
          return;
        }
        setChatLog([
          ...next,
          { role: "assistant", content: `Ошибка: ${e instanceof Error ? e.message : e}` },
        ]);
      } finally {
        if (abortRef.current === ac) abortRef.current = null;
        setBusy(false);
      }
    },
    [projectId, busy, chatLog, setChatLog, setOpen],
  );

  const sendChat = useCallback(async () => {
    if (!chatInput.trim()) return;
    const msg = chatInput.trim();
    setChatInput("");
    await sendMessage(msg);
  }, [chatInput, sendMessage]);

  const resolveRemove = useCallback(
    async (msgIndex: number, confirm: PendingConfirm, approve: boolean) => {
      if (projectId == null || busy) return;
      setBusy(true);
      try {
        if (approve) {
          const r = await api.dbOrchestratorConfirmRemove(projectId, {
            node_key: confirm.node_key,
            node_type: confirm.node_type,
            only: confirm.only,
          });
          setChatLog((prev) => [
            ...prev.map((m, i) =>
              i === msgIndex && m.confirm ? { ...m, confirm: { ...m.confirm, resolved: true } } : m,
            ),
            { role: "assistant", content: `Подтверждено: ${r.remove_node}.` },
          ]);
        } else {
          setChatLog((prev) => [
            ...prev.map((m, i) =>
              i === msgIndex && m.confirm ? { ...m, confirm: { ...m.confirm, resolved: true } } : m,
            ),
            { role: "assistant", content: "Удаление отменено — ноды на месте." },
          ]);
        }
      } catch (e) {
        setChatLog((prev) => [
          ...prev,
          {
            role: "assistant",
            content: `Ошибка удаления: ${e instanceof Error ? e.message : e}`,
          },
        ]);
      } finally {
        setBusy(false);
      }
    },
    [projectId, busy, setChatLog],
  );

  const resolveGitPush = useCallback(
    async (msgIndex: number, confirm: PendingConfirm, approve: boolean) => {
      if (projectId == null || busy) return;
      setBusy(true);
      try {
        if (approve) {
          const r = await api.dbOrchestratorConfirmGitPush(projectId, {
            message: confirm.message || "fix: orchestrator",
            files: confirm.files ?? confirm.nodes,
          });
          setChatLog((prev) => [
            ...prev.map((m, i) =>
              i === msgIndex && m.confirm ? { ...m, confirm: { ...m.confirm, resolved: true } } : m,
            ),
            { role: "assistant", content: `${r.git_commit_push}` },
          ]);
        } else {
          setChatLog((prev) => [
            ...prev.map((m, i) =>
              i === msgIndex && m.confirm ? { ...m, confirm: { ...m.confirm, resolved: true } } : m,
            ),
            {
              role: "assistant",
              content: "Push отменён — правки на диске остались, в git не ушли.",
            },
          ]);
        }
      } catch (e) {
        setChatLog((prev) => [
          ...prev,
          {
            role: "assistant",
            content: `Ошибка push: ${e instanceof Error ? e.message : e}`,
          },
        ]);
      } finally {
        setBusy(false);
      }
    },
    [projectId, busy, setChatLog],
  );

  return (
    <div
      className={cn(
        "absolute z-40 border border-white/[0.1] bg-[#0c0c0c]/97 shadow-2xl backdrop-blur transition-all",
        open && expanded
          ? "inset-x-3 bottom-3 top-[18%] max-h-[78vh] rounded-xl"
          : "inset-x-0 bottom-0 rounded-none border-x-0 border-b-0",
      )}
    >
      <div className="flex h-10 items-center gap-2 border-b border-white/[0.08] px-3">
        <button
          type="button"
          onClick={() => setOpen(!open)}
          className="flex min-w-0 flex-1 items-center gap-2 text-left text-[11px] text-white/60 hover:text-white/80"
        >
          <MessageSquare className="h-3.5 w-3.5 shrink-0 text-primary" />
          <span className="font-semibold text-white/85">Оркестратор</span>
          <span className="truncate text-white/35">
            {projectId == null
              ? "открой проект"
              : busy
                ? "думает… можно Отмена"
                : "шаги · база · фикс багов → push"}
          </span>
          {open ? (
            <ChevronDown className="ml-auto h-4 w-4 shrink-0" />
          ) : (
            <ChevronUp className="ml-auto h-4 w-4 shrink-0" />
          )}
        </button>
        {busy ? (
          <Button
            size="sm"
            variant="destructive"
            className="h-7 gap-1.5 text-[11px]"
            onClick={cancelBusy}
            title="Прервать запрос — разморозить Studio"
          >
            <Square className="h-3 w-3 fill-current" />
            Отмена
          </Button>
        ) : (
          <Button
            size="sm"
            variant="outline"
            className="h-7 gap-1.5 border-amber-400/40 bg-amber-500/10 text-[11px] text-amber-100 hover:bg-amber-500/20"
            disabled={projectId == null}
            onClick={() => {
              setExpanded(true);
              setOpen(true);
              void sendMessage(FIX_BUGS_PROMPT);
            }}
            title="Найти и починить баги в коде, подготовить push"
          >
            <Bug className="h-3.5 w-3.5" />
            Фикс багов
          </Button>
        )}
        <Button
          size="sm"
          variant="ghost"
          className="h-7 w-7 p-0 text-white/50"
          onClick={() => {
            setOpen(true);
            setExpanded(!expanded);
          }}
          title={expanded ? "Свернуть окно" : "Большое окно"}
        >
          {expanded ? <Minimize2 className="h-3.5 w-3.5" /> : <Maximize2 className="h-3.5 w-3.5" />}
        </Button>
      </div>

      {open ? (
        <div
          className={cn(
            "flex flex-col",
            expanded ? "h-[calc(100%-2.5rem)]" : "h-52",
          )}
        >
          <div className="min-h-0 flex-1 overflow-y-auto px-3 py-2">
            {chatLog.length === 0 ? (
              <div className="text-[11px] text-white/25">
                Пиши по-русски: шаги пайплайна, правки базы, или жми «Фикс багов» в шапке.
                Если зависло — кнопка «Отмена».
              </div>
            ) : (
              chatLog.map((m, i) => (
                <div key={i} className="mb-2 text-[12px] leading-relaxed">
                  <span className={m.role === "user" ? "text-primary" : "text-white/45"}>
                    {m.role === "user" ? "ты" : "оркестратор"}:{" "}
                  </span>
                  <span className="whitespace-pre-wrap text-white/85">{m.content}</span>
                  {m.confirm && !m.confirm.resolved && m.confirm.kind === "remove_node" ? (
                    <span className="mt-1.5 flex items-center gap-2 rounded-md border border-red-400/30 bg-red-500/10 px-2.5 py-1.5">
                      <Trash2 className="h-3.5 w-3.5 text-red-400" />
                      <span className="flex-1 text-[11px] text-white/80">
                        Удалить {m.confirm.count} нод
                        {m.confirm.node_type ? ` типа «${m.confirm.node_type}»` : ""}
                        {m.confirm.only === "duplicates" ? " (только дубли)" : ""}?
                      </span>
                      <Button
                        size="sm"
                        variant="destructive"
                        className="h-7 text-[11px]"
                        disabled={busy}
                        onClick={() => void resolveRemove(i, m.confirm!, true)}
                      >
                        Подтвердить удаление
                      </Button>
                      <Button
                        size="sm"
                        variant="outline"
                        className="h-7 text-[11px]"
                        disabled={busy}
                        onClick={() => void resolveRemove(i, m.confirm!, false)}
                      >
                        Отмена
                      </Button>
                    </span>
                  ) : null}
                  {m.confirm && !m.confirm.resolved && m.confirm.kind === "git_commit_push" ? (
                    <span className="mt-1.5 flex items-center gap-2 rounded-md border border-emerald-400/30 bg-emerald-500/10 px-2.5 py-1.5">
                      <GitBranch className="h-3.5 w-3.5 text-emerald-400" />
                      <span className="flex-1 text-[11px] text-white/80">
                        Push: {m.confirm.message || "commit"}
                        {(m.confirm.files ?? m.confirm.nodes).length
                          ? ` (${(m.confirm.files ?? m.confirm.nodes).slice(0, 3).join(", ")})`
                          : ""}
                      </span>
                      <Button
                        size="sm"
                        className="h-7 text-[11px]"
                        disabled={busy}
                        onClick={() => void resolveGitPush(i, m.confirm!, true)}
                      >
                        Подтвердить push
                      </Button>
                      <Button
                        size="sm"
                        variant="outline"
                        className="h-7 text-[11px]"
                        disabled={busy}
                        onClick={() => void resolveGitPush(i, m.confirm!, false)}
                      >
                        Отмена
                      </Button>
                    </span>
                  ) : null}
                </div>
              ))
            )}
          </div>
          <div className="flex items-center gap-2 border-t border-white/[0.06] px-3 py-2">
            <input
              value={chatInput}
              onChange={(e) => setChatInput(e.target.value)}
              onKeyDown={(e) => e.key === "Enter" && !busy && void sendChat()}
              disabled={projectId == null || busy}
              placeholder={
                projectId == null
                  ? "сначала открой проект"
                  : busy
                    ? "оркестратор думает… жми Отмена в шапке"
                    : "сообщение оркестратору…"
              }
              className="h-9 flex-1 rounded-md border border-white/10 bg-black/40 px-3 text-xs disabled:opacity-40"
            />
            <Button
              size="sm"
              variant="outline"
              disabled={projectId == null || busy || !chatInput.trim()}
              onClick={() => void sendChat()}
              className="h-9 gap-1.5 text-xs"
            >
              <Send className="h-3.5 w-3.5" />
              {busy ? "…" : "Отправить"}
            </Button>
          </div>
        </div>
      ) : null}
    </div>
  );
}
