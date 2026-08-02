"use client";

import {
  useCallback,
  useEffect,
  useRef,
  useState,
  type MouseEvent as ReactMouseEvent,
} from "react";
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
  X,
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

/** Префикс к сообщению пользователя — только после того как он описал баг. */
const FIX_BUGS_PREFIX =
  "Режим «Фикс багов». Исправь ТОЛЬКО баг ниже (не сканируй весь проект вслепую). " +
  "Кратко по-русски → точечный edit_files (1–3 файла) → " +
  "run_tests только tests/test_code_autofix_allowlist.py → " +
  "git_commit_push auto=false. Ветка = ORCHESTRATOR_GIT_BRANCH.\n\nБАГ:\n";

export function OrchestratorPanel({ projectId }: Props) {
  const [open, setOpen] = usePersistedState("vp-orchestrator-open", true);
  const [expanded, setExpanded] = usePersistedState("vp-orchestrator-expanded", true);
  const storageKey = `vp-orchestrator-log-${projectId ?? "none"}`;
  const [chatLog, setChatLog] = usePersistedState<ChatMsg[]>(storageKey, []);
  const [chatInput, setChatInput] = useState("");
  const [busy, setBusy] = useState(false);
  const [fixBugsMode, setFixBugsMode] = useState(false);
  const abortRef = useRef<AbortController | null>(null);
  const inputRef = useRef<HTMLInputElement | null>(null);

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
    async (msg: string, opts?: { fixBugs?: boolean }) => {
      if (projectId == null || !msg.trim() || busy) return;
      const userText = msg.trim();
      const asFix = Boolean(opts?.fixBugs || fixBugsMode);
      const apiText = asFix ? `${FIX_BUGS_PREFIX}${userText}` : userText;
      const displayText = asFix ? `[фикс багов] ${userText}` : userText;
      if (asFix) setFixBugsMode(false);

      abortRef.current?.abort();
      const ac = new AbortController();
      abortRef.current = ac;
      setBusy(true);
      setOpen(true);
      const next: ChatMsg[] = [...chatLog, { role: "user", content: displayText }];
      setChatLog(next);
      try {
        const r = await api.dbOrchestratorChat(
          projectId,
          apiText,
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
    [projectId, busy, chatLog, fixBugsMode, setChatLog, setOpen],
  );

  const sendChat = useCallback(async () => {
    if (!chatInput.trim()) return;
    const msg = chatInput.trim();
    setChatInput("");
    await sendMessage(msg, { fixBugs: fixBugsMode });
  }, [chatInput, sendMessage, fixBugsMode]);

  /** Вход в режим фикса — отдельная кнопка (не toggle). */
  const enterFixBugsMode = useCallback(
    (e: ReactMouseEvent) => {
      e.preventDefault();
      e.stopPropagation();
      abortRef.current?.abort();
      abortRef.current = null;
      setBusy(false);
      setFixBugsMode(true);
      setOpen(true);
      setExpanded(true);
      setChatInput("");
      window.setTimeout(() => inputRef.current?.focus(), 50);
    },
    [setOpen, setExpanded],
  );

  /** Выход из режима фикса — отдельная кнопка, всегда только false. */
  const exitFixBugsMode = useCallback((e: ReactMouseEvent) => {
    e.preventDefault();
    e.stopPropagation();
    setFixBugsMode(false);
  }, []);

  const onToggleOpen = useCallback(
    (e: ReactMouseEvent) => {
      e.preventDefault();
      e.stopPropagation();
      setOpen(!open);
    },
    [open, setOpen],
  );

  const onToggleExpanded = useCallback(
    (e: ReactMouseEvent) => {
      e.preventDefault();
      e.stopPropagation();
      if (!open) {
        setOpen(true);
        setExpanded(true);
        return;
      }
      setExpanded(!expanded);
    },
    [open, expanded, setOpen, setExpanded],
  );

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
        "absolute z-40 flex flex-col border border-white/[0.1] bg-[#0c0c0c]/97 shadow-2xl backdrop-blur",
        open && expanded
          ? "inset-x-3 bottom-3 top-[14%] rounded-xl"
          : "inset-x-0 bottom-0 rounded-none border-x-0 border-b-0",
      )}
    >
      <div className="flex h-10 shrink-0 items-center gap-1.5 border-b border-white/[0.08] px-2">
        <div className="flex min-w-0 flex-1 items-center gap-2 px-1 text-[11px] text-white/60">
          <MessageSquare className="h-3.5 w-3.5 shrink-0 text-primary" />
          <span className="shrink-0 font-semibold text-white/85">Оркестратор</span>
          <span className="truncate text-white/35">
            {projectId == null
              ? "открой проект"
              : busy
                ? "думает… жми Отмена"
                : fixBugsMode
                  ? "режим фикса — опиши баг и Enter"
                  : "шаги · база · фикс багов"}
          </span>
        </div>

        {busy ? (
          <Button
            type="button"
            size="sm"
            variant="destructive"
            className="h-7 shrink-0 gap-1.5 text-[11px]"
            onClick={(e) => {
              e.stopPropagation();
              cancelBusy();
            }}
            title="Прервать запрос"
          >
            <Square className="h-3 w-3 fill-current" />
            Отмена
          </Button>
        ) : fixBugsMode ? (
          <Button
            type="button"
            size="sm"
            variant="destructive"
            className="h-7 shrink-0 gap-1.5 border-amber-400/50 bg-amber-600/80 text-[11px] text-white hover:bg-amber-500"
            onClick={exitFixBugsMode}
            title="Выйти из режима фикса без запроса к GPT"
          >
            <X className="h-3.5 w-3.5" />
            Выйти из фикса
          </Button>
        ) : (
          <Button
            type="button"
            size="sm"
            variant="outline"
            className="h-7 shrink-0 gap-1.5 border-amber-400/40 bg-amber-500/10 text-[11px] text-amber-100 hover:bg-amber-500/20"
            disabled={projectId == null}
            onClick={enterFixBugsMode}
            title="Включить режим фикса — потом опиши баг в поле ввода"
          >
            <Bug className="h-3.5 w-3.5" />
            Фикс багов
          </Button>
        )}

        <Button
          type="button"
          size="sm"
          variant="ghost"
          className="h-7 w-7 shrink-0 p-0 text-white/50"
          onClick={onToggleExpanded}
          title={expanded && open ? "Меньше окно" : "Большое окно"}
        >
          {expanded && open ? (
            <Minimize2 className="h-3.5 w-3.5" />
          ) : (
            <Maximize2 className="h-3.5 w-3.5" />
          )}
        </Button>

        <Button
          type="button"
          size="sm"
          variant="ghost"
          className="h-7 w-7 shrink-0 p-0 text-white/50"
          onClick={onToggleOpen}
          title={open ? "Свернуть панель" : "Открыть панель"}
        >
          {open ? <ChevronDown className="h-4 w-4" /> : <ChevronUp className="h-4 w-4" />}
        </Button>
      </div>

      {open ? (
        <div
          className={cn(
            "flex min-h-0 flex-1 flex-col",
            expanded ? "min-h-0" : "h-52",
          )}
        >
          <div className="min-h-0 flex-1 overflow-y-auto px-3 py-2">
            {fixBugsMode ? (
              <div className="mb-2 flex items-center gap-2 rounded-md border border-amber-400/30 bg-amber-500/10 px-2.5 py-2 text-[11px] text-amber-100/90">
                <span className="flex-1">
                  Режим фикса. Напиши баг → Enter. Окно не сворачивается.
                </span>
                <Button
                  type="button"
                  size="sm"
                  variant="outline"
                  className="h-7 shrink-0 border-amber-300/40 text-[11px] text-amber-50"
                  onClick={exitFixBugsMode}
                >
                  <X className="h-3.5 w-3.5" />
                  Выйти
                </Button>
              </div>
            ) : null}
            {chatLog.length === 0 && !fixBugsMode ? (
              <div className="text-[11px] text-white/25">
                «Фикс багов» → опиши баг → Отправить. Стрелка вниз — свернуть панель, □ — размер.
              </div>
            ) : null}
            {chatLog.map((m, i) => (
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
            ))}
          </div>
          <div className="flex shrink-0 items-center gap-2 border-t border-white/[0.06] px-3 py-2">
            <input
              ref={inputRef}
              value={chatInput}
              onChange={(e) => setChatInput(e.target.value)}
              onKeyDown={(e) => e.key === "Enter" && !busy && void sendChat()}
              disabled={projectId == null || busy}
              placeholder={
                projectId == null
                  ? "сначала открой проект"
                  : busy
                    ? "оркестратор думает… жми Отмена"
                    : fixBugsMode
                      ? "опиши баг для фикса…"
                      : "сообщение оркестратору…"
              }
              className={cn(
                "h-9 flex-1 rounded-md border bg-black/40 px-3 text-xs disabled:opacity-40",
                fixBugsMode ? "border-amber-400/50" : "border-white/10",
              )}
            />
            <Button
              type="button"
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
