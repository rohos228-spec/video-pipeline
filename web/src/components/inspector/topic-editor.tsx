"use client";

import { useEffect, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Loader2 } from "lucide-react";
import { toast } from "sonner";
import { api } from "@/lib/api";
import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/textarea";

export function TopicEditor({
  projectId,
  initialTopic,
}: {
  projectId: number;
  /** Если не передано — читаем из API. */
  initialTopic?: string;
}) {
  const qc = useQueryClient();
  const project = useQuery({
    queryKey: ["project", projectId],
    queryFn: () => api.getProject(projectId),
    enabled: initialTopic === undefined,
  });

  const topicFromApi = project.data?.topic?.trim() ?? "";
  const seed = initialTopic !== undefined ? initialTopic.trim() : topicFromApi;
  const [topic, setTopic] = useState(seed);

  useEffect(() => {
    setTopic(seed);
  }, [seed, projectId]);

  const save = useMutation({
    mutationFn: () => api.patchProject(projectId, { topic: topic.trim() }),
    onSuccess: () => {
      toast.success("Тема ролика сохранена");
      qc.invalidateQueries({ queryKey: ["project", projectId] });
    },
    onError: (e) => toast.error(String(e)),
  });

  return (
    <div className="flex flex-col gap-3">
      <p className="text-xs leading-relaxed text-muted-foreground">
        Тема задаёт направление всего ролика — как в боте перед шагом «Сценарий». Может быть
        длинным описанием идеи.
      </p>
      <Textarea
        value={topic}
        onChange={(e) => setTopic(e.target.value)}
        rows={6}
        placeholder="Например: Почему кошки всегда приземляются на лапы"
        className="text-sm"
        autoFocus
      />
      <Button
        size="sm"
        className="w-full"
        disabled={!topic.trim() || save.isPending}
        onClick={() => save.mutate()}
      >
        {save.isPending ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : null}
        Сохранить тему
      </Button>
    </div>
  );
}
