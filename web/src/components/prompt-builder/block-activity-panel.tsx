"use client";

import { useQuery } from "@tanstack/react-query";
import { cn } from "@/lib/utils";
import { api, type BlockActivityEntry } from "@/lib/api";

const EVENT_LABELS: Record<string, string> = {
  block_discovered: "Обнаружен на диске",
  block_created: "Создан",
  block_updated: "Изменён",
  block_selected: "Выбран в пресете",
  block_viewed: "Просмотр",
  created: "Добавлен в библиотеку",
  updated: "Версия обновлена",
};

function formatTime(iso: string): string {
  try {
    const d = new Date(iso);
    return d.toLocaleString("ru-RU", {
      day: "2-digit",
      month: "2-digit",
      hour: "2-digit",
      minute: "2-digit",
    });
  } catch {
    return iso;
  }
}

function entryTitle(e: BlockActivityEntry): string {
  const cat = e.category ?? "—";
  const id = e.block_id ?? e.path?.split("/").pop()?.replace(".md", "") ?? "—";
  return `${cat} · ${id}`;
}

export function BlockActivityPanel({
  className,
  categoryFilter,
  compact = false,
}: {
  className?: string;
  categoryFilter?: string | null;
  compact?: boolean;
}) {
  const activity = useQuery({
    queryKey: ["block-activity", categoryFilter ?? "all"],
    queryFn: () =>
      api.promptStudioBlockActivity({
        limit: compact ? 12 : 40,
        category: categoryFilter ?? undefined,
      }),
    refetchInterval: 30_000,
  });

  const sync = useQuery({
    queryKey: ["block-sync-once"],
    queryFn: () => api.promptStudioSyncBlocks(),
    staleTime: 60_000,
  });

  const rows = activity.data ?? [];
  const discovered = sync.data?.discovered_count ?? 0;

  return (
    <section className={cn("pb-block-log", className)}>
      <header className="pb-block-log-head">
        <p className="pb-label-caps">Журнал блоков</p>
        {!compact && discovered > 0 && (
          <span className="pb-block-log-badge">+{discovered} новых</span>
        )}
      </header>
      <div className={cn("pb-block-log-list pb-scroll-fade", compact && "max-h-[140px]")}>
        {activity.isLoading ? (
          <p className="pb-block-log-empty">Загрузка…</p>
        ) : rows.length === 0 ? (
          <p className="pb-block-log-empty">Пока нет событий</p>
        ) : (
          rows.map((e) => (
            <div key={e.id} className="pb-block-log-row">
              <span className="pb-block-log-time">{formatTime(e.created_at)}</span>
              <span className="pb-block-log-type">
                {EVENT_LABELS[e.event_type] ?? e.event_type}
              </span>
              <span className="pb-block-log-target" title={e.path ?? undefined}>
                {entryTitle(e)}
              </span>
            </div>
          ))
        )}
      </div>
    </section>
  );
}
