"use client";

import { useQuery } from "@tanstack/react-query";
import { Info, FileText, Hash, Folder, ExternalLink } from "lucide-react";
import { api } from "@/lib/api";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { ScrollArea } from "@/components/ui/scroll-area";
import { formatNodeCategory, formatNodeKeyLabel, formatHeroMode, formatProjectStatus, humanizeSlug } from "@/lib/format-labels";
import { projectDisplayName } from "@/lib/project-display";
import { formatRelativeTime } from "@/lib/utils";
import { getNodeSpec } from "@/lib/node-catalog";
import { nodeTypeFromKey } from "@/lib/node-key";
import { ProjectSettingsPanel } from "@/components/inspector/project-settings";
import { TopicEditor } from "@/components/inspector/topic-editor";
import { MontageHandoffCard } from "@/components/fleet/montage-handoff-card";
import { useUi } from "@/components/shell/topbar";

export function Inspector({
  projectId,
  selectedNodeKey,
  onOpenNodeStudio,
}: {
  projectId: number | null;
  selectedNodeKey: string | null;
  onOpenNodeStudio?: () => void;
}) {
  const project = useQuery({
    queryKey: ["project", projectId],
    queryFn: () => api.getProject(projectId!),
    enabled: projectId != null,
  });
  const frames = useQuery({
    queryKey: ["frames", projectId],
    queryFn: () => api.listFrames(projectId!),
    enabled: projectId != null,
    refetchInterval: 6000,
  });

  return (
    <aside className="flex w-80 shrink-0 flex-col border-l border-border bg-card/20">
      <div className="flex h-10 items-center gap-2 border-b border-border px-4">
        <Info className="h-3.5 w-3.5 text-muted-foreground" />
        <span className="text-xs font-medium uppercase tracking-wider text-muted-foreground">
          {selectedNodeKey
            ? nodeTypeFromKey(selectedNodeKey) === "topic"
              ? "Тема ролика"
              : "Нода"
            : "Инспектор"}
        </span>
      </div>
      <ScrollArea className="flex-1">
        <div className="flex flex-col gap-5 p-4 text-sm">
          {projectId == null && (
            <p className="text-xs text-muted-foreground">
              Выбери проект слева, чтобы увидеть детали.
            </p>
          )}
          {selectedNodeKey && (
            <div className="flex flex-col gap-3">
              <NodeInspector nodeKey={selectedNodeKey} projectId={projectId} />
              {project.data ? <MontageHandoffCard project={project.data} /> : null}
              {onOpenNodeStudio && nodeTypeFromKey(selectedNodeKey) !== "topic" && (
                <Button size="sm" variant="default" className="w-full" onClick={onOpenNodeStudio}>
                  Открыть студию ноды (GPT)
                </Button>
              )}
            </div>
          )}
          {projectId != null && !selectedNodeKey && project.data && (
            <div className="flex flex-col gap-4">
              <div>
                <div className="text-[10px] uppercase tracking-wider text-muted-foreground">Название</div>
                <div className="mt-1 text-sm font-medium leading-snug">
                  {projectDisplayName(project.data)}
                </div>
              </div>
              <div>
                <div className="text-[10px] uppercase tracking-wider text-muted-foreground">Тема ролика</div>
                <div className="mt-1 text-sm leading-snug text-muted-foreground">
                  {project.data.topic?.trim() || "— не задана (нода «Тема ролика»)"}
                </div>
              </div>
              <Row icon={<Hash className="h-3.5 w-3.5" />} label="ID / slug">
                #{project.data.id} · <span className="font-mono text-xs">{project.data.slug}</span>
              </Row>
              <Row icon={<Folder className="h-3.5 w-3.5" />} label="Статус">
                <Badge variant="default">{formatProjectStatus(project.data.status)}</Badge>
              </Row>
              <Row label="Главный герой">{formatHeroMode(project.data.hero_mode)}</Row>
              <Row label="Создан">{formatRelativeTime(project.data.created_at)}</Row>
              <Row label="Обновлён">{formatRelativeTime(project.data.updated_at)}</Row>
              <MontageHandoffCard project={project.data} />
              <ProjectSettingsPanel project={project.data} />
              {project.data.general_plan && (
                <div>
                  <div className="text-[10px] uppercase tracking-wider text-muted-foreground">
                    Сценарий
                  </div>
                  <p className="mt-1 whitespace-pre-wrap rounded-md bg-muted/40 p-2.5 font-mono text-[11px] leading-relaxed text-foreground">
                    {project.data.general_plan}
                  </p>
                </div>
              )}
              {frames.data && frames.data.length > 0 && (
                <FramesPreview projectId={projectId} count={frames.data.length} preview={frames.data.slice(0, 5)} />
              )}
            </div>
          )}
        </div>
      </ScrollArea>
    </aside>
  );
}

function FramesPreview({
  projectId,
  count,
  preview,
}: {
  projectId: number;
  count: number;
  preview: { id: number; number: number; voiceover_text: string }[];
}) {
  const ui = useUi();
  return (
    <div>
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-2">
          <FileText className="h-3.5 w-3.5 text-muted-foreground" />
          <span className="text-[10px] uppercase tracking-wider text-muted-foreground">
            Кадры ({count})
          </span>
        </div>
        <Button
          size="sm"
          variant="ghost"
          className="h-6 gap-1 px-1.5 text-[10px]"
          onClick={() => ui.openFrames(projectId)}
        >
          <ExternalLink className="h-3 w-3" />
          Открыть
        </Button>
      </div>
      <div className="mt-2 flex flex-col gap-1">
        {preview.map((f) => (
          <div
            key={f.id}
            className="flex items-start gap-2 rounded-md border border-border px-2 py-1.5"
          >
            <span className="mt-0.5 font-mono text-[10px] text-muted-foreground">
              #{f.number}
            </span>
            <span className="line-clamp-2 text-[11px] leading-snug">
              {f.voiceover_text}
            </span>
          </div>
        ))}
        {count > preview.length && (
          <button
            type="button"
            onClick={() => ui.openFrames(projectId)}
            className="px-2 py-1 text-left text-[10px] text-primary hover:underline"
          >
            +{count - preview.length} ещё — открыть все
          </button>
        )}
      </div>
    </div>
  );
}

function NodeInspector({
  nodeKey,
  projectId,
}: {
  nodeKey: string;
  projectId: number | null;
}) {
  const type = nodeTypeFromKey(nodeKey);
  const spec = getNodeSpec(type);
  return (
    <div className="flex flex-col gap-3">
      <div>
        <div className="text-[10px] uppercase tracking-wider text-muted-foreground">Нода</div>
        <div className="mt-1 text-base font-semibold">{spec.label}</div>
        <div className="mt-1 text-[12px] text-muted-foreground">{spec.description}</div>
      </div>
      {type === "topic" && projectId != null ? (
        <TopicEditor projectId={projectId} />
      ) : (
        <>
          <Row label="Тип">{humanizeSlug(spec.type)}</Row>
          <Row label="Категория">{formatNodeCategory(spec.category)}</Row>
          <Row label="Ключ">{formatNodeKeyLabel(nodeKey)}</Row>
        </>
      )}
    </div>
  );
}

function Row({
  label,
  icon,
  children,
}: {
  label: string;
  icon?: React.ReactNode;
  children: React.ReactNode;
}) {
  return (
    <div className="flex flex-col gap-1">
      <div className="flex items-center gap-1.5 text-[10px] uppercase tracking-wider text-muted-foreground">
        {icon}
        {label}
      </div>
      <div className="text-[12px]">{children}</div>
    </div>
  );
}
