"use client";

import { useEffect, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Loader2, RotateCcw, Save, BookmarkPlus } from "lucide-react";
import { toast } from "sonner";
import { api } from "@/lib/api";
import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/textarea";
import { Badge } from "@/components/ui/badge";

export function GptTextPanel({
  projectId,
  stepCode,
}: {
  projectId: number;
  stepCode: string;
}) {
  const qc = useQueryClient();
  const [draft, setDraft] = useState("");
  const [dirty, setDirty] = useState(false);
  const [templateName, setTemplateName] = useState("");

  const data = useQuery({
    queryKey: ["gpt-text", projectId, stepCode],
    queryFn: () => api.getProjectGptText(projectId, stepCode),
    enabled: Boolean(projectId && stepCode),
  });

  useEffect(() => {
    if (data.data && !dirty) {
      setDraft(data.data.text);
    }
  }, [data.data, dirty]);

  useEffect(() => {
    setDirty(false);
  }, [stepCode, projectId]);

  const save = useMutation({
    mutationFn: () => api.saveProjectGptText(projectId, stepCode, draft),
    onSuccess: () => {
      toast.success("Текст для GPT сохранён");
      setDirty(false);
      qc.invalidateQueries({ queryKey: ["gpt-text", projectId, stepCode] });
      qc.invalidateQueries({ queryKey: ["project", projectId] });
    },
    onError: (e) => toast.error(String(e)),
  });

  const saveTemplate = useMutation({
    mutationFn: () => {
      const name = templateName.trim();
      if (!name) return Promise.reject(new Error("Введите имя шаблона"));
      return api.saveGptTextAsTemplate(projectId, stepCode, {
        name,
        text: draft,
      });
    },
    onSuccess: (r) => {
      toast.success(`Шаблон сохранён: ${r.filename}`);
      setTemplateName("");
      qc.invalidateQueries({ queryKey: ["prompt-files", stepCode] });
    },
    onError: (e) => toast.error(String(e)),
  });

  const reset = useMutation({
    mutationFn: () => api.resetProjectGptText(projectId, stepCode),
    onSuccess: (r) => {
      setDraft(r.text);
      setDirty(false);
      toast.success("Сброшено к автоматическому тексту");
      qc.invalidateQueries({ queryKey: ["gpt-text", projectId, stepCode] });
      qc.invalidateQueries({ queryKey: ["project", projectId] });
    },
    onError: (e) => toast.error(String(e)),
  });

  if (data.isLoading) {
    return (
      <div className="flex justify-center py-12">
        <Loader2 className="h-6 w-6 animate-spin text-muted-foreground" />
      </div>
    );
  }

  if (data.data && !data.data.supported) {
    return (
      <p className="text-sm text-muted-foreground">
        Для этого шага нет редактируемого сопроводительного текста — он формируется автоматически
        или шаг не использует ChatGPT.
      </p>
    );
  }

  return (
    <section className="flex flex-col gap-3">
      <div className="flex flex-wrap items-center gap-2">
        <h3 className="text-xs font-medium uppercase tracking-wider text-muted-foreground">
          Текстовый вариант для ChatGPT
        </h3>
        {data.data?.is_override ? (
          <Badge variant="muted" className="text-[9px]">
            изменён вручную
          </Badge>
        ) : (
          <Badge variant="muted" className="text-[9px]">
            автоматический
          </Badge>
        )}
      </div>
      <p className="text-xs text-muted-foreground">
        {stepCode === "anim_pr" ? (
          <>
            Сопроводительное сообщение в ChatGPT (мастер-промт уходит отдельным файлом, как на
            шаге «Промты картинок»). В тексте — закадровка по кадрам; дальше воркер шлёт
            картинки пачками по 5.
          </>
        ) : stepCode === "hero" ? (
          <>
            Полный текст сообщения в ChatGPT. Плейсхолдеры{" "}
            <code className="text-[10px]">{"{{BRIEF}}"}</code>,{" "}
            <code className="text-[10px]">{"{{HERO_STYLE}}"}</code> подставляются при запуске.
          </>
        ) : stepCode === "plan" || stepCode === "script" || stepCode === "split" ? (
          <>
            Короткое сопроводительное сообщение в ChatGPT. Параметры из вкладки «Настройки» ноды
            (длина, символы, размеры ячеек) автоматически добавляются в конец при отправке.
            Мастер-промт и Excel — отдельными файлами.
          </>
        ) : (
          <>
            Короткое сопроводительное сообщение в ChatGPT (не мастер-промт). Мастер-промт и Excel
            уходят отдельными файлами-вложениями — их редактирование через «Файлы промтов» и Excel.
          </>
        )}
      </p>
      <Textarea
        value={draft}
        onChange={(e) => {
          setDraft(e.target.value);
          setDirty(true);
        }}
        rows={22}
        className="font-mono text-[11px] leading-relaxed"
        placeholder="Текст сопроводительного сообщения для GPT…"
      />
      <div className="flex flex-col gap-2 rounded-lg border border-white/10 bg-white/[0.03] p-2.5">
        <label className="text-[10px] font-medium uppercase tracking-wider text-muted-foreground">
          Сохранить как шаблон для новых проектов
        </label>
        <div className="flex flex-wrap gap-2">
          <input
            type="text"
            value={templateName}
            onChange={(e) => setTemplateName(e.target.value)}
            placeholder="имя_шаблона"
            className="h-8 min-w-[140px] flex-1 rounded-md border border-white/10 bg-background px-2 text-xs"
          />
          <Button
            size="sm"
            variant="secondary"
            disabled={!templateName.trim() || saveTemplate.isPending}
            onClick={() => saveTemplate.mutate()}
          >
            {saveTemplate.isPending ? (
              <Loader2 className="h-3.5 w-3.5 animate-spin" />
            ) : (
              <BookmarkPlus className="h-3.5 w-3.5" />
            )}
            Сохранить как шаблон
          </Button>
        </div>
        <p className="text-[10px] text-muted-foreground">
          Файл попадёт в prompts/{stepCode}/ и будет доступен при создании следующих проектов.
        </p>
      </div>
      <div className="flex flex-wrap gap-2">
        <Button size="sm" disabled={!dirty || save.isPending} onClick={() => save.mutate()}>
          {save.isPending ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Save className="h-3.5 w-3.5" />}
          Сохранить текст
        </Button>
        <Button
          size="sm"
          variant="outline"
          disabled={reset.isPending || !data.data?.is_override}
          onClick={() => reset.mutate()}
        >
          {reset.isPending ? (
            <Loader2 className="h-3.5 w-3.5 animate-spin" />
          ) : (
            <RotateCcw className="h-3.5 w-3.5" />
          )}
          Сбросить override
        </Button>
      </div>
    </section>
  );
}
