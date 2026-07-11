"use client";

import { useState } from "react";
import {
  Loader2,
  ArrowRight,
  CheckCircle2,
  XCircle,
  Upload,
  X,
} from "lucide-react";
import { toast } from "sonner";
import type { FleetTransferState } from "@/lib/types";
import { cn } from "@/lib/utils";
import { Button } from "@/components/ui/button";
import { isFleetTransferRunning } from "@/hooks/use-fleet-transfer";

const PHASE_LABEL: Record<string, string> = {
  waiting: "Ожидание — отправка только вручную",
  packing: "Упаковка bundle",
  upload: "Отправка на главный ПК",
  download: "Загрузка с NucBox",
  receive: "Приём bundle на hub",
  send: "Отдача bundle hub",
  done: "Передача завершена",
  error: "Ошибка передачи",
  cancelled: "Остановлено",
};

function phaseTitle(t: FleetTransferState): string {
  if (t.message) return t.message;
  return PHASE_LABEL[t.phase] ?? t.phase;
}

function routeLabel(t: FleetTransferState): string {
  const from = t.source_node || "NucBox";
  const to = t.target
    ? t.target.replace(/^https?:\/\//, "").replace(/:\d+$/, "")
    : "главный ПК";
  if (t.direction === "from_agent") return `${from} → этот ПК`;
  if (t.direction === "to_hub") return `${from} → ${to}`;
  return from;
}

export function FleetTransferBanner({
  transfer,
  onPushToHub,
  onCancelTransfer,
  onDismiss,
}: {
  transfer: FleetTransferState | null;
  onPushToHub?: () => Promise<void>;
  onCancelTransfer?: () => Promise<void>;
  onDismiss?: () => void;
}) {
  const [pushing, setPushing] = useState(false);
  const [closing, setClosing] = useState(false);
  if (!transfer) return null;

  const isActive = transfer.status === "active";
  const isRunning = isFleetTransferRunning(transfer);
  const isError = transfer.status === "error";
  const isDone = transfer.status === "done";
  const showPushBtn =
    Boolean(onPushToHub) &&
    !pushing &&
    !isRunning &&
    (transfer.phase === "waiting" || (isError && transfer.phase === "error"));
  const pct = isDone ? 100 : Math.max(0, Math.min(100, transfer.percent ?? 0));
  const sizeLine =
    (transfer.total_mb ?? 0) > 0
      ? `${(transfer.sent_mb ?? 0).toFixed(0)} / ${(transfer.total_mb ?? 0).toFixed(0)} MB`
      : (transfer.sent_mb ?? 0) > 0
        ? `${(transfer.sent_mb ?? 0).toFixed(0)} MB`
        : null;

  const handleClose = () => {
    if (closing) return;
    if (isRunning && onCancelTransfer) {
      setClosing(true);
      void onCancelTransfer()
        .then(() => {
          toast.message("Передача остановлена");
          onDismiss?.();
        })
        .catch((err) => toast.error(String(err)))
        .finally(() => setClosing(false));
      return;
    }
    onDismiss?.();
  };

  return (
    <div className="pointer-events-none fixed inset-x-0 bottom-8 z-[200] flex justify-center px-4">
      <div
        className={cn(
          "pointer-events-auto relative w-full max-w-xl rounded-xl border px-4 py-3 pr-10 shadow-lg backdrop-blur-md",
          isError
            ? "border-destructive/50 bg-destructive/15"
            : isDone
              ? "border-[hsl(var(--success))]/40 bg-[hsl(var(--success))]/10"
              : "border-primary/40 bg-card/90",
        )}
      >
        <button
          type="button"
          onClick={handleClose}
          disabled={closing}
          className="absolute right-2 top-2 rounded-md p-1 text-muted-foreground transition-colors hover:bg-muted/80 hover:text-foreground disabled:opacity-50"
          title={isRunning ? "Остановить передачу и закрыть" : "Закрыть"}
          aria-label={isRunning ? "Остановить передачу" : "Закрыть"}
        >
          {closing ? (
            <Loader2 className="h-4 w-4 animate-spin" />
          ) : (
            <X className="h-4 w-4" />
          )}
        </button>

        <div className="flex items-start gap-3">
          {isError ? (
            <XCircle className="mt-0.5 h-5 w-5 shrink-0 text-destructive" />
          ) : isDone ? (
            <CheckCircle2 className="mt-0.5 h-5 w-5 shrink-0 text-[hsl(var(--success))]" />
          ) : (
            <Loader2 className="mt-0.5 h-5 w-5 shrink-0 animate-spin text-primary" />
          )}
          <div className="min-w-0 flex-1">
            <div className="flex flex-wrap items-center gap-x-2 gap-y-0.5 text-sm font-medium">
              <span>
                #{transfer.project_id}
                {transfer.slug ? ` · ${transfer.slug}` : ""}
              </span>
              {isRunning ? (
                <span className="text-primary tabular-nums">{pct}%</span>
              ) : null}
            </div>
            <p className="mt-0.5 text-xs text-muted-foreground">{phaseTitle(transfer)}</p>
            <p className="mt-1 flex items-center gap-1 text-[11px] text-foreground/80">
              <ArrowRight className="h-3 w-3 shrink-0 opacity-60" />
              {routeLabel(transfer)}
              {sizeLine ? (
                <span className="ml-2 tabular-nums text-muted-foreground">{sizeLine}</span>
              ) : null}
            </p>
            {isActive ? (
              <div className="mt-2.5 h-2 overflow-hidden rounded-full bg-muted/60">
                <div
                  className="h-full rounded-full bg-gradient-to-r from-primary via-amber-400/90 to-primary transition-all duration-500"
                  style={{ width: `${Math.max(pct, isRunning ? 2 : 1)}%` }}
                />
              </div>
            ) : null}
            {showPushBtn ? (
              <Button
                size="sm"
                className="mt-3 h-8 gap-1.5 text-xs"
                disabled={pushing}
                onClick={() => {
                  if (!onPushToHub) return;
                  setPushing(true);
                  void onPushToHub()
                    .catch((err) => toast.error(String(err)))
                    .finally(() => setPushing(false));
                }}
              >
                <Upload className="h-3.5 w-3.5" />
                Отправить на главный ПК
              </Button>
            ) : null}
          </div>
        </div>
      </div>
    </div>
  );
}
