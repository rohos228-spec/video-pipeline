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
import { gptTextSlotForNode, resolvePromptSlots, type NodePromptSlot } from "@/lib/node-prompts";
import { stepCodeForNodeType } from "@/lib/node-step-map";
import { getNodeSpec } from "@/lib/node-catalog";
import { nodeTypeFromKey } from "@/lib/node-key";
import { shouldShowStopBar } from "@/lib/project-running";
import { isAiControlMode } from "@/lib/control-mode";
import { HitlModal } from "@/components/hitl/hitl-banner";
import { hitlKindForNodeType } from "@/components/canvas/node-hitl-badge";
import { isHitlNodeType } from "@/lib/gpt-text-steps";
import { NodeResultPanel } from "@/components/canvas/node-result-panel";
import {
  resolveNodeResult,
  type NodeResultContext,
} from "@/lib/node-result-resolver";

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
  const [hitlModalId, setHitlModalId] = useState<number | null>(null);
  const [hitlModalOpen, setHitlModalOpen] = useState(false);
  const [resultPanel, setResultPanel] = useState<{ nodeKey: string; nodeType: string } | null>(
    null,
  );
  const suppressStudioOpenUntil = useRef(0);
  const qc = useQueryClient();

  useEffect(() => {
    const onOpen = (ev: Event) => {
      const d = (ev as CustomEvent<{ hitlId: number }>).detail;
      if (d?.hitlId == null) return;
      setHitlModalId(d.hitlId);
      setHitlModalOpen(true);
    };
    window.addEventListener("canvas-open-hitl-modal", onOpen);
    return () => window.removeEventListener("canvas-open-hitl-modal", onOpen);
  }, []);

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
      shouldShowStopBar(q.state.data?.status, q.state.data?.generation_active) ? 1500 : false,
  });

  const hitlList = useQuery({
    queryKey: ["hitl", projectId],
    queryFn: () => api.listProjectHitl(projectId!),
    enabled: projectId != null,
    refetchInterval: 4000,
  });

  const artifacts = useQuery({
    queryKey: ["artifacts", projectId],
    queryFn: () => api.listArtifacts({ project_id: projectId! }),
    enabled: projectId != null,
    refetchInterval: 8000,
  });

  const projectAssets = useQuery({
    queryKey: ["project-assets", projectId, "all"],
    queryFn: () => api.listProjectAssets(projectId!, "all"),
    enabled: projectId != null,
    refetchInterval: 8000,
  });

  const frames = useQuery({
    queryKey: ["frames", projectId],
    queryFn: () => api.listFrames(projectId!),
    enabled: projectId != null,
    refetchInterval: 8000,
  });

  const mediaImages = useQuery({
    queryKey: ["media-review", projectId, "images"],
    queryFn: () => api.listMediaReview(projectId!, "images"),
    enabled: projectId != null,
    refetchInterval: 8000,
  });

  const mediaVideos = useQuery({
    queryKey: ["media-review", projectId, "videos"],
    queryFn: () => api.listMediaReview(projectId!, "videos"),
    enabled: projectId != null,
    refetchInterval: 8000,
  });

  const resultContext = useMemo((): NodeResultContext => {
    const assets = projectAssets.data ?? [];
    const mapMedia = (rows: NonNullable<typeof mediaImages.data>, kind: "images" | "videos") =>
      rows
        .filter((r) => r.preview_url)
        .map((r) => ({
          source: "frame" as const,
          id: String(r.frame_id),
          kind,
          path: r.file_path,
          preview_url: r.preview_url,
          label: `Кадр ${r.number}`,
          frame_id: r.frame_id,
          voiceover: r.voiceover_text,
        }));
    return {
      project: project.data ?? null,
      artifacts: artifacts.data ?? [],
      assets,
      frames: frames.data ?? [],
      mediaImages: mapMedia(mediaImages.data ?? [], "images"),
      mediaVideos: mapMedia(mediaVideos.data ?? [], "videos"),
    };
  }, [project.data, artifacts.data, projectAssets.data, frames.data, mediaImages.data, mediaVideos.data]);

  const getNodeResult = useCallback(
    (nodeType: string) => resolveNodeResult(nodeType, resultContext),
    [resultContext],
  );

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
      const stored = meta.custom_prompts?.[nodeKey];
      return resolvePromptSlots(nodeType, stored);
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
      project: project.data ?? null,
      autoMode: project.data?.auto_mode ?? false,
      aiControl: isAiControlMode((project.data?.meta || {}) as Record<string, unknown>),
      hitlList: hitlList.data ?? [],
      disabledNodes,
      vMenuNodeKey,
      setVMenuNodeKey,
      getPromptSlots,
      getNodeResult,
      onOpenPrompt: (nodeKey: string, nodeType: string, slot: NodePromptSlot) => {
        onSelectNode(nodeKey);
        setPromptFocus(slot);
        setStudioTab(slot.kind === "excel" ? "excel" : "prompts");
        onStudioOpenChange(true);
      },
      onOpenGptText: (nodeKey: string, nodeType: string) => {
        const slot = gptTextSlotForNode(nodeType);
        if (!slot) return;
        onSelectNode(nodeKey);
        setPromptFocus(slot);
        setStudioTab("prompts");
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
        const list = resolvePromptSlots(nodeType, custom[nodeKey]);
        const n = list.filter((s) => s.id.startsWith("custom_")).length + 1;
        list.push({
          id: `custom_${n}`,
          title: `Промт ${n}`,
          kind: "gpt",
          stepCode: stepCodeForNodeType(nodeType),
          custom: true,
        });
        custom[nodeKey] = resolvePromptSlots(nodeType, list);
        await persistMeta({ custom_prompts: custom });
        const step = stepCodeForNodeType(nodeType);
        const slotId = `custom_${n}`;
        if (step) {
          try {
            await api.savePromptFile(
              step,
              slotId,
              `# ${list[list.length - 1]?.title ?? slotId}\n\n`,
            );
          } catch {
            /* файл может уже существовать */
          }
        }
        toast.success("Промт добавлен в схему ноды");
      },
      onRemovePrompt: async (nodeKey: string, nodeType: string, slot: NodePromptSlot) => {
        if (slot.kind === "excel" || slot.id === "excel") {
          toast.error("Excel нельзя удалить — он обязателен первым в каждой ноде");
          return;
        }
        const meta = (project.data?.meta || {}) as {
          custom_prompts?: Record<string, NodePromptSlot[]>;
        };
        const custom = { ...(meta.custom_prompts || {}) };
        const list = resolvePromptSlots(nodeType, custom[nodeKey]).filter(
          (s) => s.id !== slot.id,
        );
        custom[nodeKey] = resolvePromptSlots(nodeType, list);
        await persistMeta({ custom_prompts: custom });
        toast.success("Промт удалён");
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
      onOpenHitlReview: (_nodeKey: string, nodeType: string) => {
        const kind = hitlKindForNodeType(nodeType);
        if (!kind) return;
        const rows = (hitlList.data ?? [])
          .filter((h) => h.kind === kind)
          .sort((a, b) => b.id - a.id);
        const pending = rows.find((h) => h.decision === "pending");
        if (pending) {
          setHitlModalId(pending.id);
          setHitlModalOpen(true);
          return;
        }
        const latest = rows[0];
        if (latest) {
          setHitlModalId(latest.id);
          setHitlModalOpen(true);
          if (latest.decision !== "pending") {
            toast.message("Карточка уже обработана — можно посмотреть решение");
          }
          return;
        }
        toast.info(
          "Ручная проверка: сначала завершите шаг — затем одобрите результат, как в Telegram",
        );
        const trayKind = assetTrayKindForNodeType(nodeType);
        if (trayKind && projectId) {
          setAssetTray({ kind: trayKind, nodeType });
        }
      },
      onOpenHitlById: (hitlId: number) => {
        setHitlModalId(hitlId);
        setHitlModalOpen(true);
      },
      onOpenNodeResult: (nodeKey: string, nodeType: string) => {
        setResultPanel({ nodeKey, nodeType });
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
      project.data,
      getNodeResult,
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
          onNodeActivate={(nodeKey, nodeType) => {
            if (Date.now() < suppressStudioOpenUntil.current) return;
            if (nodeType === "topic") {
              onSelectNode(nodeKey);
              return;
            }
            if (isHitlNodeType(nodeType)) {
              canvasActions.onOpenHitlReview(nodeKey, nodeType);
              return;
            }
            const kind = hitlKindForNodeType(nodeType);
            if (kind) {
              const pending = (hitlList.data ?? []).find(
                (h) => h.kind === kind && h.decision === "pending",
              );
              if (pending) {
                canvasActions.onOpenHitlReview(nodeKey, nodeType);
                return;
              }
            }
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
        <HitlModal
          hitlId={hitlModalId}
          projectId={projectId}
          open={hitlModalOpen}
          onOpenChange={(o) => {
            setHitlModalOpen(o);
            if (!o) setHitlModalId(null);
          }}
        />
      )}
      {projectId != null && resultPanel && (
        <NodeResultPanel
          open
          onOpenChange={(o) => {
            if (!o) setResultPanel(null);
          }}
          projectId={projectId}
          nodeType={resultPanel.nodeType}
          snapshot={getNodeResult(resultPanel.nodeType)}
        />
      )}
    </CanvasActionsProvider>
  );
}
