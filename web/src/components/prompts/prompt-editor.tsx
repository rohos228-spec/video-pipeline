"use client";

import { useEffect, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Loader2, Save, KeyRound } from "lucide-react";
import { toast } from "sonner";
import { api } from "@/lib/api";
import type { PromptDTO } from "@/lib/types";
import { Button } from "@/components/ui/button";
import {
  Sheet,
  SheetContent,
  SheetDescription,
  SheetHeader,
  SheetTitle,
} from "@/components/ui/sheet";
import { Badge } from "@/components/ui/badge";
import { cn } from "@/lib/utils";

const PROMPT_LABELS: Record<string, string> = {
  PLAN_SHORTS: "План",
  SCRIPT_SHORTS: "Сценарий",
  IMAGE_SHORTS: "Image prompt",
  VIDEO_SHORTS: "Animation prompt",
  IMAGE_CHECK: "Проверка картинок",
  VIDEO_CHECK: "Проверка видео",
  HERO_SHORTS: "Герой (reference)",
  RAZBIVKA_SLOV: "Разбивка слов",
};

export function PromptEditor({ open, onOpenChange }: { open: boolean; onOpenChange: (o: boolean) => void }) {
  const prompts = useQuery({
    queryKey: ["prompts"],
    queryFn: api.listPrompts,
    enabled: open,
  });
  const [activeId, setActiveId] = useState<number | null>(null);

  // По умолчанию выбираем первый активный.
  useEffect(() => {
    if (!activeId && prompts.data && prompts.data.length > 0) {
      setActiveId(prompts.data.find((p) => p.active)?.id ?? prompts.data[0].id);
    }
  }, [activeId, prompts.data]);

  const active = (prompts.data ?? []).find((p) => p.id === activeId) ?? null;

  return (
    <Sheet open={open} onOpenChange={onOpenChange}>
      <SheetContent side="right" className="!max-w-6xl">
        <SheetHeader>
          <SheetTitle className="flex items-center gap-2">
            <KeyRound className="h-3.5 w-3.5 text-primary" />
            Мастер-промты
          </SheetTitle>
          <SheetDescription>
            Шаблоны для ChatGPT-шагов. Подставляются в каждый шаг через
            переменные. Изменения применяются к новым проектам — старые остаются
            на своих snapshot'ах.
          </SheetDescription>
        </SheetHeader>

        <div className="flex flex-1 overflow-hidden">
          <div className="w-56 shrink-0 border-r border-border">
            <div className="flex flex-col">
              {prompts.isLoading && (
                <div className="flex items-center justify-center p-4">
                  <Loader2 className="h-4 w-4 animate-spin text-muted-foreground" />
                </div>
              )}
              {prompts.data && groupByKey(prompts.data).map(({ key, versions }) => {
                const latest = versions[0];
                const isActive = active?.id === latest.id;
                return (
                  <button
                    key={key}
                    type="button"
                    onClick={() => setActiveId(latest.id)}
                    className={cn(
                      "flex flex-col items-start gap-1 border-l-2 border-transparent px-3 py-2 text-left text-xs transition-colors",
                      isActive
                        ? "border-l-primary bg-primary/5"
                        : "hover:bg-accent/30"
                    )}
                  >
                    <span className="font-medium">
                      {PROMPT_LABELS[key] ?? key}
                    </span>
                    <span className="font-mono text-[9px] text-muted-foreground">
                      {key} · v{latest.version}
                    </span>
                  </button>
                );
              })}
            </div>
          </div>

          <div className="flex flex-1 flex-col overflow-hidden">
            {active ? (
              <PromptEditorPanel prompt={active} />
            ) : (
              <div className="flex h-full items-center justify-center text-xs text-muted-foreground">
                Выбери промт слева.
              </div>
            )}
          </div>
        </div>
      </SheetContent>
    </Sheet>
  );
}

function PromptEditorPanel({ prompt }: { prompt: PromptDTO }) {
  const qc = useQueryClient();
  const [text, setText] = useState(prompt.text);

  useEffect(() => {
    setText(prompt.text);
  }, [prompt.id, prompt.text]);

  const dirty = text !== prompt.text;

  const save = useMutation({
    mutationFn: () => api.patchPrompt(prompt.id, { text }),
    onSuccess: () => {
      toast.success(`Промт «${prompt.key}» сохранён`);
      qc.invalidateQueries({ queryKey: ["prompts"] });
    },
    onError: (e) => toast.error(`Не сохранилось: ${String(e)}`),
  });

  return (
    <div className="flex h-full flex-col">
      <div className="flex shrink-0 items-center justify-between border-b border-border bg-muted/30 px-4 py-2">
        <div className="flex items-center gap-2">
          <span className="text-sm font-medium">
            {PROMPT_LABELS[prompt.key] ?? prompt.key}
          </span>
          <Badge variant={prompt.active ? "success" : "muted"} className="h-4 px-1.5 text-[9px]">
            {prompt.active ? "active" : "inactive"}
          </Badge>
          <span className="font-mono text-[10px] text-muted-foreground">
            v{prompt.version} · {text.length.toLocaleString("ru-RU")} симв.
          </span>
        </div>
        <Button
          size="sm"
          onClick={() => save.mutate()}
          disabled={!dirty || save.isPending}
          className="h-7 gap-1.5 px-3 text-xs"
        >
          {save.isPending ? (
            <Loader2 className="h-3 w-3 animate-spin" />
          ) : (
            <Save className="h-3 w-3" />
          )}
          Сохранить
        </Button>
      </div>
      <textarea
        value={text}
        onChange={(e) => setText(e.target.value)}
        spellCheck={false}
        className="flex-1 resize-none border-0 bg-canvas-bg/30 px-4 py-3 font-mono text-[12px] leading-relaxed focus:outline-none"
        onKeyDown={(e) => {
          if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) {
            e.preventDefault();
            if (dirty && !save.isPending) save.mutate();
          }
        }}
      />
    </div>
  );
}

function groupByKey(prompts: PromptDTO[]): { key: string; versions: PromptDTO[] }[] {
  const map = new Map<string, PromptDTO[]>();
  for (const p of prompts) {
    if (!map.has(p.key)) map.set(p.key, []);
    map.get(p.key)!.push(p);
  }
  return Array.from(map.entries())
    .map(([key, versions]) => ({
      key,
      versions: versions.sort((a, b) => b.version - a.version),
    }))
    .sort((a, b) => a.key.localeCompare(b.key));
}
