import type { ProjectSummary } from "@/lib/types";

/** Имя в сайдбаре — не topic ноды «Тема ролика». */
export function projectDisplayName(
  p: { title?: string | null; topic?: string | null; slug: string },
): string {
  const title = (p.title ?? "").trim();
  if (title) return title;
  const topic = (p.topic ?? "").trim();
  if (topic) return topic;
  return p.slug;
}
