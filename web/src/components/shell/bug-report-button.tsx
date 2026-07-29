"use client";

import { useEffect, useState } from "react";
import { useMutation, useQuery } from "@tanstack/react-query";
import { Bug, Loader2 } from "lucide-react";
import { toast } from "sonner";
import { api } from "@/lib/api";
import { errorMessageFromUnknown } from "@/lib/error-message";
import { CLIENT_STUDIO_VERSION } from "@/lib/studio-version";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Textarea } from "@/components/ui/textarea";
import { cn } from "@/lib/utils";

const WINDOWS = [1, 5, 30, 60] as const;

export function BugReportButton({
  projectId,
  projectSlug,
}: {
  projectId?: number | null;
  projectSlug?: string | null;
}) {
  const [open, setOpen] = useState(false);
  const [description, setDescription] = useState("");
  const [minutes, setMinutes] = useState<(typeof WINDOWS)[number]>(5);

  const preview = useQuery({
    queryKey: ["bug-report-preview", minutes],
    queryFn: () => api.previewBugReport(minutes),
    enabled: open,
  });

  const save = useMutation({
    mutationFn: () =>
      api.createBugReport({
        description,
        minutes,
        projectId: projectId ?? undefined,
        projectSlug: projectSlug ?? undefined,
        studioVersion: CLIENT_STUDIO_VERSION,
      }),
    onSuccess: async (res) => {
      try {
        await navigator.clipboard.writeText(res.clipboardPrompt);
        toast.success(`Сохранено: ${res.rel} · промпт в буфере`);
      } catch {
        toast.success(`Сохранено: ${res.rel}`);
      }
      setDescription("");
      setOpen(false);
    },
    onError: (e) => toast.error(errorMessageFromUnknown(e)),
  });

  useEffect(() => {
    if (!open) return;
    const onKey = (ev: KeyboardEvent) => {
      if (ev.key === "Escape") setOpen(false);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open]);

  return (
    <>
      <Button
        variant="outline"
        size="sm"
        onClick={() => setOpen(true)}
        className="gap-2 border-rose-400/35 text-xs text-rose-100 hover:border-rose-400/55 hover:bg-rose-500/10"
        title="Багрепорт: описание + логи → папка баги/"
      >
        <Bug className="h-3.5 w-3.5" />
        Баг
      </Button>

      <Dialog open={open} onOpenChange={setOpen}>
        <DialogContent className="max-h-[90vh] max-w-lg overflow-y-auto border-rose-400/20">
          <DialogHeader>
            <DialogTitle>Багрепорт</DialogTitle>
            <DialogDescription>
              Описание + хвост логов сохраняются в{" "}
              <span className="font-mono text-foreground">баги/</span> с датой и
              временем. Промпт для Composer копируется в буфер.
            </DialogDescription>
          </DialogHeader>

          <label className="block text-xs font-medium text-muted-foreground">
            Что сломалось
            <Textarea
              className="mt-1.5 min-h-[110px] text-sm"
              placeholder="Шаги, ожидание, что увидели…"
              value={description}
              onChange={(e) => setDescription(e.target.value)}
            />
          </label>

          <div>
            <p className="text-xs font-medium text-muted-foreground">Окно логов</p>
            <div className="mt-1.5 flex flex-wrap gap-1.5">
              {WINDOWS.map((m) => (
                <button
                  key={m}
                  type="button"
                  onClick={() => setMinutes(m)}
                  className={cn(
                    "rounded-md border px-2.5 py-1 text-[11px] font-medium transition",
                    minutes === m
                      ? "border-rose-400/70 bg-rose-500/25 text-rose-50 ring-1 ring-rose-400/40"
                      : "border-white/10 text-muted-foreground hover:border-white/20",
                  )}
                >
                  {minutes === m ? "✓ " : ""}
                  {m} мин
                </button>
              ))}
            </div>
          </div>

          <div className="rounded-lg border border-white/10 bg-black/30 p-2">
            <p className="mb-1 text-[10px] font-semibold uppercase tracking-wider text-muted-foreground">
              Превью логов
            </p>
            {preview.isLoading ? (
              <div className="flex items-center gap-2 py-4 text-xs text-muted-foreground">
                <Loader2 className="h-3.5 w-3.5 animate-spin" />
                Читаю логи…
              </div>
            ) : (
              <div className="max-h-40 space-y-2 overflow-y-auto font-mono text-[10px] leading-relaxed text-muted-foreground">
                {(preview.data?.files || []).map((f) => (
                  <div key={f.name}>
                    <p className="text-foreground/80">
                      {f.name} · {f.chars} симв.
                    </p>
                    <pre className="whitespace-pre-wrap break-all opacity-80">
                      {f.preview || "(пусто)"}
                    </pre>
                  </div>
                ))}
                {!preview.isLoading && !(preview.data?.files || []).length ? (
                  <p>Логи не найдены в data/</p>
                ) : null}
              </div>
            )}
          </div>

          <div className="flex justify-end gap-2">
            <Button type="button" variant="ghost" onClick={() => setOpen(false)}>
              Отмена
            </Button>
            <Button
              type="button"
              variant="default"
              disabled={!description.trim() || save.isPending}
              onClick={() => save.mutate()}
              className="gap-2"
            >
              {save.isPending ? (
                <Loader2 className="h-3.5 w-3.5 animate-spin" />
              ) : (
                <Bug className="h-3.5 w-3.5" />
              )}
              Сохранить в баги/
            </Button>
          </div>
        </DialogContent>
      </Dialog>
    </>
  );
}
