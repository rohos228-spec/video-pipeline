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
  /** Одно или несколько подтверждений (удаление / push). */
  confirms?: PendingConfirm[];
  /** @deprecated legacy single confirm — читается вместе с confirms */
  confirm?: PendingConfirm;
}

/** Префикс к API (в чате пользователю не показываем). */
const FIX_BUGS_PREFIX =
  "Режим «Фикс багов» / поручение оператора ниже. " +
  "Если просят удалить/создать проекты или шаги Studio — делай это через actions " +
  "(delete_projects / create_project / run_step…), не через edit_files. " +
  "Если просят починить КОД — edit_files + короткий run_tests + git_commit_push auto=false. " +
  "Кратко по-русски.\n\nПОРУЧЕНИЕ:\n";

export function OrchestratorPanel({ projectId }: Props) {
  const [open, setOpen] = usePersistedState("vp-orchestrator-open", true);
  const [expanded, setExpanded] = usePersistedState("vp-orchestrator-expanded", true);
  const storageKey = `vp-orchestrator-log-${projectId ?? "none"}`;
  const [chatLog, setChatLog] = usePersistedState<ChatMsg[]>(storageKey, []);
  const [chatInput, setChatInput] = useState("");
  const [busy, setBusy] = useState(false);
  const [fixBugsMode, setFixBugsMode] = useState(false);
  const fixBugsModeRef = useRef(false);
  const abortRef = useRef<AbortController | null>(null);
  const userCancelRef = useRef(false);
  const inputRef = useRef<HTMLInputElement | null>(null);

  useEffect(() => {
    fixBugsModeRef.current = fixBugsMode;
  }, [fixBugsMode]);

  useEffect(() => {
    try {
      const raw = localStorage.getItem(storageKey);
      setChatLog(raw ? JSON.parse(raw) : []);
    } catch {
      setChatLog([]);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [storageKey]);

  const cancelBusy = useCallback(() => {
    userCancelRef.current = true;
    abortRef.current?.abort();
    abortRef.current = null;
    setBusy(false);
  }, []);

  const sendMessage = useCallback(
    async (msg: string, opts?: { fixBugs?: boolean }) => {
      if (projectId == null || !msg.trim() || busy) return;
      const userText = msg.trim();
      const asFix = Boolean(opts?.fixBugs || fixBugsModeRef.current || fixBugsMode);
      const apiText = asFix ? `${FIX_BUGS_PREFIX}${userText}` : userText;
      if (asFix) {
        fixBugsModeRef.current = false;
        setFixBugsMode(false);
      }

      // Тихий abort предыдущего запроса (не писать «Отменено» в чат)
      abortRef.current?.abort();
      userCancelRef.current = false;
      const ac = new AbortController();
      abortRef.current = ac;
      setBusy(true);
      setOpen(true);
      const next: ChatMsg[] = [...chatLog, { role: "user", content: userText }];
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
          else if (a.read_file) notes.push(`файл:\n${a.read_file}`);
          else if (a.edit_files) notes.push(`код: ${a.edit_files}`);
          else if (a.run_tests) notes.push(`pytest: ${a.run_tests}`);
          else if (a.git_commit_push) notes.push(`git: ${a.git_commit_push}`);
          else if (a.delete_projects) notes.push(`проекты: ${a.delete_projects}`);
          else if (a.create_project) notes.push(`создан проект: ${a.create_project}`);
          else if (a.create_child) notes.push(`дочерний: ${a.create_child}`);
          else if (a.add_node) notes.push(`нода: ${typeof a.add_node === "string" ? a.add_node : JSON.stringify(a.add_node)}`);
          else if (a.connect_edges) notes.push(`связи: ${a.connect_edges}`);
          else if (a.rename_node) notes.push(`переименовано: ${a.rename_node}`);
          else if (a.repair_graph) notes.push(`граф: ${a.repair_graph}`);
          else if (a.hitl_decision) notes.push(`HITL: ${a.hitl_decision}`);
          else if (a.set_topic) notes.push(`тема: ${a.set_topic}`);
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
          } else if (u.kind === "create") {
            window.dispatchEvent(
              new CustomEvent("studio-open-outsee", {
                detail: { projectId },
              }),
            );
            notes.push("открыл Create / Outsee");
          }
        }
        if (r.error) notes.push(`ошибка: ${r.error}`);
        const pendAll = (r.pending_confirm ?? []).map((p) => ({ ...p, resolved: false }));
        for (const p of pendAll) {
          if (p.kind === "delete_projects") {
            notes.push(`к удалению проектов: ${p.count} — кнопки ниже`);
          } else if (p.kind === "remove_node") {
            notes.push(`к удалению нод: ${p.count} — кнопки ниже`);
          } else if (p.kind === "git_commit_push") {
            notes.push(`к push: подтверди кнопку ниже`);
          }
        }
        setChatLog([
          ...next,
          {
            role: "assistant",
            content:
              (r.reply || "(пустой ответ)") + (notes.length ? `\n\n— ${notes.join("; ")}` : ""),
            confirms: pendAll.length ? pendAll : undefined,
          },
        ]);
      } catch (e) {
        if (ac.signal.aborted) {
          if (userCancelRef.current) {
            userCancelRef.current = false;
            setChatLog([
              ...next,
              { role: "assistant", content: "Отменено — Studio снова отвечает." },
            ]);
          }
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

  /** Одна кнопка: 1-й клик — режим фикса, 2-й клик — выход. */
  const onFixBugsToggle = useCallback(
    (e: ReactMouseEvent) => {
      e.preventDefault();
      e.stopPropagation();
      if (fixBugsModeRef.current) {
        fixBugsModeRef.current = false;
        setFixBugsMode(false);
        return;
      }
      abortRef.current?.abort();
      abortRef.current = null;
      setBusy(false);
      fixBugsModeRef.current = true;
      setFixBugsMode(true);
      setOpen(true);
      setExpanded(true);
      setChatInput("");
      window.setTimeout(() => inputRef.current?.focus(), 50);
    },
    [setOpen, setExpanded],
  );

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

  const withConfirmResolved = useCallback(
    (prev: ChatMsg[], msgIndex: number, confirmIndex: number, followUp: ChatMsg) => {
      const next = prev.map((m, i) => {
        if (i !== msgIndex) return m;
        const list = [...(m.confirms ?? (m.confirm ? [m.confirm] : []))];
        if (list[confirmIndex]) {
          list[confirmIndex] = { ...list[confirmIndex], resolved: true };
        }
        return { ...m, confirms: list, confirm: undefined };
      });
      return [...next, followUp];
    },
    [],
  );

  const resolveRemove = useCallback(
    async (
      msgIndex: number,
      confirmIndex: number,
      confirm: PendingConfirm,
      approve: boolean,
    ) => {
      if (projectId == null || busy) return;
      setBusy(true);
      try {
        if (approve) {
          const r = await api.dbOrchestratorConfirmRemove(projectId, {
            node_key: confirm.node_key,
            node_type: confirm.node_type,
            only: confirm.only,
          });
          setChatLog((prev) =>
            withConfirmResolved(prev, msgIndex, confirmIndex, {
              role: "assistant",
              content: `Подтверждено: ${r.remove_node}.`,
            }),
          );
        } else {
          setChatLog((prev) =>
            withConfirmResolved(prev, msgIndex, confirmIndex, {
              role: "assistant",
              content: "Удаление отменено — ноды на месте.",
            }),
          );
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
    [projectId, busy, setChatLog, withConfirmResolved],
  );

  const resolveDeleteProjects = useCallback(
    async (
      msgIndex: number,
      confirmIndex: number,
      confirm: PendingConfirm,
      approve: boolean,
    ) => {
      if (projectId == null || busy) return;
      setBusy(true);
      try {
        if (approve) {
          const ids = (confirm.nodes || [])
            .map((x) => Number(x))
            .filter((n) => Number.isFinite(n) && n > 0);
          const r = await api.dbOrchestratorConfirmDeleteProjects(projectId, { ids });
          setChatLog((prev) =>
            withConfirmResolved(prev, msgIndex, confirmIndex, {
              role: "assistant",
              content: `${r.delete_projects}`,
            }),
          );
          window.dispatchEvent(new CustomEvent("studio-projects-changed"));
          if (ids.includes(projectId)) {
            window.dispatchEvent(
              new CustomEvent("studio-select-project", { detail: { projectId: null } }),
            );
          }
        } else {
          setChatLog((prev) =>
            withConfirmResolved(prev, msgIndex, confirmIndex, {
              role: "assistant",
              content: "Удаление проектов отменено — ничего не стёрто.",
            }),
          );
        }
      } catch (e) {
        setChatLog((prev) => [
          ...prev,
          {
            role: "assistant",
            content: `Ошибка удаления проектов: ${e instanceof Error ? e.message : e}`,
          },
        ]);
      } finally {
        setBusy(false);
      }
    },
    [projectId, busy, setChatLog, withConfirmResolved],
  );

  const resolveGitPush = useCallback(
    async (
      msgIndex: number,
      confirmIndex: number,
      confirm: PendingConfirm,
      approve: boolean,
    ) => {
      if (projectId == null || busy) return;
      setBusy(true);
      try {
        if (approve) {
          const r = await api.dbOrchestratorConfirmGitPush(projectId, {
            message: confirm.message || "fix: orchestrator",
            files: confirm.files ?? confirm.nodes,
          });
          setChatLog((prev) =>
            withConfirmResolved(prev, msgIndex, confirmIndex, {
              role: "assistant",
              content: `${r.git_commit_push}`,
            }),
          );
        } else {
          setChatLog((prev) =>
            withConfirmResolved(prev, msgIndex, confirmIndex, {
              role: "assistant",
              content: "Push отменён — правки на диске остались, в git не ушли.",
            }),
          );
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
    [projectId, busy, setChatLog, withConfirmResolved],
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
        ) : (
          <Button
            type="button"
            size="sm"
            variant="outline"
            className={cn(
              "h-7 shrink-0 gap-1.5 text-[11px]",
              fixBugsMode
                ? "border-amber-300 bg-amber-500/35 text-amber-50 ring-1 ring-amber-300/40"
                : "border-amber-400/40 bg-amber-500/10 text-amber-100 hover:bg-amber-500/20",
            )}
            disabled={projectId == null}
            onClick={onFixBugsToggle}
            aria-pressed={fixBugsMode}
            title={
              fixBugsMode
                ? "Повторное нажатие — выйти из режима фикса"
                : "Нажать — режим фикса; потом опиши баг. Ещё раз — выйти"
            }
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
              <div className="mb-2 rounded-md border border-amber-400/30 bg-amber-500/10 px-2.5 py-2 text-[11px] text-amber-100/90">
                Режим фикса включён. Напиши баг → Enter. Повторное нажатие «Фикс багов» — выход.
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
                {(m.confirms ?? (m.confirm ? [m.confirm] : [])).map((c, ci) => {
                  if (c.resolved) return null;
                  if (c.kind === "remove_node") {
                    return (
                      <span
                        key={`rm-${ci}`}
                        className="mt-1.5 flex items-center gap-2 rounded-md border border-red-400/30 bg-red-500/10 px-2.5 py-1.5"
                      >
                        <Trash2 className="h-3.5 w-3.5 text-red-400" />
                        <span className="flex-1 text-[11px] text-white/80">
                          Удалить {c.count} нод
                          {c.node_type ? ` типа «${c.node_type}»` : ""}
                          {c.only === "duplicates" ? " (только дубли)" : ""}?
                        </span>
                        <Button
                          size="sm"
                          variant="destructive"
                          className="h-7 text-[11px]"
                          disabled={busy}
                          onClick={() => void resolveRemove(i, ci, c, true)}
                        >
                          Подтвердить удаление
                        </Button>
                        <Button
                          size="sm"
                          variant="outline"
                          className="h-7 text-[11px]"
                          disabled={busy}
                          onClick={() => void resolveRemove(i, ci, c, false)}
                        >
                          Отмена
                        </Button>
                      </span>
                    );
                  }
                  if (c.kind === "delete_projects") {
                    return (
                      <span
                        key={`del-${ci}`}
                        className="mt-1.5 flex items-center gap-2 rounded-md border border-red-400/30 bg-red-500/10 px-2.5 py-1.5"
                      >
                        <Trash2 className="h-3.5 w-3.5 text-red-400" />
                        <span className="flex-1 text-[11px] text-white/80">
                          Удалить {c.count} проект(ов)
                          {(c.files ?? []).length
                            ? `: ${(c.files ?? []).slice(0, 4).join(", ")}`
                            : ""}
                          ?
                        </span>
                        <Button
                          size="sm"
                          variant="destructive"
                          className="h-7 text-[11px]"
                          disabled={busy}
                          onClick={() => void resolveDeleteProjects(i, ci, c, true)}
                        >
                          Подтвердить удаление проектов
                        </Button>
                        <Button
                          size="sm"
                          variant="outline"
                          className="h-7 text-[11px]"
                          disabled={busy}
                          onClick={() => void resolveDeleteProjects(i, ci, c, false)}
                        >
                          Отмена
                        </Button>
                      </span>
                    );
                  }
                  if (c.kind === "git_commit_push") {
                    return (
                      <span
                        key={`git-${ci}`}
                        className="mt-1.5 flex items-center gap-2 rounded-md border border-emerald-400/30 bg-emerald-500/10 px-2.5 py-1.5"
                      >
                        <GitBranch className="h-3.5 w-3.5 text-emerald-400" />
                        <span className="flex-1 text-[11px] text-white/80">
                          Push: {c.message || "commit"}
                          {(c.files ?? c.nodes).length
                            ? ` (${(c.files ?? c.nodes).slice(0, 3).join(", ")})`
                            : ""}
                        </span>
                        <Button
                          size="sm"
                          className="h-7 text-[11px]"
                          disabled={busy}
                          onClick={() => void resolveGitPush(i, ci, c, true)}
                        >
                          Подтвердить push
                        </Button>
                        <Button
                          size="sm"
                          variant="outline"
                          className="h-7 text-[11px]"
                          disabled={busy}
                          onClick={() => void resolveGitPush(i, ci, c, false)}
                        >
                          Отмена
                        </Button>
                      </span>
                    );
                  }
                  return null;
                })}
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
