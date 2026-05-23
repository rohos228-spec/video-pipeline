"use client";

import { useQuery } from "@tanstack/react-query";
import { Info, FileText, Hash, Folder } from "lucide-react";
import { api } from "@/lib/api";
import { Badge } from "@/components/ui/badge";
import { ScrollArea } from "@/components/ui/scroll-area";
import { formatRelativeTime } from "@/lib/utils";
import { getNodeSpec } from "@/lib/node-catalog";

export function Inspector({
  projectId,
  selectedNodeKey,
}: {
  projectId: number | null;
  selectedNodeKey: string | null;
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
          {selectedNodeKey ? "Нода" : "Инспектор"}
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
            <NodeInspector nodeKey={selectedNodeKey} />
          )}
          {projectId != null && !selectedNodeKey && project.data && (
            <div className="flex flex-col gap-4">
              <div>
                <div className="text-[10px] uppercase tracking-wider text-muted-foreground">Тема</div>
                <div className="mt-1 text-sm font-medium leading-snug">
                  {project.data.topic}
                </div>
              </div>
              <Row icon={<Hash className="h-3.5 w-3.5" />} label="ID / slug">
                #{project.data.id} · <span className="font-mono text-xs">{project.data.slug}</span>
              </Row>
              <Row icon={<Folder className="h-3.5 w-3.5" />} label="Статус">
                <Badge variant="default">{project.data.status}</Badge>
              </Row>
              <Row label="Hero mode">{project.data.hero_mode}</Row>
              <Row label="Создан">{formatRelativeTime(project.data.created_at)}</Row>
              <Row label="Обновлён">{formatRelativeTime(project.data.updated_at)}</Row>
              {project.data.general_plan && (
                <div>
                  <div className="text-[10px] uppercase tracking-wider text-muted-foreground">
                    Общий план
                  </div>
                  <p className="mt-1 whitespace-pre-wrap rounded-md bg-muted/40 p-2.5 font-mono text-[11px] leading-relaxed text-foreground">
                    {project.data.general_plan}
                  </p>
                </div>
              )}
              {frames.data && frames.data.length > 0 && (
                <div>
                  <div className="flex items-center gap-2">
                    <FileText className="h-3.5 w-3.5 text-muted-foreground" />
                    <span className="text-[10px] uppercase tracking-wider text-muted-foreground">
                      Кадры ({frames.data.length})
                    </span>
                  </div>
                  <div className="mt-2 flex flex-col gap-1">
                    {frames.data.slice(0, 10).map((f) => (
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
                    {frames.data.length > 10 && (
                      <div className="px-2 py-1 text-[10px] text-muted-foreground">
                        +{frames.data.length - 10} ещё…
                      </div>
                    )}
                  </div>
                </div>
              )}
            </div>
          )}
        </div>
      </ScrollArea>
    </aside>
  );
}

function NodeInspector({ nodeKey }: { nodeKey: string }) {
  // nodeKey формата "n_plan" / "n_script" → восстанавливаем тип.
  const type = nodeKey.startsWith("n_") ? nodeKey.slice(2) : nodeKey;
  const spec = getNodeSpec(type);
  return (
    <div className="flex flex-col gap-3">
      <div>
        <div className="text-[10px] uppercase tracking-wider text-muted-foreground">Нода</div>
        <div className="mt-1 text-base font-semibold">{spec.label}</div>
        <div className="mt-1 text-[12px] text-muted-foreground">{spec.description}</div>
      </div>
      <Row label="Тип"><code className="font-mono text-[11px]">{spec.type}</code></Row>
      <Row label="Категория">{spec.category}</Row>
      <Row label="Ключ"><code className="font-mono text-[11px]">{nodeKey}</code></Row>
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
