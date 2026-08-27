"use client";

import { useQuery } from "@tanstack/react-query";
import { Info, FileText, Hash, Folder, ExternalLink, Copy, Sparkles } from "lucide-react";
import { toast } from "sonner";
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
import { OutseeGenPanel } from "@/components/inspector/outsee-gen-panel";
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
    <aside className="flex w-80 shrink-0 flex-col border-l border-white/[0.06] bg-black/30 backdrop-blur-xl">
      <div className="flex h-10 items-center gap-2 border-b border-white/[0.06] px-4">
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
              {project.data &&
                (nodeTypeFromKey(selectedNodeKey) === "images" ||
                  nodeTypeFromKey(selectedNodeKey) === "videos") && (
                  <OutseeGenPanel project={project.data} />
                )}
              {project.data ? <MontageHandoffCard project={project.data} /> : null}
              {onOpenNodeStudio && nodeTypeFromKey(selectedNodeKey) !== "topic" && (
                <Button
                  size="sm"
                  className="w-full h-10 text-xs font-semibold text-white bg-gradient-to-r from-violet-600 via-purple-600 to-indigo-600 hover:from-violet-500 hover:via-purple-500 hover:to-indigo-500 active:scale-[0.98] border border-purple-400/40 shadow-lg shadow-purple-600/30 rounded-xl backdrop-blur-md transition-all duration-200"
                  onClick={onOpenNodeStudio}
                >
                  Открыть ноду
                </Button>
              )}
            </div>
          )}
          {projectId != null && !selectedNodeKey && project.data && (
            <div className="flex flex-col gap-4">
              <OutseeGenPanel project={project.data} />
              <div className="rounded-xl border border-white/10 bg-white/[0.03] p-3.5 shadow-sm">
                <div className="text-[10px] font-semibold uppercase tracking-wider text-muted-foreground">
                  Проект
                </div>
                <div className="mt-1 text-sm font-semibold leading-snug text-foreground">
                  {projectDisplayName(project.data)}
                </div>
                {project.data.topic && project.data.title && project.data.topic.trim() !== project.data.title.trim() && (
                  <div className="mt-1.5 text-[11px] text-muted-foreground">
                    <span className="text-zinc-500">Тема: </span>{project.data.topic.trim()}
                  </div>
                )}
                <div className="mt-2.5 flex items-center justify-between border-t border-white/5 pt-2">
                  <span className="font-mono text-xs font-medium text-emerald-400">
                    #{project.data.id}
                  </span>
                  <button
                    type="button"
                    onClick={() => {
                      if (project.data?.slug) {
                        void navigator.clipboard.writeText(project.data.slug);
                        toast.success("Slug скопирован в буфер");
                      }
                    }}
                    className="flex items-center gap-1 font-mono text-[11px] text-muted-foreground hover:text-foreground transition-colors"
                    title="Кликните, чтобы скопировать slug"
                  >
                    <span className="truncate max-w-[170px]">{project.data.slug}</span>
                    <Copy className="h-3 w-3 shrink-0 opacity-60" />
                  </button>
                </div>
              </div>
              <Row icon={<Folder className="h-3.5 w-3.5 text-zinc-400" />} label="Статус">
                <Badge variant="default" className="text-xs font-semibold px-2 py-0.5">{formatProjectStatus(project.data.status)}</Badge>
              </Row>
              <Row label="Главный герой">{formatHeroMode(project.data.hero_mode)}</Row>
              <Row label="Создан">{formatRelativeTime(project.data.created_at)}</Row>
              <Row label="Обновлён">{formatRelativeTime(project.data.updated_at)}</Row>
              <MontageHandoffCard project={project.data} />
              <ProjectSettingsPanel project={project.data} />
              {project.data.general_plan && (
                <div className="mt-3">
                  <div className="text-xs font-bold uppercase tracking-wider text-zinc-300">
                    Сценарий
                  </div>
                  <div className="mt-2 whitespace-pre-wrap rounded-xl border border-zinc-800 bg-zinc-950/80 p-3.5 font-sans text-[13.5px] leading-relaxed text-zinc-100 shadow-inner">
                    {project.data.general_plan}
                  </div>
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
  if (type === "topic" && projectId != null) {
    return <TopicEditor projectId={projectId} />;
  }
  return (
    <div className="flex flex-col gap-3">
      <div>
        <div className="text-[10px] uppercase tracking-wider text-muted-foreground">Нода</div>
        <div className="mt-1 text-base font-semibold">{spec.label}</div>
        <div className="mt-1 text-[12px] text-muted-foreground">{spec.description}</div>
      </div>
      <Row label="Тип">{humanizeSlug(spec.type)}</Row>
      <Row label="Категория">{formatNodeCategory(spec.category)}</Row>
      <Row label="Ключ">{formatNodeKeyLabel(nodeKey)}</Row>
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
      <div className="flex items-center gap-1.5 text-xs font-semibold uppercase tracking-wider text-zinc-400">
        {icon}
        {label}
      </div>
      <div className="text-[13px] font-medium text-zinc-100">{children}</div>
    </div>
  );
}
