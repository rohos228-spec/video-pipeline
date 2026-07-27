"use client";

/**
 * GPT Workspace — свободный чат через API.
 * Сам по себе: история · вложения · результаты · скачивание.
 * Без связи с нодами/проектом пайплайна.
 */

import { useEffect, useMemo, useRef, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  Bot,
  Download,
  FileText,
  FolderOutput,
  History,
  Loader2,
  Paperclip,
  Plus,
  Send,
  Trash2,
  X,
} from "lucide-react";
import { toast } from "sonner";
import {
  api,
  type GptWorkspaceFile,
  type GptWorkspaceMessage,
  type GptWorkspaceSession,
} from "@/lib/api";
import { errorMessageFromUnknown } from "@/lib/error-message";
import { cn } from "@/lib/utils";

const ACCENT = "#D1FE17";

function stemOf(name: string): string {
  const i = name.lastIndexOf(".");
  return i > 0 ? name.slice(0, i) : name;
}

/** Найти файл после sniff-rename (.bin → .png) по старому имени из сообщения. */
function resolveFile(
  filesByName: Map<string, GptWorkspaceFile>,
  name: string,
): GptWorkspaceFile | undefined {
  const direct = filesByName.get(name);
  if (direct) return direct;
  const stem = stemOf(name);
  for (const [k, v] of filesByName) {
    if (stemOf(k) === stem) return v;
  }
  return undefined;
}

function isImageName(n: string): boolean {
  return /\.(png|jpe?g|webp|gif)$/i.test(n);
}

type Props = {
  open: boolean;
  onOpenChange: (open: boolean) => void;
};

export function GptWorkspace({ open, onOpenChange }: Props) {
  const qc = useQueryClient();
  const [sessionId, setSessionId] = useState<string | null>(null);
  const [draft, setDraft] = useState("");
  const [withAttachments, setWithAttachments] = useState(true);
  const [dragOver, setDragOver] = useState(false);
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
    refetchInterval: (q) =>
      q.state.data?.status === "running" ? 1_500 : false,
  });

  const session: GptWorkspaceSession | undefined = sessionQ.data;

  const [elapsedSec, setElapsedSec] = useState(0);

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
      // пока ждём — опрашиваем phase
      const poll = window.setInterval(() => {
        void qc.invalidateQueries({ queryKey: ["gpt-workspace", "session", sid] });
      }, 1500);
      try {
        return await api.gptAsk(sid, msg, withAttachments);
      } finally {
        window.clearInterval(poll);
      }
    },
    onSuccess: (s) => {
      setSessionId(s.id);
      void qc.invalidateQueries({ queryKey: ["gpt-workspace", "sessions"] });
      void qc.invalidateQueries({ queryKey: ["gpt-workspace", "session", s.id] });
    },
    onError: (e) => toast.error(errorMessageFromUnknown(e)),
  });

  const uploadMut = useMutation({
    mutationFn: async (fileOrFiles: File | File[]) => {
      let sid = sessionId;
      if (!sid) {
        const s = await api.gptCreateSession();
        sid = s.id;
        setSessionId(sid);
      }
      const result = await api.gptUploadAttachment(sid, fileOrFiles);
      return { sid, result };
    },
    onSuccess: ({ sid, result }) => {
      void qc.invalidateQueries({ queryKey: ["gpt-workspace", "sessions"] });
      void qc.invalidateQueries({ queryKey: ["gpt-workspace", "session", sid] });
      const n = Array.isArray(result) ? result.length : 1;
      toast.success(n > 1 ? `Прикреплено файлов: ${n}` : "Файл прикреплён");
    },
    onError: (e) => toast.error(errorMessageFromUnknown(e)),
  });

  const busy = askMut.isPending || session?.status === "running";
  const phaseLabel =
    session?.phase_detail ||
    (busy ? "GPT думает / ждём ответ…" : "");

  useEffect(() => {
    if (!busy) {
      setElapsedSec(0);
      return;
    }
    setElapsedSec(0);
    const t = window.setInterval(() => setElapsedSec((s) => s + 1), 1000);
    return () => window.clearInterval(t);
  }, [busy]);

  useEffect(() => {
    if (!busy) return;
    bottomRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [busy, phaseLabel]);

  const sessions = sessionsQ.data?.sessions ?? [];

  const filesByName = useMemo(() => {
    const map = new Map<string, GptWorkspaceFile>();
    for (const f of session?.attachments ?? []) map.set(f.name, f);
    for (const f of session?.outputs ?? []) map.set(f.name, f);
    return map;
  }, [session?.attachments, session?.outputs]);

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
              api · сам по себе
            </span>
          </div>
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
          {busy && (
            <div
              className="flex shrink-0 items-center gap-3 border-b border-[rgba(209,254,23,0.25)] bg-[rgba(209,254,23,0.08)] px-4 py-2.5"
              role="status"
              aria-live="polite"
            >
              <Loader2 className="h-4 w-4 shrink-0 animate-spin" style={{ color: ACCENT }} />
              <div className="min-w-0 flex-1">
                <div className="text-xs font-medium text-white/90">
                  {phaseLabel || "GPT думает…"}
                </div>
                <div className="mt-0.5 font-mono text-[10px] text-white/45">
                  идёт обдумывание · {elapsedSec} с
                  {elapsedSec >= 30 ? " · vision/генерация может занять несколько минут" : ""}
                </div>
              </div>
            </div>
          )}
          {/* messages */}
          <div className="min-h-0 flex-1 overflow-y-auto px-6 py-4">
            {!session && (
              <div className="mx-auto mt-24 max-w-lg text-center">
                <Bot className="mx-auto h-10 w-10 text-white/20" />
                <h2 className="mt-4 text-lg font-semibold">Работа с GPT</h2>
                <p className="mt-2 text-sm text-white/45">
                  История, вложения и результаты живут только здесь — без связи
                  с нодами пайплайна. Скачивай файлы ↓ или zip.
                </p>
              </div>
            )}
            {session?.messages.map((m) => (
              <MessageBubble
                key={m.id}
                message={m}
                filesByName={filesByName}
              />
            ))}
            {busy && (
              <div className="mb-4 flex items-center gap-2 text-xs text-white/50">
                <Loader2 className="h-3.5 w-3.5 animate-spin" style={{ color: ACCENT }} />
                {phaseLabel || "GPT обрабатывает…"} · {elapsedSec} с
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
                        <Paperclip className="h-3 w-3 shrink-0" />
                        <a
                          href={f.url}
                          target="_blank"
                          rel="noreferrer"
                          className="max-w-[140px] truncate hover:text-white"
                          title={f.name}
                        >
                          {f.name}
                        </a>
                        <a
                          href={f.download_url || `${f.url}${f.url.includes("?") ? "&" : "?"}download=1`}
                          className="text-white/35 hover:text-white"
                          title="Скачать"
                        >
                          <Download className="h-3 w-3" />
                        </a>
                        <button
                          type="button"
                          className="text-white/30 hover:text-white"
                          title="В Результаты"
                          onClick={() => {
                            void api
                              .gptAttachmentToOutputs(session.id, f.name)
                              .then((out) => {
                                toast.success(`В Результаты → ${out.name || f.name}`);
                                void qc.invalidateQueries({
                                  queryKey: ["gpt-workspace", "session", session.id],
                                });
                              })
                              .catch((e) => toast.error(errorMessageFromUnknown(e)));
                          }}
                        >
                          <FolderOutput className="h-3 w-3" />
                        </button>
                        <button
                          type="button"
                          className="text-white/30 hover:text-red-400"
                          title="Удалить"
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
                          className="max-w-[160px] truncate text-white/70 hover:text-white"
                          title={f.name}
                        >
                          {f.name}
                        </a>
                        <a
                          href={f.download_url || `${f.url}${f.url.includes("?") ? "&" : "?"}download=1`}
                          className="text-white/35 hover:text-white"
                          title="Скачать"
                        >
                          <Download className="h-3 w-3" />
                        </a>
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
              <div className="flex flex-wrap items-center gap-3 text-[11px] text-white/45">
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
                {session && session.outputs.length > 0 && (
                  <a
                    href={api.gptOutputsZipUrl(session.id)}
                    className="inline-flex items-center gap-1 hover:text-white"
                    title="Скачать все Результаты"
                  >
                    <Download className="h-3 w-3" />
                    Скачать всё (.zip)
                  </a>
                )}
              </div>
              <div
                className={cn(
                  "rounded-lg border border-dashed px-2 py-1 transition-colors",
                  dragOver
                    ? "border-[rgba(209,254,23,0.55)] bg-[rgba(209,254,23,0.06)]"
                    : "border-transparent",
                )}
                onDragOver={(e) => {
                  e.preventDefault();
                  setDragOver(true);
                }}
                onDragLeave={() => setDragOver(false)}
                onDrop={(e) => {
                  e.preventDefault();
                  setDragOver(false);
                  const list = Array.from(e.dataTransfer.files || []);
                  if (list.length) uploadMut.mutate(list.length === 1 ? list[0] : list);
                }}
              >
                <div className="flex items-end gap-2">
                  <button
                    type="button"
                    onClick={() => fileRef.current?.click()}
                    disabled={uploadMut.isPending}
                    className="rounded-md border border-white/[0.08] p-2.5 text-white/55 hover:bg-white/[0.05] hover:text-white"
                    title="Прикрепить файлы (несколько)"
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
                    multiple
                    className="hidden"
                    onChange={(e) => {
                      const list = Array.from(e.target.files || []);
                      if (list.length) uploadMut.mutate(list.length === 1 ? list[0] : list);
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
                    placeholder="Сообщение GPT… файлы: скрепка или drag&drop (Enter — отправить)"
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
    </div>
  );
}

function MessageBubble({
  message,
  filesByName,
}: {
  message: GptWorkspaceMessage;
  filesByName: Map<string, GptWorkspaceFile>;
}) {
  const isUser = message.role === "user";
  const isSystem = message.role === "system";
  const attNames = message.attachment_names ?? [];
  // reply_*.txt = копия текста ответа (уже в пузыре) — не показываем как «файл»
  const outNames = (message.output_files ?? []).filter(
    (n) => !/^reply_\d/.test(n),
  );

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
      </div>
      <pre className="whitespace-pre-wrap break-words font-sans text-sm leading-relaxed text-white/90">
        {message.content}
      </pre>
      {attNames.length > 0 && (
        <div className="mt-2 flex flex-wrap gap-2">
          {attNames.map((name) => {
            const f = resolveFile(filesByName, name);
            const label = f?.name || name;
            const href = f?.download_url || f?.url;
            return (
              <div
                key={name}
                className="rounded border border-white/[0.08] bg-black/30 p-1.5"
              >
                {f && isImageName(label) ? (
                  // eslint-disable-next-line @next/next/no-img-element
                  <img
                    src={f.url}
                    alt={label}
                    className="mb-1 max-h-40 max-w-[220px] rounded object-contain"
                  />
                ) : null}
                <div className="flex items-center gap-1.5 text-[10px] text-white/55">
                  <Paperclip className="h-3 w-3" />
                  <span className="max-w-[160px] truncate">{label}</span>
                  {href && (
                    <a
                      href={href}
                      className="text-white/40 hover:text-white"
                      title="Скачать"
                    >
                      <Download className="h-3 w-3" />
                    </a>
                  )}
                </div>
              </div>
            );
          })}
        </div>
      )}
      {outNames.length > 0 && (
        <div className="mt-2">
          <div className="mb-1 text-[10px] uppercase tracking-[0.14em] text-white/30">
            Файлы из ответа
          </div>
          <div className="flex flex-wrap gap-1.5">
          {outNames.map((name) => {
            const f = resolveFile(filesByName, name);
            const label = f?.name || name;
            const href = f?.download_url || f?.url;
            return (
              <span
                key={name}
                className="inline-flex items-center gap-1 rounded border border-white/[0.08] bg-white/[0.03] px-2 py-0.5 text-[10px] text-white/55"
              >
                {f && isImageName(label) ? (
                  // eslint-disable-next-line @next/next/no-img-element
                  <img
                    src={f.url}
                    alt={label}
                    className="max-h-24 max-w-[140px] rounded object-contain"
                  />
                ) : (
                  <FileText className="h-3 w-3" />
                )}
                {href ? (
                  <a href={href} className="hover:text-white">
                    {label}
                  </a>
                ) : (
                  label
                )}
                {href && (
                  <a href={href} title="Скачать">
                    <Download className="h-3 w-3 text-white/35 hover:text-white" />
                  </a>
                )}
              </span>
            );
          })}
          </div>
        </div>
      )}
    </div>
  );
}
