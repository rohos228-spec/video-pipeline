"use client";

import { useEffect, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Loader2 } from "lucide-react";
import { toast } from "sonner";
import { errorMessageFromUnknown } from "@/lib/error-message";
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

  const topicFromApi = project.data?.topic?.trim() || project.data?.title?.trim() || "";
  const seed = initialTopic !== undefined && initialTopic.trim() ? initialTopic.trim() : topicFromApi;
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
    onError: (e) => toast.error(errorMessageFromUnknown(e)),
  });

  return (
    <div className="flex flex-col gap-3.5">
      <div>
        <p className="text-[13.5px] font-bold text-zinc-100 leading-snug">
          Тема задаёт направление, смысл и сюжет всего ролика.
        </p>
        <p className="mt-1.5 text-[12.5px] text-zinc-300 leading-relaxed">
          💡 <span className="font-semibold text-zinc-200">Например:</span> «Полет в космос: от первого спутника до марсианских баз» или подробное описание вашей идеи.
        </p>
      </div>

      <textarea
        value={topic}
        onChange={(e) => setTopic(e.target.value)}
        rows={6}
        placeholder="Например: Полет в космос"
        className="topic-input-glow flex w-full rounded-xl bg-zinc-950/80 p-3 text-sm text-zinc-100 placeholder:text-zinc-500 transition-all duration-200 disabled:cursor-not-allowed disabled:opacity-50"
        autoFocus
      />

      <Button
        size="sm"
        className="w-full h-9 gap-1.5 text-xs font-semibold text-white bg-cyan-600 hover:bg-cyan-500 border border-cyan-400/50 shadow-md shadow-cyan-500/25 rounded-xl transition-all duration-150 disabled:opacity-50"
        disabled={!topic.trim() || save.isPending}
        onClick={() => save.mutate()}
      >
        {save.isPending ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : null}
        Сохранить тему
      </Button>
    </div>
  );
}
