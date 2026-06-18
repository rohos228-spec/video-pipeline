"use client";

import { useCallback, useEffect, useState } from "react";
import { fleetNodeFileContent, fleetNodeFiles } from "@/lib/fleet-api";
import { cn } from "@/lib/utils";
import { ChevronRight, File, Folder, Loader2 } from "lucide-react";

type Entry = { name: string; type: string; size: number | null };

export function FleetFilesPanel({
  nodeId,
  disabled,
}: {
  nodeId: number | null;
  disabled?: boolean;
}) {
  const [path, setPath] = useState(".");
  const [entries, setEntries] = useState<Entry[]>([]);
  const [preview, setPreview] = useState("");
  const [loading, setLoading] = useState(false);

  const load = useCallback(async () => {
    if (nodeId == null || disabled) return;
    setLoading(true);
    try {
      const data = await fleetNodeFiles(nodeId, path);
      if (data.type === "dir") {
        setEntries(data.entries || []);
        setPreview("");
      } else {
        const text = await fleetNodeFileContent(nodeId, path);
        setPreview(text.content || "");
        setEntries([]);
      }
    } catch {
      setEntries([]);
      setPreview("");
    } finally {
      setLoading(false);
    }
  }, [nodeId, path, disabled]);

  useEffect(() => {
    void load();
  }, [load]);

  const openEntry = (entry: Entry) => {
    const next =
      path === "." ? entry.name : `${path.replace(/\\/g, "/")}/${entry.name}`;
    setPath(next);
  };

  const crumbs = path === "." ? [] : path.split("/");

  return (
    <section className="flex min-h-0 flex-col overflow-hidden p-3">
      <p className="mb-2 text-xs font-medium">Файлы станции</p>
      <div className="mb-2 flex flex-wrap items-center gap-1 text-[10px] text-muted-foreground">
        <button type="button" className="hover:text-foreground" onClick={() => setPath(".")}>
          .
        </button>
        {crumbs.map((part, i) => (
          <span key={i} className="flex items-center gap-1">
            <ChevronRight className="h-3 w-3" />
            <button
              type="button"
              className="hover:text-foreground"
              onClick={() => setPath(crumbs.slice(0, i + 1).join("/"))}
            >
              {part}
            </button>
          </span>
        ))}
      </div>
      <div className="min-h-0 flex-1 overflow-y-auto rounded border border-border/60 bg-background/40 p-2">
        {loading ? (
          <div className="flex items-center gap-2 text-xs text-muted-foreground">
            <Loader2 className="h-4 w-4 animate-spin" />
            Загрузка…
          </div>
        ) : preview ? (
          <pre className="whitespace-pre-wrap break-all text-[10px]">{preview}</pre>
        ) : entries.length === 0 ? (
          <p className="text-xs text-muted-foreground">Пусто</p>
        ) : (
          entries.map((e) => (
            <button
              key={e.name}
              type="button"
              disabled={disabled}
              onClick={() => openEntry(e)}
              className={cn(
                "mb-1 flex w-full items-center gap-2 rounded px-1 py-1 text-left text-xs hover:bg-muted/50",
              )}
            >
              {e.type === "dir" ? (
                <Folder className="h-3.5 w-3.5 shrink-0 text-amber-500" />
              ) : (
                <File className="h-3.5 w-3.5 shrink-0" />
              )}
              <span className="truncate">{e.name}</span>
            </button>
          ))
        )}
      </div>
    </section>
  );
}
