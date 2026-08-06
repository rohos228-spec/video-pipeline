"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  Bot,
  Loader2,
  Plus,
  SendHorizonal,
  Square,
  Wrench,
  X,
} from "lucide-react";

import {
  api,
  type AgentAutonomy,
  type AgentChoiceCard,
  type AgentMessage,
} from "@/lib/api";
import { cn } from "@/lib/utils";
import { Button } from "@/components/ui/button";

const AUTONOMY_LABEL: Record<AgentAutonomy, string> = {
  advisor: "Советник (только читает)",
  operator: "Оператор (может управлять)",
  autopilot: "Автопилот (включая сбросы)",
};

function ChoiceCardView({
  card,
  onPick,
  busy,
}: {
  card: AgentChoiceCard;
  onPick: (id: string) => void;
  busy: boolean;
}) {
  return (
    <div className="mx-auto mb-4 w-full max-w-xl rounded-xl border border-[#D1FE17]/30 bg-[#111506] p-4">
      <div className="mb-3 text-sm font-semibold text-white">
        {card.question}
      </div>
      <div className="flex flex-col gap-2">
        {card.options.map((o) => (
          <button
            key={o.id}
            disabled={busy}
            onClick={() => onPick(o.id)}
            className={cn(
              "rounded-lg border border-white/10 bg-white/[0.04] px-3 py-2 text-left",
              "transition-colors hover:border-[#D1FE17]/60 hover:bg-[#D1FE17]/10",
              "disabled:opacity-50",
            )}
          >
            <div className="text-sm font-medium text-white">{o.label}</div>
            {o.description ? (
              <div className="mt-0.5 text-xs text-white/50">{o.description}</div>
            ) : null}
          </button>
        ))}
      </div>
    </div>
  );
}

function MessageView({ m }: { m: AgentMessage }) {
  if (m.role === "tool") {
    const status = (m.payload?.status as string) || "ok";
    return (
      <div className="mb-2 flex items-start gap-2 text-xs text-white/45">
        <Wrench className="mt-0.5 h-3 w-3 shrink-0" />
        <span
          className={cn(
            "line-clamp-3 break-all",
            status === "error" && "text-red-300/70",
            status === "denied" && "text-amber-300/70",
          )}
        >
          {m.content}
        </span>
      </div>
    );
  }
  if (m.role === "event") {
    return (
      <div className="mx-auto mb-3 max-w-xl rounded-lg border border-white/[0.08] bg-white/[0.03] px-3 py-1.5 text-center text-xs text-white/50">
        {m.content}
      </div>
    );
  }
  const isUser = m.role === "user";
  return (
    <div
      className={cn(
        "mb-3 max-w-3xl whitespace-pre-wrap rounded-lg border px-3 py-2.5 text-sm",
        isUser
          ? "ml-auto border-white/[0.08] bg-white/[0.04] text-white"
          : "mr-auto border-white/[0.06] bg-[#121212] text-white/90",
      )}
    >
      {m.content}
    </div>
  );
}

export function AgentWorkspace({
  open,
  onOpenChange,
}: {
  open: boolean;
  onOpenChange: (v: boolean) => void;
}) {
  const qc = useQueryClient();
  const [sessionUuid, setSessionUuid] = useState<string | null>(null);
  const [autonomy, setAutonomy] = useState<AgentAutonomy>("operator");
  const [draft, setDraft] = useState("");
  const scrollRef = useRef<HTMLDivElement>(null);

  const sessionsQ = useQuery({
    queryKey: ["agent", "sessions"],
    queryFn: () => api.agentListSessions(),
    enabled: open,
    refetchInterval: open ? 6_000 : false,
  });

  const sessionQ = useQuery({
    queryKey: ["agent", "session", sessionUuid],
    queryFn: () => api.agentGetSession(sessionUuid!),
    enabled: open && !!sessionUuid,
    refetchInterval: (q) => {
      const st = q.state.data?.status;
      return st === "running" ? 1_200 : st === "waiting_choice" ? 4_000 : false;
    },
  });

  const session = sessionQ.data;
  const busy = session?.status === "running";

  const createMut = useMutation({
    mutationFn: () => api.agentCreateSession("", autonomy),
    onSuccess: (s) => {
      setSessionUuid(s.uuid);
      qc.invalidateQueries({ queryKey: ["agent", "sessions"] });
    },
  });

  const askMut = useMutation({
    mutationFn: (text: string) => api.agentAsk(sessionUuid!, text),
    onSettled: () => {
      qc.invalidateQueries({ queryKey: ["agent", "session", sessionUuid] });
      qc.invalidateQueries({ queryKey: ["agent", "sessions"] });
    },
  });

  const answerMut = useMutation({
    mutationFn: (choiceId: string) => api.agentAnswer(sessionUuid!, choiceId),
    onSettled: () => {
      qc.invalidateQueries({ queryKey: ["agent", "session", sessionUuid] });
      qc.invalidateQueries({ queryKey: ["agent", "sessions"] });
    },
  });

  const stopMut = useMutation({
    mutationFn: () => api.agentStop(sessionUuid!),
    onSettled: () =>
      qc.invalidateQueries({ queryKey: ["agent", "session", sessionUuid] }),
  });

  const messages = useMemo(() => session?.messages ?? [], [session]);

  useEffect(() => {
    const el = scrollRef.current;
    if (el) el.scrollTop = el.scrollHeight;
  }, [messages.length, session?.status]);

  const send = async () => {
    const text = draft.trim();
    if (!text) return;
    if (!sessionUuid) {
      const s = await createMut.mutateAsync();
      setDraft("");
      await api.agentAsk(s.uuid, text);
      qc.invalidateQueries({ queryKey: ["agent", "session", s.uuid] });
      qc.invalidateQueries({ queryKey: ["agent", "sessions"] });
      return;
    }
    setDraft("");
    askMut.mutate(text);
  };

  if (!open) return null;

  return (
    <div className="fixed inset-0 z-[80] flex flex-col bg-[#0a0a0a] text-white">
      <div className="flex items-center gap-3 border-b border-white/[0.06] px-4 py-3">
        <Bot className="h-4 w-4 text-[#D1FE17]" />
        <div className="text-sm font-bold">Агент оркестратора</div>
        <select
          value={autonomy}
          onChange={(e) => setAutonomy(e.target.value as AgentAutonomy)}
          className="ml-2 rounded-md border border-white/10 bg-[#141414] px-2 py-1 text-xs text-white/80"
          title="Автономия новых сессий: потолок риска действий агента"
        >
          {(Object.keys(AUTONOMY_LABEL) as AgentAutonomy[]).map((k) => (
            <option key={k} value={k}>
              {AUTONOMY_LABEL[k]}
            </option>
          ))}
        </select>
        <div className="ml-auto flex items-center gap-2">
          {busy ? (
            <Button
              variant="outline"
              size="sm"
              className="gap-1 text-xs"
              onClick={() => stopMut.mutate()}
            >
              <Square className="h-3 w-3" /> Стоп
            </Button>
          ) : null}
          <button
            onClick={() => onOpenChange(false)}
            className="rounded-md p-1.5 text-white/60 hover:bg-white/[0.06] hover:text-white"
          >
            <X className="h-4 w-4" />
          </button>
        </div>
      </div>

      <div className="flex min-h-0 flex-1">
        <div className="flex w-56 shrink-0 flex-col border-r border-white/[0.06]">
          <button
            onClick={() => createMut.mutate()}
            className="m-2 flex items-center gap-2 rounded-md border border-white/10 bg-white/[0.04] px-2 py-1.5 text-xs hover:border-[#D1FE17]/50"
          >
            <Plus className="h-3.5 w-3.5" /> Новая сессия
          </button>
          <div className="flex-1 overflow-y-auto px-2 pb-2">
            {(sessionsQ.data?.sessions ?? []).map((s) => (
              <button
                key={s.uuid}
                onClick={() => setSessionUuid(s.uuid)}
                className={cn(
                  "mb-1 w-full rounded-md border px-2 py-1.5 text-left text-xs",
                  s.uuid === sessionUuid
                    ? "border-[#D1FE17]/50 bg-[#D1FE17]/10"
                    : "border-white/[0.06] bg-white/[0.02] hover:bg-white/[0.05]",
                )}
              >
                <div className="truncate font-medium">{s.title || s.uuid}</div>
                <div className="text-[10px] text-white/40">
                  {s.status} · {s.autonomy}
                </div>
              </button>
            ))}
          </div>
        </div>

        <div className="flex min-w-0 flex-1 flex-col">
          <div ref={scrollRef} className="flex-1 overflow-y-auto px-4 py-4">
            {!sessionUuid ? (
              <div className="mx-auto mt-16 max-w-md text-center text-sm text-white/40">
                Создай сессию и напиши задачу — агент сам поднимет статусы
                проектов, логи и предложит действия кнопками.
              </div>
            ) : null}
            {messages.map((m, i) => (
              <MessageView key={i} m={m} />
            ))}
            {busy ? (
              <div className="mb-3 flex items-center gap-2 text-xs text-white/40">
                <Loader2 className="h-3.5 w-3.5 animate-spin" /> агент думает…
              </div>
            ) : null}
            {session?.status === "waiting_choice" && session.pending_choice ? (
              <ChoiceCardView
                card={session.pending_choice}
                busy={answerMut.isPending}
                onPick={(id) => answerMut.mutate(id)}
              />
            ) : null}
          </div>

          <div className="border-t border-white/[0.06] bg-[#141414] p-3">
            <div className="mx-auto flex max-w-3xl items-end gap-2">
              <textarea
                value={draft}
                onChange={(e) => setDraft(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === "Enter" && !e.shiftKey) {
                    e.preventDefault();
                    void send();
                  }
                }}
                rows={2}
                placeholder={
                  session?.status === "waiting_choice"
                    ? "Сначала ответь на карточку выше…"
                    : "Задача агенту… (Enter — отправить)"
                }
                disabled={!sessionUuid && createMut.isPending}
                className="min-w-0 flex-1 resize-none rounded-lg border border-white/10 bg-[#0d0d0d] px-3 py-2 text-sm outline-none placeholder:text-white/30 focus:border-[#D1FE17]/50"
              />
              <Button
                size="sm"
                className="gap-1 bg-[#D1FE17] text-black hover:bg-[#c4ef0f]"
                disabled={!draft.trim() || busy || session?.status === "waiting_choice"}
                onClick={() => void send()}
              >
                <SendHorizonal className="h-3.5 w-3.5" />
              </Button>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}
