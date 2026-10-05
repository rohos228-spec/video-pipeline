"use client";
import { useEffect, useState } from "react";
import {
  describeChangeError, describeHuman, describeVersionContent, formatWhen, listVersions, shortDigest, versionKindLabel,
  type ConfigVersion,
} from "@/lib/change-control";
import { cn } from "@/lib/utils";

interface Props {
  target: string;
  /** Меняется после применения/отката: список читается заново. */
  refreshKey?: number;
}

/** История версий цели (новые сверху): версия, вид, родитель, хэш, кто и когда. */
export function VersionsList({ target, refreshKey = 0 }: Props) {
  const [items, setItems] = useState<ConfigVersion[] | null>(null);
  const [current, setCurrent] = useState<number | null>(null);
  const [error, setError] = useState("");

  useEffect(() => {
    let cancelled = false;
    setError("");
    void (async () => {
      try {
        const r = await listVersions(target);
        if (!cancelled) { setItems(r.items); setCurrent(r.current_version); }
      } catch (e) {
        if (!cancelled) setError(describeChangeError(e));
      }
    })();
    return () => { cancelled = true; };
  }, [target, refreshKey]);

  if (error) return <p role="alert" className="text-red-300">История версий недоступна: {error}</p>;
  if (!items) return <p className="text-white/50">Загружаю историю версий…</p>;
  if (items.length === 0) return <p className="text-white/55" data-testid="versions-empty">Версий у этой цели ещё нет: изменения не применялись.</p>;
  return (
    <div className="overflow-x-auto" data-testid="versions-list">
      <p className="mb-1 text-white/60">Текущая версия: {current ?? "неизвестна"} · версии не удаляются, откат добавляет новую строку.</p>
      <table className="w-full text-left" aria-label={`Версии цели ${target}`}>
        <thead>
          <tr className="text-white/50">
            {["Версия", "Что это", "Родитель", "Хэш", "Кто", "Когда", "Содержимое"].map((h) => <th key={h} className="pr-3 font-normal">{h}</th>)}
          </tr>
        </thead>
        <tbody>
          {items.map((v) => (
            <tr key={v.id} data-version={v.version} className={cn("border-t border-white/10 align-top", v.version === current && "bg-white/[0.04]")}>
              <td className="py-0.5 pr-3 whitespace-nowrap">
                <strong>v{v.version}</strong>{v.version === current && <span className="ml-1 text-emerald-300">текущая</span>}
              </td>
              <td className="pr-3">{versionKindLabel(v.kind)}</td>
              <td className="pr-3">{v.parent_version != null ? `v${v.parent_version}` : "—"}</td>
              <td className="pr-3 font-mono text-[11px]" title={v.digest}>{shortDigest(v.digest)}</td>
              <td className="pr-3">{describeHuman(v.applied_by)}{v.source_proposal_id && <span className="text-white/40"> · предл. {v.source_proposal_id.slice(0, 8)}</span>}</td>
              <td className="pr-3 whitespace-nowrap">{formatWhen(v.applied_at)}</td>
              <td className="text-white/70">{describeVersionContent(v.content)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
