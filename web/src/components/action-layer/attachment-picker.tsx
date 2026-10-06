"use client";
import {useEffect, useId, useRef, useState} from "react";
import {Button} from "@/components/ui/button";
import {deleteAttachment, describeError, getAttachmentRules, uploadAttachment} from "@/lib/action-layer";
import {
  DEFAULT_RULES, MAX_ATTACHMENTS, acceptAttr, allowedList, checkFile, formatSize, itemStatusText, kindOfFile,
  type AttachmentItem, type AttachmentRules,
} from "@/lib/attachment-view";
import {cn} from "@/lib/utils";

type Update = (fn: (prev: AttachmentItem[]) => AttachmentItem[]) => void;

interface Props {
  projectId: number | null;
  items: AttachmentItem[];
  onItems: Update;
  disabled?: boolean;
}

let counter = 0;

/** Кнопка выбора файла для командной строки: проверка типа и размера, прогресс загрузки, «убрать». */
export function AttachmentPicker({projectId, items, onItems, disabled}: Props) {
  const inputId = useId();
  const inputRef = useRef<HTMLInputElement>(null);
  const aborts = useRef(new Map<string, AbortController>());
  const [rules, setRules] = useState<AttachmentRules>(DEFAULT_RULES);
  const [note, setNote] = useState("");

  useEffect(() => {
    let cancelled = false;
    void getAttachmentRules().then((r) => { if (r && !cancelled) setRules(r); });
    return () => { cancelled = true; };
  }, []);

  const patch = (key: string, p: Partial<AttachmentItem>) =>
    onItems((prev) => prev.map((i) => (i.key === key ? {...i, ...p} : i)));

  function start(file: File, pid: number) {
    const key = `att-${Date.now()}-${++counter}`;
    const base: AttachmentItem = {key, name: file.name, size: file.size, status: "uploading", progress: 0, kind: kindOfFile(file.name, rules) ?? undefined};
    const problem = checkFile(file, rules);
    if (problem) {
      onItems((prev) => [...prev, {...base, status: "error", error: problem}]);
      return;
    }
    const ctrl = new AbortController();
    aborts.current.set(key, ctrl);
    onItems((prev) => [...prev, base]);
    uploadAttachment(file, pid, (p) => patch(key, {progress: p}), ctrl.signal)
      .then((info) => patch(key, {status: "ready", progress: 100, attachmentId: info.attachment_id, kind: info.kind, name: info.name, size: info.size}))
      .catch((e: unknown) => { if (!ctrl.signal.aborted) patch(key, {status: "error", error: describeError(e)}); })
      .finally(() => { aborts.current.delete(key); });
  }

  function pick(list: FileList | null) {
    setNote("");
    if (!list || projectId == null) return;
    const files = Array.from(list);
    const room = MAX_ATTACHMENTS - items.length;
    if (files.length > room) setNote(`Можно прикрепить не больше ${MAX_ATTACHMENTS} файлов: лишние пропущены.`);
    files.slice(0, Math.max(0, room)).forEach((f) => start(f, projectId));
    if (inputRef.current) inputRef.current.value = "";
  }

  function remove(item: AttachmentItem) {
    aborts.current.get(item.key)?.abort();
    if (item.status === "ready" && item.attachmentId && projectId != null) {
      void deleteAttachment(item.attachmentId, projectId).catch(() => undefined); // не вышло - сервер уберёт по сроку хранения
    }
    onItems((prev) => prev.filter((i) => i.key !== item.key));
    setNote("");
  }

  const full = items.length >= MAX_ATTACHMENTS;
  return (
    <div className="space-y-1.5" data-testid="attachment-picker">
      <div className="flex flex-wrap items-center gap-2">
        <input ref={inputRef} id={inputId} type="file" multiple className="sr-only" tabIndex={-1}
          accept={acceptAttr(rules)} disabled={disabled || projectId == null || full}
          onChange={(e) => pick(e.target.files)} data-testid="attachment-input" />
        <Button type="button" size="sm" variant="outline" className="h-7 text-xs" disabled={disabled || projectId == null || full}
          onClick={() => inputRef.current?.click()} title={`Можно: ${allowedList(rules)}`}>
          Прикрепить файл
        </Button>
        <span className="text-white/40">
          {items.length === 0 ? `Файл для загрузки в проект: ${allowedList(rules)}` : `Файлов: ${items.length} из ${MAX_ATTACHMENTS}`}
        </span>
      </div>
      {note && <p className="text-amber-200" role="status">{note}</p>}
      {items.length > 0 && (
        <ul className="space-y-1" aria-label="Прикреплённые файлы" data-testid="attachment-list">
          {items.map((i) => (
            <li key={i.key} data-testid="attachment-item" data-status={i.status}
              className={cn("rounded border px-2 py-1", i.status === "error" ? "border-red-400/40" : "border-white/10")}>
              <div className="flex items-center gap-2">
                <span className="min-w-0 flex-1 truncate text-white/85" title={i.name}>{i.name}</span>
                <span className="shrink-0 text-white/45">{formatSize(i.size)}</span>
                <span className={cn("shrink-0", i.status === "error" ? "text-red-300" : i.status === "ready" ? "text-emerald-300" : "text-white/60")}>
                  {i.status === "error" ? "ошибка" : itemStatusText(i)}
                </span>
                <Button type="button" size="sm" variant="ghost" className="h-6 px-2 text-[11px]"
                  aria-label={`Убрать файл ${i.name}`} onClick={() => remove(i)}>
                  Убрать
                </Button>
              </div>
              {i.status === "uploading" && (
                <div className="mt-1 h-1 overflow-hidden rounded bg-white/10" role="progressbar" aria-label={`Загрузка ${i.name}`}
                  aria-valuemin={0} aria-valuemax={100} aria-valuenow={Math.round(i.progress)}>
                  <div className="h-full bg-sky-400/70" style={{width: `${Math.max(2, i.progress)}%`}} />
                </div>
              )}
              {i.status === "error" && <p role="alert" className="mt-0.5 text-red-300">{i.error}</p>}
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
