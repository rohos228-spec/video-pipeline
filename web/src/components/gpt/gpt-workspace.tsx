"use client";

/**
 * GPT Workspace — свободный чат через API.
 * История сессий · вложения · обработка · сохранение в проект.
 * Визуально рядом с Генерацией (Outsee): тёмный фон, акцент #D1FE17.
 */

import { useEffect, useMemo, useRef, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  Bot,
  FileText,
  History,
  Loader2,
  Paperclip,
  Plus,
  Save,
  Send,
  Trash2,
  X,
} from "lucide-react";
import { toast } from "sonner";
import {
  api,
  type GptWorkspaceMessage,
  type GptWorkspaceSession,
} from "@/lib/api";
import { errorMessageFromUnknown } from "@/lib/error-message";
import { cn } from "@/lib/utils";

const ACCENT = "#D1FE17";

type Props = {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  projectId: number | null;
};

export function GptWorkspace({ open, onOpenChange, projectId }: Props) {
  const qc = useQueryClient();
  const [sessionId, setSessionId] = useState<string | null>(null);
  const [draft, setDraft] = useState("");
  const [withAttachments, setWithAttachments] = useState(true);
  const fileRef = useRef<HTMLInputElement>(null);
  const bottomRef = useRef<HTMLDivElement>(null);

  const sessionsQ = useQuery({
    queryKey: ["gpt-workspace", "sessions"],
    queryFn: () => api.gptListSessions(),
    enabled: open,
    refetchInterval: open ? 8_000 : false,
  });

  const sessionQ = useQuery({
    queryKey: ["gpt-workspace", "session", sessionId],
    queryFn: () => api.gptGetSession(sessionId!),
    enabled: open && !!sessionId,
  });

  const session: GptWorkspaceSession | undefined = sessionQ.data;

  useEffect(() => {
    if (!open) return;
    bottomRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [open, session?.messages?.length]);

  const createMut = useMutation({
    mutationFn: () => api.gptCreateSession(),
    onSuccess: (s) => {
      setSessionId(s.id);
      void qc.invalidateQueries({ queryKey: ["gpt-workspace", "sessions"] });
    },
    onError: (e) => toast.error(errorMessageFromUnknown(e)),
  });

  const deleteMut = useMutation({
    mutationFn: (id: string) => api.gptDeleteSession(id),
    onSuccess: (_, id) => {
      if (sessionId === id) setSessionId(null);
      void qc.invalidateQueries({ queryKey: ["gpt-workspace", "sessions"] });
    },
    onError: (e) => toast.error(errorMessageFromUnknown(e)),
  });

  const askMut = useMutation({
    mutationFn: async () => {
      let sid = sessionId;
      if (!sid) {
        const s = await api.gptCreateSession();
        sid = s.id;
        setSessionId(sid);
      }
      const msg = draft.trim();
      if (!msg) throw new Error("Введите сообщение");
      setDraft("");
      return api.gptAsk(sid, msg, withAttachments);
    },
    onSuccess: (s) => {
      setSessionId(s.id);
      void qc.invalidateQueries({ queryKey: ["gpt-workspace", "sessions"] });
      void qc.invalidateQueries({ queryKey: ["gpt-workspace", "session", s.id] });
    },
    onError: (e) => toast.error(errorMessageFromUnknown(e)),
  });

  const uploadMut = useMutation({
    mutationFn: async (file: File) => {
      let sid = sessionId;
      if (!sid) {
        const s = await api.gptCreateSession();
        sid = s.id;
        setSessionId(sid);
      }
      return api.gptUploadAttachment(sid, file);
    },
    onSuccess: () => {
      if (sessionId) {
        void qc.invalidateQueries({ queryKey: ["gpt-workspace", "session", sessionId] });
      }
      toast.success("Файл прикреплён");
    },
    onError: (e) => toast.error(errorMessageFromUnknown(e)),
  });

  const saveVoiceMut = useMutation({
    mutationFn: (messageId?: string) => {
      if (!projectId) throw new Error("Выбери проект в сайдбаре");
      if (!sessionId) throw new Error("Нет сессии");
      return api.gptSaveVoiceover(sessionId, {
        project_id: projectId,
        message_id: messageId,
      });
    },
    onSuccess: (r) => toast.success(`Сохранено → ${r.name}`),
    onError: (e) => toast.error(errorMessageFromUnknown(e)),
  });

  const saveFileMut = useMutation({
    mutationFn: (outputName: string) => {
      if (!projectId) throw new Error("Выбери проект в сайдбаре");
      if (!sessionId) throw new Error("Нет сессии");
      const asName =
        outputName.toLowerCase().endsWith(".xlsx") ? "project.xlsx" : undefined;
      return api.gptSaveToProject(sessionId, {
        project_id: projectId,
        output_name: outputName,
        as_name: asName,
      });
    },
    onSuccess: (r) => toast.success(`Сохранено → ${r.name}`),
    onError: (e) => toast.error(errorMessageFromUnknown(e)),
  });

  const busy = askMut.isPending || session?.status === "running";

  const sessions = sessionsQ.data?.sessions ?? [];

  const lastAssistant = useMemo(() => {
    const msgs = session?.messages ?? [];
    for (let i = msgs.length - 1; i >= 0; i--) {
      if (msgs[i].role === "assistant") return msgs[i];
    }
    return null;
  }, [session?.messages]);

  if (!open) return null;

  return (
    <div className="fixed inset-0 z-[80] flex flex-col bg-[#0a0a0a] text-white">
      {/* header */}
      <header className="flex h-12 shrink-0 items-center justify-between border-b border-white/[0.06] px-4">
        <div className="flex items-center gap-3">
          <button
            type="button"
            onClick={() => onOpenChange(false)}
            className="rounded-md p-1.5 text-white/50 hover:bg-white/[0.06] hover:text-white"
            title="Закрыть"
          >
            <X className="h-4 w-4" />
          </button>
          <div className="flex items-center gap-2">
            <Bot className="h-4 w-4" style={{ color: ACCENT }} />
            <span className="text-sm font-semibold tracking-tight">GPT</span>
            <span className="font-mono text-[10px] uppercase tracking-[0.14em] text-white/40">
              api · память сессии
            </span>
          </div>
          {projectId != null && (
            <span className="rounded border border-white/[0.08] bg-white/[0.03] px-2 py-0.5 font-mono text-[10px] text-white/50">
              проект #{projectId}
            </span>
          )}
        </div>
        <div className="flex items-center gap-2">
          <button
            type="button"
            onClick={() => createMut.mutate()}
            disabled={createMut.isPending}
            className="inline-flex items-center gap-1.5 rounded-md border border-white/[0.08] bg-white/[0.03] px-2.5 py-1 text-xs text-white/80 hover:bg-white/[0.06]"
          >
            <Plus className="h-3.5 w-3.5" />
            Новый чат
          </button>
        </div>
      </header>

      <div className="flex min-h-0 flex-1">
        {/* history rail */}
        <aside className="flex w-56 shrink-0 flex-col border-r border-white/[0.06] bg-black/40">
          <div className="flex items-center gap-2 border-b border-white/[0.06] px-3 py-2 text-[10px] uppercase tracking-[0.16em] text-white/40">
            <History className="h-3 w-3" />
            История
          </div>
          <div className="min-h-0 flex-1 overflow-y-auto p-2">
            {sessions.length === 0 && (
              <p className="px-2 py-4 text-xs text-white/35">
                Пока пусто. Напиши сообщение или создай чат.
              </p>
            )}
            {sessions.map((s) => (
              <div
                key={s.id}
                className={cn(
                  "group mb-1 flex items-start gap-1 rounded-md border px-2 py-1.5",
                  sessionId === s.id
                    ? "border-[rgba(209,254,23,0.35)] bg-[rgba(209,254,23,0.08)]"
                    : "border-transparent hover:bg-white/[0.04]",
                )}
              >
                <button
                  type="button"
                  className="min-w-0 flex-1 text-left"
                  onClick={() => setSessionId(s.id)}
                >
                  <div className="truncate text-xs text-white/90">{s.title}</div>
                  <div className="mt-0.5 font-mono text-[10px] text-white/35">
                    {s.message_count} сообщ. · {s.status}
                  </div>
                </button>
                <button
                  type="button"
                  className="opacity-0 group-hover:opacity-100"
                  title="Удалить"
                  onClick={() => deleteMut.mutate(s.id)}
                >
                  <Trash2 className="h-3 w-3 text-white/40 hover:text-red-400" />
                </button>
              </div>
            ))}
          </div>
        </aside>

        {/* main */}
        <div className="flex min-w-0 flex-1 flex-col">
          {/* messages */}
          <div className="min-h-0 flex-1 overflow-y-auto px-6 py-4">
            {!session && (
              <div className="mx-auto mt-24 max-w-lg text-center">
                <Bot className="mx-auto h-10 w-10 text-white/20" />
                <h2 className="mt-4 text-lg font-semibold">Работа с GPT</h2>
                <p className="mt-2 text-sm text-white/45">
                  История чатов уходит в API как контекст (память диалога). Вложения
                  (xlsx/txt/картинки), сохранение ответа в проект.
                </p>
              </div>
            )}
            {session?.messages.map((m) => (
              <MessageBubble
                key={m.id}
                message={m}
                onSaveVoiceover={
                  m.role === "assistant" && projectId != null
                    ? () => saveVoiceMut.mutate(m.id)
                    : undefined
                }
              />
            ))}
            {busy && (
              <div className="mb-4 flex items-center gap-2 text-xs text-white/50">
                <Loader2 className="h-3.5 w-3.5 animate-spin" style={{ color: ACCENT }} />
                GPT обрабатывает…
              </div>
            )}
            <div ref={bottomRef} />
          </div>

          {/* outputs + attachments strip */}
          {session && (session.attachments.length > 0 || session.outputs.length > 0) && (
            <div className="flex shrink-0 gap-4 border-t border-white/[0.06] bg-black/30 px-4 py-2">
              {session.attachments.length > 0 && (
                <div className="min-w-0 flex-1">
                  <div className="mb-1 text-[10px] uppercase tracking-[0.14em] text-white/35">
                    Вложения
                  </div>
                  <div className="flex flex-wrap gap-1.5">
                    {session.attachments.map((f) => (
                      <span
                        key={f.name}
                        className="inline-flex items-center gap-1 rounded border border-white/[0.08] bg-white/[0.03] px-2 py-0.5 text-[11px] text-white/70"
                      >
                        <Paperclip className="h-3 w-3" />
                        {f.name}
                        <button
                          type="button"
                          className="text-white/30 hover:text-red-400"
                          onClick={() =>
                            api
                              .gptDeleteAttachment(session.id, f.name)
                              .then(() =>
                                qc.invalidateQueries({
                                  queryKey: ["gpt-workspace", "session", session.id],
                                }),
                              )
                          }
                        >
                          <X className="h-3 w-3" />
                        </button>
                      </span>
                    ))}
                  </div>
                </div>
              )}
              {session.outputs.length > 0 && (
                <div className="min-w-0 flex-1">
                  <div className="mb-1 text-[10px] uppercase tracking-[0.14em] text-white/35">
                    Результаты
                  </div>
                  <div className="flex flex-wrap gap-1.5">
                    {session.outputs.map((f) => (
                      <span
                        key={f.name}
                        className="inline-flex items-center gap-1 rounded border border-white/[0.08] bg-white/[0.03] px-2 py-0.5 text-[11px]"
                      >
                        <a
                          href={f.url}
                          target="_blank"
                          rel="noreferrer"
                          className="text-white/70 hover:text-white"
                        >
                          {f.name}
                        </a>
                        {projectId != null && (
                          <button
                            type="button"
                            title="Сохранить в проект"
                            className="text-white/35 hover:text-[color:var(--accent)]"
                            style={{ ["--accent" as string]: ACCENT }}
                            onClick={() => saveFileMut.mutate(f.name)}
                          >
                            <Save className="h-3 w-3" />
                          </button>
                        )}
                      </span>
                    ))}
                  </div>
                </div>
              )}
            </div>
          )}

          {/* compose dock */}
          <div className="shrink-0 border-t border-white/[0.06] bg-[#0d0d0d] px-4 py-3">
            <div className="mx-auto flex max-w-3xl flex-col gap-2">
              <div className="flex items-center gap-3 text-[11px] text-white/45">
                <label className="inline-flex cursor-pointer items-center gap-1.5">
                  <input
                    type="checkbox"
                    checked={withAttachments}
                    onChange={(e) => setWithAttachments(e.target.checked)}
                    className="accent-[color:var(--a)]"
                    style={{ ["--a" as string]: ACCENT }}
                  />
                  С вложениями
                </label>
                {lastAssistant && projectId != null && (
                  <button
                    type="button"
                    className="inline-flex items-center gap-1 hover:text-white"
                    onClick={() => saveVoiceMut.mutate(lastAssistant.id)}
                  >
                    <FileText className="h-3 w-3" />
                    Ответ → voiceover.txt
                  </button>
                )}
              </div>
              <div className="flex items-end gap-2">
                <button
                  type="button"
                  onClick={() => fileRef.current?.click()}
                  disabled={uploadMut.isPending}
                  className="rounded-md border border-white/[0.08] p-2.5 text-white/55 hover:bg-white/[0.05] hover:text-white"
                  title="Прикрепить файл"
                >
                  {uploadMut.isPending ? (
                    <Loader2 className="h-4 w-4 animate-spin" />
                  ) : (
                    <Paperclip className="h-4 w-4" />
                  )}
                </button>
                <input
                  ref={fileRef}
                  type="file"
                  className="hidden"
                  onChange={(e) => {
                    const f = e.target.files?.[0];
                    if (f) uploadMut.mutate(f);
                    e.target.value = "";
                  }}
                />
                <textarea
                  value={draft}
                  onChange={(e) => setDraft(e.target.value)}
                  onKeyDown={(e) => {
                    if (e.key === "Enter" && !e.shiftKey) {
                      e.preventDefault();
                      if (!busy && draft.trim()) askMut.mutate();
                    }
                  }}
                  rows={3}
                  placeholder="Сообщение GPT… (Enter — отправить, Shift+Enter — новая строка)"
                  className="min-h-[72px] flex-1 resize-none rounded-lg border border-white/[0.08] bg-[#141414] px-3 py-2 text-sm text-white placeholder:text-white/30 focus:border-[rgba(209,254,23,0.4)] focus:outline-none"
                />
                <button
                  type="button"
                  disabled={busy || !draft.trim()}
                  onClick={() => askMut.mutate()}
                  className="inline-flex h-[72px] w-12 items-center justify-center rounded-lg font-semibold text-black disabled:opacity-40"
                  style={{ backgroundColor: ACCENT }}
                  title="Отправить"
                >
                  {busy ? (
                    <Loader2 className="h-4 w-4 animate-spin" />
                  ) : (
                    <Send className="h-4 w-4" />
                  )}
                </button>
              </div>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}

function MessageBubble({
  message,
  onSaveVoiceover,
}: {
  message: GptWorkspaceMessage;
  onSaveVoiceover?: () => void;
}) {
  const isUser = message.role === "user";
  const isSystem = message.role === "system";
  return (
    <div
      className={cn(
        "mb-4 max-w-3xl rounded-lg border px-3 py-2.5",
        isUser && "ml-auto border-white/[0.08] bg-white/[0.04]",
        !isUser && !isSystem && "mr-auto border-white/[0.06] bg-[#121212]",
        isSystem && "mx-auto border-red-500/20 bg-red-500/5 text-red-200/80",
      )}
    >
      <div className="mb-1 flex items-center justify-between gap-2">
        <span className="font-mono text-[10px] uppercase tracking-[0.14em] text-white/35">
          {message.role}
        </span>
        {onSaveVoiceover && (
          <button
            type="button"
            onClick={onSaveVoiceover}
            className="inline-flex items-center gap-1 text-[10px] text-white/40 hover:text-white"
          >
            <Save className="h-3 w-3" />
            voiceover
          </button>
        )}
      </div>
      <pre className="whitespace-pre-wrap break-words font-sans text-sm leading-relaxed text-white/90">
        {message.content}
      </pre>
      {!!message.attachment_names?.length && (
        <div className="mt-2 text-[10px] text-white/35">
          вложения: {message.attachment_names.join(", ")}
        </div>
      )}
      {!!message.output_files?.length && (
        <div className="mt-1 text-[10px] text-white/35">
          файлы: {message.output_files.join(", ")}
        </div>
      )}
    </div>
  );
}
