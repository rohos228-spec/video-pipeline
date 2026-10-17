"use client";
import {useEffect, useState} from "react";
import {observations} from "@/lib/observations";
import {
  COVERAGE_LEVEL_CLASS, coverageLevelLabel, coverageSummaryLine, forbiddenText, verifiesText,
  type NodeCoverage,
} from "@/lib/node-coverage-view";
import {cn} from "@/lib/utils";

export function NodeCoverageSection({projectId}: {projectId: number | null}) {
  const [open, setOpen] = useState(false);
  const [data, setData] = useState<NodeCoverage | null>(null);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    if (!open || projectId == null) return;
    let cancelled = false;
    setLoading(true);
    setError("");
    void observations
      .nodeCoverage(projectId)
      .then((d) => { if (!cancelled) setData(d); })
      .catch(() => { if (!cancelled) setError("Покрытие нод недоступно: сервер не ответил."); })
      .finally(() => { if (!cancelled) setLoading(false); });
    return () => { cancelled = true; };
  }, [open, projectId]);

  if (projectId == null) return null;
  const rows = data?.nodes ?? [];

  return (
    <details data-testid="node-coverage-panel" className="space-y-2 rounded border border-white/10 p-2 text-xs"
      onToggle={(e) => setOpen((e.currentTarget as HTMLDetailsElement).open)}>
      <summary className="cursor-pointer font-semibold text-white/80">Покрытие нод</summary>
      <p className="mt-2 text-white/40">
        Что оркестратор делает с каждым типом ноды: запускает и проверяет, только наблюдает или оставляет человеку.
        Данные берутся из реестра возможностей, ничего не запускает и не изменяет.
      </p>
      {loading && <p data-testid="node-coverage-loading" className="text-white/50">Загрузка…</p>}
      {error && <p role="alert" className="text-amber-200">{error}</p>}
      {data && !error && rows.length === 0 && <p className="text-white/50">В реестре нет ни одной ноды.</p>}
      {data && rows.length > 0 && (
        <>
          <p data-testid="node-coverage-summary" className="text-white/80">{coverageSummaryLine(data.summary)}</p>
          {(data.uncovered?.length ?? 0) > 0 && (
            <p role="alert" className="text-red-300">Нет описания возможностей: {data.uncovered!.join(", ")}.</p>
          )}
          <div className="overflow-x-auto">
            <table className="w-full min-w-[640px] border-collapse text-left align-top">
              <thead className="text-white/50">
                <tr>
                  <th className="border-b border-white/10 py-1 pr-2 font-normal">Нода</th>
                  <th className="border-b border-white/10 py-1 pr-2 font-normal">Уровень</th>
                  <th className="border-b border-white/10 py-1 pr-2 font-normal">Что проверяется</th>
                  <th className="border-b border-white/10 py-1 font-normal">Автовосстановление</th>
                </tr>
              </thead>
              <tbody>
                {rows.map((r) => (
                  <tr key={r.node_type} data-testid="node-coverage-row" className="border-b border-white/[0.06]">
                    <td className="py-1 pr-2">
                      <div className="text-white/85">{r.title}</div>
                      <code className="text-[10px] text-white/40">{r.node_type}</code>
                    </td>
                    <td className="py-1 pr-2">
                      <span className={cn("whitespace-nowrap rounded border px-1.5 text-[10px]", COVERAGE_LEVEL_CLASS[r.level])}>
                        {coverageLevelLabel(r.level)}
                      </span>
                    </td>
                    <td className="py-1 pr-2 text-white/65">{verifiesText(r)}</td>
                    <td className="py-1 text-white/65">
                      {forbiddenText(r)}
                      {r.forbidden?.code && !r.recovery.actions.length && (
                        <code className="ml-1 text-[10px] text-white/35">({r.forbidden.code})</code>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <p className="text-white/40">
            Смысловое качество результата не проверяется: проверки касаются формата, наличия данных и целостности.
          </p>
        </>
      )}
    </details>
  );
}
