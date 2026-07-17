"use client";

import { BlocksV2Toggle } from "@/components/studio/blocks-v2-toggle";
import { BlocksWeightPanel } from "@/components/studio/blocks-weight-panel";
import { PromptFilesPanel } from "@/components/studio/prompt-files-panel";
import { StepBlocksEditor } from "@/components/studio/step-blocks-editor";

/**
 * Отображение блочных промтов из ветки newera при «Конструктор промтов»:
 * слева — карточки ## N. из steps/<id>/template.md,
 * справа — вес/файл/свой текст для {{BLOCK:cat}}.
 */
export function NeweraBlocksConstructor({
  projectId,
  stepId,
  promptOverrides,
  blocksV2Enabled,
  promptStepCode,
  slotId,
  preferredFile,
  folderHint,
  activeVariant,
  activeVariantSourceLabel,
  onActivateVariant,
  activating,
  legacyDirLabel,
}: {
  projectId: number;
  stepId: string;
  promptOverrides: Record<string, unknown>;
  blocksV2Enabled: boolean;
  promptStepCode: string;
  slotId?: string;
  preferredFile?: string;
  folderHint?: string;
  activeVariant: string;
  activeVariantSourceLabel?: string;
  onActivateVariant: (variant: string) => void;
  activating?: boolean;
  legacyDirLabel?: string;
}) {
  return (
    <div className="flex flex-col gap-4">
      <BlocksV2Toggle projectId={projectId} enabled={blocksV2Enabled} />

      <div className="flex flex-col gap-4 md:flex-row md:items-start">
        <div className="min-w-0 flex-1 rounded-xl border border-white/10 bg-white/[0.015] p-3">
          <StepBlocksEditor key={`step-blocks-${stepId}`} stepId={stepId} />
        </div>
        <div className="min-w-0 w-full md:w-[300px] md:shrink-0 lg:w-[340px]">
          <BlocksWeightPanel
            key={`blocks-weight-${stepId}`}
            projectId={projectId}
            stepId={stepId}
            promptOverrides={promptOverrides}
          />
        </div>
      </div>

      <details className="rounded-xl border border-white/10 bg-white/[0.02] p-3 open:pb-1">
        <summary className="cursor-pointer text-[10px] font-medium uppercase tracking-wider text-muted-foreground">
          Legacy: старые файлы промтов
          {legacyDirLabel ? (
            <span className="ml-1 font-mono normal-case text-muted-foreground/80">
              (prompts/{legacyDirLabel})
            </span>
          ) : null}
          <span className="ml-1 normal-case text-muted-foreground/70">
            — используются, только если блочные промты v2 выше выключены
          </span>
        </summary>
        <div className="mt-3">
          <PromptFilesPanel
            key={`files-legacy-${slotId}-${promptStepCode}`}
            stepCode={promptStepCode}
            slotId={slotId}
            preferredFile={preferredFile ?? undefined}
            folderHint={folderHint}
            activeVariant={activeVariant}
            activeVariantSourceLabel={activeVariantSourceLabel}
            onActivateVariant={onActivateVariant}
            activating={activating}
          />
        </div>
      </details>
    </div>
  );
}
