"use client";

import { useCallback, useMemo, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import { FlowCanvas } from "@/components/canvas/flow-canvas";
import {
  CanvasActionsProvider,
  type AssetTrayKind,
} from "@/components/canvas/canvas-actions-context";
import { AssetTray } from "@/components/studio/asset-tray";
import { NodeStudio } from "@/components/studio/node-studio";
import { api } from "@/lib/api";
import { defaultPromptSlots, type NodePromptSlot } from "@/lib/node-prompts";
import { stepCodeForNodeType } from "@/lib/node-step-map";
import { getNodeSpec } from "@/lib/node-catalog";

export function StudioWorkspace({
  projectId,
  selectedNodeKey,
  onSelectNode,
  studioOpen,
  onStudioOpenChange,
}: {
  projectId: number | null;
  selectedNodeKey: string | null;
  onSelectNode: (key: string | null) => void;
  studioOpen: boolean;
  onStudioOpenChange: (open: boolean) => void;
}) {
  const [assetTray, setAssetTray] = useState<{ kind: AssetTrayKind; nodeType: string } | null>(
    null,
  );
  const [promptFocus, setPromptFocus] = useState<NodePromptSlot | null>(null);
  const [studioTab, setStudioTab] = useState<"settings" | "prompts" | "results" | "excel">(
    "settings",
  );
  const qc = useQueryClient();

  const project = useQuery({
    queryKey: ["project", projectId],
    queryFn: () => api.getProject(projectId!),
    enabled: projectId != null,
  });

  const disabledNodes = useMemo(() => {
    const meta = (project.data?.meta || {}) as { disabled_nodes?: string[] };
    return new Set(meta.disabled_nodes ?? []);
  }, [project.data?.meta]);

  const persistMeta = useCallback(
    async (patch: Record<string, unknown>) => {
      if (!projectId) return;
      const meta = { ...((project.data?.meta || {}) as Record<string, unknown>), ...patch };
      await api.patchProject(projectId, { meta });
      await qc.invalidateQueries({ queryKey: ["project", projectId] });
    },
    [projectId, project.data?.meta, qc],
  );

  const canvasActions = useMemo(
    () => ({
      projectId,
      disabledNodes,
      onOpenPrompt: (nodeKey: string, nodeType: string, slot: NodePromptSlot) => {
        onSelectNode(nodeKey);
        setPromptFocus(slot);
        setStudioTab(slot.kind === "excel" ? "excel" : "prompts");
        onStudioOpenChange(true);
      },
      onAddPrompt: async (nodeKey: string, nodeType: string) => {
        const meta = (project.data?.meta || {}) as {
          custom_prompts?: Record<string, NodePromptSlot[]>;
        };
        const custom = { ...(meta.custom_prompts || {}) };
        const list = [...(custom[nodeKey] || defaultPromptSlots(nodeType))];
        const n = list.filter((s) => s.id.startsWith("custom_")).length + 1;
        list.push({
          id: `custom_${n}`,
          title: `Промт ${n}`,
          kind: "gpt",
          stepCode: stepCodeForNodeType(nodeType),
        });
        custom[nodeKey] = list;
        await persistMeta({ custom_prompts: custom });
        toast.success("Промт добавлен в схему ноды");
      },
      onRunNode: async (nodeKey: string, nodeType: string) => {
        if (!projectId) return;
        const step = stepCodeForNodeType(nodeType);
        if (!step) {
          toast.error("У этой ноды нет шага для запуска");
          return;
        }
        try {
          await api.runProjectStep(projectId, step);
          toast.success(`Запущен: ${getNodeSpec(nodeType).label}`);
          qc.invalidateQueries({ queryKey: ["project", projectId] });
        } catch (e) {
          toast.error(String(e));
        }
      },
      onToggleDisable: async (nodeKey: string, disabled: boolean) => {
        const next = new Set(disabledNodes);
        if (disabled) next.add(nodeKey);
        else next.delete(nodeKey);
        await persistMeta({ disabled_nodes: [...next] });
        toast.message(disabled ? "Нода отключена" : "Нода включена");
      },
      onDeleteNode: (nodeKey: string) => {
        window.dispatchEvent(
          new CustomEvent("canvas-delete-node", { detail: { nodeKey } }),
        );
        if (selectedNodeKey === nodeKey) {
          onSelectNode(null);
          onStudioOpenChange(false);
        }
        toast.success("Нода удалена — сохраните граф");
      },
      onDetachNode: (nodeKey: string) => {
        window.dispatchEvent(new CustomEvent("canvas-detach-node", { detail: { nodeKey } }));
        toast.success("Связи ноды откреплены");
      },
      onOpenAssets: (kind: AssetTrayKind) => {
        if (!projectId) return;
        setAssetTray({ kind, nodeType: kind });
      },
      onDownloadPrompts: async (nodeKey: string, nodeType: string) => {
        if (!projectId) return;
        try {
          const r = await api.composePrompt({
            node_type: nodeType,
            project_id: projectId,
          });
          const blob = new Blob([r.text], { type: "text/plain;charset=utf-8" });
          const a = document.createElement("a");
          a.href = URL.createObjectURL(blob);
          a.download = `${nodeType}-prompts.txt`;
          a.click();
          toast.success("Промты скачаны");
        } catch (e) {
          toast.error(String(e));
        }
      },
    }),
    [
      projectId,
      disabledNodes,
      project.data?.meta,
      persistMeta,
      onSelectNode,
      onStudioOpenChange,
      selectedNodeKey,
      qc,
    ],
  );

  return (
    <CanvasActionsProvider value={canvasActions}>
      <div className="relative h-full w-full">
        <FlowCanvas
          projectId={projectId}
          selectedNodeKey={selectedNodeKey}
          onSelectNode={(key) => {
            onSelectNode(key);
            if (key) {
              setPromptFocus(null);
              setStudioTab("settings");
              onStudioOpenChange(true);
            }
          }}
          disabledNodes={disabledNodes}
        />
        {projectId && assetTray && (
          <AssetTray
            projectId={projectId}
            kind={assetTray.kind}
            onClose={() => setAssetTray(null)}
          />
        )}
      </div>
      <NodeStudio
        open={studioOpen && selectedNodeKey != null}
        onOpenChange={onStudioOpenChange}
        projectId={projectId}
        nodeKey={selectedNodeKey}
        initialTab={studioTab}
        promptFocus={promptFocus}
      />
    </CanvasActionsProvider>
  );
}
