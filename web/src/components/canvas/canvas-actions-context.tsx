"use client";

import { createContext, useContext } from "react";
import type { NodePromptSlot } from "@/lib/node-prompts";

export type AssetTrayKind = "hero" | "items" | "images" | "videos" | "project";

export interface CanvasActions {
  projectId: number | null;
  disabledNodes: Set<string>;
  onOpenPrompt: (nodeKey: string, nodeType: string, slot: NodePromptSlot) => void;
  onAddPrompt: (nodeKey: string, nodeType: string) => void;
  onRunNode: (nodeKey: string, nodeType: string) => void;
  onToggleDisable: (nodeKey: string, disabled: boolean) => void;
  onDeleteNode: (nodeKey: string) => void;
  onDetachNode: (nodeKey: string) => void;
  onOpenAssets: (kind: AssetTrayKind, nodeType: string) => void;
  onDownloadPrompts: (nodeKey: string, nodeType: string) => void;
}

const Ctx = createContext<CanvasActions | null>(null);

export function CanvasActionsProvider({
  value,
  children,
}: {
  value: CanvasActions;
  children: React.ReactNode;
}) {
  return <Ctx.Provider value={value}>{children}</Ctx.Provider>;
}

export function useCanvasActions(): CanvasActions {
  const v = useContext(Ctx);
  if (!v) {
    throw new Error("useCanvasActions outside provider");
  }
  return v;
}

export function useCanvasActionsOptional(): CanvasActions | null {
  return useContext(Ctx);
}
