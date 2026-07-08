"use client";

import { useState } from "react";
import { Loader2, Upload } from "lucide-react";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import { errorMessageFromUnknown } from "@/lib/error-message";
import { fleetPushToHub } from "@/lib/fleet-api";
import {
  FLEET_TRANSFER_PUSH_START,
  optimisticPushTransfer,
} from "@/hooks/use-fleet-transfer";
import { isMontageHandoffPending } from "@/lib/montage-handoff";
import type { ProjectDetail } from "@/lib/types";

export function MontageHandoffCard({ project }: { project: ProjectDetail }) {
  const [pushing, setPushing] = useState(false);
  if (!isMontageHandoffPending(project)) return null;

  const startPush = () => {
    setPushing(true);
    window.dispatchEvent(
      new CustomEvent(FLEET_TRANSFER_PUSH_START, {
        detail: optimisticPushTransfer(project.id, project.slug),
      }),
    );
    void fleetPushToHub(project.id)
      .then((res) => {
        if ("started" in res && res.started) {
          toast.message("Отправка идёт — полоска прогресса внизу экрана");
          return;
        }
        toast.success(
          res.size_mb
            ? `Отправлено на главный ПК (${res.size_mb} MB)`
            : "Отправлено на главный ПК",
        );
      })
      .catch((e) => toast.error(errorMessageFromUnknown(e)))
      .finally(() => setPushing(false));
  };

  return (
    <div className="rounded-lg border border-primary/50 bg-primary/10 p-3">
      <p className="text-[11px] font-medium text-foreground">Готово к отправке на главный ПК</p>
      <p className="mt-1 text-[10px] leading-relaxed text-muted-foreground">
        Клипы и музыка на NucBox. Монтаж FFmpeg — на hub. Полоска прогресса — внизу экрана.
      </p>
      {pushing ? (
        <div className="mt-2.5">
          <div className="mb-1 flex justify-between text-[10px] text-muted-foreground">
            <span>Отправка на hub…</span>
            <span className="tabular-nums">смотри полоску внизу</span>
          </div>
          <div className="h-1.5 overflow-hidden rounded-full bg-muted/60">
            <div className="h-full w-1/3 animate-pulse rounded-full bg-primary" />
          </div>
        </div>
      ) : null}
      <Button
        type="button"
        size="sm"
        className="mt-2.5 h-8 w-full gap-1.5 text-xs"
        disabled={pushing}
        onClick={startPush}
      >
        {pushing ? (
          <Loader2 className="h-3.5 w-3.5 animate-spin" />
        ) : (
          <Upload className="h-3.5 w-3.5" />
        )}
        Отправить на главный ПК
      </Button>
    </div>
  );
}
