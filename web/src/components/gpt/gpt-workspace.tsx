"use client";

/**
 * Премиальный Dark UI чат в едином бирюзовом стиле с вкладкой «Генерация»:
 * - Мульти-сессии: история, переименование, удаление, фильтрация
 * - Селектор моделей (GPT 5.6 Sol / Terra / Luna, GPT kie.ai, Kimi K3)
 * - Полноценный Markdown с подсветкой синтаксиса и кнопкой копирования кода
 * - Drag-and-drop вложений (картинки, docx, pdf, xlsx, txt)
 * - Скачивание готовых сгенерированных файлов и ZIP архивов
 */

import React, { useEffect, useMemo, useRef, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  Archive,
  Bot,
  Check,
  ChevronDown,
  ChevronLeft,
  ChevronRight,
  Copy,
  Download,
  Edit2,
  FileCode,
  FileSpreadsheet,
  FileText,
  FolderOutput,
  Image as ImageIcon,
  Loader2,
  MessageSquare,
  Paperclip,
  Plus,
  Search,
  Send,
  Trash2,
  Upload,
  User,
  X,
} from "lucide-react";
import { toast } from "sonner";
import {
  api,
  type GptWorkspaceFile,
  type GptWorkspaceMessage,
  type GptWorkspaceSession,
  type GptWorkspaceSessionSummary,
} from "@/lib/api";
import { errorMessageFromUnknown } from "@/lib/error-message";
import { cn } from "@/lib/utils";
import { MarkdownRenderer } from "./markdown-renderer";

function stemOf(name: string): string {
  const i = name.lastIndexOf(".");
  return i > 0 ? name.slice(0, i) : name;
}

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

function fileLabel(f: GptWorkspaceFile): string {
  return f.display_name || f.name;
}

function isImageFile(f: GptWorkspaceFile | undefined, fallbackName?: string): boolean {
  if (f?.kind === "image") return true;
  if (f?.mime?.startsWith("image/")) return true;
  const n = f ? fileLabel(f) : fallbackName || "";
  return /\.(png|jpe?g|webp|gif|bmp|svg|avif|heic|ico)$/i.test(n);
}

function downloadHref(f: GptWorkspaceFile): string {
  if (f.download_url) return f.download_url;
  return `${f.url}${f.url.includes("?") ? "&" : "?"}download=1`;
}

function triggerDownload(f: GptWorkspaceFile): void {
  const a = document.createElement("a");
  a.href = downloadHref(f);
  a.rel = "noopener";
  document.body.appendChild(a);
  a.click();
  a.remove();
}

function formatBytes(n?: number): string {
  if (n == null || !Number.isFinite(n) || n < 0) return "";
  if (n < 1024) return `${n} B`;
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`;
  return `${(n / (1024 * 1024)).toFixed(1)} MB`;
}

function getFileIcon(name: string) {
  const ext = name.split(".").pop()?.toLowerCase() || "";
  if (["png", "jpg", "jpeg", "webp", "gif", "svg"].includes(ext)) {
    return <ImageIcon className="h-4 w-4 text-[#22d3ee]" />;
  }
  if (["xlsx", "xls", "csv", "tsv"].includes(ext)) {
    return <FileSpreadsheet className="h-4 w-4 text-[#22d3ee]" />;
  }
  if (["py", "ts", "tsx", "js", "jsx", "html", "css", "json"].includes(ext)) {
    return <FileCode className="h-4 w-4 text-[#38bdf8]" />;
  }
  return <FileText className="h-4 w-4 text-amber-400" />;
}

function stripFilesNotice(content: string): string {
  let t = (content || "").trim();
  t = t.replace(
    /\n*—?\s*\n*(?:Studio положила файл[^\n]*(?:\n•[^\n]*)*|Studio вернула[\s\S]*|Готовые файлы:[\s\S]*)$/i,
    "",
  );
  t = t.replace(
    /^(?:Studio положила файл[^\n]*(?:\n•[^\n]*)*|Studio вернула[\s\S]*|Готовые файлы:[\s\S]*)$/i,
    "",
  );
  t = t.replace(/data:(?:image|application)\/[^;,\s]+;base64,[A-Za-z0-9+/=\s]+/gi, "");
  t = t.replace(/\n{3,}/g, "\n\n").trim();
  return t;
}

/** Очистка названия модели от повторов, сайтов и скобок */
function cleanModelTitle(label: string | undefined): string {
  if (!label) return "GPT 5.6 Sol";
  let t = label.trim();
  // If format is like "GPT 5.6 Luna · vibecode.moe (gpt-5.6-luna)" -> extract "GPT 5.6 Luna"
  if (t.includes("·")) {
    t = t.split("·")[0].trim();
  }
  // Strip trailing (id)
  t = t.replace(/\s*\([^)]*\)$/, "").trim();
  return t || "GPT 5.6 Sol";
}

const STARTER_PROMPTS = [
  {
    icon: "🎬",
    title: "Сценарий для видео",
    prompt: "Напиши захватывающий сценарий для 30-секундного рекламного видеоролика. Раздели на 5 ключевых сцен с закадровым голосом и визуальным описанием кадров.",
  },
  {
    icon: "📊",
    title: "Анализ Excel / данных",
    prompt: "Я хочу прикрепить таблицу. Разбери ключевые метрики, выдели закономерности и подготовь структурированный отчет с выводами.",
  },
  {
    icon: "🎨",
    title: "Промпты для визуала",
    prompt: "Составь 5 детальных фотореалистичных промптов для генерации кадров в кинематографичном стиле 8k, с описанием света, композиции и камеры.",
  },
  {
    icon: "💡",
    title: "Креативные концепты",
    prompt: "Предложи 3 вирусные креативные идеи для коротких вертикальных видео (Shorts/Reels), привлекающих внимание с первых 3 секунд.",
  },
];

type Props = {
  open: boolean;
  onOpenChange: (open: boolean) => void;
};

export function GptWorkspace({ open, onOpenChange }: Props) {
  const qc = useQueryClient();
  const [sessionId, setSessionId] = useState<string | null>(null);
  const [draft, setDraft] = useState("");
  const [withAttachments, setWithAttachments] = useState(true);
  const [sidebarOpen, setSidebarOpen] = useState(true);
  const [searchQuery, setSearchQuery] = useState("");
  const [editingTitleId, setEditingTitleId] = useState<string | null>(null);
  const [editTitleValue, setEditTitleValue] = useState("");
  const [dragOver, setDragOver] = useState(false);
  const [modelPickerOpen, setModelPickerOpen] = useState(false);
  const [copiedMessageId, setCopiedMessageId] = useState<string | null>(null);

  const fileInputRef = useRef<HTMLInputElement>(null);
  const textareaRef = useRef<HTMLTextAreaElement>(null);
  const messagesEndRef = useRef<HTMLDivElement>(null);
  const knownOutputsRef = useRef<Set<string>>(new Set());
  const seededSessionRef = useRef<string | null>(null);

  // Active Text LLM Status & Catalog
  const textLlmQ = useQuery({
    queryKey: ["text-llm-status"],
    queryFn: () => fetch("/api/text-llm", { cache: "no-store" }).then((r) => (r.ok ? r.json() : null)),
    enabled: open,
    refetchInterval: 10_000,
  });

  const sessionsQ = useQuery({
    queryKey: ["gpt-workspace", "sessions"],
    queryFn: () => api.gptListSessions(),
    enabled: open,
    refetchInterval: open ? 6_000 : false,
  });

  const sessionQ = useQuery({
    queryKey: ["gpt-workspace", "session", sessionId],
    queryFn: () => api.gptGetSession(sessionId!),
    enabled: open && !!sessionId,
    refetchInterval: (q) => (q.state.data?.status === "running" ? 1_500 : false),
  });

  const session: GptWorkspaceSession | undefined = sessionQ.data;
  const sessions: GptWorkspaceSessionSummary[] = sessionsQ.data?.sessions ?? [];

  // Auto select first session if none selected
  useEffect(() => {
    if (open && !sessionId && sessions.length > 0) {
      setSessionId(sessions[0].id);
    }
  }, [open, sessionId, sessions]);

  // Track outputs for auto-download if needed
  useEffect(() => {
    if (!session) return;
    if (seededSessionRef.current !== session.id) {
      seededSessionRef.current = session.id;
      knownOutputsRef.current = new Set(
        session.outputs.map((f) => `${session.id}:${f.name}:${f.size}`),
      );
    }
  }, [session]);

  // Auto scroll to bottom
  useEffect(() => {
    if (!open) return;
    messagesEndRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [open, session?.messages?.length, session?.phase_detail]);

  // Auto-resize textarea
  useEffect(() => {
    if (textareaRef.current) {
      textareaRef.current.style.height = "auto";
      textareaRef.current.style.height = `${Math.min(textareaRef.current.scrollHeight, 180)}px`;
    }
  }, [draft]);

  // Session Mutations
  const createMut = useMutation({
    mutationFn: () => api.gptCreateSession(),
    onSuccess: (s) => {
      setSessionId(s.id);
      void qc.invalidateQueries({ queryKey: ["gpt-workspace", "sessions"] });
      setTimeout(() => textareaRef.current?.focus(), 100);
    },
    onError: (e) => toast.error(errorMessageFromUnknown(e)),
  });

  const renameMut = useMutation({
    mutationFn: ({ id, title }: { id: string; title: string }) => api.gptRenameSession(id, title),
    onSuccess: (s) => {
      setEditingTitleId(null);
      void qc.invalidateQueries({ queryKey: ["gpt-workspace", "sessions"] });
      void qc.invalidateQueries({ queryKey: ["gpt-workspace", "session", s.id] });
      toast.success("Чат переименован");
    },
    onError: (e) => toast.error(errorMessageFromUnknown(e)),
  });

  const deleteMut = useMutation({
    mutationFn: (id: string) => api.gptDeleteSession(id),
    onSuccess: (_, id) => {
      if (sessionId === id) {
        const remaining = sessions.filter((s) => s.id !== id);
        setSessionId(remaining[0]?.id || null);
      }
      void qc.invalidateQueries({ queryKey: ["gpt-workspace", "sessions"] });
      toast.success("Чат удален");
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
      toast.success(n > 1 ? `Прикреплено файлов: ${n}` : "Файл прикреплен");
    },
    onError: (e) => toast.error(errorMessageFromUnknown(e)),
  });

  const deleteAttachMut = useMutation({
    mutationFn: ({ sid, name }: { sid: string; name: string }) => api.gptDeleteAttachment(sid, name),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ["gpt-workspace", "session", sessionId] });
      toast.success("Вложение удалено");
    },
    onError: (e) => toast.error(errorMessageFromUnknown(e)),
  });

  const selectModelMut = useMutation({
    mutationFn: async ({ provider, modelId }: { provider: string; modelId: string }) => {
      const r = await fetch("/api/text-llm", {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ provider, model_id: modelId }),
      });
      if (!r.ok) throw new Error(await r.text());
      return r.json();
    },
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ["text-llm-status"] });
      setModelPickerOpen(false);
      toast.success("Модель успешно изменена");
    },
    onError: (e) => toast.error(errorMessageFromUnknown(e)),
  });

  const busy = askMut.isPending || session?.status === "running";
  const phaseLabel = session?.phase_detail || (busy ? "Генерация ответа…" : "");

  const [elapsedSec, setElapsedSec] = useState(0);
  useEffect(() => {
    if (!busy) {
      setElapsedSec(0);
      return;
    }
    const t = window.setInterval(() => setElapsedSec((s) => s + 1), 1000);
    return () => window.clearInterval(t);
  }, [busy]);

  const filesByName = useMemo(() => {
    const map = new Map<string, GptWorkspaceFile>();
    for (const f of session?.attachments ?? []) map.set(f.name, f);
    for (const f of session?.outputs ?? []) map.set(f.name, f);
    return map;
  }, [session?.attachments, session?.outputs]);

  const filteredSessions = useMemo(() => {
    if (!searchQuery.trim()) return sessions;
    const q = searchQuery.toLowerCase();
    return sessions.filter((s) => (s.title || "").toLowerCase().includes(q));
  }, [sessions, searchQuery]);

  const handleKeyDown = (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      if (!busy && (draft.trim() || (session?.attachments?.length ?? 0) > 0)) {
        askMut.mutate();
      }
    }
  };

  const handleDrop = (e: React.DragEvent) => {
    e.preventDefault();
    setDragOver(false);
    if (e.dataTransfer.files?.length) {
      const files = Array.from(e.dataTransfer.files);
      uploadMut.mutate(files);
    }
  };

  const copyToClipboard = async (text: string, msgId: string) => {
    try {
      await navigator.clipboard.writeText(text);
      setCopiedMessageId(msgId);
      setTimeout(() => setCopiedMessageId(null), 2000);
    } catch {
      // ignore
    }
  };

  if (!open) return null;

  const activeModelDisplay = cleanModelTitle(textLlmQ.data?.active_label);

  return (
    <div
      className="fixed inset-0 z-[80] flex bg-[#0a0a0a] text-white font-sans backdrop-blur-2xl animate-in fade-in duration-200"
      onDragOver={(e) => {
        e.preventDefault();
        setDragOver(true);
      }}
      onDragLeave={(e) => {
        if (e.currentTarget.contains(e.relatedTarget as Node)) return;
        setDragOver(false);
      }}
      onDrop={handleDrop}
    >
      {/* Drag overlay */}
      {dragOver && (
        <div className="absolute inset-0 z-50 flex flex-col items-center justify-center bg-zinc-950/80 backdrop-blur-md border-2 border-dashed border-[#22d3ee]/80 animate-in fade-in">
          <Upload className="h-12 w-12 text-[#22d3ee] animate-bounce mb-3" />
          <h3 className="text-lg font-bold text-white">Перетащите файлы сюда</h3>
          <p className="text-sm text-white/60">Изображения, таблицы Excel, документы PDF/Word, скрипты</p>
        </div>
      )}

      {/* ─── LEFT SIDEBAR ─────────────────────────────────────────── */}
      <aside
        className={cn(
          "flex flex-col border-r border-white/[0.08] bg-[#121216]/90 transition-all duration-200 ease-in-out shrink-0",
          sidebarOpen ? "w-72" : "w-0 overflow-hidden border-r-0"
        )}
      >
        {/* Sidebar Header */}
        <div className="flex items-center justify-between border-b border-white/[0.08] p-3.5">
          <div className="flex items-center gap-2">
            <div className="flex h-7 w-7 items-center justify-center rounded-lg bg-[#22d3ee]/15 border border-[#22d3ee]/30 text-[#22d3ee]">
              <Bot className="h-4 w-4" />
            </div>
            <span className="text-sm font-bold tracking-tight text-white">ИИ Чат</span>
          </div>
          <button
            type="button"
            onClick={() => createMut.mutate()}
            disabled={createMut.isPending}
            className="flex items-center gap-1.5 rounded-lg border border-[#22d3ee]/40 bg-[#22d3ee]/10 px-2.5 py-1.5 text-xs font-semibold text-[#22d3ee] transition hover:bg-[#22d3ee]/20 shadow-sm outline-none focus:outline-none focus-visible:outline-none ring-0"
            title="Создать новый диалог"
          >
            <Plus className="h-3.5 w-3.5" />
            <span>Новый чат</span>
          </button>
        </div>

        {/* Search */}
        <div className="p-3 border-b border-white/[0.06]">
          <div className="relative">
            <Search className="absolute left-2.5 top-2.5 h-3.5 w-3.5 text-white/40" />
            <input
              type="text"
              placeholder="Поиск диалогов..."
              value={searchQuery}
              onChange={(e) => setSearchQuery(e.target.value)}
              className="h-8 w-full rounded-lg border border-white/10 bg-white/[0.04] pl-8 pr-3 text-xs text-white placeholder:text-white/40 focus:border-[#22d3ee]/50 focus:outline-none focus:ring-1 focus:ring-[#22d3ee]/30 outline-none"
            />
          </div>
        </div>

        {/* Sessions List */}
        <div className="flex-1 overflow-y-auto p-2 space-y-1">
          {filteredSessions.length === 0 ? (
            <div className="p-4 text-center text-xs text-white/40">
              {searchQuery ? "Ничего не найдено" : "Пока нет диалогов. Создайте новый чат!"}
            </div>
          ) : (
            filteredSessions.map((s) => {
              const isSelected = s.id === sessionId;
              const isEditing = editingTitleId === s.id;

              return (
                <div
                  key={s.id}
                  onClick={() => {
                    if (!isEditing) setSessionId(s.id);
                  }}
                  className={cn(
                    "group relative flex items-center justify-between rounded-xl px-3 py-2.5 text-xs transition-all cursor-pointer outline-none",
                    isSelected
                      ? "bg-white/[0.08] text-white font-medium shadow-sm border border-white/15"
                      : "text-white/60 hover:bg-white/[0.04] hover:text-white border border-transparent"
                  )}
                >
                  <div className="flex items-center gap-2.5 min-w-0 flex-1">
                    <MessageSquare className={cn("h-3.5 w-3.5 shrink-0", isSelected ? "text-[#22d3ee]" : "text-white/40")} />
                    {isEditing ? (
                      <input
                        type="text"
                        autoFocus
                        value={editTitleValue}
                        onChange={(e) => setEditTitleValue(e.target.value)}
                        onBlur={() => {
                          if (editTitleValue.trim()) {
                            renameMut.mutate({ id: s.id, title: editTitleValue.trim() });
                          } else {
                            setEditingTitleId(null);
                          }
                        }}
                        onKeyDown={(e) => {
                          if (e.key === "Enter") {
                            if (editTitleValue.trim()) {
                              renameMut.mutate({ id: s.id, title: editTitleValue.trim() });
                            }
                          } else if (e.key === "Escape") {
                            setEditingTitleId(null);
                          }
                        }}
                        className="h-6 w-full rounded border border-[#22d3ee] bg-[#16161b] px-1.5 text-xs text-white focus:outline-none outline-none ring-0"
                      />
                    ) : (
                      <span className="truncate" title={s.title}>
                        {s.title || "Диалог"}
                      </span>
                    )}
                  </div>

                  {!isEditing && (
                    <div className="flex items-center gap-1 opacity-0 group-hover:opacity-100 transition-opacity">
                      <button
                        type="button"
                        onClick={(e) => {
                          e.stopPropagation();
                          setEditingTitleId(s.id);
                          setEditTitleValue(s.title || "");
                        }}
                        className="rounded p-1 text-white/50 hover:bg-white/10 hover:text-white outline-none"
                        title="Переименовать"
                      >
                        <Edit2 className="h-3 w-3" />
                      </button>
                      <button
                        type="button"
                        onClick={(e) => {
                          e.stopPropagation();
                          if (confirm("Удалить этот чат?")) {
                            deleteMut.mutate(s.id);
                          }
                        }}
                        className="rounded p-1 text-white/50 hover:bg-red-950/60 hover:text-red-400 outline-none"
                        title="Удалить"
                      >
                        <Trash2 className="h-3 w-3" />
                      </button>
                    </div>
                  )}
                </div>
              );
            })
          )}
        </div>

        {/* Sidebar Footer */}
        <div className="border-t border-white/[0.08] p-3 bg-[#121216] text-xs text-white/60 flex items-center justify-between">
          <div className="flex items-center gap-2 truncate">
            <span className="h-2 w-2 rounded-full bg-[#22d3ee] animate-pulse" />
            <span className="truncate font-mono text-[11px] text-white/80">{activeModelDisplay}</span>
          </div>
          <button
            type="button"
            onClick={() => setSidebarOpen(false)}
            className="rounded-lg p-1.5 text-white/50 hover:bg-white/10 hover:text-white outline-none"
            title="Свернуть боковую панель"
          >
            <ChevronLeft className="h-4 w-4" />
          </button>
        </div>
      </aside>

      {/* ─── MAIN CHAT VIEW ───────────────────────────────────────── */}
      <main className="flex flex-1 flex-col min-w-0 bg-[#0a0a0a]">
        {/* Top Navigation Bar */}
        <header className="flex h-14 shrink-0 items-center justify-between border-b border-white/[0.08] bg-[#121216]/80 px-4 backdrop-blur-xl">
          <div className="flex items-center gap-3 min-w-0">
            {!sidebarOpen && (
              <button
                type="button"
                onClick={() => setSidebarOpen(true)}
                className="rounded-lg border border-white/10 bg-white/[0.04] p-1.5 text-white/60 hover:bg-white/10 hover:text-white outline-none"
                title="Развернуть историю"
              >
                <ChevronRight className="h-4 w-4" />
              </button>
            )}

            <div className="flex items-center gap-2 min-w-0">
              <span className="truncate text-sm font-semibold text-white">
                {session?.title || "Новый диалог"}
              </span>
            </div>
          </div>

          <div className="flex items-center gap-2">
            {/* Model Selector Dropdown Button (Clean, compact, no sparkles, turquoise style) */}
            <div className="relative">
              <button
                type="button"
                onClick={() => setModelPickerOpen(!modelPickerOpen)}
                className="flex items-center gap-2 rounded-xl border border-white/15 bg-[#16161b] px-3 py-1.5 text-xs font-semibold text-white transition hover:border-[#22d3ee]/50 hover:bg-white/[0.08] shadow-sm outline-none focus:outline-none focus-visible:outline-none ring-0"
              >
                <span>{activeModelDisplay}</span>
                <ChevronDown className="h-3.5 w-3.5 text-white/50" />
              </button>

              {/* Model Picker Popover Menu */}
              {modelPickerOpen && (
                <div className="absolute right-0 top-full mt-2 z-50 w-80 rounded-2xl border border-white/15 bg-[#16161b]/98 p-2.5 shadow-2xl backdrop-blur-2xl animate-in fade-in zoom-in-95 duration-150 ring-1 ring-white/10">
                  <div className="px-2.5 py-1.5 text-[11px] font-bold uppercase tracking-wider text-[#22d3ee] border-b border-white/[0.08] mb-1 flex items-center justify-between">
                    <span>Выберите ИИ модель</span>
                    <span className="text-[10px] text-white/40 font-mono">vibecode / kie</span>
                  </div>
                  <div className="max-h-96 overflow-y-auto space-y-2 pr-1">
                    {["OpenAI", "Google", "DeepSeek", "KIE"].map((groupName) => {
                      const groupModels = (textLlmQ.data?.models || []).filter(
                        (m: any) => (m.group || (m.provider === "kie" ? "KIE" : "OpenAI")) === groupName
                      );
                      if (!groupModels.length) return null;
                      return (
                        <div key={groupName} className="space-y-0.5">
                          <div className="px-2.5 pt-1.5 pb-1 font-mono text-[10px] font-bold uppercase tracking-wider text-white/35">
                            {groupName === "Google"
                              ? "✨ Google Gemini"
                              : groupName === "DeepSeek"
                                ? "🧠 DeepSeek"
                                : groupName === "OpenAI"
                                  ? "⚡ OpenAI (GPT 5.6)"
                                  : "🌐 KIE API"}
                          </div>
                          {groupModels.map((m: any) => {
                            const active = m.active;
                            return (
                              <button
                                key={m.id}
                                type="button"
                                onClick={() => {
                                  selectModelMut.mutate({ provider: m.provider, modelId: m.id });
                                  setModelPickerOpen(false);
                                }}
                                className={cn(
                                  "flex w-full items-center justify-between rounded-xl px-2.5 py-1.5 text-left text-xs transition outline-none",
                                  active
                                    ? "bg-[#22d3ee]/15 text-[#22d3ee] font-semibold border border-[#22d3ee]/35 shadow-[0_0_12px_rgba(34,211,238,0.15)]"
                                    : "text-white/80 hover:bg-white/[0.06] hover:text-white border border-transparent"
                                )}
                              >
                                <div className="min-w-0 flex-1">
                                  <div className="truncate font-medium flex items-center gap-1.5">
                                    <span>{m.label}</span>
                                    {m.id.includes("3.7") && (
                                      <span className="rounded bg-[#22d3ee] px-1 py-0.2 font-mono text-[9px] font-extrabold text-black">
                                        NEW
                                      </span>
                                    )}
                                  </div>
                                  <div className="text-[10px] text-white/40">{m.site}</div>
                                </div>
                                {active && <Check className="h-4 w-4 text-[#22d3ee] shrink-0 ml-2" />}
                              </button>
                            );
                          })}
                        </div>
                      );
                    })}
                  </div>
                </div>
              )}
            </div>

            {/* ZIP Download button if session has outputs */}
            {(session?.outputs?.length ?? 0) > 0 && (
              <a
                href={api.gptOutputsZipUrl(session!.id)}
                download
                className="flex items-center gap-1.5 rounded-xl border border-white/15 bg-white/[0.04] px-3 py-1.5 text-xs font-semibold text-white transition hover:bg-white/[0.08] outline-none"
                title="Скачать все сгенерированные файлы архивом"
              >
                <Archive className="h-3.5 w-3.5 text-[#22d3ee]" />
                <span>ZIP ({session!.outputs.length})</span>
              </a>
            )}

            {/* Close Button */}
            <button
              type="button"
              onClick={() => onOpenChange(false)}
              className="rounded-xl border border-white/10 bg-white/[0.04] p-2 text-white/60 transition hover:bg-white/10 hover:text-white outline-none"
              title="Закрыть (Esc)"
            >
              <X className="h-4 w-4" />
            </button>
          </div>
        </header>

        {/* ─── MESSAGES SCROLL AREA ─────────────────────────────────── */}
        <div className="flex-1 overflow-y-auto px-4 py-6 md:px-8 lg:px-16 space-y-6">
          {/* If chat has no messages → Starter Prompts Screen */}
          {(!session?.messages || session.messages.length === 0) ? (
            <div className="flex flex-col items-center justify-center min-h-[50vh] text-center max-w-2xl mx-auto">
              <div className="flex h-16 w-16 items-center justify-center rounded-2xl bg-[#16161b] border border-white/15 shadow-2xl mb-5">
                {/* eslint-disable-next-line @next/next/no-img-element */}
                <img src="/icon.svg" alt="Studio" className="h-10 w-10 shrink-0 rounded-lg shadow-sm" />
              </div>
              <h2 className="text-2xl font-bold tracking-tight text-white mb-2">
                Чем я могу помочь сегодня?
              </h2>
              <p className="text-xs text-white/50 mb-8 max-w-md leading-relaxed">
                Свободный ИИ-чат для сценариев, идей, анализа Excel/PDF и генерации документов. Прикрепляйте файлы через скрепку или перетаскиванием.
              </p>

              <div className="grid grid-cols-1 sm:grid-cols-2 gap-3 w-full">
                {STARTER_PROMPTS.map((item, idx) => (
                  <button
                    key={idx}
                    type="button"
                    onClick={() => {
                      setDraft(item.prompt);
                      textareaRef.current?.focus();
                    }}
                    className="flex flex-col items-start rounded-2xl border border-white/10 bg-white/[0.02] p-4 text-left transition-all hover:border-[#22d3ee]/40 hover:bg-white/[0.05] hover:shadow-lg group outline-none"
                  >
                    <div className="text-xl mb-2">{item.icon}</div>
                    <div className="text-xs font-bold text-white group-hover:text-[#22d3ee] transition-colors">
                      {item.title}
                    </div>
                    <div className="text-[11px] text-white/45 line-clamp-2 mt-1">
                      {item.prompt}
                    </div>
                  </button>
                ))}
              </div>
            </div>
          ) : (
            /* Render message list */
            session.messages.map((m, idx) => {
              const isUser = m.role === "user";
              const isSystem = m.role === "system";
              const cleanContent = stripFilesNotice(m.content);
              const hasOutputs = (m.output_files?.length ?? 0) > 0;

              return (
                <div
                  key={m.id || idx}
                  className={cn(
                    "flex gap-3.5 max-w-4xl mx-auto",
                    isUser ? "justify-end" : "justify-start"
                  )}
                >
                  {/* Assistant Avatar */}
                  {!isUser && (
                    <div className="flex h-8 w-8 shrink-0 items-center justify-center rounded-xl bg-[#22d3ee]/15 border border-[#22d3ee]/30 text-[#22d3ee] shadow-sm mt-0.5">
                      <Bot className="h-4 w-4" />
                    </div>
                  )}

                  {/* Message Bubble Container */}
                  <div
                    className={cn(
                      "flex flex-col min-w-0 max-w-[85%] rounded-2xl p-4 shadow-md",
                      isUser
                        ? "bg-[#164e63] text-white rounded-br-sm border border-[#22d3ee]/30"
                        : isSystem
                        ? "bg-red-950/40 border border-red-800/50 text-red-200"
                        : "bg-[#16161b] border border-white/10 text-white rounded-bl-sm"
                    )}
                  >
                    {/* User attachments tags */}
                    {isUser && m.attachment_names && m.attachment_names.length > 0 && (
                      <div className="mb-2.5 flex flex-wrap gap-1.5">
                        {m.attachment_names.map((name) => {
                          const f = resolveFile(filesByName, name);
                          const isImg = isImageFile(f, name);
                          return (
                            <div
                              key={name}
                              className="flex items-center gap-1.5 rounded-lg bg-black/30 px-2.5 py-1 text-[11px] font-medium text-[#22d3ee] border border-white/10"
                            >
                              {isImg && f ? (
                                // eslint-disable-next-line @next/next/no-img-element
                                <img src={f.url} alt="" className="h-3.5 w-3.5 rounded object-cover" />
                              ) : (
                                <Paperclip className="h-3 w-3 text-[#22d3ee]" />
                              )}
                              <span className="truncate max-w-[140px]">{name}</span>
                            </div>
                          );
                        })}
                      </div>
                    )}

                    {/* Message Body */}
                    {isUser ? (
                      <div className="text-xs leading-relaxed whitespace-pre-wrap selection:bg-black/40 font-medium">
                        {m.content}
                      </div>
                    ) : (
                      <div className="text-xs leading-relaxed">
                        <MarkdownRenderer content={cleanContent} />
                      </div>
                    )}

                    {/* Generated Outputs File Cards */}
                    {hasOutputs && (
                      <div className="mt-3.5 pt-3 border-t border-white/10 space-y-2">
                        <div className="flex items-center gap-1.5 text-[11px] font-bold uppercase tracking-wider text-[#22d3ee]">
                          <FolderOutput className="h-3.5 w-3.5" />
                          <span>Сгенерированные файлы ({m.output_files!.length})</span>
                        </div>
                        <div className="grid grid-cols-1 sm:grid-cols-2 gap-2">
                          {m.output_files!.map((name) => {
                            const f = resolveFile(filesByName, name);
                            const label = f ? fileLabel(f) : name;
                            const size = formatBytes(f?.size);
                            const isImg = isImageFile(f, name);

                            return (
                              <div
                                key={name}
                                className="flex items-center justify-between gap-2 rounded-xl border border-white/15 bg-black/40 p-2.5 shadow-sm"
                              >
                                <div className="flex items-center gap-2 min-w-0">
                                  {isImg && f ? (
                                    // eslint-disable-next-line @next/next/no-img-element
                                    <img src={f.url} alt="" className="h-8 w-8 rounded-lg object-cover bg-black" />
                                  ) : (
                                    <div className="flex h-8 w-8 shrink-0 items-center justify-center rounded-lg bg-white/[0.06]">
                                      {getFileIcon(label)}
                                    </div>
                                  )}
                                  <div className="min-w-0 flex-1">
                                    <div className="truncate text-xs font-semibold text-white" title={label}>
                                      {label}
                                    </div>
                                    {size && <div className="font-mono text-[10px] text-white/40">{size}</div>}
                                  </div>
                                </div>

                                {f && (
                                  <button
                                    type="button"
                                    onClick={() => triggerDownload(f)}
                                    className="flex items-center gap-1 rounded-lg bg-[#22d3ee]/15 border border-[#22d3ee]/30 px-2 py-1 text-[11px] font-semibold text-[#22d3ee] hover:bg-[#22d3ee]/25 transition shadow-sm outline-none"
                                    title="Скачать файл"
                                  >
                                    <Download className="h-3 w-3" />
                                    <span>Скачать</span>
                                  </button>
                                )}
                              </div>
                            );
                          })}
                        </div>
                      </div>
                    )}

                    {/* Assistant Message Actions */}
                    {!isUser && (
                      <div className="mt-3 flex items-center justify-between pt-2 border-t border-white/[0.06] text-[11px] text-white/40">
                        <span>{m.at ? new Date(m.at).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" }) : ""}</span>
                        <button
                          type="button"
                          onClick={() => copyToClipboard(cleanContent, m.id || String(idx))}
                          className="flex items-center gap-1 rounded-md px-1.5 py-0.5 text-white/60 hover:bg-white/10 hover:text-white transition outline-none"
                          title="Скопировать текст ответа"
                        >
                          {copiedMessageId === (m.id || String(idx)) ? (
                            <>
                              <Check className="h-3 w-3 text-[#22d3ee]" />
                              <span className="text-[#22d3ee]">Скопировано</span>
                            </>
                          ) : (
                            <>
                              <Copy className="h-3 w-3" />
                              <span>Копировать</span>
                            </>
                          )}
                        </button>
                      </div>
                    )}
                  </div>

                  {/* User Avatar */}
                  {isUser && (
                    <div className="flex h-8 w-8 shrink-0 items-center justify-center rounded-xl bg-white/[0.08] border border-white/15 text-white shadow-sm mt-0.5">
                      <User className="h-4 w-4" />
                    </div>
                  )}
                </div>
              );
            })
          )}

          {/* Thinking / Running Banner */}
          {busy && (
            <div className="flex gap-3.5 max-w-4xl mx-auto animate-in fade-in">
              <div className="flex h-8 w-8 shrink-0 items-center justify-center rounded-xl bg-[#22d3ee]/15 border border-[#22d3ee]/30 text-[#22d3ee] animate-pulse">
                <Bot className="h-4 w-4" />
              </div>
              <div className="flex items-center gap-3 rounded-2xl border border-white/15 bg-[#16161b] px-4 py-3 shadow-md">
                <Loader2 className="h-4 w-4 animate-spin text-[#22d3ee]" />
                <div className="flex flex-col gap-0.5">
                  <span className="text-xs font-semibold text-white">{phaseLabel}</span>
                  <span className="text-[10px] text-white/40 font-mono">Прошло: {elapsedSec} сек</span>
                </div>
              </div>
            </div>
          )}

          <div ref={messagesEndRef} />
        </div>

        {/* ─── BOTTOM INPUT CONTAINER (Style like Generation workspace) ──────── */}
        <div className="p-4 md:px-8 lg:px-16 border-t border-white/[0.08] bg-[#0a0a0a]">
          <div className="max-w-4xl mx-auto flex flex-col gap-2">
            {/* Attachment preview chips above input */}
            {(session?.attachments?.length ?? 0) > 0 && (
              <div className="flex flex-wrap gap-2 pb-1">
                {session!.attachments.map((f) => {
                  const isImg = isImageFile(f);
                  const label = fileLabel(f);
                  const size = formatBytes(f.size);

                  return (
                    <div
                      key={f.name}
                      className="group flex items-center gap-2 rounded-xl border border-white/15 bg-[#16161b] px-2.5 py-1.5 text-xs text-white shadow-sm"
                    >
                      {isImg ? (
                        // eslint-disable-next-line @next/next/no-img-element
                        <img src={f.url} alt="" className="h-5 w-5 rounded object-cover bg-black" />
                      ) : (
                        getFileIcon(label)
                      )}
                      <span className="truncate max-w-[150px] font-medium" title={label}>
                        {label}
                      </span>
                      {size && <span className="font-mono text-[10px] text-white/40">{size}</span>}
                      <button
                        type="button"
                        onClick={() => deleteAttachMut.mutate({ sid: session!.id, name: f.name })}
                        className="text-white/40 hover:text-red-400 transition outline-none"
                        title="Удалить вложение"
                      >
                        <X className="h-3 w-3" />
                      </button>
                    </div>
                  );
                })}
              </div>
            )}

            {/* Input Box Card (styled with smooth subtle borders, no glowing borders) */}
            <div className="relative flex flex-col rounded-2xl border border-white/15 bg-[#121216]/95 backdrop-blur-2xl shadow-[0_20px_60px_rgba(0,0,0,0.85)] ring-1 ring-white/10 transition-all">
              <textarea
                ref={textareaRef}
                value={draft}
                disabled={busy}
                onChange={(e) => setDraft(e.target.value)}
                onKeyDown={handleKeyDown}
                rows={1}
                style={{ outline: "none" }}
                placeholder="Спросите что угодно или перетащите файл (Enter — отправить, Shift+Enter — перенос строки)..."
                className="w-full resize-none bg-transparent px-4 pt-3.5 pb-2 text-xs text-white placeholder:text-white/40 border-0 outline-none ring-0 focus:outline-none focus:ring-0 focus:border-0 min-h-[44px] max-h-[180px] leading-relaxed"
              />

              {/* Bottom action bar inside container */}
              <div className="flex items-center justify-between px-3 pb-2.5 pt-1 border-t border-white/[0.08]">
                <div className="flex items-center gap-2">
                  {/* Attach Button */}
                  <input
                    ref={fileInputRef}
                    type="file"
                    multiple
                    className="hidden"
                    onChange={(e) => {
                      if (e.target.files?.length) {
                        const files = Array.from(e.target.files);
                        uploadMut.mutate(files);
                        e.target.value = "";
                      }
                    }}
                  />
                  <button
                    type="button"
                    onClick={() => fileInputRef.current?.click()}
                    disabled={busy || uploadMut.isPending}
                    className="flex items-center gap-1.5 rounded-xl border border-white/15 bg-white/[0.04] px-2.5 py-1 text-xs font-medium text-white/80 hover:bg-white/10 hover:text-white transition shadow-sm outline-none"
                    title="Прикрепить файлы (картинки, Excel, PDF, Word, скрипты)"
                  >
                    {uploadMut.isPending ? (
                      <Loader2 className="h-3.5 w-3.5 animate-spin text-[#22d3ee]" />
                    ) : (
                      <Paperclip className="h-3.5 w-3.5 text-[#22d3ee]" />
                    )}
                    <span>Прикрепить</span>
                  </button>

                  {/* With Attachments Toggle */}
                  <button
                    type="button"
                    onClick={() => setWithAttachments(!withAttachments)}
                    className={cn(
                      "flex items-center gap-1.5 rounded-xl px-2.5 py-1 text-[11px] font-medium transition outline-none",
                      withAttachments
                        ? "border border-[#22d3ee]/40 bg-[#22d3ee]/15 text-[#22d3ee]"
                        : "border border-white/10 bg-white/[0.02] text-white/40"
                    )}
                    title="Передавать ли вложения в запрос модели"
                  >
                    <Check className={cn("h-3 w-3", withAttachments ? "opacity-100" : "opacity-0")} />
                    <span>Вложения активны</span>
                  </button>
                </div>

                <div className="flex items-center gap-2">
                  <span className="text-[10px] text-white/40 hidden sm:inline font-mono">
                    Shift+Enter для переноса
                  </span>

                  {/* Send Button (styled in turquoise/cyan like Outsee Create primary button) */}
                  <button
                    type="button"
                    onClick={() => {
                      if (!busy && (draft.trim() || (session?.attachments?.length ?? 0) > 0)) {
                        askMut.mutate();
                      }
                    }}
                    disabled={busy || (!draft.trim() && (session?.attachments?.length ?? 0) === 0)}
                    className={cn(
                      "flex h-8 w-8 items-center justify-center rounded-xl transition-all shadow-md outline-none",
                      draft.trim() || (session?.attachments?.length ?? 0) > 0
                        ? "bg-[#22d3ee] hover:bg-[#06b6d4] text-black shadow-[#22d3ee]/20 cursor-pointer scale-105 font-bold"
                        : "bg-white/[0.06] text-white/30 cursor-not-allowed opacity-60"
                    )}
                    title="Отправить сообщение"
                  >
                    {busy ? (
                      <Loader2 className="h-4 w-4 animate-spin text-black" />
                    ) : (
                      <Send className="h-3.5 w-3.5" />
                    )}
                  </button>
                </div>
              </div>
            </div>
          </div>
        </div>
      </main>
    </div>
  );
}
