"use client";

import { useEffect, useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  Blocks,
  Download,
  FileText,
  Loader2,
  Save,
  Settings2,
  Sparkles,
} from "lucide-react";
import { toast } from "sonner";
import { api } from "@/lib/api";
import { getNodeSpec, NODE_CATALOG } from "@/lib/node-catalog";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/input";
import { Sheet, SheetContent, SheetDescription, SheetHeader, SheetTitle } from "@/components/ui/sheet";
import { Badge } from "@/components/ui/badge";
import { ScrollArea } from "@/components/ui/scroll-area";
import { cn } from "@/lib/utils";

const NODE_TO_STEP: Record<string, string> = {
  plan: "plan",
  script: "script",
  split: "split",
  hero: "hero",
  items: "items",
  enrich_1: "enrich_1",
  enrich_2: "enrich_2",
  enrich_3: "enrich_3",
  image_prompts: "img_pr",
  animation_prompts: "anim_pr",
};

export function NodeStudio({
  open,
  onOpenChange,
  projectId,
  nodeKey,
}: {
  open: boolean;
  onOpenChange: (o: boolean) => void;
  projectId: number | null;
  nodeKey: string | null;
}) {
  const nodeType = nodeKey?.startsWith("n_") ? nodeKey.slice(2) : nodeKey ?? "";
  const spec = getNodeSpec(nodeType);
  const stepCode = NODE_TO_STEP[nodeType];

  const [tab, setTab] = useState<"settings" | "prompts" | "results">("settings");
  const [composed, setComposed] = useState("");
  const [legacyVariant, setLegacyVariant] = useState("default");
  const [blocks, setBlocks] = useState<Record<string, string>>({});
  const [stylePreset, setStylePreset] = useState("cats_pixelart_short");

  const qc = useQueryClient();
  const project = useQuery({
    queryKey: ["project", projectId],
    queryFn: () => api.getProject(projectId!),
    enabled: open && projectId != null,
  });
  const catalog = useQuery({
    queryKey: ["prompt-studio-catalog"],
    queryFn: api.promptStudioCatalog,
    enabled: open,
  });
  const variants = useQuery({
    queryKey: ["prompt-variants", stepCode],
    queryFn: () =>
      fetch(`/api/prompt-studio/variants/${stepCode}`).then((r) => r.json() as Promise<string[]>),
    enabled: open && !!stepCode,
  });
  const artifacts = useQuery({
    queryKey: ["artifacts", projectId, nodeType],
    queryFn: () => api.listArtifacts({ project_id: projectId! }),
    enabled: open && projectId != null,
  });

  useEffect(() => {
    if (!open) return;
    const po = (project.data?.prompt_overrides || {}) as Record<string, unknown>;
    if (typeof po.style_profile === "string") setStylePreset(po.style_profile);
    if (po.blocks && typeof po.blocks === "object") {
      setBlocks(po.blocks as Record<string, string>);
    }
    if (stepCode && typeof po[stepCode] === "string") {
      setLegacyVariant(po[stepCode] as string);
    }
  }, [open, project.data, stepCode]);

  const compose = useMutation({
    mutationFn: () =>
      api.composePrompt({
        node_type: nodeType,
        project_id: projectId ?? undefined,
        style_preset: stylePreset,
        blocks: Object.keys(blocks).length ? blocks : undefined,
      }),
    onSuccess: (r) => {
      setComposed(r.text);
      toast.success("Промт собран");
    },
    onError: (e) => toast.error(String(e)),
  });

  const saveConfig = useMutation({
    mutationFn: () =>
      api.patchProjectPromptConfig(projectId!, {
        style_profile: stylePreset,
        blocks,
        use_blocks_v2: true,
        legacy: stepCode ? { [stepCode]: legacyVariant } : {},
      }),
    onSuccess: () => {
      toast.success("Настройки ноды сохранены");
      qc.invalidateQueries({ queryKey: ["project", projectId] });
    },
    onError: (e) => toast.error(String(e)),
  });

  const blockCategories = catalog.data?.block_categories ?? {};
  const presets = catalog.data?.style_presets ?? [];

  const filteredArtifacts = useMemo(() => {
    const list = artifacts.data ?? [];
    if (nodeType.includes("image") || nodeType === "images") {
      return list.filter((a) => a.kind.includes("image") || a.kind.includes("scene"));
    }
    if (nodeType.includes("video") || nodeType === "videos") {
      return list.filter((a) => a.kind.includes("video"));
    }
    if (nodeType === "hero" || nodeType === "items") {
      return list.filter((a) => a.kind.includes("hero") || a.kind.includes("item"));
    }
    return list.slice(0, 12);
  }, [artifacts.data, nodeType]);

  if (!nodeKey) return null;

  return (
    <Sheet open={open} onOpenChange={onOpenChange}>
      <SheetContent side="right" className="!max-w-[min(920px,92vw)] w-full p-0">
        <div className="flex h-full flex-col">
          <SheetHeader className="shrink-0 border-b border-border px-5 py-4">
            <div className="flex items-start justify-between gap-4 pr-8">
              <div>
                <SheetTitle className="flex items-center gap-2">
                  <Sparkles className="h-4 w-4 text-primary" />
                  {spec.label}
                </SheetTitle>
                <SheetDescription>{spec.description}</SheetDescription>
                <div className="mt-2 flex flex-wrap gap-1.5">
                  <Badge variant="muted" className="font-mono text-[10px]">
                    {nodeKey}
                  </Badge>
                  <Badge variant="outline" className="text-[10px]">
                    {spec.category}
                  </Badge>
                </div>
              </div>
              <Button size="sm" onClick={() => saveConfig.mutate()} disabled={!projectId || saveConfig.isPending}>
                {saveConfig.isPending ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Save className="h-3.5 w-3.5" />}
                Сохранить
              </Button>
            </div>
            <div className="mt-3 flex gap-1">
              {(
                [
                  ["settings", "Настройки", Settings2],
                  ["prompts", "Промты GPT", Blocks],
                  ["results", "Результаты", FileText],
                ] as const
              ).map(([id, label, Icon]) => (
                <Button
                  key={id}
                  type="button"
                  size="sm"
                  variant={tab === id ? "default" : "ghost"}
                  className="gap-1.5 text-xs"
                  onClick={() => setTab(id)}
                >
                  <Icon className="h-3.5 w-3.5" />
                  {label}
                </Button>
              ))}
            </div>
          </SheetHeader>

          <ScrollArea className="flex-1">
            <div className="p-5">
              {tab === "settings" && (
                <div className="flex flex-col gap-4">
                  <section>
                    <h3 className="text-xs font-medium uppercase tracking-wider text-muted-foreground">
                      Профиль ролика
                    </h3>
                    <div className="mt-2 grid gap-2 sm:grid-cols-2">
                      {presets.map((p) => (
                        <button
                          key={p.id}
                          type="button"
                          onClick={() => setStylePreset(p.id)}
                          className={cn(
                            "rounded-lg border px-3 py-2 text-left text-xs transition-colors",
                            stylePreset === p.id
                              ? "border-primary/50 bg-primary/10"
                              : "border-border hover:bg-accent/50",
                          )}
                        >
                          <div className="font-medium">{p.label}</div>
                          {p.description && (
                            <div className="mt-0.5 text-muted-foreground">{p.description}</div>
                          )}
                        </button>
                      ))}
                    </div>
                  </section>
                  <section>
                    <h3 className="text-xs font-medium uppercase tracking-wider text-muted-foreground">
                      Блоки (Lego)
                    </h3>
                    <div className="mt-2 flex flex-col gap-3">
                      {Object.entries(blockCategories).map(([cat, names]) => (
                        <div key={cat} className="flex flex-col gap-1">
                          <label className="text-[10px] uppercase text-muted-foreground">{cat}</label>
                          <select
                            className="h-8 rounded-md border border-input bg-background px-2 text-xs"
                            value={blocks[cat] ?? ""}
                            onChange={(e) =>
                              setBlocks((b) => ({ ...b, [cat]: e.target.value }))
                            }
                          >
                            <option value="">— дефолт —</option>
                            {names.map((n) => (
                              <option key={n} value={n}>
                                {n}
                              </option>
                            ))}
                          </select>
                        </div>
                      ))}
                    </div>
                  </section>
                </div>
              )}

              {tab === "prompts" && (
                <div className="flex flex-col gap-4">
                  {stepCode && (
                    <section>
                      <h3 className="text-xs font-medium uppercase tracking-wider text-muted-foreground">
                        Legacy-вариант (.md)
                      </h3>
                      <select
                        className="mt-2 h-9 w-full rounded-md border border-input bg-background px-2 text-sm"
                        value={legacyVariant}
                        onChange={(e) => setLegacyVariant(e.target.value)}
                      >
                        {(variants.data ?? ["default"]).map((v) => (
                          <option key={v} value={v}>
                            {v}
                          </option>
                        ))}
                      </select>
                    </section>
                  )}
                  <div className="flex gap-2">
                    <Button size="sm" onClick={() => compose.mutate()} disabled={compose.isPending}>
                      {compose.isPending ? (
                        <Loader2 className="h-3.5 w-3.5 animate-spin" />
                      ) : (
                        <Blocks className="h-3.5 w-3.5" />
                      )}
                      Собрать из блоков
                    </Button>
                    <Button
                      size="sm"
                      variant="outline"
                      onClick={() => {
                        const blob = new Blob([composed], { type: "text/plain" });
                        const a = document.createElement("a");
                        a.href = URL.createObjectURL(blob);
                        a.download = `${nodeType}-prompt.txt`;
                        a.click();
                      }}
                      disabled={!composed}
                    >
                      <Download className="h-3.5 w-3.5" />
                      Скачать
                    </Button>
                  </div>
                  <Textarea
                    value={composed}
                    onChange={(e) => setComposed(e.target.value)}
                    rows={18}
                    className="font-mono text-[11px] leading-relaxed"
                    placeholder="Нажми «Собрать из блоков» — здесь финальный промт для ChatGPT"
                  />
                </div>
              )}

              {tab === "results" && (
                <div className="grid gap-3 sm:grid-cols-2">
                  {filteredArtifacts.length === 0 ? (
                    <p className="text-sm text-muted-foreground">Артефактов пока нет.</p>
                  ) : (
                    filteredArtifacts.map((a) => (
                      <div
                        key={a.id}
                        className="rounded-lg border border-border bg-muted/20 p-2"
                      >
                        <div className="text-[10px] uppercase text-muted-foreground">{a.kind}</div>
                        {a.path.match(/\.(mp4|webm)$/i) ? (
                          <video
                            controls
                            className="mt-1 max-h-40 w-full rounded"
                            src={api.artifactFileUrl(a.uuid)}
                          />
                        ) : (
                          <img
                            alt=""
                            className="mt-1 max-h-40 w-full rounded object-contain"
                            src={api.artifactFileUrl(a.uuid)}
                          />
                        )}
                        <a
                          href={api.artifactFileUrl(a.uuid)}
                          download
                          className="mt-2 inline-flex items-center gap-1 text-[10px] text-primary hover:underline"
                        >
                          <Download className="h-3 w-3" />
                          Скачать
                        </a>
                      </div>
                    ))
                  )}
                </div>
              )}
            </div>
          </ScrollArea>
        </div>
      </SheetContent>
    </Sheet>
  );
}

export { NODE_CATALOG };
