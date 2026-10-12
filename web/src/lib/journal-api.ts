import { http } from "./api";
import {
  buildExportUrl,
  buildTimelineUrl,
  exportFileName,
  normalizePage,
  normalizeTotals,
  type ServerTotals,
  type JournalFilters,
  type JournalPage,
} from "./journal-view";

/** Страница ленты проекта. Чтение без токена оператора и без побочных эффектов. */
export async function fetchJournalPage(
  projectId: number,
  filters: JournalFilters,
  cursor: string | null,
  signal?: AbortSignal,
): Promise<JournalPage> {
  const raw = await http<unknown>(buildTimelineUrl(projectId, filters, { cursor }), { signal });
  return normalizePage(raw);
}

/** Итоги по всему проекту (деньги, ошибки, пробелы, диск). */
export async function fetchJournalTotals(projectId: number, signal?: AbortSignal): Promise<ServerTotals> {
  return normalizeTotals(await http<unknown>(`/api/projects/${projectId}/timeline/totals`, { signal }));
}

/** Скачивает экспорт ленты файлом. При ошибке бросает Error с числовым полем `status`. */
export async function downloadJournalExport(
  projectId: number,
  filters: JournalFilters,
  format: "csv" | "json",
): Promise<void> {
  const res = await fetch(buildExportUrl(projectId, filters, format));
  if (!res.ok) throw Object.assign(new Error(`export ${res.status}`), { status: res.status });
  const blob = await res.blob();
  const href = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = href;
  a.download = exportFileName(projectId, format);
  document.body.appendChild(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(href), 10_000);
}
