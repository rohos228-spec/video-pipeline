"use client";

import { useEffect, useState } from "react";
import { useMutation, useQuery } from "@tanstack/react-query";
import { Loader2, Play } from "lucide-react";
import { toast } from "sonner";
import { errorMessageFromUnknown } from "@/lib/error-message";
import { api } from "@/lib/api";
import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/textarea";
import { Badge } from "@/components/ui/badge";

export function GptVerdictPanel({
  projectId,
  stepCode,
}: {
  projectId: number;
  stepCode: string;
}) {
  const [draft, setDraft] = useState("");
  const [dirty, setDirty] = useState(false);
  const [lastRaw, setLastRaw] = useState("");

  const ctx = useQuery({
    queryKey: ["gpt-verdict", projectId, stepCode],
    queryFn: () => api.getGptVerdictContext(projectId, stepCode),
    enabled: Boolean(projectId && stepCode),
  });

  useEffect(() => {
    if (ctx.data && !dirty) {
      setDraft(ctx.data.prompt);
    }
  }, [ctx.data, dirty]);

  useEffect(() => {
    setDirty(false);
    setLastRaw("");
  }, [stepCode, projectId]);

  const run = useMutation({
    mutationFn: () => api.runGptVerdict(projectId, stepCode, draft),
    onSuccess: (r) => {
      setLastRaw(r.last_raw);
      if (r.approved) {
        toast.success("GPT: одобрено");
      } else {
        toast.error(r.fix_text || "GPT: не одобрено");
      }
    },
    onError: (e) => toast.error(errorMessageFromUnknown(e)),
  });

  if (ctx.isLoading) {
    return (
      <div className="flex items-center gap-2 text-sm text-muted-foreground">
        <Loader2 className="h-4 w-4 animate-spin" />
        Загрузка проверки…
      </div>
    );
  }

  if (ctx.isError) {
    return (
      <p className="text-sm text-destructive">
        {errorMessageFromUnknown(ctx.error)}
      </p>
    );
  }

  const attachments = ctx.data?.attachments ?? [];

  return (
    <div className="flex flex-col gap-3">
      <div className="flex flex-wrap items-center gap-2">
        <span className="text-sm font-medium">Проверка GPT (формат «Вердикт»)</span>
        {attachments.length > 0 && (
          <Badge variant="muted">{attachments.join(", ")}</Badge>
        )}
      </div>
      <Textarea
        value={draft}
        onChange={(e) => {
          setDraft(e.target.value);
          setDirty(true);
        }}
        rows={12}
        className="font-mono text-xs"
      />
      <Button
        size="sm"
        className="w-fit"
        disabled={run.isPending}
        onClick={() => run.mutate()}
      >
        {run.isPending ? (
          <Loader2 className="mr-2 h-4 w-4 animate-spin" />
        ) : (
          <Play className="mr-2 h-4 w-4" />
        )}
        Запустить проверку
      </Button>
      {lastRaw ? (
        <pre className="max-h-64 overflow-auto rounded-md border bg-muted/40 p-3 text-xs whitespace-pre-wrap">
          {lastRaw}
        </pre>
      ) : null}
    </div>
  );
}
