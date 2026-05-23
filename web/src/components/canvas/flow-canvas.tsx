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
  applyNodeChanges,
  Position,
  Handle,
} from "@xyflow/react";
import "@xyflow/react/dist/style.css";
import { useQuery } from "@tanstack/react-query";
import { Loader2, Play, Save, Trash2 } from "lucide-react";
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
import { PipelineNode, type PipelineNodeData } from "./pipeline-node";
import { useRunEvents } from "@/hooks/use-bus";
import { Button } from "@/components/ui/button";
import { HitlBanner } from "@/components/hitl/hitl-banner";

const nodeTypes = {
  pipeline: PipelineNode,
};

export function FlowCanvas({
  projectId,
  selectedNodeKey,
  onSelectNode,
}: {
  projectId: number | null;
  selectedNodeKey: string | null;
  onSelectNode: (key: string | null) => void;
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

  // 3) Конвертим Workflow + Run в React Flow ноды.
  const initialNodes = useMemo(() => {
    if (!workflow.data) return [];
    return workflowToReactFlowNodes(workflow.data, run.data ?? null);
  }, [workflow.data, run.data]);
  const initialEdges = useMemo(() => {
    if (!workflow.data) return [];
    return workflowToReactFlowEdges(workflow.data);
  }, [workflow.data]);

  const [nodes, setNodes] = useNodesState<Node<PipelineNodeData>>([]);
  const [edges, setEdges] = useEdgesState<Edge>([]);

  // Каждый раз когда меняется initialNodes/Edges — синхронизируем.
  useEffect(() => {
    setNodes(initialNodes as Node<PipelineNodeData>[]);
  }, [initialNodes, setNodes]);
  useEffect(() => {
    setEdges(initialEdges);
  }, [initialEdges, setEdges]);

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
      await api.saveWorkflow(workflow.data.id, {
        nodes: wfNodes,
        edges: wfEdges,
      });
      toast.success("Граф сохранён");
      await workflow.refetch();
    } catch (e) {
      toast.error(`Не сохранилось: ${String(e)}`);
    } finally {
      setSaving(false);
    }
  }, [workflow, nodes, edges]);

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
        nodesConnectable={false}
        elementsSelectable
        onSelectionChange={(sel) => {
          const first = sel.nodes[0];
          onSelectNode(first ? (first.data as PipelineNodeData).nodeKey : null);
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
      />
      <RunOverlay
        projectId={projectId}
        workflow={workflow.data ?? null}
        run={run.data ?? null}
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
}: {
  onSave: () => void;
  saving: boolean;
  onAddNode: (type: string) => void;
  onDelete: () => void;
  canDelete: boolean;
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
  onRunCreated,
}: {
  projectId: number;
  workflow: WorkflowDetail | null;
  run: WorkflowRunDetail | null;
  onRunCreated: () => void;
}) {
  const [busy, setBusy] = useState(false);
  if (!workflow) return null;

  const handleStart = async () => {
    setBusy(true);
    try {
      const created = await api.startRunFromWorkflow(workflow.id, {
        project_id: projectId,
      });
      await api.runProjectStep(projectId, "plan");
      onRunCreated();
      toast.success(`Run #${created.id} · шаг «План» запущен`, {
        description: "Воркер подхватит planning — HITL в веб-UI",
      });
    } catch (e) {
      toast.error(`Не получилось запустить: ${String(e)}`);
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="pointer-events-none absolute right-4 top-4 z-10 flex items-center gap-2">
      <div className="pointer-events-auto flex items-center gap-2 rounded-lg border border-border bg-card/70 px-3 py-1.5 text-xs shadow-sm backdrop-blur-sm">
        <span className="text-muted-foreground">Run:</span>
        <span className="font-mono font-medium">
          {run ? `#${run.id} · ${run.status}` : "не запущен"}
        </span>
      </div>
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
