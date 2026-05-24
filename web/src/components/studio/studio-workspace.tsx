"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import { FlowCanvas } from "@/components/canvas/flow-canvas";
import {
  assetTrayKindForNodeType,
  CanvasActionsProvider,
  type AssetTrayKind,
} from "@/components/canvas/canvas-actions-context";
import { AiNodeDialog } from "@/components/canvas/ai-node-dialog";
import { AssetTray } from "@/components/studio/asset-tray";
import { NodeStudio } from "@/components/studio/node-studio";
import { api } from "@/lib/api";
import { defaultPromptSlots, type NodePromptSlot } from "@/lib/node-prompts";
import { stepCodeForNodeType } from "@/lib/node-step-map";
import { getNodeSpec } from "@/lib/node-catalog";
import { nodeTypeFromKey } from "@/lib/node-key";
import { isProjectRunningStatus } from "@/lib/project-running";
import { StopGenerationBar } from "@/components/studio/stop-generation-bar";

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
  const [vMenuNodeKey, setVMenuNodeKey] = useState<string | null>(null);
  const [aiOpen, setAiOpen] = useState(false);
  const [aiCtx, setAiCtx] = useState<{ nodeKey: string; nodeType: string } | null>(
    null,
  );
  const suppressStudioOpenUntil = useRef(0);
  const qc = useQueryClient();

  // Слушаем событие "открыть AI-диалог для ноды" (диспатчится из
  // pipeline-node.tsx, когда юзер кликает на фиолетовый кружок справа
  // от выделенной ноды).
  useEffect(() => {
    const onOpen = (ev: Event) => {
      const d = (ev as CustomEvent<{ nodeKey: string; nodeType: string }>).detail;
      if (!d?.nodeKey || !d?.nodeType) return;
      onSelectNode(d.nodeKey);
      setAiCtx({ nodeKey: d.nodeKey, nodeType: d.nodeType });
      setAiOpen(true);
    };
    window.addEventListener("canvas-open-ai-node", onOpen);
    return () => window.removeEventListener("canvas-open-ai-node", onOpen);
  }, [onSelectNode]);

  // Пользователь явно закрыл студию (крестик / backdrop / Esc).
  // 1) Гасим студию.
  // 2) Снимаем выделение ноды — иначе по очередному ре-рендеру / селект-эвенту
  //    React Flow студия может тут же открыться обратно.
  // 3) Подкручиваем suppress-таймер до 1.5 сек, чтобы пережить любые
  //    последующие синтетические клики/select-events.
  const closeStudio = useCallback(() => {
    suppressStudioOpenUntil.current = Date.now() + 1500;
    onStudioOpenChange(false);
    onSelectNode(null);
  }, [onStudioOpenChange, onSelectNode]);

  const project = useQuery({
    queryKey: ["project", projectId],
    queryFn: () => api.getProject(projectId!),
    enabled: projectId != null,
    refetchInterval: (q) =>
      isProjectRunningStatus(q.state.data?.status) ? 1500 : false,
  });

  const generationRunning = isProjectRunningStatus(project.data?.status);

  const hitlList = useQuery({
    queryKey: ["hitl", projectId],
    queryFn: () => api.listProjectHitl(projectId!),
    enabled: projectId != null,
    refetchInterval: 4000,
  });

  useEffect(() => {
    if (!projectId) return;
    api.ensureProjectRun(projectId).catch(() => {
      /* бэкенд может быть недоступен при первом рендере */
    });
  }, [projectId]);

  const disabledNodes = useMemo(() => {
    const meta = (project.data?.meta || {}) as { disabled_nodes?: string[] };
    return new Set(meta.disabled_nodes ?? []);
  }, [project.data?.meta]);

  const getPromptSlots = useCallback(
    (nodeKey: string, nodeType: string): NodePromptSlot[] => {
      const meta = (project.data?.meta || {}) as {
        custom_prompts?: Record<string, NodePromptSlot[]>;
      };
      return meta.custom_prompts?.[nodeKey] ?? defaultPromptSlots(nodeType);
    },
    [project.data?.meta],
  );

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
      autoMode: project.data?.auto_mode ?? false,
      hitlList: hitlList.data ?? [],
      disabledNodes,
      vMenuNodeKey,
      setVMenuNodeKey,
      getPromptSlots,
      onOpenPrompt: (nodeKey: string, nodeType: string, slot: NodePromptSlot) => {
        onSelectNode(nodeKey);
        setPromptFocus(slot);
        setStudioTab(slot.kind === "excel" ? "excel" : "prompts");
        onStudioOpenChange(true);
      },
      onViewAllPrompts: (nodeKey: string, _nodeType: string) => {
        onSelectNode(nodeKey);
        setPromptFocus(null);
        setStudioTab("prompts");
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
        if (disabledNodes.has(nodeKey)) {
          toast.error("Нода отключена — включите её в меню V");
          return;
        }
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
          new CustomEvent("canvas-delete-node", { detail: { nodeKey, autoSave: true } }),
        );
        if (selectedNodeKey === nodeKey) {
          onSelectNode(null);
          onStudioOpenChange(false);
        }
      },
      onDetachNode: (nodeKey: string) => {
        window.dispatchEvent(
          new CustomEvent("canvas-detach-node", { detail: { nodeKey, autoSave: true } }),
        );
        toast.success("Связи ноды откреплены");
      },
      onOpenAssets: (kind: AssetTrayKind, nodeType: string) => {
        if (!projectId) return;
        setAssetTray({ kind, nodeType });
      },
      onNodeBodyClick: (nodeKey: string, nodeType: string) => {
        onSelectNode(nodeKey);
        const kind = assetTrayKindForNodeType(nodeType);
        if (kind && projectId) {
          setAssetTray({ kind, nodeType });
        }
      },
      onDownloadPrompts: async (nodeKey: string, nodeType: string) => {
        if (!projectId) return;
        try {
          const slots = getPromptSlots(nodeKey, nodeType);
          const r = await api.composePrompt({
            node_type: nodeType,
            project_id: projectId,
          });
          const header = slots.map((s, i) => `${i + 1}. ${s.title} (${s.kind})`).join("\n");
          const blob = new Blob([`${header}\n\n---\n\n${r.text}`], {
            type: "text/plain;charset=utf-8",
          });
          const a = document.createElement("a");
          a.href = URL.createObjectURL(blob);
          a.download = `${getNodeSpec(nodeType).label}-промты.txt`;
          a.click();
          toast.success("Промты скачаны");
        } catch (e) {
          toast.error(String(e));
        }
      },
    }),
    [
      projectId,
      project.data?.auto_mode,
      hitlList.data,
      disabledNodes,
      vMenuNodeKey,
      getPromptSlots,
      project.data?.meta,
      persistMeta,
      onSelectNode,
      onStudioOpenChange,
      selectedNodeKey,
      qc,
    ],
  );

  // Esc — единое место закрытия студии с клавиатуры.
  useEffect(() => {
    if (!studioOpen) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        e.preventDefault();
        closeStudio();
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [studioOpen, closeStudio]);

  return (
    <CanvasActionsProvider value={canvasActions}>
      <div className="relative h-full w-full">
        <FlowCanvas
          projectId={projectId}
          selectedNodeKey={selectedNodeKey}
          onSelectNode={(key) => {
            onSelectNode(key);
          }}
          onNodeActivate={(nodeKey) => {
            // Клик в тело ноды открывает Node Studio с вкладкой
            // «Настройки». Suppress-таймер (1.5 сек после закрытия
            // студии) пропускает остаточные синтетические клики /
            // select-events от React Flow, иначе студия моргает —
            // закрылась → тут же открылась.
            if (Date.now() < suppressStudioOpenUntil.current) return;
            onSelectNode(nodeKey);
            setPromptFocus(null);
            setStudioTab("settings");
            onStudioOpenChange(true);
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
        open={studioOpen}
        onOpenChange={(open) => {
          if (!open) closeStudio();
          else onStudioOpenChange(true);
        }}
        projectId={projectId}
        nodeKey={selectedNodeKey}
        initialTab={studioTab}
        promptFocus={promptFocus}
        nodeDisabled={selectedNodeKey != null && disabledNodes.has(selectedNodeKey)}
        promptSlots={
          selectedNodeKey
            ? getPromptSlots(selectedNodeKey, nodeTypeFromKey(selectedNodeKey))
            : []
        }
      />
      {projectId && aiCtx && (
        <AiNodeDialog
          open={aiOpen}
          onOpenChange={setAiOpen}
          projectId={projectId}
          nodeType={aiCtx.nodeType}
          nodeLabel={getNodeSpec(aiCtx.nodeType).label}
        />
      )}
      {projectId != null && (
        <StopGenerationBar
          projectId={projectId}
          visible={generationRunning}
          hint={
            generationRunning
              ? "Прерывает цикл outsee/GPT и откатывает шаг — как ⏹ в меню проекта Telegram"
              : undefined
          }
        />
      )}
    </CanvasActionsProvider>
  );
}
