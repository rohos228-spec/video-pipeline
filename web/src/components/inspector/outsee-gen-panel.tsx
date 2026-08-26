"use client";

/**
 * Компактный вход в полный клон outsee Create (история + модели + dock).
 * Сами настройки — в OutseeCreateWorkspace.
 */

import { ExternalLink, Wand2, Sparkles } from "lucide-react";
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
            <div className="text-[10px] text-white/40">глобально · Фото / Видео / Аудио</div>
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
      <div className="flex flex-col gap-2.5 p-3">
        <p className="text-xs leading-relaxed text-zinc-300">
          Настройки и история общие для Studio (не привязаны к проекту). В проекте сейчас:{" "}
          <span className="font-mono text-zinc-100 font-semibold">{slug}</span>
          {project.aspect_ratio ? (
            <>
              {" "}
              ·{" "}
              <span className="font-mono text-zinc-100 font-semibold">
                {project.aspect_ratio.replace("_", ":")}
              </span>
            </>
          ) : null}
        </p>
        <Button
          type="button"
          size="sm"
          className="h-9 w-full text-xs font-semibold text-amber-100 bg-gradient-to-r from-violet-700 via-purple-600 to-indigo-700 hover:from-violet-600 hover:via-purple-500 hover:to-indigo-600 border border-amber-400/40 shadow-lg shadow-purple-950/40 rounded-xl transition-all duration-150"
          onClick={() => openOutsee(project.id)}
        >
          Открыть генерацию
        </Button>
      </div>
    </div>
  );
}
