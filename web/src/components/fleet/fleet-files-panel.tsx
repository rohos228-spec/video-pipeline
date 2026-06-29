"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { flushSync } from "react-dom";
import {
  fleetNodeFileContent,
  fleetNodeFileDelete,
  fleetNodeFileDownload,
  fleetNodeFiles,
  fleetNodeFileUpload,
  fleetNodePipelineLogStream,
  fleetNodePowerShellStream,
} from "@/lib/fleet-api";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { Input } from "@/components/ui/input";
import { cn } from "@/lib/utils";
import {
  ChevronDown,
  ChevronUp,
  Copy,
  Download,
  Eye,
  File,
  Folder,
  Loader2,
  Square,
  Terminal,
  Trash2,
  Upload,
} from "lucide-react";

type DirEntry = { name: string; type: string; size?: number | null };

type DirListing = {
  path: string;
  entries: DirEntry[];
};

function normalizePath(path: string): string {
  const p = path.replace(/\\/g, "/").replace(/^\/+/, "");
  return p || ".";
}

function joinPath(base: string, name: string): string {
  const b = normalizePath(base);
  if (b === ".") return name;
  return `${b}/${name}`;
}

function parentPath(path: string): string {
  const p = normalizePath(path);
  if (p === ".") return ".";
  const parts = p.split("/").filter(Boolean);
  parts.pop();
  return parts.length ? parts.join("/") : ".";
}

function formatPathLabel(path: string): string {
  return normalizePath(path) === "." ? "корень pipeline" : normalizePath(path);
}

export function FleetFilesPanel({
  nodeId,
  disabled,
}: {
  nodeId: number | null;
  disabled?: boolean;
}) {
  const [filesPath, setFilesPath] = useState(".");
  const [dirListing, setDirListing] = useState<DirListing | null>(null);
  const [rootDirs, setRootDirs] = useState<DirEntry[]>([]);
  const [selectedPath, setSelectedPath] = useState<string | null>(null);
  const [filesLoading, setFilesLoading] = useState(false);
  const [filesError, setFilesError] = useState("");
  const [foldersCollapsed, setFoldersCollapsed] = useState(false);
  const [logMode, setLogMode] = useState<"pipeline" | "powershell">("pipeline");
  const [pipelineOut, setPipelineOut] = useState("");
  const [pipelineLive, setPipelineLive] = useState(false);
  const [psCmd, setPsCmd] = useState("Get-Location");
  const [psOut, setPsOut] = useState("");
  const [psRunning, setPsRunning] = useState(false);
  const [viewerOpen, setViewerOpen] = useState(false);
  const [viewerTitle, setViewerTitle] = useState("");
  const [viewerContent, setViewerContent] = useState("");
  const [actionBusy, setActionBusy] = useState<string | null>(null);
  const uploadRef = useRef<HTMLInputElement>(null);
  const psAbortRef = useRef<AbortController | null>(null);
  const pipelineAbortRef = useRef<AbortController | null>(null);
  const logEndRef = useRef<HTMLPreElement>(null);

  const loadDir = useCallback(
    async (path: string) => {
      if (nodeId == null) return;
      setFilesLoading(true);
      setFilesError("");
      try {
        const data = await fleetNodeFiles(nodeId, normalizePath(path));
        if (data.type !== "dir" || !Array.isArray(data.entries)) {
          throw new Error("ожидалась папка");
        }
        const listing: DirListing = {
          path: normalizePath(String(data.path ?? path)),
          entries: data.entries as DirEntry[],
        };
        setFilesPath(listing.path);
        setDirListing(listing);
        setSelectedPath(null);
        if (normalizePath(path) === ".") {
          setRootDirs(listing.entries.filter((e) => e.type === "dir"));
        }
      } catch (err) {
        setFilesError(err instanceof Error ? err.message : "Не удалось открыть папку");
      } finally {
        setFilesLoading(false);
      }
    },
    [nodeId],
  );

  useEffect(() => {
    if (nodeId == null) {
      setDirListing(null);
      setSelectedPath(null);
      setFilesPath(".");
      setPsOut("");
      return;
    }
    void loadDir(".");
  }, [nodeId, loadDir]);

  useEffect(() => {
    logEndRef.current?.scrollIntoView({ behavior: "smooth", block: "end" });
  }, [pipelineOut, psOut, logMode]);

  useEffect(() => {
    if (nodeId == null || logMode !== "pipeline") {
      pipelineAbortRef.current?.abort();
      pipelineAbortRef.current = null;
      setPipelineLive(false);
      return;
    }

    const controller = new AbortController();
    pipelineAbortRef.current = controller;
    setPipelineOut("");
    setPipelineLive(false);

    void (async () => {
      try {
        await fleetNodePipelineLogStream(
          nodeId,
          {
            onChunk: (text) => {
              flushSync(() => {
                setPipelineOut((prev) => {
                  const next = prev + text;
                  return next.length > 200_000 ? next.slice(-200_000) : next;
                });
              });
              setPipelineLive(true);
            },
          },
          controller.signal,
        );
      } catch (err) {
        if (!(err instanceof DOMException && err.name === "AbortError")) {
          setPipelineOut(
            (prev) =>
              `${prev}\n[ошибка стрима: ${err instanceof Error ? err.message : "unknown"}]\n`,
          );
        }
      } finally {
        setPipelineLive(false);
      }
    })();

    return () => {
      controller.abort();
    };
  }, [nodeId, logMode]);

  const subdirs = useMemo(
    () => (dirListing?.entries ?? []).filter((e) => e.type === "dir"),
    [dirListing],
  );

  const files = useMemo(
    () => (dirListing?.entries ?? []).filter((e) => e.type !== "dir"),
    [dirListing],
  );

  const folderOptions = useMemo(() => {
    const opts: Array<{ label: string; path: string }> = [{ label: "корень pipeline", path: "." }];
    for (const d of rootDirs) {
      opts.push({ label: d.name, path: d.name });
    }
    if (filesPath !== ".") {
      opts.push({ label: formatPathLabel(filesPath), path: filesPath });
    }
    return opts;
  }, [filesPath, rootDirs]);

  const goUp = () => {
    void loadDir(parentPath(filesPath));
  };

  const runPowerShell = async () => {
    if (nodeId == null || !psCmd.trim()) return;
    psAbortRef.current?.abort();
    const controller = new AbortController();
    psAbortRef.current = controller;
    setPsRunning(true);
    setPsOut(`> ${psCmd}\n`);
    try {
      await fleetNodePowerShellStream(
        nodeId,
        psCmd,
        filesPath === "." ? "." : filesPath,
        {
          onChunk: (_type, text) => {
            flushSync(() => {
              setPsOut((prev) => prev + text);
            });
          },
          onExit: (code) => {
            flushSync(() => {
              setPsOut((prev) => prev + `\n[exit ${code}]`);
            });
          },
        },
        controller.signal,
      );
    } catch (err) {
      if (!(err instanceof DOMException && err.name === "AbortError")) {
        setPsOut((prev) => prev + `\n${err instanceof Error ? err.message : "ошибка"}`);
      }
    } finally {
      setPsRunning(false);
      psAbortRef.current = null;
    }
  };

  const stopPowerShell = () => {
    psAbortRef.current?.abort();
    setPsRunning(false);
  };

  const copyLogs = async () => {
    const text = logMode === "pipeline" ? pipelineOut : psOut;
    if (!text) return;
    await navigator.clipboard.writeText(text);
  };

  const clearLogs = () => {
    if (logMode === "pipeline") setPipelineOut("");
    else setPsOut("");
  };

  const activeLogText = logMode === "pipeline" ? pipelineOut : psOut;

  const viewFile = async (path: string) => {
    if (nodeId == null) return;
    setActionBusy(path);
    try {
      const data = await fleetNodeFileContent(nodeId, path);
      setViewerTitle(data.path);
      setViewerContent(data.content);
      setViewerOpen(true);
      setSelectedPath(path);
    } catch (err) {
      setFilesError(err instanceof Error ? err.message : "Не удалось открыть файл");
    } finally {
      setActionBusy(null);
    }
  };

  const downloadFile = async (path: string) => {
    if (nodeId == null) return;
    setActionBusy(path);
    try {
      await fleetNodeFileDownload(nodeId, path);
    } catch (err) {
      setFilesError(err instanceof Error ? err.message : "Не удалось скачать");
    } finally {
      setActionBusy(null);
    }
  };

  const deleteEntry = async (path: string, isDir: boolean) => {
    if (nodeId == null) return;
    const label = isDir ? "папку" : "файл";
    if (!window.confirm(`Удалить ${label} ${path}?`)) return;
    setActionBusy(path);
    try {
      await fleetNodeFileDelete(nodeId, path);
      if (selectedPath === path) setSelectedPath(null);
      await loadDir(filesPath);
    } catch (err) {
      setFilesError(err instanceof Error ? err.message : "Не удалось удалить");
    } finally {
      setActionBusy(null);
    }
  };

  const onUploadPick = async (event: React.ChangeEvent<HTMLInputElement>) => {
    const file = event.target.files?.[0];
    event.target.value = "";
    if (!file || nodeId == null) return;
    const dest = joinPath(filesPath, file.name);
    setActionBusy(dest);
    try {
      await fleetNodeFileUpload(nodeId, dest, file);
      await loadDir(filesPath);
    } catch (err) {
      setFilesError(err instanceof Error ? err.message : "Не удалось загрузить");
    } finally {
      setActionBusy(null);
    }
  };

  const renderEntryActions = (path: string, isDir: boolean) => (
    <div className="ml-auto flex shrink-0 items-center gap-0.5 opacity-80 group-hover:opacity-100">
      {!isDir ? (
        <>
          <button
            type="button"
            title="Просмотр"
            className="rounded p-0.5 hover:bg-muted"
            disabled={actionBusy === path}
            onClick={() => void viewFile(path)}
          >
            <Eye className="h-3 w-3" />
          </button>
          <button
            type="button"
            title="Скачать"
            className="rounded p-0.5 hover:bg-muted"
            disabled={actionBusy === path}
            onClick={() => void downloadFile(path)}
          >
            <Download className="h-3 w-3" />
          </button>
        </>
      ) : null}
      <button
        type="button"
        title="Удалить"
        className="rounded p-0.5 hover:bg-destructive/20 hover:text-destructive"
        disabled={actionBusy === path}
        onClick={() => void deleteEntry(path, isDir)}
      >
        <Trash2 className="h-3 w-3" />
      </button>
    </div>
  );

  return (
    <section className="flex min-h-0 flex-col overflow-hidden p-3">
      <div className="mb-2 flex items-center justify-between gap-2">
        <p className="flex items-center gap-1 text-xs font-medium">
          <Terminal className="h-3.5 w-3.5" />
          Файлы / PowerShell
        </p>
        <Button
          type="button"
          variant="outline"
          size="sm"
          className="h-7 gap-1 px-2 text-[10px]"
          disabled={disabled}
          onClick={() => uploadRef.current?.click()}
        >
          <Upload className="h-3 w-3" />
          Загрузить
        </Button>
        <input ref={uploadRef} type="file" className="hidden" onChange={(e) => void onUploadPick(e)} />
      </div>

      <div className="mb-2 flex items-center gap-1">
        <Button
          type="button"
          variant="outline"
          size="sm"
          className="h-7 px-2"
          disabled={disabled || normalizePath(filesPath) === "."}
          onClick={goUp}
          title="Вверх"
        >
          <ChevronUp className="h-3.5 w-3.5" />
        </Button>

        <DropdownMenu>
          <DropdownMenuTrigger asChild>
            <Button
              type="button"
              variant="outline"
              size="sm"
              className="h-7 min-w-0 flex-1 justify-between gap-1 px-2 text-[10px] font-normal"
              disabled={disabled || filesLoading}
            >
              <span className="flex min-w-0 items-center gap-1 truncate">
                <Folder className="h-3.5 w-3.5 shrink-0 text-primary" />
                {formatPathLabel(filesPath)}
              </span>
              <ChevronDown className="h-3.5 w-3.5 shrink-0 opacity-60" />
            </Button>
          </DropdownMenuTrigger>
          <DropdownMenuContent align="start" className="max-h-64 w-64 overflow-y-auto">
            <DropdownMenuLabel>Папки</DropdownMenuLabel>
            {folderOptions.map((opt) => (
              <DropdownMenuItem
                key={opt.path}
                className={cn("text-xs", opt.path === filesPath && "bg-muted")}
                onClick={() => void loadDir(opt.path)}
              >
                <Folder className="h-3.5 w-3.5 text-primary" />
                <span className="truncate">{opt.label}</span>
              </DropdownMenuItem>
            ))}
            {subdirs.length ? (
              <>
                <DropdownMenuSeparator />
                <DropdownMenuLabel>Внутри текущей</DropdownMenuLabel>
                {subdirs.map((d) => {
                  const p = joinPath(filesPath, d.name);
                  return (
                    <DropdownMenuItem key={p} className="text-xs" onClick={() => void loadDir(p)}>
                      <Folder className="h-3.5 w-3.5" />
                      {d.name}
                    </DropdownMenuItem>
                  );
                })}
              </>
            ) : null}
          </DropdownMenuContent>
        </DropdownMenu>

        {filesLoading ? <Loader2 className="h-4 w-4 shrink-0 animate-spin text-muted-foreground" /> : null}
      </div>

      {filesError ? <p className="mb-2 text-[10px] text-destructive">{filesError}</p> : null}

      {!foldersCollapsed ? (
        <div className="mb-1 min-h-[7rem] max-h-[40%] flex-1 overflow-y-auto rounded border border-border/60 p-2 font-mono text-[10px]">
          {!dirListing && !filesLoading ? (
            <p className="text-muted-foreground">Выберите станцию…</p>
          ) : null}
          {subdirs.map((e) => {
            const path = joinPath(filesPath, e.name);
            return (
              <div
                key={`dir-${e.name}`}
                className="group flex w-full items-center gap-1 rounded px-1 py-0.5 hover:bg-muted/60"
              >
                <button
                  type="button"
                  className="flex min-w-0 flex-1 items-center gap-1 text-left"
                  onClick={() => void loadDir(path)}
                >
                  <Folder className="h-3 w-3 shrink-0 text-primary" />
                  <span className="truncate">{e.name}</span>
                </button>
                {renderEntryActions(path, true)}
              </div>
            );
          })}
          {files.map((e) => {
            const path = joinPath(filesPath, e.name);
            return (
              <div
                key={`file-${e.name}`}
                className={cn(
                  "group flex w-full items-center gap-1 rounded px-1 py-0.5 hover:bg-muted/60",
                  selectedPath === path && "bg-muted",
                )}
              >
                <button
                  type="button"
                  className="flex min-w-0 flex-1 items-center gap-1 text-left"
                  onClick={() => setSelectedPath(path)}
                >
                  <File className="h-3 w-3 shrink-0 text-muted-foreground" />
                  <span className="truncate">{e.name}</span>
                </button>
                {renderEntryActions(path, false)}
              </div>
            );
          })}
          {dirListing && subdirs.length === 0 && files.length === 0 ? (
            <p className="text-muted-foreground">Папка пустая</p>
          ) : null}
        </div>
      ) : (
        <p className="mb-1 rounded border border-dashed border-border/60 px-2 py-3 text-[10px] text-muted-foreground">
          Папки свёрнуты · {formatPathLabel(filesPath)}
        </p>
      )}

      <Button
        type="button"
        variant="ghost"
        size="sm"
        className="mb-2 h-7 w-full gap-1 text-[10px]"
        onClick={() => setFoldersCollapsed((v) => !v)}
      >
        {foldersCollapsed ? (
          <>
            <ChevronDown className="h-3.5 w-3.5" />
            Показать папки
          </>
        ) : (
          <>
            <ChevronUp className="h-3.5 w-3.5" />
            Свернуть папки
          </>
        )}
      </Button>

      <div className="mb-1 flex flex-wrap items-center gap-1">
        <Button
          type="button"
          variant={logMode === "pipeline" ? "secondary" : "ghost"}
          size="sm"
          className="h-7 text-[10px]"
          onClick={() => setLogMode("pipeline")}
        >
          Пайплайн
          {pipelineLive ? <span className="ml-1 text-green-500">● live</span> : null}
        </Button>
        <Button
          type="button"
          variant={logMode === "powershell" ? "secondary" : "ghost"}
          size="sm"
          className="h-7 text-[10px]"
          onClick={() => setLogMode("powershell")}
        >
          PowerShell
        </Button>
        <Button
          size="sm"
          variant="outline"
          className="ml-auto h-7 px-2"
          disabled={!activeLogText}
          onClick={() => void copyLogs()}
          title="Копировать лог"
        >
          <Copy className="h-3.5 w-3.5" />
        </Button>
        <Button
          size="sm"
          variant="outline"
          className="h-7 px-2"
          disabled={!activeLogText}
          onClick={clearLogs}
          title="Очистить экран"
        >
          <Trash2 className="h-3.5 w-3.5" />
        </Button>
      </div>

      {logMode === "powershell" ? (
        <div className="mb-2 flex gap-1">
          <Input
            className="h-8 text-xs"
            value={psCmd}
            disabled={psRunning}
            onChange={(e) => setPsCmd(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter") void runPowerShell();
            }}
          />
          {psRunning ? (
            <Button size="sm" variant="destructive" className="h-8" onClick={stopPowerShell}>
              <Square className="h-3 w-3" />
            </Button>
          ) : (
            <Button size="sm" className="h-8" disabled={disabled} onClick={() => void runPowerShell()}>
              Run
            </Button>
          )}
        </div>
      ) : (
        <p className="mb-2 text-[10px] text-muted-foreground">
          Live tail: data/studio-live.log (то же, что в окне backend)
        </p>
      )}

      <pre
        ref={logEndRef}
        className="min-h-[5rem] flex-1 overflow-auto whitespace-pre-wrap rounded bg-muted/40 p-2 text-[10px]"
      >
        {logMode === "pipeline"
          ? pipelineOut || (disabled ? "Выберите станцию…" : "Подключение к логу пайплайна…")
          : psOut || "вывод PowerShell (live)…"}
      </pre>

      <Dialog open={viewerOpen} onOpenChange={setViewerOpen}>
        <DialogContent className="max-h-[80vh] max-w-3xl overflow-hidden">
          <DialogHeader>
            <DialogTitle className="truncate text-sm">{viewerTitle}</DialogTitle>
          </DialogHeader>
          <div className="flex justify-end gap-2">
            <Button
              size="sm"
              variant="outline"
              onClick={() => void navigator.clipboard.writeText(viewerContent)}
            >
              <Copy className="mr-1 h-3.5 w-3.5" />
              Копировать
            </Button>
            <Button
              size="sm"
              variant="outline"
              onClick={() => viewerTitle && void downloadFile(viewerTitle)}
            >
              <Download className="mr-1 h-3.5 w-3.5" />
              Скачать
            </Button>
          </div>
          <pre className="max-h-[60vh] overflow-auto rounded border border-border/60 bg-muted/30 p-3 text-xs whitespace-pre-wrap">
            {viewerContent}
          </pre>
        </DialogContent>
      </Dialog>
    </section>
  );
}
