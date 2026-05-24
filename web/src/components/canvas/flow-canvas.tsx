"use client";

import { useEffect, useMemo, useState, useCallback } from "react";
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
  type Connection,
  applyNodeChanges,
  addEdge,
  Position,
  Handle,
} from "@xyflow/react";
import "@xyflow/react/dist/style.css";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Copy, Loader2, Play, Save, Square, Trash2 } from "lucide-react";
import { toast } from "sonner";
import { api } from "@/lib/api";
import type {
  NodeRunDTO,
  WorkflowDetail,
  WorkflowEdge,
  WorkflowNode,
  WorkflowRunDetail,
} from "@/lib/types";
import { getNodeSpec, NODE_CATALOG } from "@/lib/node-catalog";
import { stepCodeForNodeType } from "@/lib/node-step-map";
import { formatRunStatus, formatStepCode } from "@/lib/format-labels";
import { nodeTypeFromKey } from "@/lib/node-key";
import { PipelineNode, type PipelineNodeData } from "./pipeline-node";
import { useRunEvents } from "@/hooks/use-bus";
import { Button } from "@/components/ui/button";
import { HitlBanner } from "@/components/hitl/hitl-banner";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";

const nodeTypes = {
  pipeline: PipelineNode,
};

export function FlowCanvas({
  projectId,
  selectedNodeKey,
  onSelectNode,
  onNodeActivate,
  disabledNodes = new Set<string>(),
}: {
  projectId: number | null;
  selectedNodeKey: string | null;
  onSelectNode: (key: string | null) => void;
  onNodeActivate?: (nodeKey: string, nodeType: string) => void;
  disabledNodes?: Set<string>;
}) {
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
  });

  // 2) Run для выбранного проекта (если есть).
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

  // Базовая структура графа — только при смене workflow (не при каждом poll run).
  const baseNodes = useMemo(() => {
    if (!workflow.data) return [];
    return workflowToReactFlowNodes(workflow.data, null);
  }, [workflow.data]);
  const baseEdges = useMemo(() => {
    if (!workflow.data) return [];
    return workflowToReactFlowEdges(workflow.data);
  }, [workflow.data]);

  const [nodes, setNodes] = useNodesState<Node<PipelineNodeData>>([]);
  const [edges, setEdges] = useEdgesState<Edge>([]);
  const [graphVersion, setGraphVersion] = useState<string>("");

  useEffect(() => {
    if (!workflow.data) return;
    const ver = `${workflow.data.id}:${workflow.data.updated_at}`;
    if (ver === graphVersion && nodes.length > 0) return;
    setGraphVersion(ver);
    setNodes(baseNodes as Node<PipelineNodeData>[]);
    setEdges(baseEdges);
  }, [workflow.data, baseNodes, baseEdges, graphVersion, nodes.length, setNodes, setEdges]);

  // Статусы run — отдельно, без сброса позиций нод.
  useEffect(() => {
    if (!run.data || nodes.length === 0) return;
    const nodeRunByKey = new Map(run.data.node_runs.map((nr) => [nr.node_key, nr]));
    setNodes((prev) =>
      prev.map((n) => {
        const nr = nodeRunByKey.get(n.id);
        if (!nr) return n;
        return {
          ...n,
          data: {
            ...n.data,
            status: nr.status as PipelineNodeData["status"],
            progress: nr.progress ?? 0,
            progressText: nr.progress_text ?? null,
            error: nr.error ?? null,
            attempts: nr.attempts ?? 0,
          },
        };
      }),
    );
  }, [run.data, setNodes, nodes.length]);

  // WS: обновления статуса нод (event-driven, без ожидания polling).
  useRunEvents(run.data?.id ?? null, (evt) => {
    if (
      typeof evt === "object" &&
      evt !== null &&
      (evt as { type?: string }).type === "node_status_changed"
    ) {
      const e = evt as { node_type: string; to: string };
      setNodes((prev) =>
        prev.map((n) => {
          if (n.data.type === e.node_type) {
            return {
              ...n,
              data: { ...n.data, status: e.to as PipelineNodeData["status"] },
            };
          }
          return n;
        })
      );
    }
  });

  const onNodesChange = useCallback(
    (changes: NodeChange[]) =>
      setNodes((ns) => applyNodeChanges(changes, ns) as Node<PipelineNodeData>[]),
    [setNodes]
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
      toast.message("Связь добавлена");
    },
    [setEdges],
  );

  const [saving, setSaving] = useState(false);

  const persistWorkflow = useCallback(async () => {
    if (!workflow.data) return;
    setSaving(true);
    try {
      const wfNodes: WorkflowNode[] = nodes.map((n) => ({
        id: n.id,
        type: (n.data as PipelineNodeData).type,
        position: n.position,
        data: { label: getNodeSpec((n.data as PipelineNodeData).type).label },
      }));
      const wfEdges: WorkflowEdge[] = edges.map((e) => ({
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
      await api.saveWorkflow(workflow.data.id, {
        nodes: wfNodes,
        edges: wfEdges,
      });
      toast.success("Граф сохранён");
      await workflow.refetch();
      if (projectId) {
        await api.ensureProjectRun(projectId).catch(() => undefined);
      }
    } catch (e) {
      toast.error(`Не сохранилось: ${String(e)}`);
    } finally {
      setSaving(false);
    }
  }, [workflow, nodes, edges, projectId]);

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

  const addNode = useCallback(
    (type: string) => {
      if (!workflow.data) return;
      const id = `n_${type}_${Date.now()}`;
      const maxX = nodes.reduce((m, n) => Math.max(m, n.position.x), 80);
      const spec = getNodeSpec(type);
      const newNode: Node<PipelineNodeData> = {
        id,
        type: "pipeline",
        position: { x: maxX + 290, y: 200 },
        data: {
          nodeKey: id,
          type,
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
      if (nodes.length > 0) {
        const last = nodes[nodes.length - 1];
        setEdges((prev) => [
          ...prev,
          {
            id: `e_${last.id}_${id}`,
            source: last.id,
            target: id,
            sourceHandle: "out",
            targetHandle: "in",
            type: "smoothstep",
          },
        ]);
      }
      toast.message(`Добавлена нода: ${spec.label}`);
    },
    [workflow.data, nodes, setNodes, setEdges]
  );

  const deleteSelectedNode = useCallback(() => {
    if (!selectedNodeKey) return;
    setNodes((prev) => prev.filter((n) => n.id !== selectedNodeKey));
    setEdges((prev) =>
      prev.filter((e) => e.source !== selectedNodeKey && e.target !== selectedNodeKey)
    );
    onSelectNode(null);
    toast.success("Нода удалена (нажми «Сохранить граф»)");
  }, [selectedNodeKey, setNodes, setEdges, onSelectNode]);

  if (workflows.isLoading || workflow.isLoading) {
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
      <ReactFlow
        nodes={nodes}
        edges={edges}
        onNodesChange={onNodesChange}
        nodeTypes={nodeTypes}
        fitView
        fitViewOptions={{ padding: 0.12, maxZoom: 0.85, minZoom: 0.2 }}
        minZoom={0.15}
        maxZoom={1.5}
        proOptions={{ hideAttribution: true }}
        nodesDraggable
        nodesConnectable
        edgesReconnectable
        nodeDragThreshold={6}
        selectNodesOnDrag={false}
        onConnect={onConnect}
        connectionLineStyle={{ strokeDasharray: "6 4", stroke: "hsl(var(--primary))" }}
        elementsSelectable
        deleteKeyCode={["Backspace", "Delete"]}
        onNodeClick={(_, node) => {
          const d = node.data as PipelineNodeData;
          onSelectNode(d.nodeKey);
          onNodeActivate?.(d.nodeKey, d.type);
        }}
        onSelectionChange={(sel) => {
          const first = sel.nodes[0];
          if (first) {
            onSelectNode((first.data as PipelineNodeData).nodeKey);
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
        <Controls position="bottom-right" showInteractive={false} />
        <MiniMap
          pannable
          zoomable
          position="bottom-left"
          style={{ width: 168, height: 112 }}
          nodeColor={(node) => {
            const data = node.data as PipelineNodeData;
            if (data.status === "running") return "hsl(var(--primary))";
            if (data.status === "done") return "hsl(var(--success))";
            if (data.status === "failed") return "hsl(var(--destructive))";
            if (data.status === "waiting_hitl") return "hsl(var(--warning))";
            return "hsl(var(--muted-foreground) / 0.4)";
          }}
          nodeStrokeWidth={2}
          nodeBorderRadius={4}
          maskColor="hsl(var(--background) / 0.7)"
        />
      </ReactFlow>
      <WorkflowToolbar
        onSave={persistWorkflow}
        saving={saving}
        onAddNode={addNode}
        onDelete={deleteSelectedNode}
        canDelete={!!selectedNodeKey}
        onDuplicateBelow={() => {
          if (nodes.length === 0) return;
          const offsetY = 480;
          const stamp = Date.now();
          const idMap = new Map<string, string>();
          const clones = nodes.map((n) => {
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
          const cloneEdges = edges.map((e) => ({
            ...e,
            id: `${e.id}_lane_${stamp}`,
            source: idMap.get(e.source) ?? e.source,
            target: idMap.get(e.target) ?? e.target,
          }));
          setNodes((prev) => [...prev, ...clones]);
          setEdges((prev) => [...prev, ...cloneEdges]);
          toast.success("Граф продублирован вниз — сохраните при необходимости");
        }}
      />
      <RunOverlay
        projectId={projectId}
        workflow={workflow.data ?? null}
        run={run.data ?? null}
        selectedNodeKey={selectedNodeKey}
        onRunCreated={() => run.refetch()}
      />
      <HitlBanner projectId={projectId} />
    </>
  );
}

function WorkflowToolbar({
  onSave,
  saving,
  onAddNode,
  onDelete,
  canDelete,
  onDuplicateBelow,
}: {
  onSave: () => void;
  saving: boolean;
  onAddNode: (type: string) => void;
  onDelete: () => void;
  canDelete: boolean;
  onDuplicateBelow: () => void;
}) {
  const addable = Object.keys(NODE_CATALOG).filter((t) => !t.startsWith("hitl_"));
  return (
    <div className="pointer-events-none absolute left-4 top-4 z-10 flex max-w-[calc(100%-2rem)] flex-wrap gap-2">
      <div className="pointer-events-auto flex flex-wrap items-center gap-1 rounded-lg border border-border bg-card/80 p-1 backdrop-blur-sm">
        <select
          className="h-8 max-w-[140px] rounded-md bg-transparent px-2 text-xs"
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
          onClick={onDuplicateBelow}
          title="Дублировать весь граф ниже (массовые потоки)"
        >
          <Copy className="h-3.5 w-3.5" />
        </Button>
        <Button
          size="sm"
          variant="ghost"
          className="h-8 gap-1 text-xs text-destructive"
          onClick={onDelete}
          disabled={!canDelete}
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
        Каждый проект — это граф из 19 нод: от плана и сценария до сборки и публикации.
        Кликни «+» в сайдбаре, чтобы создать новый ролик.
      </p>
    </div>
  );
}

function RunOverlay({
  projectId,
  workflow,
  run,
  selectedNodeKey,
  onRunCreated,
}: {
  projectId: number;
  workflow: WorkflowDetail | null;
  run: WorkflowRunDetail | null;
  selectedNodeKey: string | null;
  onRunCreated: () => void;
}) {
  const [busy, setBusy] = useState(false);
  const [pausing, setPausing] = useState(false);
  const [massOpen, setMassOpen] = useState(false);
  const [massCount, setMassCount] = useState("3");
  const qc = useQueryClient();
  if (!workflow) return null;

  const nodeType = nodeTypeFromKey(selectedNodeKey);
  const stepCode = stepCodeForNodeType(nodeType);

  const handlePause = async () => {
    setPausing(true);
    try {
      await api.pauseProject(projectId);
      toast.success("Проект на паузе");
      onRunCreated();
    } catch (e) {
      toast.error(String(e));
    } finally {
      setPausing(false);
    }
  };

  const handleResume = async () => {
    setPausing(true);
    try {
      await api.resumeProject(projectId);
      toast.success("Проект продолжен");
      onRunCreated();
    } catch (e) {
      toast.error(String(e));
    } finally {
      setPausing(false);
    }
  };

  const handleStopProject = async () => {
    setBusy(true);
    try {
      const r = await api.stopProject(projectId);
      toast.success(r.message || "⏹ Шаг остановлен");
      qc.invalidateQueries({ queryKey: ["project", projectId] });
      qc.invalidateQueries({ queryKey: ["project-run", projectId] });
      qc.invalidateQueries({ queryKey: ["runs"] });
      onRunCreated();
    } catch (e) {
      toast.error(String(e));
    } finally {
      setBusy(false);
    }
  };

  const handleMassStart = async () => {
    const count = Math.max(1, Math.min(20, parseInt(massCount, 10) || 1));
    setBusy(true);
    try {
      const r = await api.startMassLanes(projectId, { count });
      toast.success(`Создано ${r.count} массовых потоков (auto_mode)`);
      qc.invalidateQueries({ queryKey: ["projects"] });
      setMassOpen(false);
    } catch (e) {
      toast.error(String(e));
    } finally {
      setBusy(false);
    }
  };

  const handleCancelRun = async () => {
    if (!run) return;
    setBusy(true);
    try {
      await api.cancelRun(run.id);
      toast.success("Run отменён");
      onRunCreated();
    } catch (e) {
      toast.error(String(e));
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
      toast.error(String(e));
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
      await api.runProjectStep(projectId, stepCode);
      onRunCreated();
      toast.success(`Run #${created.id} · шаг «${formatStepCode(stepCode)}» запущен`, {
        description: "Воркер подхватит шаг — HITL в веб-UI",
      });
    } catch (e) {
      toast.error(`Не получилось запустить: ${String(e)}`);
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
      <Button
        size="sm"
        variant="outline"
        onClick={() => setMassOpen(true)}
        disabled={busy}
        className="pointer-events-auto text-xs"
      >
        Массовая
      </Button>
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
        title="Как ⏹ в Telegram: откат running-шага + выкл. auto_mode"
      >
        {busy ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Square className="h-3.5 w-3.5 fill-current" />}
        ⏹ Стоп
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
        disabled={busy}
        className="pointer-events-auto gap-1.5"
      >
        {busy ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Play className="h-3.5 w-3.5" />}
        {run ? "Перезапустить" : "Создать Run"}
      </Button>
    </div>
    <Dialog open={massOpen} onOpenChange={setMassOpen}>
      <DialogContent className="max-w-sm">
        <DialogHeader>
          <DialogTitle>Массовая генерация</DialogTitle>
          <DialogDescription>
            Создаёт копии проекта с auto_mode (GPT-проверка), как массовый батч в боте.
          </DialogDescription>
        </DialogHeader>
        <div className="flex flex-col gap-2">
          <label className="text-xs text-muted-foreground">Число потоков (1–20)</label>
          <Input
            value={massCount}
            onChange={(e) => setMassCount(e.target.value)}
            type="number"
            min={1}
            max={20}
          />
        </div>
        <DialogFooter>
          <Button variant="ghost" onClick={() => setMassOpen(false)}>
            Отмена
          </Button>
          <Button onClick={handleMassStart} disabled={busy}>
            {busy ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : null}
            Запустить
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
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
  return wf.nodes.map((n) => {
    const nr = nodeRunByKey.get(n.id);
    return {
      id: n.id,
      type: "pipeline",
      position: n.position,
      data: {
        nodeKey: n.id,
        type: n.type,
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

// Re-export для удобства внешних компонентов.
export { Handle, Position };
