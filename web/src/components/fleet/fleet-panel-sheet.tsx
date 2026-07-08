"use client";

import { FleetPanel } from "@/components/fleet/fleet-panel";
import {
  Sheet,
  SheetContent,
  SheetDescription,
  SheetHeader,
  SheetTitle,
} from "@/components/ui/sheet";

export function FleetPanelSheet({
  open,
  onOpenChange,
  onOpenProject,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  onOpenProject?: (projectId: number) => void;
}) {
  return (
    <Sheet open={open} onOpenChange={onOpenChange}>
      <SheetContent
        side="right"
        className="flex w-full max-w-[min(96vw,1200px)] flex-col gap-0 p-0 sm:max-w-[min(96vw,1200px)]"
      >
        <SheetHeader className="border-b border-border px-4 py-3">
          <SheetTitle>Сеть · Tailscale</SheetTitle>
          <SheetDescription>
            Станции пайплайна и очередь монтажа. Воркеры должны быть role=agent с FLEET_HUB_URL
            главного ПК.
          </SheetDescription>
        </SheetHeader>
        <div className="min-h-0 flex-1 overflow-hidden">
          <FleetPanel
            onOpenProject={(projectId) => {
              onOpenProject?.(projectId);
              onOpenChange(false);
            }}
          />
        </div>
      </SheetContent>
    </Sheet>
  );
}
