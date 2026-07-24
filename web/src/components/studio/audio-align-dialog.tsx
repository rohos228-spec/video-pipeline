"use client";

import { useEffect, useState } from "react";
import { useMutation, useQuery } from "@tanstack/react-query";
import { toast } from "sonner";
import { AudioLines, Loader2 } from "lucide-react";
import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover";
import { Button } from "@/components/ui/button";
import { api } from "@/lib/api";
import { errorMessageFromUnknown } from "@/lib/error-message";

/** Выбор методики разбора аудио — popover у кнопки в шапке монтажа. */
export function AudioAlignPopover({
  projectId,
  onFinished,
}: {
  projectId: number | null;
  onFinished?: () => void;
}) {
  const [open, setOpen] = useState(false);
  const [method, setMethod] = useState("nemo_auto");
  const [forceAsr, setForceAsr] = useState(false);
  const [runAssemble, setRunAssemble] = useState(false);
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
    enabled: projectId != null && (open || polling),
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
      const dbErr = statusQ.data?.job?.result?.db_frames_error;
      toast.success(
        crumbs != null
          ? `Разбор аудио готов (крошки ≤0.1с: ${crumbs})`
          : "Разбор аудио готов",
      );
      if (typeof dbErr === "string" && dbErr) {
        toast.message("R15 записана; БД кадров занята — доска читает Excel");
      }
      onFinished?.();
    } else if (st === "error") {
      const err = statusQ.data?.job?.error || "Разбор аудио не удался";
      toast.error(
        /database is locked/i.test(String(err))
          ? "SQLite занята — подожди 2–3с и запусти ещё раз"
          : err,
      );
    }
  }, [
    statusQ.data?.job?.status,
    statusQ.data?.job?.error,
    statusQ.data?.job?.result?.crumbs,
    statusQ.data?.job?.result?.db_frames_error,
    notifiedStatus,
    onFinished,
  ]);

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
    <Popover open={open} onOpenChange={setOpen}>
      <PopoverTrigger asChild>
        <Button
          type="button"
          size="sm"
          variant="default"
          className="h-9 gap-1.5 bg-amber-500 text-xs text-black hover:bg-amber-400"
          disabled={!projectId}
          title="5 методик разбора речи (NeMo / паузы) → таймкоды R15"
        >
          {busy ? (
            <Loader2 className="h-4 w-4 animate-spin" />
          ) : (
            <AudioLines className="h-4 w-4" />
          )}
          Разбор аудио
        </Button>
      </PopoverTrigger>
      <PopoverContent
        align="end"
        side="bottom"
        sideOffset={6}
        className="z-[10060] w-[min(96vw,420px)] max-h-[min(80vh,560px)] overflow-y-auto p-3"
        onOpenAutoFocus={(e) => e.preventDefault()}
      >
        <h3 className="mb-1 text-sm font-semibold">Разбор речи → R15</h3>
        <p className="mb-3 text-[11px] text-muted-foreground">
          Разбор озвучки NeMo: каждому кадру — свой отрезок ASR-слов (как в речи). Рекомендуется «auto».
        </p>

        {projectId == null ? (
          <p className="text-xs text-muted-foreground">Проект не выбран</p>
        ) : (
          <div className="flex flex-col gap-2.5">
            <div className="flex flex-col gap-1.5">
              {methods.map((m) => (
                <button
                  key={m.id}
                  type="button"
                  disabled={busy}
                  onClick={() => setMethod(m.id)}
                  className={`rounded-lg border px-2.5 py-2 text-left transition-colors ${
                    method === m.id
                      ? "border-amber-400/70 bg-amber-500/15"
                      : "border-border hover:bg-muted/40"
                  }`}
                >
                  <span className="block text-xs font-medium">{m.title}</span>
                  <span className="mt-0.5 block text-[10px] leading-snug text-muted-foreground">
                    {m.summary}
                  </span>
                </button>
              ))}
              {methodsQ.isLoading ? (
                <p className="text-[11px] text-muted-foreground">Загрузка методик…</p>
              ) : null}
            </div>

            <label className="flex items-center gap-2 text-xs">
              <input
                type="checkbox"
                checked={forceAsr}
                onChange={(e) => setForceAsr(e.target.checked)}
                disabled={busy}
              />
              Пересчитать NeMo заново
            </label>
            <label className="flex items-center gap-2 text-xs">
              <input
                type="checkbox"
                checked={runAssemble}
                onChange={(e) => setRunAssemble(e.target.checked)}
                disabled={busy}
              />
              Сразу собрать ролик
            </label>

            {last ? (
              <p className="text-[10px] text-muted-foreground">
                Последний: {String(last.method)} · крошки={String(last.crumbs)}
              </p>
            ) : null}
            {statusQ.data?.job?.status === "running" ? (
              <p className="text-[11px] text-amber-500">Идёт разбор / сборка…</p>
            ) : null}
            {statusQ.data?.job?.status === "error" && statusQ.data.job.error ? (
              <p className="text-[11px] text-destructive">{statusQ.data.job.error}</p>
            ) : null}

            <Button
              type="button"
              size="sm"
              className="mt-1 h-8 w-full text-xs"
              disabled={projectId == null || busy || !method}
              onClick={() => runMut.mutate()}
            >
              {busy ? "Работает…" : "Запустить"}
            </Button>
          </div>
        )}
      </PopoverContent>
    </Popover>
  );
}
