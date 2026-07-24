"use client";

import { useEffect, useMemo, useState, useCallback, useRef } from "react";
import {
  ReactFlow,
  Background,
  Controls,
  MiniMap,
  BackgroundVariant,
  type Node,
  type Edge,
  useNodesState,
  useEdgesState,
  type NodeChange,
  type EdgeChange,
  type Connection,
  applyNodeChanges,
  addEdge,
  Position,
  Handle,
  useStore,
  type ReactFlowInstance,
} from "@xyflow/react";
import "@xyflow/react/dist/style.css";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import {
  ChevronDown,
  Copy,
  ClipboardPaste,
  FileSpreadsheet,
  Film,
  ImageIcon,
  Loader2,
  Play,
  Save,
  Sparkles,
  Square,
  Trash2,
  Video,
} from "lucide-react";
import { toast } from "sonner";
import { api, subscribeWS } from "@/lib/api";
import type {
  NodeRunDTO,
  WorkflowDetail,
  WorkflowEdge,
  WorkflowNode,
  WorkflowRunDetail,
} from "@/lib/types";
import { getNodeSpec, NODE_CATALOG } from "@/lib/node-catalog";
import { stepCodeForNodeType } from "@/lib/node-step-map";
import { formatNodeKeyLabel, formatRunStatus, formatStepCode } from "@/lib/format-labels";
import { buildExcelLaneBindings } from "@/lib/excel-lane-bindings";
import {
  inferNodeStatusFromProject,
  reconcileNodeRunStatus,
  workflowStructureKey,
} from "@/lib/node-run-status";
import { errorMessageFromUnknown } from "@/lib/error-message";
import { nodeTypeFromKey } from "@/lib/node-key";
import {
  isEditableTarget,
  readCanvasClipboard,
  writeCanvasClipboard,
  type CanvasClipboardPayload,
} from "@/lib/canvas-clipboard";
import { excelGptSlotIndex } from "@/lib/excel-gpt-config";
import { PipelineNode, type PipelineNodeData } from "./pipeline-node";
import {
  assignExcelGptSlotIndices,
  migrateWorkflowNodes,
  workflowNodeFromCanvas,
} from "@/lib/workflow-node-serialize";
import {
  buildCanvasGraph,
  readCanvasGraph,
} from "@/lib/canvas-graph-storage";
import { mergeGraphNodesWithRuntime } from "@/lib/canvas-node-merge";
import { NodeAiReviewControls } from "./node-ai-review-controls";
import { useRunEvents } from "@/hooks/use-bus";
import { Button } from "@/components/ui/button";
import { HitlBanner } from "@/components/hitl/hitl-banner";
import { RightButtonMarquee } from "@/components/canvas/right-button-marquee";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";

const nodeTypes = {
  pipeline: PipelineNode,
};

export function FlowCanvas({
  projectId,
  selectedNodeKey,
  runStepNodeKey,
  onSelectNode,
  onNodeActivate,
  disabledNodes = new Set<string>(),
  onCanvasZoom,
}: {
  projectId: number | null;
  selectedNodeKey: string | null;
  /** Нода для «Создать Run» / сброса — по умолчанию selectedNodeKey. */
  runStepNodeKey?: string | null;
  onSelectNode: (key: string | null) => void;
  onNodeActivate?: (nodeKey: string, nodeType: string) => void;
  disabledNodes?: Set<string>;
  onCanvasZoom?: (zoom: number) => void;
}) {
  const qc = useQueryClient();
  // 1) Дефолтный Workflow с бэкенда.
  const workflows = useQuery({
    queryKey: ["workflows"],
    queryFn: api.listWorkflows,
  });
  const defaultWorkflow = useMemo(
    () => (workflows.data ?? []).find((w) => w.is_default) ?? null,
    [workflows.data]
  );
  const workflow = useQuery({
    queryKey: ["workflow", defaultWorkflow?.id],
    queryFn: () => api.getWorkflow(defaultWorkflow!.id),
    enabled: !!defaultWorkflow,
    staleTime: 0,
  });

  // 2) Run для выбранного проекта (если есть).
  const project = useQuery({
    queryKey: ["project", projectId],
    queryFn: () => api.getProject(projectId!),
    enabled: projectId != null,
    refetchInterval: 4000,
  });

  const run = useQuery({
    queryKey: ["project-run", projectId],
    queryFn: async () => {
      if (!projectId) return null;
      const runs = await api.listRuns();
      const found = runs.find((r) => r.project_id === projectId);
      if (!found) return null;
      return api.getRun(found.id);
    },
    enabled: projectId != null,
    refetchInterval: 4000,
  });

  useEffect(() => {
    if (projectId == null) return;
    return subscribeWS(`projects.${projectId}`, (raw) => {
      const evt = raw as { type?: string; payload?: { stopped?: boolean } };
      if (
        evt.payload?.stopped ||
        evt.type === "node_status_changed" ||
        evt.type === "project_updated"
      ) {
        void qc.invalidateQueries({ queryKey: ["project-run", projectId] });
        void qc.invalidateQueries({ queryKey: ["project", projectId] });
      }
    });
  }, [projectId, qc]);

  // Базовая структура графа — из project.meta.canvas_graph (приоритет) или workflow.
  const canvasGraph = useMemo(() => {
    if (!project.data?.meta || !defaultWorkflow?.id) return null;
    return readCanvasGraph(
      project.data.meta as Record<string, unknown>,
      defaultWorkflow.id,
    );
  }, [project.data?.meta, defaultWorkflow?.id]);

  const graphSource = useMemo((): WorkflowDetail | null => {
    if (!workflow.data) return null;
    if (canvasGraph) {
      return {
        ...workflow.data,
        nodes: canvasGraph.nodes,
        edges: canvasGraph.edges,
      };
    }
    // Не рисуем factory-layout, пока project.meta ещё грузится — иначе
    // позиции залипают и потом перезаписывают canvas_graph при autosave.
    if (projectId != null && !project.isFetched) return null;
    return workflow.data;
  }, [workflow.data, canvasGraph, projectId, project.isFetched]);

  const baseNodes = useMemo(() => {
    if (!graphSource) return [];
    return workflowToReactFlowNodes(graphSource, null);
  }, [graphSource]);
  const baseEdges = useMemo(() => {
    if (!graphSource) return [];
    return workflowToReactFlowEdges(graphSource);
  }, [graphSource]);

  const [nodes, setNodes, onNodesChangeInternal] = useNodesState<Node<PipelineNodeData>>([]);
  const [edges, setEdges, onEdgesChangeInternal] = useEdgesState<Edge>([]);
  const [graphVersion, setGraphVersion] = useState<string>("");
  const nodesRef = useRef(nodes);
  const edgesRef = useRef(edges);
  const selectedNodesRef = useRef<Node<PipelineNodeData>[]>([]);
  /** Подавить onSelectionChange после программного setNodes(selected). */
  const ignoreSelectionChangeRef = useRef(0);
  const saveTimerRef = useRef<number | null>(null);
  const reactFlowRef = useRef<ReactFlowInstance<Node<PipelineNodeData>, Edge> | null>(null);
  const paneRef = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    nodesRef.current = nodes;
  }, [nodes]);
  useEffect(() => {
    edgesRef.current = edges;
  }, [edges]);

  // Смена проекта — не тащим позиции/версию соседа с теми же node_key.
  useEffect(() => {
    setGraphVersion("");
  }, [projectId]);

  useEffect(() => {
    if (!graphSource) return;
    // Только структура + projectId (без saved_at): drag/autosave не пересобирает граф.
    // Позиции из source — ждём project.isFetched, чтобы не залипнуть на factory.
    const ver = `${projectId ?? "none"}|${workflowStructureKey(graphSource)}`;
    if (ver === graphVersion && nodes.length > 0) return;
    setGraphVersion(ver);
    setNodes((prev) =>
      mergeGraphNodesWithRuntime(
        baseNodes as Node<PipelineNodeData>[],
        prev,
      ) as Node<PipelineNodeData>[],
    );
    setEdges(baseEdges);
  }, [
    graphSource,
    baseNodes,
    baseEdges,
    graphVersion,
    nodes.length,
    projectId,
    setNodes,
    setEdges,
  ]);

  // excel_gpt: подтянуть inputSource/label из project.meta → data ноды (для V-меню).
  useEffect(() => {
    if (!project.data?.meta || nodes.length === 0) return;
    const configs = (project.data.meta as { excel_gpt_nodes?: Record<string, Record<string, unknown>> })
      .excel_gpt_nodes;
    if (!configs || !Object.keys(configs).length) return;
    setNodes((prev) => {
      let changed = false;
      const next = prev.map((n) => {
        const d = n.data as PipelineNodeData;
        if (d.type !== "excel_gpt") return n;
        const cfg = configs[n.id];
        if (!cfg) return n;
        const patch: Partial<PipelineNodeData> = {
          slotIndex: excelGptSlotIndex(n.id, (cfg.slotIndex as number | undefined) ?? d.slotIndex),
        };
        if (cfg.label && cfg.label !== d.label) patch.label = cfg.label as string;
        if (cfg.inputSource && cfg.inputSource !== d.inputSource) {
          patch.inputSource = cfg.inputSource as PipelineNodeData["inputSource"];
        }
        if (cfg.uploadedFileName !== undefined && cfg.uploadedFileName !== d.uploadedFileName) {
          patch.uploadedFileName = cfg.uploadedFileName as string;
        }
        if (cfg.workMode && cfg.workMode !== d.workMode) {
          patch.workMode = cfg.workMode as PipelineNodeData["workMode"];
        }
        if (!Object.keys(patch).length) return n;
        changed = true;
        return { ...n, data: { ...d, ...patch } };
      });
      return changed ? next : prev;
    });
  }, [project.data?.meta, nodes.length, setNodes]);

  // Статусы run — только NodeRun (SSoT). Project.status не вмешивается.
  useEffect(() => {
    if (nodes.length === 0) return;
    // Не сбрасываем в pending при кратковременном отсутствии run.data (refetch/invalidate).
    if (!run.data) return;
    const nodeRunByKey = new Map(run.data.node_runs.map((nr) => [nr.node_key, nr]));
    setNodes((prev) =>
      prev.map((n) => {
        const nr = nodeRunByKey.get(n.id);
        if (!nr) {
          const status = inferNodeStatusFromProject(n.data.type);
          return {
            ...n,
            data: {
              ...n.data,
              status,
              progress: 0,
              progressText: null,
              error: null,
            },
          };
        }
        const status = reconcileNodeRunStatus(
          n.data.type,
          nr.status as PipelineNodeData["status"],
        );
        const progress = status === "running" ? (nr.progress ?? 0) : 0;
        const progressText = status === "running" ? (nr.progress_text ?? null) : null;
        return {
          ...n,
          data: {
            ...n.data,
            status,
            progress,
            progressText,
            error: nr.error ?? null,
            attempts: nr.attempts ?? 0,
          },
        };
      }),
    );
  }, [run.data, setNodes, nodes.length, projectId]);

  // Выделение на канвасе ← selectedNodeKey (кнопка V без клика по телу ноды).
  useEffect(() => {
    setNodes((prev) => {
      const target = selectedNodeKey;
      const needsUpdate = prev.some(
        (n) => !!n.selected !== (target != null && n.id === target),
      );
      if (!needsUpdate) return prev;
      ignoreSelectionChangeRef.current += 1;
      return prev.map((n) => ({
        ...n,
        selected: target != null && n.id === target,
      }));
    });
  }, [selectedNodeKey, setNodes]);

  // WS: только по node_key — матч по node_type зажигал все ноды одного типа.
  useRunEvents(run.data?.id ?? null, (evt) => {
    if (
      typeof evt === "object" &&
      evt !== null &&
      (evt as { type?: string }).type === "node_status_changed"
    ) {
      const e = evt as { node_key?: string; to: string };
      if (!e.node_key) return;
      setNodes((prev) =>
        prev.map((n) => {
          if (n.id !== e.node_key) return n;
          const to = reconcileNodeRunStatus(
            n.data.type,
            e.to as PipelineNodeData["status"],
          );
          return {
            ...n,
            data: {
              ...n.data,
              status: to,
              progress: to === "running" ? n.data.progress : 0,
              progressText: to === "running" ? n.data.progressText : null,
            },
          };
        }),
      );
    }
  });

  const scheduleSaveWorkflow = useCallback(() => {
    if (saveTimerRef.current != null) {
      window.clearTimeout(saveTimerRef.current);
    }
    saveTimerRef.current = window.setTimeout(() => {
      saveTimerRef.current = null;
      window.dispatchEvent(new CustomEvent("canvas-save-workflow"));
    }, 400);
  }, []);

  const onNodesChange = useCallback(
    (changes: NodeChange[]) => {
      setNodes((ns) => applyNodeChanges(changes, ns) as Node<PipelineNodeData>[]);
      if (
        changes.some(
          (c) => c.type === "position" && "dragging" in c && c.dragging === false,
        )
      ) {
        scheduleSaveWorkflow();
      }
    },
    [setNodes, scheduleSaveWorkflow],
  );

  const onEdgesChange = useCallback(
    (changes: EdgeChange[]) => {
      onEdgesChangeInternal(changes);
      if (changes.some((c) => c.type === "remove" || c.type === "add")) {
        scheduleSaveWorkflow();
      }
    },
    [onEdgesChangeInternal, scheduleSaveWorkflow],
  );

  const onConnect = useCallback(
    (connection: Connection) => {
      setEdges((eds) =>
        addEdge(
          {
            ...connection,
            id: `e_${connection.source}_${connection.target}_${Date.now()}`,
            type: "smoothstep",
          },
          eds,
        ),
      );
      toast.message("Связь добавлена — сохраняю…");
      scheduleSaveWorkflow();
    },
    [setEdges, scheduleSaveWorkflow],
  );

  const [saving, setSaving] = useState(false);
  const [selectedCount, setSelectedCount] = useState(0);
  const [hasClipboard, setHasClipboard] = useState(false);

  useEffect(() => {
    setHasClipboard(!!readCanvasClipboard()?.nodes.length);
  }, [projectId]);

  const persistWorkflow = useCallback(async () => {
    if (!workflow.data) return;
    setSaving(true);
    try {
      const currentNodes = nodesRef.current;
      const currentEdges = edgesRef.current;
      const wfNodes: WorkflowNode[] = assignExcelGptSlotIndices(
        currentNodes.map((n) => workflowNodeFromCanvas(n)),
      );
      const wfEdges: WorkflowEdge[] = currentEdges.map((e) => ({
        id: e.id,
        source: e.source,
        target: e.target,
        sourceHandle: e.sourceHandle ?? "out",
        targetHandle: e.targetHandle ?? "in",
      }));
      const check = await api.validateWorkflow({ nodes: wfNodes, edges: wfEdges });
      if (!check.valid) {
        toast.error(`Граф не сохранён: ${check.errors.join("; ")}`);
        return;
      }
      if (check.warnings.length) {
        toast.message(check.warnings[0]);
      }
      if (projectId) {
        const projectData = project.data ?? (await api.getProject(projectId));
        const meta = { ...((projectData.meta || {}) as Record<string, unknown>) };
        meta.canvas_graph = buildCanvasGraph(workflow.data.id, wfNodes, wfEdges);
        const topics = Array.isArray(meta.mass_excel_topics)
          ? (meta.mass_excel_topics as string[])
          : [];
        const bindings = buildExcelLaneBindings(currentNodes, currentEdges, topics);
        if (bindings.length) {
          meta.excel_lane_bindings = bindings;
        }
        await api.patchProject(projectId, { meta });
        await qc.invalidateQueries({ queryKey: ["project", projectId] });
        await api.ensureProjectRun(projectId).catch(() => undefined);
      } else {
        await api.saveWorkflow(workflow.data.id, {
          nodes: wfNodes,
          edges: wfEdges,
        });
      }
      toast.success("Граф сохранён");
    } catch (e) {
      toast.error(`Не сохранилось: ${errorMessageFromUnknown(e)}`);
    } finally {
      setSaving(false);
    }
  }, [workflow, projectId, project.data, qc]);

  useEffect(() => {
    const onPatch = (ev: Event) => {
      const detail = (ev as CustomEvent<{ nodeKey: string; patch: Record<string, unknown> }>)
        .detail;
      if (!detail?.nodeKey) return;
      setNodes((prev) =>
        prev.map((n) => {
          if (n.id !== detail.nodeKey) return n;
          return {
            ...n,
            data: {
              ...(n.data as PipelineNodeData),
              ...detail.patch,
            },
          };
        }),
      );
    };
    window.addEventListener("canvas-patch-node-data", onPatch);
    return () => window.removeEventListener("canvas-patch-node-data", onPatch);
  }, [setNodes]);

  useEffect(() => {
    const onDetach = (ev: Event) => {
      const detail = (ev as CustomEvent<{ nodeKey: string; autoSave?: boolean }>).detail;
      const key = detail?.nodeKey;
      if (!key) return;
      setEdges((prev) => prev.filter((e) => e.source !== key && e.target !== key));
      if (detail?.autoSave) {
        window.setTimeout(() => {
          window.dispatchEvent(new CustomEvent("canvas-save-workflow"));
        }, 50);
      }
    };
    // Отсоединить только одну сторону ноды (in/out) — через крестик
    // на hover'е соединительного кружка.
    const onDetachHandle = (ev: Event) => {
      const detail = (ev as CustomEvent<{
        nodeKey: string;
        side: "in" | "out";
        autoSave?: boolean;
      }>).detail;
      const key = detail?.nodeKey;
      const side = detail?.side;
      if (!key || (side !== "in" && side !== "out")) return;
      setEdges((prev) =>
        prev.filter((e) =>
          side === "in" ? e.target !== key : e.source !== key,
        ),
      );
      if (detail?.autoSave) {
        window.setTimeout(() => {
          window.dispatchEvent(new CustomEvent("canvas-save-workflow"));
        }, 50);
      } else {
        toast.success(
          side === "in" ? "Входящие связи сняты" : "Исходящие связи сняты",
        );
      }
    };
    const onDelete = (ev: Event) => {
      const detail = (ev as CustomEvent<{ nodeKey: string; autoSave?: boolean }>).detail;
      const key = detail?.nodeKey;
      if (!key) return;
      setNodes((prev) => prev.filter((n) => n.id !== key));
      setEdges((prev) => prev.filter((e) => e.source !== key && e.target !== key));
      if (detail?.autoSave) {
        window.setTimeout(() => {
          window.dispatchEvent(new CustomEvent("canvas-save-workflow"));
        }, 50);
      } else {
        toast.success("Нода удалена — сохраните граф");
      }
    };
    const onSaveRequest = () => {
      void persistWorkflow();
    };
    window.addEventListener("canvas-detach-node", onDetach);
    window.addEventListener("canvas-detach-handle", onDetachHandle);
    window.addEventListener("canvas-delete-node", onDelete);
    window.addEventListener("canvas-save-workflow", onSaveRequest);
    return () => {
      window.removeEventListener("canvas-detach-node", onDetach);
      window.removeEventListener("canvas-detach-handle", onDetachHandle);
      window.removeEventListener("canvas-delete-node", onDelete);
      window.removeEventListener("canvas-save-workflow", onSaveRequest);
    };
  }, [setNodes, setEdges, persistWorkflow]);

  const deleteNodesByIds = useCallback(
    (ids: Set<string>, opts?: { toast?: boolean }) => {
      if (!ids.size) return;
      setNodes((prev) => prev.filter((n) => !ids.has(n.id)));
      setEdges((prev) =>
        prev.filter((e) => !ids.has(e.source) && !ids.has(e.target)),
      );
      if (selectedNodeKey && ids.has(selectedNodeKey)) {
        onSelectNode(null);
      }
      selectedNodesRef.current = selectedNodesRef.current.filter((n) => !ids.has(n.id));
      setSelectedCount(selectedNodesRef.current.length);
      scheduleSaveWorkflow();
      if (opts?.toast !== false) {
        toast.success(
          ids.size === 1 ? "Нода удалена" : `Удалено ${ids.size} нод`,
        );
      }
    },
    [onSelectNode, scheduleSaveWorkflow, selectedNodeKey, setEdges, setNodes],
  );

  const copySelectedNodes = useCallback(() => {
    const selected = selectedNodesRef.current;
    if (!selected.length) {
      toast.error("Выделите одну или несколько нод (Ctrl+клик или рамкой)");
      return;
    }
    const ids = new Set(selected.map((n) => n.id));
    const payload: CanvasClipboardPayload = {
      version: 1,
      sourceProjectId: projectId,
      copiedAt: Date.now(),
      nodes: selected.map((n) => {
        const wf = workflowNodeFromCanvas(n);
        return {
          id: wf.id,
          type: wf.type,
          position: wf.position,
          data: wf.data ?? {},
        };
      }),
      edges: edgesRef.current
        .filter((e) => ids.has(e.source) && ids.has(e.target))
        .map((e) => ({
          id: e.id,
          source: e.source,
          target: e.target,
          sourceHandle: e.sourceHandle ?? "out",
          targetHandle: e.targetHandle ?? "in",
        })),
    };
    writeCanvasClipboard(payload);
    setHasClipboard(true);
    toast.success(
      `Скопировано ${selected.length} нод${payload.edges.length ? ` и ${payload.edges.length} связей` : ""}`,
    );
  }, [projectId]);

  const pasteFromClipboard = useCallback(() => {
    const clip = readCanvasClipboard();
    if (!clip?.nodes.length) {
      toast.error("Буфер пуст — выделите ноды и нажмите Ctrl+C");
      return;
    }
    const stamp = Date.now();
    const idMap = new Map<string, string>();
    clip.nodes.forEach((n, idx) => {
      idMap.set(n.id, `n_${n.type}_${stamp}_${idx}`);
    });
    const excelRemap: Record<string, string> = {};
    clip.nodes.forEach((n) => {
      if (n.type !== "excel_gpt") return;
      const newId = idMap.get(n.id);
      if (newId) excelRemap[n.id] = newId;
    });
    const offsetX = 56;
    const offsetY = 56;
    const newNodes: Node<PipelineNodeData>[] = clip.nodes.map((n) => {
      const newId = idMap.get(n.id)!;
      const srcData = (n.data ?? {}) as Partial<PipelineNodeData>;
      return {
        id: newId,
        type: "pipeline",
        position: {
          x: n.position.x + offsetX,
          y: n.position.y + offsetY,
        },
        data: {
          nodeKey: newId,
          type: n.type,
          label: (srcData.label as string | undefined) ?? (n.data?.label as string | undefined),
          description: srcData.description ?? (n.data?.description as string | undefined),
          slotIndex: (srcData.slotIndex as number | undefined) ?? (n.data?.slotIndex as number | undefined),
          inputSource: (srcData.inputSource as PipelineNodeData["inputSource"]) ??
            (n.data?.inputSource as PipelineNodeData["inputSource"]),
          uploadedFileName:
            (srcData.uploadedFileName as string | undefined) ??
            (n.data?.uploadedFileName as string | undefined),
          workMode:
            (srcData.workMode as PipelineNodeData["workMode"]) ??
            (n.data?.workMode as PipelineNodeData["workMode"]),
          status: "pending",
          progress: 0,
          progressText: null,
          error: null,
          attempts: 0,
        },
        sourcePosition: Position.Right,
        targetPosition: Position.Left,
        selected: true,
      };
    });
    const newEdges: Edge[] = clip.edges.map((e, idx) => ({
      id: `e_paste_${stamp}_${idx}`,
      source: idMap.get(e.source) ?? e.source,
      target: idMap.get(e.target) ?? e.target,
      sourceHandle: e.sourceHandle ?? "out",
      targetHandle: e.targetHandle ?? "in",
      type: "smoothstep",
    }));
    setNodes((prev) => [
      ...prev.map((n) => ({ ...n, selected: false })),
      ...newNodes,
    ]);
    setEdges((prev) => [...prev, ...newEdges]);
    selectedNodesRef.current = newNodes;
    setSelectedCount(newNodes.length);
    if (newNodes[0]) {
      onSelectNode((newNodes[0].data as PipelineNodeData).nodeKey);
    }
    if (projectId && Object.keys(excelRemap).length > 0) {
      void api.remapExcelGptNodes(projectId, excelRemap).catch((e) => {
        toast.error(`Не перенесены настройки Excel: ${errorMessageFromUnknown(e)}`);
      });
      for (const [oldId, newId] of Object.entries(excelRemap)) {
        const src = clip.nodes.find((n) => n.id === oldId);
        const cfg = (src?.data ?? {}) as Record<string, unknown>;
        void api
          .patchExcelGptConfig(projectId, newId, {
            label: cfg.label as string | undefined,
            inputSource: cfg.inputSource as string | undefined,
            uploadedFileName: cfg.uploadedFileName as string | undefined,
            slotIndex: cfg.slotIndex as number | undefined,
            workMode: cfg.workMode as string | undefined,
          })
          .catch(() => undefined);
      }
    }
    scheduleSaveWorkflow();
    const fromOther =
      clip.sourceProjectId != null &&
      projectId != null &&
      clip.sourceProjectId !== projectId;
    toast.success(
      fromOther
        ? `Вставлено ${newNodes.length} нод из проекта #${clip.sourceProjectId}`
        : `Вставлено ${newNodes.length} нод`,
    );
  }, [onSelectNode, projectId, scheduleSaveWorkflow, setEdges, setNodes]);

  const deleteSelectedNodes = useCallback(() => {
    const selected = selectedNodesRef.current;
    if (!selected.length && selectedNodeKey) {
      deleteNodesByIds(new Set([selectedNodeKey]));
      return;
    }
    if (!selected.length) {
      toast.error("Выделите ноды для удаления");
      return;
    }
    deleteNodesByIds(new Set(selected.map((n) => n.id)));
  }, [deleteNodesByIds, selectedNodeKey]);

  useEffect(() => {
    if (!projectId) return;
    const onKeyDown = (ev: KeyboardEvent) => {
      if (isEditableTarget(ev.target)) return;
      const mod = ev.ctrlKey || ev.metaKey;
      if (mod && ev.key.toLowerCase() === "c") {
        ev.preventDefault();
        copySelectedNodes();
        return;
      }
      if (mod && ev.key.toLowerCase() === "v") {
        ev.preventDefault();
        pasteFromClipboard();
      }
    };
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [projectId, copySelectedNodes, pasteFromClipboard]);

  const addNode = useCallback(
    (type: string) => {
      if (!workflow.data) return;
      const id = `n_${type}_${Date.now()}`;
      const spec = getNodeSpec(type);
      const excelCount = nodes.filter(
        (n) => (n.data as PipelineNodeData).type === "excel_gpt",
      ).length;

      let position = { x: 120, y: 200 };
      const rf = reactFlowRef.current;
      const pane = paneRef.current;
      if (rf && pane) {
        const rect = pane.getBoundingClientRect();
        position = rf.screenToFlowPosition({
          x: rect.left + rect.width / 2,
          y: rect.top + rect.height / 2,
        });
        position.x -= 110;
        position.y -= 36;
      }

      const newNode: Node<PipelineNodeData> = {
        id,
        type: "pipeline",
        position,
        data: {
          nodeKey: id,
          type,
          label: spec.label,
          ...(type === "excel_gpt"
            ? { slotIndex: Math.min(excelCount + 1, 5) }
            : {}),
          status: "pending",
          progress: 0,
          progressText: null,
          error: null,
          attempts: 0,
        },
        sourcePosition: Position.Right,
        targetPosition: Position.Left,
      };
      setNodes((prev) => [...prev, newNode]);
      toast.message(`Добавлена нода: ${spec.label}`);
    },
    [workflow.data, nodes, setNodes],
  );

  const onNodesDelete = useCallback(
    (deleted: Node[]) => {
      const ids = new Set(deleted.map((n) => n.id));
      setEdges((prev) =>
        prev.filter((e) => !ids.has(e.source) && !ids.has(e.target)),
      );
      selectedNodesRef.current = selectedNodesRef.current.filter(
        (n) => !ids.has(n.id),
      );
      setSelectedCount(selectedNodesRef.current.length);
      if (selectedNodeKey && ids.has(selectedNodeKey)) {
        onSelectNode(null);
      }
      scheduleSaveWorkflow();
      toast.success(
        deleted.length === 1
          ? "Нода удалена"
          : `Удалено ${deleted.length} нод`,
      );
    },
    [onSelectNode, scheduleSaveWorkflow, selectedNodeKey, setEdges],
  );

  const canvasBootLoading =
    (workflows.isLoading && !workflows.data) ||
    (Boolean(defaultWorkflow) && workflow.isLoading && !workflow.data);
  if (canvasBootLoading) {
    return (
      <div className="flex h-full items-center justify-center">
        <Loader2 className="h-5 w-5 animate-spin text-muted-foreground" />
      </div>
    );
  }

  if (workflows.isError) {
    return (
      <div className="flex h-full items-center justify-center">
        <div className="max-w-md rounded-lg border border-destructive/30 bg-destructive/5 p-6 text-center">
          <h3 className="text-sm font-semibold text-destructive">
            Бэкенд не отвечает
          </h3>
          <p className="mt-1 text-xs text-muted-foreground">
            Запусти его командой <code className="rounded bg-muted px-1.5 py-0.5 font-mono">python -m app.main</code>
            {" "}— веб-UI поднимется на :8765.
          </p>
        </div>
      </div>
    );
  }

  // Пустой выбор → онбординг.
  if (!projectId) {
    return (
      <div className="flex h-full items-center justify-center">
        <EmptyState />
      </div>
    );
  }

  return (
    <>
      <div ref={paneRef} className="relative h-full w-full">
      <ReactFlow
        nodes={nodes}
        edges={edges}
        onInit={(inst) => {
          reactFlowRef.current = inst;
        }}
        onNodesChange={onNodesChange}
        onEdgesChange={onEdgesChange}
        nodeTypes={nodeTypes}
        fitView={!canvasGraph?.saved_at}
        fitViewOptions={{ padding: 0.12, maxZoom: 0.85, minZoom: 0.2 }}
        minZoom={0.15}
        maxZoom={1.5}
        proOptions={{ hideAttribution: true }}
        nodesDraggable
        nodesConnectable
        edgesReconnectable
        nodeDragThreshold={6}
        selectNodesOnDrag={false}
        selectionOnDrag={false}
        panOnDrag={[0, 1]}
        multiSelectionKeyCode={["Control", "Meta"]}
        selectionKeyCode={null}
        onPaneContextMenu={(e) => e.preventDefault()}
        onConnect={onConnect}
        connectionLineStyle={{ strokeDasharray: "6 4", stroke: "hsl(var(--primary))" }}
        elementsSelectable
        deleteKeyCode={["Backspace", "Delete"]}
        onNodesDelete={onNodesDelete}
        onNodeClick={(ev, node) => {
          const t = ev.target as HTMLElement;
          if (
            t.closest(".node-v-trigger") ||
            t.closest(".node-v-menu") ||
            t.closest(".node-ai-review-trigger")
          ) {
            return;
          }
          const d = node.data as PipelineNodeData;
          onSelectNode(d.nodeKey);
          onNodeActivate?.(d.nodeKey, d.type);
        }}
        onSelectionChange={(sel) => {
          selectedNodesRef.current = sel.nodes as Node<PipelineNodeData>[];
          setSelectedCount(sel.nodes.length);
          if (ignoreSelectionChangeRef.current > 0) {
            ignoreSelectionChangeRef.current -= 1;
            return;
          }
          const first = sel.nodes[0];
          if (first) {
            onSelectNode((first.data as PipelineNodeData).nodeKey);
          } else if (sel.nodes.length === 0) {
            onSelectNode(null);
          }
        }}
        defaultEdgeOptions={{ animated: true }}
      >
        <Background
          color="hsl(var(--canvas-grid))"
          gap={20}
          size={1}
          variant={BackgroundVariant.Dots}
        />
        {onCanvasZoom ? <ViewportZoomReporter onZoom={onCanvasZoom} /> : null}
        <NodeAiReviewControls />
        <Controls position="bottom-right" showInteractive={false} />
        {selectedNodeKey && (
          <button
            type="button"
            title="ИИ-помощник для выделенной ноды"
            aria-label="ИИ-помощник"
            className="absolute bottom-[4.5rem] right-4 z-20 flex h-10 w-10 items-center justify-center rounded-full border border-violet-400/60 bg-gradient-to-br from-violet-500/80 to-amber-500/50 text-white shadow-lg shadow-violet-500/25 transition hover:scale-105 hover:border-violet-300"
            onClick={() => {
              const node = nodes.find((n) => n.id === selectedNodeKey);
              const nodeType = nodeTypeFromKey(selectedNodeKey);
              if (!node) return;
              window.dispatchEvent(
                new CustomEvent("canvas-open-ai-node", {
                  detail: {
                    nodeKey: selectedNodeKey,
                    nodeType: (node.data as PipelineNodeData).type || nodeType,
                  },
                }),
              );
            }}
          >
            <Sparkles className="h-4 w-4" />
          </button>
        )}
        <MiniMap
          pannable
          zoomable
          position="bottom-left"
          style={{ width: 168, height: 112 }}
          nodeColor={(node) => {
            const data = node.data as PipelineNodeData;
            if (data.status === "running") return "hsl(var(--primary))";
            if (data.status === "queued") return "hsl(200 80% 50%)";
            if (data.status === "done") return "hsl(var(--success))";
            if (data.status === "failed") return "hsl(var(--destructive))";
            if (data.status === "waiting_hitl") return "hsl(var(--warning))";
            return "hsl(var(--muted-foreground) / 0.4)";
          }}
          nodeStrokeWidth={2}
          nodeBorderRadius={4}
          maskColor="hsl(var(--background) / 0.7)"
        />
        <RightButtonMarquee />
      </ReactFlow>
      </div>
      <WorkflowToolbar
        workflowId={workflow.data?.id}
        onSave={persistWorkflow}
        saving={saving}
        onAddNode={addNode}
        onCopy={copySelectedNodes}
        onPaste={pasteFromClipboard}
        canPaste={hasClipboard}
        onDelete={deleteSelectedNodes}
        canDelete={selectedCount > 0 || !!selectedNodeKey}
        onAddExcelFeed={() => {
          const stamp = Date.now();
          const id = `excel_feed_${stamp}`;
          const minX = nodes.reduce((m, n) => Math.min(m, n.position.x), 0);
          const minY = nodes.reduce((m, n) => Math.min(m, n.position.y), 0);
          setNodes((prev) => [
            ...prev,
            {
              id,
              type: "pipeline",
              position: { x: minX - 320, y: minY },
              data: {
                nodeKey: id,
                type: "excel_feed",
                status: "pending",
                progress: 0,
                progressText: null,
                error: null,
                attempts: 0,
              },
              sourcePosition: Position.Right,
              targetPosition: Position.Left,
            } as Node<PipelineNodeData>,
          ]);
          toast.success("Нода Excel добавлена слева — загрузите topics.xlsx");
        }}
        onDuplicateBelow={() => {
          if (nodes.length === 0) return;
          const offsetY = 480;
          const stamp = Date.now();
          const idMap = new Map<string, string>();
          const clones = nodes
            .filter((n) => (n.data as PipelineNodeData).type !== "excel_feed")
            .map((n) => {
              const newId = `${n.id}_lane_${stamp}`;
              idMap.set(n.id, newId);
              const d = n.data as PipelineNodeData;
              return {
                ...n,
                id: newId,
                position: { x: n.position.x, y: n.position.y + offsetY },
                data: { ...d, nodeKey: newId },
                selected: false,
              };
            });
          const cloneEdges = edges
            .filter(
              (e) =>
                idMap.has(e.source) &&
                idMap.has(e.target) &&
                (nodes.find((n) => n.id === e.source)?.data as PipelineNodeData)?.type !==
                  "excel_feed",
            )
            .map((e) => ({
              ...e,
              id: `${e.id}_lane_${stamp}`,
              source: idMap.get(e.source) ?? e.source,
              target: idMap.get(e.target) ?? e.target,
            }));
          const excelNode = nodes.find(
            (n) => (n.data as PipelineNodeData).type === "excel_feed",
          );
          const excelEdges: Edge[] = [];
          if (excelNode) {
            for (const p of clones) {
              const dt = (p.data as PipelineNodeData).type;
              if (dt === "plan" || dt === "topic") {
                excelEdges.push({
                  id: `excel_${stamp}_${p.id}`,
                  source: excelNode.id,
                  target: p.id,
                  type: "smoothstep",
                  animated: true,
                  style: { stroke: "hsl(142 70% 45%)", strokeWidth: 2 },
                  className: "excel-lane-edge",
                });
              }
            }
          }
          setNodes((prev) => [...prev, ...clones]);
          setEdges((prev) => [...prev, ...cloneEdges, ...excelEdges]);
          toast.success(
            excelEdges.length
              ? `Граф продублирован (${excelEdges.length} связей от Excel)`
              : "Граф продублирован вниз — сохраните при необходимости",
          );
        }}
      />
      <RunOverlay
        projectId={projectId}
        workflow={workflow.data ?? null}
        run={run.data ?? null}
        runStepNodeKey={runStepNodeKey ?? selectedNodeKey}
        onRunCreated={() => run.refetch()}
      />
      <HitlBanner projectId={projectId} />
    </>
  );
}

function WorkflowToolbar({
  workflowId,
  onSave,
  saving,
  onAddNode,
  onCopy,
  onPaste,
  canPaste,
  onDelete,
  canDelete,
  onDuplicateBelow,
  onAddExcelFeed,
}: {
  workflowId?: number;
  onSave: () => void;
  saving: boolean;
  onAddNode: (type: string) => void;
  onCopy: () => void;
  onPaste: () => void;
  canPaste: boolean;
  onDelete: () => void;
  canDelete: boolean;
  onDuplicateBelow: () => void;
  onAddExcelFeed: () => void;
}) {
  const addable = Object.keys(NODE_CATALOG).filter(
    (t) => !t.startsWith("hitl_") && t !== "excel_feed",
  );
  const qc = useQueryClient();

  const duplicateWf = async () => {
    if (!workflowId) return;
    try {
      await api.duplicateWorkflow(workflowId);
      await qc.invalidateQueries({ queryKey: ["workflows"] });
      toast.success("Workflow скопирован на сервере");
    } catch (e) {
      toast.error(errorMessageFromUnknown(e));
    }
  };

  return (
    <div className="pointer-events-none absolute left-4 top-4 z-10 flex max-w-[calc(100%-2rem)] flex-wrap gap-2">
      <div className="pointer-events-auto flex flex-wrap items-center gap-1 rounded-lg border border-border bg-card/80 p-1 backdrop-blur-sm">
        <select
          className="studio-select h-8 max-w-[140px] rounded-md border border-input bg-card px-2 text-xs"
          defaultValue=""
          onChange={(e) => {
            if (e.target.value) {
              onAddNode(e.target.value);
              e.target.value = "";
            }
          }}
        >
          <option value="">+ Нода</option>
          {addable.map((t) => (
            <option key={t} value={t}>
              {getNodeSpec(t).label}
            </option>
          ))}
        </select>
        <Button size="sm" variant="ghost" className="h-8 gap-1 text-xs" onClick={onSave} disabled={saving}>
          {saving ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Save className="h-3.5 w-3.5" />}
          Сохранить граф
        </Button>
        <Button
          size="sm"
          variant="ghost"
          className="h-8 gap-1 text-xs"
          onClick={onCopy}
          title="Копировать выделенные ноды (Ctrl+C)"
        >
          <Copy className="h-3.5 w-3.5" />
          Копировать
        </Button>
        <Button
          size="sm"
          variant="ghost"
          className="h-8 gap-1 text-xs"
          onClick={onPaste}
          disabled={!canPaste}
          title="Вставить скопированные ноды (Ctrl+V) — работает и в другом проекте"
        >
          <ClipboardPaste className="h-3.5 w-3.5" />
          Вставить
        </Button>
        <Button
          size="sm"
          variant="ghost"
          className="h-8 gap-1 text-xs"
          onClick={onDuplicateBelow}
          title="Дублировать весь граф ниже (массовые потоки)"
        >
          <Copy className="h-3.5 w-3.5 opacity-60" />
        </Button>
        <Button
          size="sm"
          variant="ghost"
          className="h-8 gap-1 text-xs"
          onClick={onAddExcelFeed}
          title="Источник Excel для массовой генерации"
        >
          <FileSpreadsheet className="h-3.5 w-3.5" />
        </Button>
        {workflowId != null && (
          <Button
            size="sm"
            variant="ghost"
            className="h-8 gap-1 text-xs"
            onClick={() => void duplicateWf()}
            title="Сохранить копию workflow на сервере"
          >
            <Copy className="h-3.5 w-3.5 opacity-60" />
            WF
          </Button>
        )}
        <Button
          size="sm"
          variant="ghost"
          className="h-8 gap-1 text-xs text-destructive"
          onClick={onDelete}
          disabled={!canDelete}
          title="Удалить выделенные ноды (Delete)"
        >
          <Trash2 className="h-3.5 w-3.5" />
        </Button>
      </div>
    </div>
  );
}

function EmptyState() {
  return (
    <div className="flex max-w-md flex-col items-center gap-3 text-center">
      <div className="flex h-12 w-12 items-center justify-center rounded-full bg-primary/10">
        <Play className="h-5 w-5 text-primary" />
      </div>
      <h2 className="text-lg font-semibold tracking-tight">Выбери проект слева</h2>
      <p className="text-sm text-muted-foreground">
        Каждый проект — это граф из 20 нод: от темы и плана до сборки и публикации.
        Кликни «+» в сайдбаре, чтобы создать новый ролик.
      </p>
    </div>
  );
}

function RunOverlay({
  projectId,
  workflow,
  run,
  runStepNodeKey,
  onRunCreated,
}: {
  projectId: number;
  workflow: WorkflowDetail | null;
  run: WorkflowRunDetail | null;
  runStepNodeKey: string | null;
  onRunCreated: () => void;
}) {
  const [busy, setBusy] = useState(false);
  const [pausing, setPausing] = useState(false);
  const [finishBusy, setFinishBusy] = useState<"images" | "videos" | "animation_prompts" | null>(
    null,
  );
  const qc = useQueryClient();

  if (!workflow) return null;

  const nodeType = nodeTypeFromKey(runStepNodeKey);
  const stepCode = stepCodeForNodeType(nodeType);
  const stepLabel = stepCode ? formatStepCode(stepCode) : null;

  const handlePause = async () => {
    setPausing(true);
    try {
      await api.pauseProject(projectId);
      toast.success("Проект на паузе");
      onRunCreated();
    } catch (e) {
      toast.error(errorMessageFromUnknown(e));
    } finally {
      setPausing(false);
    }
  };

  const handleResume = async () => {
    setPausing(true);
    try {
      const r = await api.continueProject(projectId);
      if (r.advanced) {
        toast.success(`Следующий шаг: ${r.status}`);
      } else if (r.action === "resumed") {
        toast.success("Проект продолжен");
      } else {
        toast.message("Нет шага для автопродвижения — запустите шаг вручную");
      }
      onRunCreated();
    } catch (e) {
      toast.error(errorMessageFromUnknown(e));
    } finally {
      setPausing(false);
    }
  };

  const handleStopProject = async () => {
    setBusy(true);
    try {
      const r = await api.stopProject(projectId);
      toast.success(r.message || "⏹ Шаг остановлен");
      qc.invalidateQueries({ queryKey: ["project-run", projectId] });
      qc.invalidateQueries({ queryKey: ["project", projectId] });
      await qc.refetchQueries({ queryKey: ["project", projectId] });
      await qc.refetchQueries({ queryKey: ["project-run", projectId] });
      qc.invalidateQueries({ queryKey: ["runs"] });
      onRunCreated();
    } catch (e) {
      toast.error(errorMessageFromUnknown(e));
    } finally {
      setBusy(false);
    }
  };

  const handleFinishImages = async () => {
    setFinishBusy("images");
    try {
      const r = await api.finishMissingImages(projectId);
      if (r.queued > 0) toast.success(r.message);
      else toast.message(r.message);
      qc.invalidateQueries({ queryKey: ["project", projectId] });
      onRunCreated();
    } catch (e) {
      toast.error(errorMessageFromUnknown(e));
    } finally {
      setFinishBusy(null);
    }
  };

  const handleFinishVideos = async () => {
    setFinishBusy("videos");
    try {
      const r = await api.finishMissingVideos(projectId);
      if (r.queued > 0) toast.success(r.message);
      else toast.message(r.message);
      qc.invalidateQueries({ queryKey: ["project", projectId] });
      onRunCreated();
    } catch (e) {
      toast.error(errorMessageFromUnknown(e));
    } finally {
      setFinishBusy(null);
    }
  };

  const handleFinishAnimationPrompts = async () => {
    setFinishBusy("animation_prompts");
    try {
      const r = await api.finishMissingAnimationPrompts(projectId);
      if (r.queued > 0) toast.success(r.message);
      else toast.message(r.message);
      qc.invalidateQueries({ queryKey: ["project", projectId] });
      onRunCreated();
    } catch (e) {
      toast.error(errorMessageFromUnknown(e));
    } finally {
      setFinishBusy(null);
    }
  };

  const handleCancelRun = async () => {
    if (!run) return;
    setBusy(true);
    try {
      await api.stopProject(projectId);
      await api.cancelRun(run.id);
      toast.success("Run остановлен (task.cancel)");
      qc.invalidateQueries({ queryKey: ["project-run", projectId] });
      qc.invalidateQueries({ queryKey: ["project", projectId] });
      await qc.refetchQueries({ queryKey: ["project", projectId] });
      await qc.refetchQueries({ queryKey: ["project-run", projectId] });
      onRunCreated();
    } catch (e) {
      toast.error(errorMessageFromUnknown(e));
    } finally {
      setBusy(false);
    }
  };

  const handleResetStep = async () => {
    if (!stepCode) {
      toast.error("У выбранной ноды нет шага для сброса");
      return;
    }
    setBusy(true);
    try {
      await api.resetProjectStep(projectId, stepCode);
      toast.success(`Шаг «${formatStepCode(stepCode)}» сброшен`);
      onRunCreated();
    } catch (e) {
      toast.error(errorMessageFromUnknown(e));
    } finally {
      setBusy(false);
    }
  };

  const handleStart = async () => {
    if (!stepCode) {
      toast.error("Выберите ноду с шагом пайплайна");
      return;
    }
    setBusy(true);
    try {
      const created = await api.startRunFromWorkflow(workflow.id, {
        project_id: projectId,
      });
      await api.runProjectStep(projectId, stepCode, {
        nodeKey: runStepNodeKey ?? undefined,
      });
      onRunCreated();
      toast.success(`Run #${created.id} · шаг «${formatStepCode(stepCode)}» запущен`, {
        description: "Воркер подхватит шаг — HITL в веб-UI",
      });
    } catch (e) {
      toast.error(`Не получилось запустить: ${errorMessageFromUnknown(e)}`);
    } finally {
      setBusy(false);
    }
  };

  return (
    <>
    <div className="pointer-events-none absolute right-4 top-4 z-10 flex flex-wrap items-center justify-end gap-2 max-w-[min(100%,520px)]">
      <div className="pointer-events-auto flex items-center gap-2 rounded-lg border border-border bg-card/70 px-3 py-1.5 text-xs shadow-sm backdrop-blur-sm">
        <span className="text-muted-foreground">Run:</span>
        <span className="font-medium">
          {run ? `#${run.id} · ${formatRunStatus(run.status)}` : "не запущен"}
        </span>
      </div>
      <DropdownMenu>
        <DropdownMenuTrigger asChild>
          <Button
            size="sm"
            variant="outline"
            disabled={busy || finishBusy !== null}
            className="pointer-events-auto gap-1 text-xs"
          >
            {finishBusy ? (
              <Loader2 className="h-3.5 w-3.5 animate-spin" />
            ) : (
              <ChevronDown className="h-3.5 w-3.5 opacity-60" />
            )}
            Доделка
          </Button>
        </DropdownMenuTrigger>
        <DropdownMenuContent align="end" className="w-52">
          <DropdownMenuItem
            disabled={finishBusy !== null}
            onSelect={(e) => {
              e.preventDefault();
              void handleFinishImages();
            }}
          >
            <ImageIcon className="mr-2 h-4 w-4" />
            Доделка картинок
          </DropdownMenuItem>
          <DropdownMenuItem
            disabled={finishBusy !== null}
            onSelect={(e) => {
              e.preventDefault();
              void handleFinishVideos();
            }}
          >
            <Video className="mr-2 h-4 w-4" />
            Доделка видео
          </DropdownMenuItem>
          <DropdownMenuItem
            disabled={finishBusy !== null}
            onSelect={(e) => {
              e.preventDefault();
              void handleFinishAnimationPrompts();
            }}
          >
            <Film className="mr-2 h-4 w-4" />
            Доделка промтов анимации
          </DropdownMenuItem>
        </DropdownMenuContent>
      </DropdownMenu>
      <Button
        size="sm"
        variant="outline"
        onClick={handlePause}
        disabled={pausing}
        className="pointer-events-auto gap-1 text-xs"
      >
        {pausing ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : null}
        Пауза
      </Button>
      <Button
        size="sm"
        variant="outline"
        onClick={handleResume}
        disabled={pausing}
        className="pointer-events-auto text-xs"
      >
        Продолжить
      </Button>
      <Button
        size="sm"
        variant="destructive"
        onClick={handleStopProject}
        disabled={busy}
        className="pointer-events-auto gap-1.5 text-xs font-semibold"
        title="Откат running-шага; автопродвижение не сбрасывается"
      >
        {busy ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Square className="h-3.5 w-3.5 fill-current" />}
        ⏹ Остановить текущий шаг
      </Button>
      {run && (
        <Button
          size="sm"
          variant="outline"
          onClick={handleCancelRun}
          disabled={busy}
          className="pointer-events-auto gap-1 text-xs text-destructive"
        >
          Отмена run
        </Button>
      )}
      <Button
        size="sm"
        variant="ghost"
        onClick={handleResetStep}
        disabled={busy}
        className="pointer-events-auto text-xs"
      >
        Сброс шага
      </Button>
      <Button
        size="sm"
        onClick={handleStart}
        disabled={busy || !stepCode}
        className="pointer-events-auto gap-1.5"
        title={
          stepLabel
            ? `Запустить шаг «${stepLabel}» для ноды ${formatNodeKeyLabel(runStepNodeKey ?? "")}`
            : "Выберите ноду с шагом пайплайна"
        }
      >
        {busy ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Play className="h-3.5 w-3.5" />}
        {run ? "Перезапустить" : "Создать Run"}
        {stepLabel ? ` · ${stepLabel}` : ""}
      </Button>
    </div>
    </>
  );
}

function workflowToReactFlowNodes(
  wf: WorkflowDetail,
  run: WorkflowRunDetail | null
): Node<PipelineNodeData>[] {
  const nodeRunByKey = new Map<string, NodeRunDTO>();
  if (run) {
    for (const nr of run.node_runs) {
      nodeRunByKey.set(nr.node_key, nr);
    }
  }
  const migrated = migrateWorkflowNodes(wf.nodes);
  return migrated.map((n) => {
    const nr = nodeRunByKey.get(n.id);
    const spec = getNodeSpec(n.type);
    const data = (n.data ?? {}) as Record<string, unknown>;
    return {
      id: n.id,
      type: "pipeline",
      position: n.position,
      data: {
        nodeKey: n.id,
        type: n.type,
        label: (typeof data.label === "string" && data.label.trim()) || spec.label,
        description: (data.description as string | undefined) ?? spec.description,
        slotIndex: data.slotIndex as number | undefined,
        inputSource: data.inputSource as PipelineNodeData["inputSource"],
        uploadedFileName: data.uploadedFileName as string | undefined,
        workMode: data.workMode as PipelineNodeData["workMode"],
        status: (nr?.status ?? "pending") as PipelineNodeData["status"],
        progress: nr?.progress ?? 0,
        progressText: nr?.progress_text ?? null,
        error: nr?.error ?? null,
        attempts: nr?.attempts ?? 0,
      },
      sourcePosition: Position.Right,
      targetPosition: Position.Left,
    };
  });
}

function workflowToReactFlowEdges(wf: WorkflowDetail): Edge[] {
  return wf.edges.map((e) => ({
    id: e.id,
    source: e.source,
    target: e.target,
    sourceHandle: e.sourceHandle ?? undefined,
    targetHandle: e.targetHandle ?? undefined,
    type: "smoothstep",
  }));
}

function ViewportZoomReporter({ onZoom }: { onZoom: (zoom: number) => void }) {
  const zoom = useStore((s) => s.transform[2]);
  useEffect(() => {
    onZoom(zoom);
  }, [zoom, onZoom]);
  return null;
}

// Re-export для удобства внешних компонентов.
export { Handle, Position };
