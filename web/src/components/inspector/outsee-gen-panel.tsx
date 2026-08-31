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
    <div className="overflow-hidden rounded-xl border border-white/10 bg-black/40 shadow-md backdrop-blur-md">
      <div className="flex items-center justify-between gap-2 border-b border-white/10 bg-white/[0.02] px-3.5 py-2.5">
        <div className="flex items-center gap-2">
          <Wand2 className="h-3.5 w-3.5 text-cyan-400" />
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
          className="inline-flex items-center gap-1 text-[10px] text-white/45 hover:text-cyan-400 transition-colors"
        >
          сайт
          <ExternalLink className="h-3 w-3" />
        </a>
      </div>
      <div className="flex flex-col gap-2.5 p-3.5">
        <p className="text-xs leading-relaxed text-zinc-300">
          Настройки и история общие для Studio (не привязаны к проекту). В проекте сейчас:{" "}
          <span className="font-mono text-cyan-300 font-semibold">{slug}</span>
          {project.aspect_ratio ? (
            <>
              {" "}
              ·{" "}
              <span className="font-mono text-cyan-300 font-semibold">
                {project.aspect_ratio.replace("_", ":")}
              </span>
            </>
          ) : null}
        </p>
        <Button
          type="button"
          size="sm"
          className="h-10 w-full text-xs font-semibold text-white bg-gradient-to-r from-violet-600 via-purple-600 to-indigo-600 hover:from-violet-500 hover:via-purple-500 hover:to-indigo-500 active:scale-[0.98] border border-purple-400/40 shadow-lg shadow-purple-600/30 rounded-xl backdrop-blur-md transition-all duration-200"
          onClick={() => openOutsee(project.id)}
        >
          Открыть генерацию
        </Button>
      </div>
    </div>
  );
}
