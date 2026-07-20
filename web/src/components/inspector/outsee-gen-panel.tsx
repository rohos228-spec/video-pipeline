"use client";

/**
 * Компактный вход в полный клон outsee Create (история + модели + dock).
 * Сами настройки — в OutseeCreateWorkspace.
 */

import { ExternalLink, Wand2 } from "lucide-react";
import type { ProjectDetail } from "@/lib/types";
import { Button } from "@/components/ui/button";
import { useUi } from "@/components/shell/topbar";
import { studioIdToSlug, outseeCreateUrl } from "@/lib/outsee-catalog";

export function OutseeGenPanel({ project }: { project: ProjectDetail }) {
  const { openOutsee } = useUi();
  const slug = studioIdToSlug(project.image_generator, "image");

  return (
    <div className="overflow-hidden rounded-xl border border-white/10 bg-[#121212] shadow-[inset_0_1px_0_rgba(255,255,255,0.04)]">
      <div className="flex items-center justify-between gap-2 border-b border-white/10 bg-[#171717] px-3 py-2.5">
        <div className="flex items-center gap-2">
          <Wand2 className="h-3.5 w-3.5 text-[rgba(209,254,23,0.9)]" />
          <div>
            <div className="text-[11px] font-semibold tracking-wide text-white/90">
              Outsee · Create
            </div>
            <div className="text-[10px] text-white/40">полный интерфейс + история</div>
          </div>
        </div>
        <a
          href={outseeCreateUrl("image", slug)}
          target="_blank"
          rel="noreferrer"
          className="inline-flex items-center gap-1 text-[10px] text-white/45 hover:text-[rgba(209,254,23,0.9)]"
        >
          сайт
          <ExternalLink className="h-3 w-3" />
        </a>
      </div>
      <div className="flex flex-col gap-2 p-3">
        <p className="text-[10px] leading-relaxed text-white/40">
          Модели, соотношения, разрешения, детализация и история генераций — как на
          outsee.io/create. Сейчас в проекте:{" "}
          <span className="font-mono text-white/60">{slug}</span>
          {project.aspect_ratio ? (
            <>
              {" "}
              · <span className="font-mono text-white/60">{project.aspect_ratio.replace("_", ":")}</span>
            </>
          ) : null}
          {project.image_resolution ? (
            <>
              {" "}
              · <span className="font-mono text-white/60">{project.image_resolution.toUpperCase()}</span>
            </>
          ) : null}
        </p>
        <Button
          type="button"
          size="sm"
          className="h-9 w-full text-xs font-semibold text-black hover:brightness-110"
          style={{ backgroundColor: "#D1FE17" }}
          onClick={() => openOutsee(project.id)}
        >
          <Wand2 className="mr-1.5 h-3.5 w-3.5" />
          Открыть Генерацию
        </Button>
      </div>
    </div>
  );
}
