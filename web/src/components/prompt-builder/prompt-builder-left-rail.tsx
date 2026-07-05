"use client";

import { PanelLeft } from "lucide-react";
import { TemplateMenu } from "./template-menu";
import type { PromptTemplate } from "@/lib/prompt-builder/types";

/** Левая иконочная рейка: проекты + шаблоны (как в /design/prompt-builder). */
export function PromptBuilderLeftRail({
  templates,
  activeTemplateId,
  onPickTemplate,
  onOpenProjects,
}: {
  templates: PromptTemplate[];
  activeTemplateId: string;
  onPickTemplate: (id: string) => void;
  onOpenProjects: () => void;
}) {
  return (
    <nav className="relative z-30 flex h-full w-10 shrink-0 flex-col items-center border-r border-[var(--pb-border)] bg-[var(--pb-rail)] py-2">
      <button
        type="button"
        title="Проекты"
        aria-label="Меню проектов"
        onClick={onOpenProjects}
        className="pb-template-rail-btn mb-2 flex h-8 w-8 items-center justify-center rounded-md"
      >
        <PanelLeft className="h-4 w-4" strokeWidth={1.75} />
      </button>
      <TemplateMenu
        templates={templates}
        activeId={activeTemplateId}
        onPick={onPickTemplate}
      />
    </nav>
  );
}
