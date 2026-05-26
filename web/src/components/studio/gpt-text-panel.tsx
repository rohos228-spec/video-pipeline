"use client";

import { useEffect, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Loader2, RotateCcw, Save } from "lucide-react";
import { toast } from "sonner";
import { api } from "@/lib/api";
import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/textarea";
import { Badge } from "@/components/ui/badge";

export function GptTextPanel({
  projectId,
  stepCode,
}: {
  projectId: number;
  stepCode: string;
}) {
  const qc = useQueryClient();
  const [draft, setDraft] = useState("");
  const [dirty, setDirty] = useState(false);

  const data = useQuery({
    queryKey: ["gpt-text", projectId, stepCode],
    queryFn: () => api.getProjectGptText(projectId, stepCode),
    enabled: Boolean(projectId && stepCode),
  });

  useEffect(() => {
    if (data.data && !dirty) {
      setDraft(data.data.text);
    }
  }, [data.data, dirty]);

  useEffect(() => {
    setDirty(false);
  }, [stepCode, projectId]);

  const save = useMutation({
    mutationFn: () => api.saveProjectGptText(projectId, stepCode, draft),
    onSuccess: () => {
      toast.success("Текст для GPT сохранён");
      setDirty(false);
      qc.invalidateQueries({ queryKey: ["gpt-text", projectId, stepCode] });
      qc.invalidateQueries({ queryKey: ["project", projectId] });
    },
    onError: (e) => toast.error(String(e)),
  });

  const reset = useMutation({
    mutationFn: () => api.resetProjectGptText(projectId, stepCode),
    onSuccess: (r) => {
      setDraft(r.text);
      setDirty(false);
      toast.success("Сброшено к автоматическому тексту");
      qc.invalidateQueries({ queryKey: ["gpt-text", projectId, stepCode] });
      qc.invalidateQueries({ queryKey: ["project", projectId] });
    },
    onError: (e) => toast.error(String(e)),
  });

  if (data.isLoading) {
    return (
      <div className="flex justify-center py-12">
        <Loader2 className="h-6 w-6 animate-spin text-muted-foreground" />
      </div>
    );
  }

  if (data.data && !data.data.supported) {
    return (
      <p className="text-sm text-muted-foreground">
        Для этого шага нет редактируемого сопроводительного текста — он формируется автоматически
        или шаг не использует ChatGPT.
      </p>
    );
  }

  return (
    <section className="flex flex-col gap-3">
      <div className="flex flex-wrap items-center gap-2">
        <h3 className="text-xs font-medium uppercase tracking-wider text-muted-foreground">
          Текстовый вариант для ChatGPT
        </h3>
        {data.data?.is_override ? (
          <Badge variant="muted" className="text-[9px]">
            изменён вручную
          </Badge>
        ) : (
          <Badge variant="muted" className="text-[9px]">
            автоматический
          </Badge>
        )}
      </div>
      <p className="text-xs text-muted-foreground">
        {stepCode === "anim_pr" ? (
          <>
            Сопроводительное сообщение в диалог ChatGPT для каждого кадра (не мастер-промт из
            «Промт анимации»). При запуске шага подставляются плейсхолдеры{" "}
            <code className="text-[10px]">{"{{N}}"}</code>,{" "}
            <code className="text-[10px]">{"{{DURATION}}"}</code>,{" "}
            <code className="text-[10px]">{"{{VOICEOVER}}"}</code>,{" "}
            <code className="text-[10px]">{"{{IMAGE_PROMPT}}"}</code>.
          </>
        ) : (
          <>
            Короткое сопроводительное сообщение в ChatGPT (не мастер-промт). Мастер-промт и Excel
            уходят отдельными файлами-вложениями — их редактирование через «Файлы промтов» и Excel.
          </>
        )}
      </p>
      <Textarea
        value={draft}
        onChange={(e) => {
          setDraft(e.target.value);
          setDirty(true);
        }}
        rows={22}
        className="font-mono text-[11px] leading-relaxed"
        placeholder="Текст сопроводительного сообщения для GPT…"
      />
      <div className="flex flex-wrap gap-2">
        <Button size="sm" disabled={!dirty || save.isPending} onClick={() => save.mutate()}>
          {save.isPending ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Save className="h-3.5 w-3.5" />}
          Сохранить текст
        </Button>
        <Button
          size="sm"
          variant="outline"
          disabled={reset.isPending || !data.data?.is_override}
          onClick={() => reset.mutate()}
        >
          {reset.isPending ? (
            <Loader2 className="h-3.5 w-3.5 animate-spin" />
          ) : (
            <RotateCcw className="h-3.5 w-3.5" />
          )}
          Сбросить override
        </Button>
      </div>
    </section>
  );
}
