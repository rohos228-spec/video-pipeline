"use client";

import { useState } from "react";
import { useMutation } from "@tanstack/react-query";
import { Loader2, Sparkles, Send } from "lucide-react";
import { toast } from "sonner";
import { errorMessageFromUnknown } from "@/lib/error-message";
import { api } from "@/lib/api";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";
import { Textarea } from "@/components/ui/textarea";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";

export function AiNodeDialog({
  open,
  onOpenChange,
  projectId,
  nodeType,
  nodeLabel,
}: {
  open: boolean;
  onOpenChange: (o: boolean) => void;
  projectId: number | null;
  nodeType: string;
  nodeLabel: string;
}) {
  const [instruction, setInstruction] = useState("");
  const [composed, setComposed] = useState("");

  const compose = useMutation({
    mutationFn: () =>
      api.composePrompt({
        node_type: nodeType,
        project_id: projectId ?? undefined,
        vars: instruction.trim() ? { USER_HINT: instruction.trim() } : undefined,
      }),
    onSuccess: (r) => {
      setComposed(r.text);
      toast.success("Промт собран через GPT");
    },
    onError: (e) => toast.error(errorMessageFromUnknown(e)),
  });

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-2xl">
        <DialogHeader>
          <DialogTitle className="flex items-center gap-2">
            <Sparkles className="h-4 w-4 text-violet-400" />
            ИИ-помощник: {nodeLabel}
          </DialogTitle>
          <DialogDescription>
            Соберите или уточните промт для этой ноды. Текст уходит в шаблоны prompts/steps и blocks.
          </DialogDescription>
        </DialogHeader>
        <div className="flex flex-col gap-3">
          <Textarea
            value={instruction}
            onChange={(e) => setInstruction(e.target.value)}
            rows={3}
            placeholder="Подсказка для GPT (необязательно): «сделай тон веселее», «добавь киберпанк»…"
            className="text-sm"
          />
          <div className="flex gap-2">
            <Button size="sm" onClick={() => compose.mutate()} disabled={compose.isPending}>
              {compose.isPending ? (
                <Loader2 className="h-3.5 w-3.5 animate-spin" />
              ) : (
                <Send className="h-3.5 w-3.5" />
              )}
              Собрать промт
            </Button>
          </div>
          <Textarea
            value={composed}
            onChange={(e) => setComposed(e.target.value)}
            rows={14}
            className="font-mono text-[11px]"
            placeholder="Здесь появится собранный промт"
          />
        </div>
      </DialogContent>
    </Dialog>
  );
}

export function AiNodeButton({
  onClick,
  className,
}: {
  onClick: (e: React.MouseEvent) => void;
  className?: string;
}) {
  return (
    <button
      type="button"
      className={cn(
        "flex h-12 w-12 items-center justify-center rounded-full border border-violet-400/50 bg-gradient-to-br from-violet-500/40 to-amber-500/25 text-violet-100 shadow-xl shadow-black/50 hover:scale-110 hover:border-violet-300",
        className,
      )}
      onClick={onClick}
      title="ИИ-помощник для выбранной ноды"
    >
      <Sparkles className="h-5 w-5" />
    </button>
  );
}
