"use client";

import { createContext, useContext } from "react";
import type { HITLDTO, ProjectDetail } from "@/lib/types";
import type { NodePromptSlot } from "@/lib/node-prompts";
import type { NodeResultSnapshot } from "@/lib/node-result-resolver";

export type AssetTrayKind = "hero" | "items" | "images" | "videos" | "project";

export interface CanvasActions {
  projectId: number | null;
  project: ProjectDetail | null;
  autoMode: boolean;
  hitlList: HITLDTO[];
  disabledNodes: Set<string>;
  vMenuNodeKey: string | null;
  setVMenuNodeKey: (key: string | null) => void;
  getPromptSlots: (nodeKey: string, nodeType: string) => NodePromptSlot[];
  getNodeResult: (nodeType: string) => NodeResultSnapshot;
  onOpenPrompt: (nodeKey: string, nodeType: string, slot: NodePromptSlot) => void;
  onViewAllPrompts: (nodeKey: string, nodeType: string) => void;
  onAddPrompt: (nodeKey: string, nodeType: string) => void;
  onRunNode: (nodeKey: string, nodeType: string) => void;
  onToggleDisable: (nodeKey: string, disabled: boolean) => void;
  onDeleteNode: (nodeKey: string) => void;
  onDetachNode: (nodeKey: string) => void;
  onOpenAssets: (kind: AssetTrayKind, nodeType: string) => void;
  onDownloadPrompts: (nodeKey: string, nodeType: string) => void;
  onNodeBodyClick: (nodeKey: string, nodeType: string) => void;
  onOpenHitlReview: (nodeKey: string, nodeType: string) => void;
  onOpenNodeResult: (nodeKey: string, nodeType: string) => void;
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

/** Тип ассетов для нижней панели по типу ноды. */
export function assetTrayKindForNodeType(nodeType: string): AssetTrayKind | null {
  if (nodeType === "hero" || nodeType === "hitl_hero") return "hero";
  if (nodeType === "items") return "items";
  if (nodeType === "images" || nodeType === "hitl_images") return "images";
  if (nodeType === "videos" || nodeType === "hitl_videos") return "videos";
  if (
    nodeType === "plan" ||
    nodeType === "script" ||
    nodeType === "split" ||
    nodeType === "publish" ||
    nodeType === "assemble"
  ) {
    return "project";
  }
  return null;
}
