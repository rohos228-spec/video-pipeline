"use client";

import { useEffect, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Loader2, Save, ShieldAlert } from "lucide-react";
import { toast } from "sonner";
import { api, type StepTemplateBlock } from "@/lib/api";
import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/textarea";
import { cn } from "@/lib/utils";

const isTechnicalBlock = (title: string) => title.trim().toUpperCase().includes("ТЕХНИЧЕСКАЯ ЧАСТЬ");

/**
 * Визуальный блочный редактор `prompts/steps/<id>/template.md` — каждый
 * `## N. ЗАГОЛОВОК` секции шаблона показывается отдельной карточкой
 * (5-7 штук), а не одним текстовым полем. Блок 1 (техническая часть)
 * визуально выделен — его редактирование меняет протокол ввода-вывода
 * шага, а не творческое содержимое.
 *
 * Внутри тела блока могут быть плейсхолдеры `{{BLOCK:категория}}` — что
 * именно в них подставится (файл из библиотеки / свой текст / вес),
 * настраивается отдельно в панели ниже (`BlocksWeightPanel`).
 */
export function StepBlocksEditor({ stepId }: { stepId: string }) {
  const qc = useQueryClient();
  const template = useQuery({
    queryKey: ["step-template", stepId],
    queryFn: () => api.getStepTemplate(stepId),
  });

  const [draft, setDraft] = useState<StepTemplateBlock[] | null>(null);
  const [dirty, setDirty] = useState(false);

  useEffect(() => {
    if (!template.data || dirty) return;
    setDraft(template.data.blocks);
  }, [template.data, dirty]);

  const save = useMutation({
    mutationFn: (blocks: StepTemplateBlock[]) => api.saveStepTemplate(stepId, blocks),
    onSuccess: (data) => {
      toast.success(`Шаблон «${stepId}» сохранён`);
      setDirty(false);
      setDraft(data.blocks);
      qc.invalidateQueries({ queryKey: ["step-template", stepId] });
    },
    onError: (e) => toast.error(String(e)),
  });

  const updateBody = (number: number, body: string) => {
    setDraft((prev) => (prev ? prev.map((b) => (b.number === number ? { ...b, body } : b)) : prev));
    setDirty(true);
  };

  if (template.isLoading) {
    return (
      <div className="flex items-center justify-center py-8">
        <Loader2 className="h-5 w-5 animate-spin text-muted-foreground" />
      </div>
    );
  }

  if (template.isError || !draft) {
    return (
      <p className="text-xs text-muted-foreground">
        Для этого шага нет блочного шаблона (steps/{stepId}/template.md).
      </p>
    );
  }

  return (
    <section className="flex flex-col gap-3">
      <div className="flex items-center justify-between gap-2">
        <div>
          <h3 className="text-xs font-medium uppercase tracking-wider text-amber-400/90">
            Блочный промт ({draft.length} блок{draft.length === 1 ? "" : "ов"})
          </h3>
          <p className="mt-0.5 font-mono text-[10px] text-muted-foreground/70">
            prompts/steps/{stepId}/template.md
          </p>
        </div>
        <Button
          size="sm"
          variant={dirty ? "default" : "outline"}
          className="h-7 gap-1 px-2 text-[10px]"
          onClick={() => draft && save.mutate(draft)}
          disabled={!dirty || save.isPending}
        >
          {save.isPending ? (
            <Loader2 className="h-3 w-3 animate-spin" />
          ) : (
            <Save className="h-3 w-3" />
          )}
          Сохранить все блоки
        </Button>
      </div>

      <div className="flex flex-col gap-2">
        {draft.map((block) => {
          const technical = isTechnicalBlock(block.title);
          return (
            <div
              key={block.number}
              className={cn(
                "rounded-xl border p-3",
                technical
                  ? "border-amber-400/30 bg-amber-400/[0.04]"
                  : "border-white/10 bg-white/[0.02]",
              )}
            >
              <div className="mb-1.5 flex items-center gap-1.5">
                <span
                  className={cn(
                    "flex h-5 w-5 shrink-0 items-center justify-center rounded-full text-[10px] font-semibold",
                    technical ? "bg-amber-400/20 text-amber-300" : "bg-white/10 text-foreground/70",
                  )}
                >
                  {block.number}
                </span>
                <h4 className="text-[11px] font-medium uppercase tracking-wide text-foreground/90">
                  {block.title}
                </h4>
                {technical && (
                  <span className="ml-auto flex items-center gap-1 text-[9px] text-amber-300/80">
                    <ShieldAlert className="h-3 w-3" />
                    технический — меняет протокол ввода/вывода шага
                  </span>
                )}
              </div>
              <Textarea
                value={block.body}
                onChange={(e) => updateBody(block.number, e.target.value)}
                rows={technical ? 4 : 3}
                className="font-mono text-[10px] leading-relaxed"
              />
            </div>
          );
        })}
      </div>
      <p className="text-[10px] text-muted-foreground">
        Плейсхолдеры <span className="font-mono">{"{{BLOCK:категория}}"}</span> и{" "}
        <span className="font-mono">{"{{VAR:ИМЯ}}"}</span> подставляются автоматически при сборке
        промта. Что подставится в <span className="font-mono">{"{{BLOCK:...}}"}</span> — настраивается
        в панели «Блоки промта — вес и содержимое» ниже.
      </p>
    </section>
  );
}
