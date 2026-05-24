"use client";

import { useEffect, useRef, useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { Download, ExternalLink, Loader2, Replace, Upload } from "lucide-react";
import { toast } from "sonner";
import { api } from "@/lib/api";
import type { NodeResultItem, NodeResultSnapshot } from "@/lib/node-result-resolver";
import { getNodeSpec } from "@/lib/node-catalog";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/textarea";
import { ScrollArea } from "@/components/ui/scroll-area";
import { cn } from "@/lib/utils";
import type { AssetTrayKind } from "./canvas-actions-context";

export function NodeResultPanel({
  open,
  onOpenChange,
  projectId,
  nodeType,
  snapshot,
  onOpenAssets,
  onOpenStudio,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  projectId: number;
  nodeType: string;
  snapshot: NodeResultSnapshot;
  onOpenAssets?: (kind: AssetTrayKind) => void;
  onOpenStudio?: () => void;
}) {
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [editText, setEditText] = useState("");
  const fileRef = useRef<HTMLInputElement>(null);
  const qc = useQueryClient();
  const spec = getNodeSpec(nodeType);

  const items = snapshot.items;
  const selected = items.find((i) => i.id === selectedId) ?? items[0] ?? null;

  useEffect(() => {
    if (!open) return;
    const first = snapshot.items[0];
    setSelectedId(first?.id ?? null);
    if (first && (first.kind === "text" || first.kind === "frames")) {
      setEditText(first.content ?? "");
    } else {
      setEditText("");
    }
  }, [open, snapshot]);

  const saveText = useMutation({
    mutationFn: async (text: string) => {
      if (!snapshot.textField) throw new Error("Текстовое поле недоступно");
      return api.patchProject(projectId, { [snapshot.textField]: text });
    },
    onSuccess: () => {
      toast.success("Текст сохранён");
      qc.invalidateQueries({ queryKey: ["project", projectId] });
      qc.invalidateQueries({ queryKey: ["project-assets", projectId] });
    },
    onError: (e) => toast.error(String(e)),
  });

  const uploadXlsx = useMutation({
    mutationFn: (file: File) => api.uploadProjectXlsx(projectId, file),
    onSuccess: () => {
      toast.success("Excel заменён");
      qc.invalidateQueries({ queryKey: ["project", projectId] });
      qc.invalidateQueries({ queryKey: ["project-assets", projectId] });
      qc.invalidateQueries({ queryKey: ["xlsx-preview", projectId] });
    },
    onError: (e) => toast.error(String(e)),
  });

  const assetKind = assetKindForReplace(nodeType);

  const openItem = (item: NodeResultItem) => {
    setSelectedId(item.id);
    if (item.kind === "text" || item.kind === "frames") {
      setEditText(item.content ?? "");
    }
  };

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-h-[85vh] max-w-2xl overflow-hidden">
        <DialogHeader>
          <DialogTitle>Результат — {spec.label}</DialogTitle>
          <DialogDescription>{snapshot.summary}</DialogDescription>
        </DialogHeader>

        {!snapshot.hasResult ? (
          <div className="space-y-3 py-2">
            <p className="text-sm text-muted-foreground">
              Результат этого шага ещё не готов. Запустите ноду или дождитесь завершения генерации.
            </p>
            {snapshot.replaceMode === "text" && snapshot.textField && (
              <div className="space-y-2 rounded-lg border border-white/10 bg-white/5 p-3">
                <p className="text-xs font-medium">Или вставьте текст вручную</p>
                <Textarea
                  value={editText}
                  onChange={(e) => setEditText(e.target.value)}
                  rows={6}
                  className="text-xs"
                />
                <Button
                  size="sm"
                  disabled={saveText.isPending || !editText.trim()}
                  onClick={() => saveText.mutate(editText)}
                >
                  {saveText.isPending ? (
                    <Loader2 className="h-3.5 w-3.5 animate-spin" />
                  ) : (
                    <Replace className="h-3.5 w-3.5" />
                  )}
                  Заменить текст
                </Button>
              </div>
            )}
            {snapshot.replaceMode === "xlsx" && (
              <div className="rounded-lg border border-white/10 bg-white/5 p-3">
                <input
                  ref={fileRef}
                  type="file"
                  accept=".xlsx,.xls"
                  className="hidden"
                  onChange={(e) => {
                    const f = e.target.files?.[0];
                    if (f) uploadXlsx.mutate(f);
                    e.target.value = "";
                  }}
                />
                <Button
                  size="sm"
                  variant="outline"
                  disabled={uploadXlsx.isPending}
                  onClick={() => fileRef.current?.click()}
                >
                  {uploadXlsx.isPending ? (
                    <Loader2 className="h-3.5 w-3.5 animate-spin" />
                  ) : (
                    <Upload className="h-3.5 w-3.5" />
                  )}
                  Загрузить Excel
                </Button>
              </div>
            )}
          </div>
        ) : (
          <div className="grid min-h-0 gap-3 md:grid-cols-[180px_1fr]">
            {items.length > 1 && (
              <ScrollArea className="max-h-[50vh] rounded-lg border border-white/10">
                <div className="p-1">
                  {items.map((item) => (
                    <button
                      key={item.id}
                      type="button"
                      onClick={() => openItem(item)}
                      className={cn(
                        "mb-1 w-full rounded-md px-2 py-1.5 text-left text-[11px] transition",
                        selected?.id === item.id
                          ? "bg-primary/20 text-primary"
                          : "text-muted-foreground hover:bg-white/5",
                      )}
                    >
                      {item.label}
                    </button>
                  ))}
                </div>
              </ScrollArea>
            )}

            <div className="min-h-0 space-y-3">
              {selected ? (
                <>
                  <ResultPreview item={selected} />
                  <div className="flex flex-wrap gap-2">
                    {selected.downloadUrl && (
                      <Button size="sm" variant="outline" asChild>
                        <a href={selected.downloadUrl} download target="_blank" rel="noreferrer">
                          <Download className="h-3.5 w-3.5" />
                          Скачать
                        </a>
                      </Button>
                    )}
                    {selected.kind === "xlsx" && (
                      <>
                        <Button size="sm" variant="outline" asChild>
                          <a
                            href={api.downloadProjectXlsx(projectId)}
                            download
                            target="_blank"
                            rel="noreferrer"
                          >
                            <Download className="h-3.5 w-3.5" />
                            Скачать Excel
                          </a>
                        </Button>
                        <Button
                          size="sm"
                          variant="outline"
                          disabled={uploadXlsx.isPending}
                          onClick={() => fileRef.current?.click()}
                        >
                          <Replace className="h-3.5 w-3.5" />
                          Заменить
                        </Button>
                        <input
                          ref={fileRef}
                          type="file"
                          accept=".xlsx,.xls"
                          className="hidden"
                          onChange={(e) => {
                            const f = e.target.files?.[0];
                            if (f) uploadXlsx.mutate(f);
                            e.target.value = "";
                          }}
                        />
                      </>
                    )}
                    {(selected.kind === "text" || selected.kind === "frames") && snapshot.textField && (
                      <div className="w-full space-y-2">
                        <Textarea
                          value={editText}
                          onChange={(e) => setEditText(e.target.value)}
                          rows={5}
                          className="text-xs"
                        />
                        <Button
                          size="sm"
                          disabled={saveText.isPending}
                          onClick={() => saveText.mutate(editText)}
                        >
                          <Replace className="h-3.5 w-3.5" />
                          Заменить текст
                        </Button>
                      </div>
                    )}
                    {snapshot.replaceMode === "assets" && assetKind && onOpenAssets && (
                      <Button
                        size="sm"
                        variant="outline"
                        onClick={() => {
                          onOpenAssets(assetKind);
                          onOpenChange(false);
                        }}
                      >
                        <ExternalLink className="h-3.5 w-3.5" />
                        Медиа / замена файлов
                      </Button>
                    )}
                    {snapshot.replaceMode === "studio" && onOpenStudio && (
                      <Button
                        size="sm"
                        variant="outline"
                        onClick={() => {
                          onOpenStudio();
                          onOpenChange(false);
                        }}
                      >
                        <ExternalLink className="h-3.5 w-3.5" />
                        Открыть в студии
                      </Button>
                    )}
                  </div>
                </>
              ) : (
                <p className="text-sm text-muted-foreground">Шаг завершён — файлы появятся после обновления.</p>
              )}
            </div>
          </div>
        )}
      </DialogContent>
    </Dialog>
  );
}

function assetKindForReplace(nodeType: string): AssetTrayKind | null {
  if (nodeType === "hero" || nodeType === "hitl_hero") return "hero";
  if (nodeType === "items") return "items";
  if (nodeType === "images" || nodeType === "hitl_images") return "images";
  if (nodeType === "videos" || nodeType === "hitl_videos") return "videos";
  if (nodeType === "assemble" || nodeType === "publish" || nodeType === "hitl_final") return "project";
  return null;
}

function ResultPreview({ item }: { item: NodeResultItem }) {
  if (item.kind === "text" || item.kind === "frames") {
    return (
      <pre className="max-h-64 overflow-auto whitespace-pre-wrap rounded-lg bg-black/30 p-3 text-xs text-muted-foreground">
        {item.content}
      </pre>
    );
  }
  if (item.kind === "video" && item.previewUrl) {
    return <video src={item.previewUrl} controls className="max-h-64 w-full rounded-lg" />;
  }
  if (item.kind === "image" && item.previewUrl) {
    return <img src={item.previewUrl} alt="" className="max-h-64 w-full rounded-lg object-contain" />;
  }
  return (
    <p className="text-sm text-muted-foreground">
      {item.label}
      {item.downloadUrl ? " — файл доступен для скачивания" : ""}
    </p>
  );
}
