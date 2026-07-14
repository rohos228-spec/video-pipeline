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
import { errorMessageFromUnknown } from "@/lib/error-message";
import { gptTextSlotForNode, resolvePromptSlots, resolvePromptSlotsForNode, type NodePromptSlot } from "@/lib/node-prompts";
import { isExcelGptNode, EXCEL_GPT_STEP_CODE } from "@/lib/excel-gpt-config";
import { withSlotVariant } from "@/lib/prompt-slot-storage";
import { stepCodeForNodeType } from "@/lib/node-step-map";
import { getNodeSpec } from "@/lib/node-catalog";
import { nodeTypeFromKey } from "@/lib/node-key";
import { shouldShowStopBar } from "@/lib/project-running";
import { isAiControlMode } from "@/lib/control-mode";
import { HitlModal } from "@/components/hitl/hitl-banner";
import { hitlKindForNodeType } from "@/components/canvas/node-hitl-badge";
import { NodeAiReviewDialog } from "@/components/canvas/node-ai-review-dialog";
import { isHitlNodeType } from "@/lib/gpt-text-steps";
import {
  resolveNodeResult,
  type NodeResultContext,
} from "@/lib/node-result-resolver";
import { NodeResultPanel } from "@/components/canvas/node-result-panel";
import { AssembleMontageBoard } from "@/components/canvas/assemble-montage-board";
import { PromptBuilderStudio } from "@/components/prompt-builder/prompt-builder-studio";

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
  /** Синхронный target студии — без гонки с selectedNodeKey из setState. */
  const [studioTarget, setStudioTarget] = useState<{
    nodeKey: string;
    nodeType: string;
    promptFocus: NodePromptSlot | null;
    tab: "settings" | "prompts" | "results" | "excel";
  } | null>(null);
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
  const [canvasZoom, setCanvasZoom] = useState(1);
  const [aiReview, setAiReview] = useState<{ nodeKey: string; nodeType: string } | null>(
    null,
  );
  const [promptBuilderCtx, setPromptBuilderCtx] = useState<{
    nodeKey: string;
    nodeType: string;
    stepCode: string;
  } | null>(null);
  const [montageBoardOpen, setMontageBoardOpen] = useState(false);
  const suppressStudioOpenUntil = useRef(0);
  const qc = useQueryClient();

  const setVMenuNodeKeySynced = useCallback(
    (key: string | null) => {
      if (key) onSelectNode(key);
      setVMenuNodeKey(key);
    },
    [onSelectNode],
  );

  /** V-меню и студия важнее устаревшего selected с канваса. */
  const effectiveNodeKey =
    vMenuNodeKey ?? studioTarget?.nodeKey ?? selectedNodeKey ?? null;
  const effectiveNodeType = effectiveNodeKey
    ? nodeTypeFromKey(effectiveNodeKey)
    : (studioTarget?.nodeType ?? "");

  useEffect(() => {
    setAssetTray(null);
    setPromptFocus(null);
    setStudioTarget(null);
    setVMenuNodeKey(null);
    setAiOpen(false);
    setAiCtx(null);
    setHitlModalOpen(false);
    setHitlModalId(null);
    setResultPanel(null);
    setAiReview(null);
    setPromptBuilderCtx(null);
    setMontageBoardOpen(false);
  }, [projectId]);

  const closeStudio = useCallback(() => {
    suppressStudioOpenUntil.current = Date.now() + 1500;
    onStudioOpenChange(false);
    onSelectNode(null);
    setPromptFocus(null);
    setStudioTarget(null);
    setStudioTab("settings");
    setVMenuNodeKey(null);
  }, [onStudioOpenChange, onSelectNode]);

  const openStudioForNode = useCallback(
    (
      nodeKey: string,
      nodeType: string,
      slot: NodePromptSlot | null,
      tab: "settings" | "prompts" | "results" | "excel" = "prompts",
    ) => {
      suppressStudioOpenUntil.current = 0;
      const resolvedTab =
        slot?.kind === "excel" && isExcelGptNode(nodeType)
          ? "settings"
          : slot?.kind === "excel"
            ? "excel"
            : tab;
      let focus = slot;
      if (!focus) {
        const slots = resolvePromptSlots(nodeType, null);
        focus =
          slots.find((s) => s.kind === "gpt" || s.kind === "frame_prompts") ??
          slots.find((s) => s.kind !== "excel") ??
          slots[0] ??
          null;
      }
      const focusTab =
        slot?.kind === "excel" && isExcelGptNode(nodeType)
          ? "settings"
          : focus?.kind === "excel"
            ? "excel"
            : resolvedTab;
      setStudioTarget({
        nodeKey,
        nodeType,
        promptFocus: focus,
        tab: focusTab,
      });
      onSelectNode(nodeKey);
      setVMenuNodeKey(null);
      setPromptFocus(focus);
      setStudioTab(focusTab);
      onStudioOpenChange(true);
    },
    [onSelectNode, onStudioOpenChange],
  );

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

  useEffect(() => {
    const onOpen = (ev: Event) => {
      const d = (ev as CustomEvent<{ nodeKey: string; nodeType: string; stepCode?: string }>).detail;
      if (!projectId || !d?.nodeKey || !d?.nodeType) return;
      onSelectNode(d.nodeKey);
      setPromptBuilderCtx({
        nodeKey: d.nodeKey,
        nodeType: d.nodeType,
        stepCode: d.stepCode ?? stepCodeForNodeType(d.nodeType) ?? "plan",
      });
    };
    window.addEventListener("studio-open-prompt-builder", onOpen);
    return () => window.removeEventListener("studio-open-prompt-builder", onOpen);
  }, [projectId, onSelectNode]);

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

  // Шапка «Промты» → студия выбранной ноды (не legacy PromptEditor из SQLite).
  useEffect(() => {
    const onOpenPrompts = (ev: Event) => {
      if (!projectId) {
        toast.error("Сначала выберите проект");
        return;
      }
      const d = (ev as CustomEvent<{ nodeKey?: string | null }>).detail ?? {};
      const nodeKey = d.nodeKey ?? selectedNodeKey;
      if (!nodeKey) {
        toast.error("Выберите ноду на канвасе");
        return;
      }
      const nodeType = nodeTypeFromKey(nodeKey);
      if (nodeType === "topic") {
        toast.message("У ноды «Тема» нет файловых промтов");
        return;
      }
      openStudioForNode(nodeKey, nodeType, null, "prompts");
    };
    window.addEventListener("studio-open-node-prompts", onOpenPrompts);
    return () => window.removeEventListener("studio-open-node-prompts", onOpenPrompts);
  }, [projectId, selectedNodeKey, openStudioForNode]);

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
    (nodeType: string, nodeStatus?: import("@/lib/types").NodeRunStatus) =>
      resolveNodeResult(nodeType, resultContext, nodeStatus),
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
      return resolvePromptSlotsForNode(nodeKey, nodeType, meta.custom_prompts);
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
      setVMenuNodeKey: setVMenuNodeKeySynced,
      aiReviewNodeKey: aiReview?.nodeKey ?? null,
      canvasZoom,
      getPromptSlots,
      getNodeResult,
      onOpenPrompt: (nodeKey: string, nodeType: string, slot: NodePromptSlot) => {
        openStudioForNode(nodeKey, nodeType, slot);
      },
      onOpenGptText: (nodeKey: string, nodeType: string) => {
        const slot = gptTextSlotForNode(nodeType);
        if (!slot) return;
        openStudioForNode(nodeKey, nodeType, slot, "prompts");
      },
      onViewAllPrompts: (nodeKey: string, nodeType: string) => {
        if (nodeType === "images") {
          const excel = getPromptSlots(nodeKey, nodeType).find((s) => s.kind === "excel");
          openStudioForNode(nodeKey, nodeType, excel ?? null, "excel");
        } else {
          openStudioForNode(nodeKey, nodeType, null, "prompts");
        }
      },
      onAddPrompt: async (nodeKey: string, nodeType: string) => {
        const meta = (project.data?.meta || {}) as {
          custom_prompts?: Record<string, NodePromptSlot[]>;
        };
        const custom = { ...(meta.custom_prompts || {}) };
        const prev = custom[nodeKey] ?? [];
        const n = prev.filter((s) => s.custom || s.id.startsWith("custom_")).length + 1;
        const saveStep = isExcelGptNode(nodeType)
          ? EXCEL_GPT_STEP_CODE
          : stepCodeForNodeType(nodeType);
        const newSlot: NodePromptSlot = {
          id: `custom_${n}`,
          title: `Промт ${n}`,
          kind: "gpt",
          stepCode: saveStep ?? EXCEL_GPT_STEP_CODE,
          custom: true,
        };
        custom[nodeKey] = [...prev, newSlot];
        const slotId = `custom_${n}`;
        const metaBase = (project.data?.meta || {}) as Record<string, unknown>;
        const metaPatch = withSlotVariant(
          { ...metaBase, custom_prompts: custom },
          nodeKey,
          slotId,
          slotId,
        );
        await persistMeta({
          custom_prompts: custom,
          prompt_slot_variants: metaPatch.prompt_slot_variants,
        });
        if (saveStep) {
          try {
            await api.savePromptFile(
              saveStep,
              slotId,
              `# ${newSlot.title}\n\n`,
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
        const list = resolvePromptSlotsForNode(nodeKey, nodeType, custom).filter(
          (s) => s.id !== slot.id,
        );
        custom[nodeKey] = list;
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
          await api.reloadProjectXlsx(projectId).catch(() => undefined);
          if (nodeType === "excel_gpt" || nodeType.startsWith("enrich_")) {
            await api.patchExcelGptConfig(projectId, nodeKey, {}).catch(() => undefined);
          }
          await api.runProjectStep(projectId, step, { nodeKey });
          toast.success(`Запущен: ${getNodeSpec(nodeType).label}`);
          qc.invalidateQueries({ queryKey: ["project", projectId] });
        } catch (e) {
          toast.error(errorMessageFromUnknown(e));
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
      onOpenAiReview: (nodeKey: string, nodeType: string) => {
        setAiReview({ nodeKey, nodeType });
      },
      onOpenHitlById: (hitlId: number) => {
        setHitlModalId(hitlId);
        setHitlModalOpen(true);
      },
      onOpenNodeResult: (nodeKey: string, nodeType: string) => {
        setResultPanel({ nodeKey, nodeType });
      },
      montageBoardOpen,
      onOpenMontageBoard: () => setMontageBoardOpen(true),
      onCloseMontageBoard: () => setMontageBoardOpen(false),
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
          toast.error(errorMessageFromUnknown(e));
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
      setVMenuNodeKeySynced,
      aiReview,
      canvasZoom,
      montageBoardOpen,
      getPromptSlots,
      project.data?.meta,
      persistMeta,
      onSelectNode,
      onStudioOpenChange,
      selectedNodeKey,
      openStudioForNode,
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
          onCanvasZoom={setCanvasZoom}
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
            setStudioTarget({
              nodeKey,
              nodeType,
              promptFocus: null,
              tab: "settings",
            });
            setPromptFocus(null);
            setStudioTab("settings");
            onStudioOpenChange(true);
          }}
          disabledNodes={disabledNodes}
          runStepNodeKey={effectiveNodeKey}
        />
        {projectId && assetTray && (
          <AssetTray
            projectId={projectId}
            kind={assetTray.kind}
            onClose={() => setAssetTray(null)}
          />
        )}
      </div>
      <AssembleMontageBoard
        open={montageBoardOpen}
        projectId={projectId}
        onClose={() => setMontageBoardOpen(false)}
      />
      <NodeStudio
        open={studioOpen}
        onOpenChange={(open) => {
          if (!open) closeStudio();
          else onStudioOpenChange(true);
        }}
        projectId={projectId}
        nodeKey={effectiveNodeKey}
        initialTab={studioTarget?.tab ?? studioTab}
        promptFocus={
          effectiveNodeKey && studioTarget?.nodeKey === effectiveNodeKey
            ? studioTarget.promptFocus ?? promptFocus
            : promptFocus
        }
        nodeDisabled={
          effectiveNodeKey != null && disabledNodes.has(effectiveNodeKey)
        }
        promptSlots={
          effectiveNodeKey
            ? getPromptSlots(effectiveNodeKey, effectiveNodeType)
            : []
        }
      />
      {projectId != null && promptBuilderCtx && (
        <PromptBuilderStudio
          fullscreen
          projectId={projectId}
          nodeType={promptBuilderCtx.nodeType}
          stepCode={promptBuilderCtx.stepCode}
          onClose={() => setPromptBuilderCtx(null)}
          onOpenProjects={() => {
            setPromptBuilderCtx(null);
            window.dispatchEvent(new CustomEvent("studio-open-projects-sidebar"));
          }}
        />
      )}
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
      {projectId != null && aiReview && (
        <NodeAiReviewDialog
          open
          onOpenChange={(o) => {
            if (!o) setAiReview(null);
          }}
          projectId={projectId}
          nodeKey={aiReview.nodeKey}
          nodeType={aiReview.nodeType}
          projectMeta={(project.data?.meta || {}) as Record<string, unknown>}
          onOpenPrompt={(nodeKey, nodeType) => {
            setAiReview(null);
            openStudioForNode(nodeKey, nodeType, null, "prompts");
          }}
          onOpenGptText={(nodeKey, nodeType) => {
            setAiReview(null);
            openStudioForNode(nodeKey, nodeType, gptTextSlotForNode(nodeType), "prompts");
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
