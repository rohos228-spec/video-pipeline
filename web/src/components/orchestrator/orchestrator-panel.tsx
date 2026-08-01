"use client";

import { useCallback, useState } from "react";
import { ChevronDown, ChevronUp, MessageSquare, Send } from "lucide-react";
import { Button } from "@/components/ui/button";
import { api } from "@/lib/api";
import { usePersistedState } from "@/hooks/use-persisted-state";

interface Props {
  projectId: number | null;
}

export function OrchestratorPanel({ projectId }: Props) {
  const [open, setOpen] = usePersistedState("vp-orchestrator-open", true);
  const [chatLog, setChatLog] = useState<{ role: string; content: string }[]>([]);
  const [chatInput, setChatInput] = useState("");
  const [busy, setBusy] = useState(false);

  const sendChat = useCallback(async () => {
    if (projectId == null || !chatInput.trim() || busy) return;
    const msg = chatInput.trim();
    setChatInput("");
    setBusy(true);
    const next = [...chatLog, { role: "user", content: msg }];
    setChatLog(next);
    try {
      const r = await api.dbOrchestratorChat(projectId, msg, next.slice(-9, -1));
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
      }
      for (const u of r.ui_actions ?? []) {
        if (u.kind === "step_prompts" && u.node_type) {
          window.dispatchEvent(
            new CustomEvent("studio-open-node-prompts", {
              detail: { nodeKey: `n_${u.node_type}` },
            }),
          );
          notes.push(`открыл окно промтов шага «${u.step}» — выбирай вариант там`);
        }
      }
      if (r.error) notes.push(`ошибка: ${r.error}`);
      setChatLog([
        ...next,
        {
          role: "assistant",
          content: (r.reply || "(пустой ответ)") + (notes.length ? `\n\n— ${notes.join("; ")}` : ""),
        },
      ]);
    } catch (e) {
      setChatLog([
        ...next,
        { role: "assistant", content: `Ошибка: ${e instanceof Error ? e.message : e}` },
      ]);
    } finally {
      setBusy(false);
    }
  }, [projectId, chatInput, busy, chatLog]);

  return (
    <div className="absolute inset-x-0 bottom-0 z-30 border-t border-white/[0.08] bg-[#0c0c0c]/95 backdrop-blur">
      <button
        type="button"
        onClick={() => setOpen(!open)}
        className="flex h-8 w-full items-center gap-2 px-3 text-left text-[11px] text-white/60 hover:bg-white/5"
      >
        <MessageSquare className="h-3.5 w-3.5 text-primary" />
        <span className="font-semibold text-white/80">Оркестратор</span>
        <span className="text-white/35">
          {projectId == null
            ? "открой проект, чтобы писать"
            : "пиши по-русски — правит базу и запускает шаги"}
        </span>
        <span className="flex-1" />
        {open ? <ChevronDown className="h-4 w-4" /> : <ChevronUp className="h-4 w-4" />}
      </button>
      {open ? (
        <div className="flex h-52 flex-col">
          <div className="min-h-0 flex-1 overflow-y-auto px-3 py-1.5">
            {chatLog.length === 0 ? (
              <div className="text-[11px] text-white/25">
                Например: «какой следующий шаг?», «запусти anim_pr», «перепиши закадр
                во 2 кадре на …». Изменения видны в «Базе», шаги — на пайплайне.
              </div>
            ) : (
              chatLog.map((m, i) => (
                <div key={i} className="mb-1.5 text-[11px]">
                  <span className={m.role === "user" ? "text-primary" : "text-white/50"}>
                    {m.role === "user" ? "ты" : "оркестратор"}:{" "}
                  </span>
                  <span className="whitespace-pre-wrap text-white/80">{m.content}</span>
                </div>
              ))
            )}
          </div>
          <div className="flex items-center gap-2 px-3 pb-2">
            <input
              value={chatInput}
              onChange={(e) => setChatInput(e.target.value)}
              onKeyDown={(e) => e.key === "Enter" && void sendChat()}
              disabled={projectId == null || busy}
              placeholder={
                projectId == null ? "сначала открой проект" : "сообщение оркестратору…"
              }
              className="h-8 flex-1 rounded-md border border-white/10 bg-black/40 px-2 text-xs disabled:opacity-40"
            />
            <Button
              size="sm"
              variant="outline"
              disabled={projectId == null || busy || !chatInput.trim()}
              onClick={() => void sendChat()}
              className="gap-1.5 text-xs"
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
