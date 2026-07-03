"use client";

import { useMutation, useQueryClient } from "@tanstack/react-query";
import { CheckCircle2, CircleDashed, Loader2 } from "lucide-react";
import { toast } from "sonner";
import { api } from "@/lib/api";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";

/**
 * Переключатель "блочные промты v2" на уровне проекта.
 *
 * Реальная сборка промта (`compose_step()`) использует
 * `prompts/steps/<id>/template.md` только если у проекта включён
 * `prompt_overrides.use_blocks_v2` (или задан непустой `blocks`) —
 * иначе используется старый файл `prompts/<step>/default.md`. Без этого
 * тумблера редактирование блочного шаблона ничего не меняло бы в реальной
 * генерации, поэтому он обязателен рядом с блочным редактором.
 */
export function BlocksV2Toggle({
  projectId,
  enabled,
}: {
  projectId: number;
  enabled: boolean;
}) {
  const qc = useQueryClient();
  const toggle = useMutation({
    mutationFn: (next: boolean) =>
      api.patchProjectPromptConfig(projectId, { use_blocks_v2: next }),
    onSuccess: (_, next) => {
      toast.success(next ? "Блочные промты v2 включены для проекта" : "Возврат к старым файлам-промтам");
      qc.invalidateQueries({ queryKey: ["project", projectId] });
    },
    onError: (e) => toast.error(String(e)),
  });

  return (
    <div
      className={cn(
        "flex items-center justify-between gap-2 rounded-xl border p-3",
        enabled ? "border-emerald-400/30 bg-emerald-400/[0.04]" : "border-white/10 bg-white/[0.02]",
      )}
    >
      <div className="flex items-center gap-2 text-xs">
        {enabled ? (
          <CheckCircle2 className="h-4 w-4 text-emerald-400" />
        ) : (
          <CircleDashed className="h-4 w-4 text-muted-foreground" />
        )}
        <div>
          <p className="font-medium text-foreground/90">
            Блочные промты v2: {enabled ? "включены" : "выключены"}
          </p>
          <p className="text-[10px] text-muted-foreground">
            {enabled
              ? "Генерация использует steps/<шаг>/template.md (блоки ниже)."
              : "Генерация использует старый файл prompts/<шаг>/*.md — блоки ниже пока ни на что не влияют."}
          </p>
        </div>
      </div>
      <Button
        size="sm"
        variant={enabled ? "outline" : "default"}
        className="h-7 shrink-0 gap-1 px-2 text-[10px]"
        onClick={() => toggle.mutate(!enabled)}
        disabled={toggle.isPending}
      >
        {toggle.isPending ? <Loader2 className="h-3 w-3 animate-spin" /> : null}
        {enabled ? "Выключить" : "Включить"}
      </Button>
    </div>
  );
}
