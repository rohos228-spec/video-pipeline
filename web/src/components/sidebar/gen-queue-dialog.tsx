"use client";

import { useMemo } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Loader2, ListOrdered } from "lucide-react";
import { toast } from "sonner";
import { errorMessageFromUnknown } from "@/lib/error-message";
import { api } from "@/lib/api";
import { getNodeSpec } from "@/lib/node-catalog";
import type { ProjectSummary, WorkflowNode } from "@/lib/types";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { cn } from "@/lib/utils";

const QUEUE_TARGET_TYPES = new Set([
  "plan",
  "script",
  "split",
  "hero",
  "items",
  "enrich_1",
  "enrich_2",
  "enrich_3",
  "enrich_4",
  "enrich_5",
  "image_prompts",
  "images",
  "animation_prompts",
  "videos",
  "audio",
  "music",
  "assemble",
  "publish",
]);

function workflowTargetNodes(nodes: WorkflowNode[]): WorkflowNode[] {
  const seen = new Set<string>();
  const out: WorkflowNode[] = [];
  for (const n of nodes) {
    if (!QUEUE_TARGET_TYPES.has(n.type) || seen.has(n.type)) continue;
    seen.add(n.type);
    out.push(n);
  }
  return out;
}

export function GenQueueDialog({
  open,
  onOpenChange,
  project,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  project: ProjectSummary | null;
}) {
  const qc = useQueryClient();
  const workflows = useQuery({
    queryKey: ["workflows"],
    queryFn: api.listWorkflows,
    enabled: open,
  });
  const defaultWf = useMemo(
    () => (workflows.data ?? []).find((w) => w.is_default) ?? workflows.data?.[0] ?? null,
    [workflows.data],
  );
  const workflow = useQuery({
    queryKey: ["workflow", defaultWf?.id],
    queryFn: () => api.getWorkflow(defaultWf!.id),
    enabled: open && defaultWf != null,
  });

  const targets = useMemo(
    () => workflowTargetNodes(workflow.data?.nodes ?? []),
    [workflow.data?.nodes],
  );

  const enqueue = useMutation({
    mutationFn: (body: {
      mode: "full" | "until_node";
      target_node_key?: string;
      target_node_type?: string;
    }) =>
      api.enqueueGenQueue({
        project_id: project!.id,
        ...body,
      }),
    onSuccess: (data) => {
      const positions = (data.gen_queue_positions || {}) as Record<string, number>;
      qc.setQueryData<ProjectSummary[]>(["projects"], (old) => {
        if (!old) return old;
        return old.map((p) => {
          const raw = positions[p.id] ?? positions[String(p.id)];
          return {
            ...p,
            gen_queue_position: typeof raw === "number" ? raw : null,
          };
        });
      });
      qc.invalidateQueries({ queryKey: ["projects"] });
      qc.invalidateQueries({ queryKey: ["sidebar-layout"] });
      const pos = data.position;
      toast.success(pos ? `Проект в очереди (#${pos})` : "Проект в очереди");
      onOpenChange(false);
    },
    onError: (e) => toast.error(errorMessageFromUnknown(e)),
  });

  const submitFull = () => {
    if (!project) return;
    if (!project.auto_mode) {
      toast.error("Включите режим ИИ (auto_mode) для проекта");
      return;
    }
    enqueue.mutate({ mode: "full" });
  };

  const submitUntil = (node: WorkflowNode) => {
    if (!project) return;
    if (!project.auto_mode) {
      toast.error("Включите режим ИИ (auto_mode) для проекта");
      return;
    }
    enqueue.mutate({
      mode: "until_node",
      target_node_key: node.id,
      target_node_type: node.type,
    });
  };

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-h-[85vh] max-w-md overflow-hidden sm:max-w-lg">
        <DialogHeader>
          <DialogTitle className="flex items-center gap-2">
            <ListOrdered className="h-4 w-4 text-sky-400" />
            Очередь генерации
          </DialogTitle>
          <DialogDescription>
            {project ? (
              <>
                Проект <span className="font-medium text-foreground">#{project.id}</span> — до
                какой ноды выполнить работу?
              </>
            ) : (
              "Выберите цель прогона"
            )}
          </DialogDescription>
        </DialogHeader>

        <div className="flex max-h-[50vh] flex-col gap-2 overflow-y-auto pr-1">
          <Button
            type="button"
            variant="default"
            className="h-auto justify-start px-3 py-2.5 text-left"
            disabled={enqueue.isPending || !project}
            onClick={submitFull}
          >
            <span className="font-medium">Выполнить проект полностью</span>
            <span className="mt-0.5 block text-[10px] font-normal text-primary-foreground/80">
              До публикации или конца пайплайна
            </span>
          </Button>

          <p className="pt-1 text-[10px] uppercase tracking-wider text-muted-foreground">
            Или до ноды (включительно)
          </p>

          {workflow.isLoading || workflows.isLoading ? (
            <div className="flex justify-center py-8">
              <Loader2 className="h-5 w-5 animate-spin text-muted-foreground" />
            </div>
          ) : (
            targets.map((node) => {
              const spec = getNodeSpec(node.type);
              const label =
                (node.data?.label as string | undefined)?.trim() || spec.label;
              return (
                <Button
                  key={node.id}
                  type="button"
                  variant="outline"
                  className={cn(
                    "h-auto justify-start px-3 py-2 text-left",
                    enqueue.isPending && "opacity-60",
                  )}
                  disabled={enqueue.isPending || !project}
                  onClick={() => submitUntil(node)}
                >
                  <span className="font-medium">{label}</span>
                  <span className="mt-0.5 block text-[10px] font-normal text-muted-foreground">
                    {spec.description}
                  </span>
                </Button>
              );
            })
          )}
        </div>

        <DialogFooter className="sm:justify-start">
          <p className="text-[10px] text-muted-foreground">
            Нужен включённый режим ИИ. HITL-точки при auto_mode проходятся автоматически.
          </p>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
