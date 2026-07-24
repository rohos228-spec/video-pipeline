"use client";

import { useEffect, useState } from "react";
import { useMutation, useQuery } from "@tanstack/react-query";
import { toast } from "sonner";
import { AudioLines } from "lucide-react";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Button } from "@/components/ui/button";
import { api } from "@/lib/api";
import { errorMessageFromUnknown } from "@/lib/error-message";

export function AudioAlignDialog({
  open,
  onOpenChange,
  projectId,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  projectId: number | null;
}) {
  const [method, setMethod] = useState("direct");
  const [forceAsr, setForceAsr] = useState(false);
  const [runAssemble, setRunAssemble] = useState(true);
  const [polling, setPolling] = useState(false);
  const [notifiedStatus, setNotifiedStatus] = useState<string | null>(null);

  const methodsQ = useQuery({
    queryKey: ["audio-align-methods"],
    queryFn: () => api.listAudioAlignMethods(),
    enabled: open,
    staleTime: 60_000,
  });

  const statusQ = useQuery({
    queryKey: ["audio-align-status", projectId],
    queryFn: () => api.getAudioAlignStatus(projectId!),
    enabled: open && projectId != null,
    refetchInterval: polling ? 2000 : false,
  });

  useEffect(() => {
    const st = statusQ.data?.job?.status;
    if (st === "running") setPolling(true);
    if (st === "done" || st === "error" || st === "cancelled") {
      setPolling(false);
    }
  }, [statusQ.data?.job?.status]);

  useEffect(() => {
    const st = statusQ.data?.job?.status;
    if (!st || st === "running" || st === "idle") return;
    if (notifiedStatus === st) return;
    setNotifiedStatus(st);
    if (st === "done") {
      const crumbs = statusQ.data?.job?.result?.crumbs;
      toast.success(
        crumbs != null
          ? `Разбор аудио готов (крошки ≤0.1с: ${crumbs})`
          : "Разбор аудио готов",
      );
    } else if (st === "error") {
      toast.error(statusQ.data?.job?.error || "Разбор аудио не удался");
    }
  }, [statusQ.data?.job?.status, statusQ.data?.job?.error, statusQ.data?.job?.result?.crumbs, notifiedStatus]);

  const runMut = useMutation({
    mutationFn: () =>
      api.runAudioAlign(projectId!, {
        method,
        force_asr: forceAsr,
        run_assemble: runAssemble,
      }),
    onSuccess: (res) => {
      if (res.already_running) {
        toast.message("Разбор уже выполняется");
        setPolling(true);
        return;
      }
      setNotifiedStatus(null);
      toast.message(`Методика «${method}» запущена`);
      setPolling(true);
      void statusQ.refetch();
    },
    onError: (e) => toast.error(errorMessageFromUnknown(e)),
  });

  const methods = methodsQ.data?.methods ?? [];
  const busy = statusQ.data?.job?.status === "running" || runMut.isPending;
  const last = statusQ.data?.last;

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-xl">
        <DialogHeader>
          <DialogTitle className="flex items-center gap-2">
            <AudioLines className="h-4 w-4" />
            Разбор аудио → R15
          </DialogTitle>
          <DialogDescription>
            Одна транскрипция (NeMo), пять методик границ кадров. Выбери вариант и
            сравни ролик. По умолчанию слова берутся из words.json без повторного ASR.
          </DialogDescription>
        </DialogHeader>

        {projectId == null ? (
          <p className="text-sm text-muted-foreground">
            Сначала выбери проект в сайдбаре.
          </p>
        ) : (
          <div className="flex flex-col gap-3">
            <div className="flex flex-col gap-2">
              {methods.map((m) => (
                <label
                  key={m.id}
                  className={`flex cursor-pointer gap-3 rounded-lg border px-3 py-2 text-left transition-colors ${
                    method === m.id
                      ? "border-primary bg-primary/10"
                      : "border-border hover:bg-muted/40"
                  }`}
                >
                  <input
                    type="radio"
                    name="audio-align-method"
                    className="mt-1"
                    checked={method === m.id}
                    onChange={() => setMethod(m.id)}
                    disabled={busy}
                  />
                  <span className="min-w-0">
                    <span className="block text-sm font-medium">{m.title}</span>
                    <span className="block text-xs text-muted-foreground">
                      {m.summary}
                    </span>
                  </span>
                </label>
              ))}
              {methodsQ.isLoading ? (
                <p className="text-xs text-muted-foreground">Загрузка методик…</p>
              ) : null}
            </div>

            <label className="flex items-center gap-2 text-sm">
              <input
                type="checkbox"
                checked={forceAsr}
                onChange={(e) => setForceAsr(e.target.checked)}
                disabled={busy}
              />
              Пересчитать ASR заново (медленно)
            </label>
            <label className="flex items-center gap-2 text-sm">
              <input
                type="checkbox"
                checked={runAssemble}
                onChange={(e) => setRunAssemble(e.target.checked)}
                disabled={busy}
              />
              Сразу собрать ролик после записи R15
            </label>

            {last ? (
              <p className="text-[11px] text-muted-foreground">
                Последний прогон: {String(last.method)} · крошки={String(last.crumbs)} ·{" "}
                {String(last.words_source)}
              </p>
            ) : null}
            {statusQ.data?.job?.status === "running" ? (
              <p className="text-xs text-amber-600">Идёт разбор / сборка…</p>
            ) : null}
            {statusQ.data?.job?.status === "error" && statusQ.data.job.error ? (
              <p className="text-xs text-destructive">{statusQ.data.job.error}</p>
            ) : null}
          </div>
        )}

        <DialogFooter>
          <Button variant="ghost" onClick={() => onOpenChange(false)}>
            Закрыть
          </Button>
          <Button
            disabled={projectId == null || busy || !method}
            onClick={() => runMut.mutate()}
          >
            {busy ? "Работает…" : "Запустить"}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
