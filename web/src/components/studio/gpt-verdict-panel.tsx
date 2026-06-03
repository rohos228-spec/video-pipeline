"use client";



import { useEffect, useState } from "react";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { Loader2, Play, Save, Trash2 } from "lucide-react";

import { toast } from "sonner";

import { errorMessageFromUnknown } from "@/lib/error-message";

import { api } from "@/lib/api";

import {

  readGptVerdictTemplate,

  writeGptVerdictTemplate,

} from "@/lib/gpt-verdict-template-storage";

import { Button } from "@/components/ui/button";

import { Textarea } from "@/components/ui/textarea";

import { Badge } from "@/components/ui/badge";

import { Input } from "@/components/ui/input";



export function GptVerdictPanel({

  projectId,

  stepCode,

  projectMeta,

  onPersistMeta,

}: {

  projectId: number;

  stepCode: string;

  projectMeta: Record<string, unknown>;

  onPersistMeta: (meta: Record<string, unknown>) => void;

}) {

  const qc = useQueryClient();

  const [draft, setDraft] = useState("");

  const [dirty, setDirty] = useState(false);

  const [lastRaw, setLastRaw] = useState("");

  const [activeTemplate, setActiveTemplate] = useState(() =>

    readGptVerdictTemplate(projectMeta, stepCode),

  );

  const [saveName, setSaveName] = useState("");



  const ctx = useQuery({

    queryKey: ["gpt-verdict", projectId, stepCode, activeTemplate],

    queryFn: () => api.getGptVerdictContext(projectId, stepCode, activeTemplate),

    enabled: Boolean(projectId && stepCode),

  });



  const templatesQuery = useQuery({

    queryKey: ["gpt-verdict-templates", stepCode],

    queryFn: () => api.listGptVerdictTemplates(stepCode),

    enabled: Boolean(stepCode),

  });



  useEffect(() => {

    if (ctx.data && !dirty) {

      setDraft(ctx.data.prompt);

    }

  }, [ctx.data, dirty]);



  useEffect(() => {

    setDirty(false);

    setLastRaw("");

    setSaveName("");

    setActiveTemplate(readGptVerdictTemplate(projectMeta, stepCode));

  }, [stepCode, projectId, projectMeta]);



  const persistTemplateChoice = (name: string) => {

    onPersistMeta(writeGptVerdictTemplate(projectMeta, stepCode, name));

  };



  const saveTemplate = useMutation({

    mutationFn: (name: string) => {

      const trimmed = name.trim();

      if (!trimmed) return Promise.reject(new Error("Введите имя шаблона"));

      if (!draft.trim()) return Promise.reject(new Error("Промт проверки пуст"));

      return api.saveGptVerdictTemplate(projectId, stepCode, {

        name: trimmed,

        content: draft,

      });

    },

    onSuccess: (_r, name) => {

      toast.success(`Шаблон «${name}» сохранён`);

      setSaveName("");

      setActiveTemplate(name);

      setDirty(false);

      persistTemplateChoice(name);

      qc.invalidateQueries({ queryKey: ["gpt-verdict", projectId, stepCode] });

      qc.invalidateQueries({ queryKey: ["gpt-verdict-templates", stepCode] });

    },

    onError: (e) => toast.error(errorMessageFromUnknown(e)),

  });



  const deleteTemplate = useMutation({

    mutationFn: (name: string) => api.deleteGptVerdictTemplate(projectId, stepCode, name),

    onSuccess: (_r, name) => {

      toast.success(`Шаблон «${name}» удалён`);

      const next = "default";

      setActiveTemplate(next);

      setDirty(false);

      persistTemplateChoice(next);

      qc.invalidateQueries({ queryKey: ["gpt-verdict", projectId, stepCode] });

      qc.invalidateQueries({ queryKey: ["gpt-verdict-templates", stepCode] });

    },

    onError: (e) => toast.error(errorMessageFromUnknown(e)),

  });



  const saveCurrentTemplate = () => saveTemplate.mutate(activeTemplate);

  const saveAsNewTemplate = () => saveTemplate.mutate(saveName.trim());



  const run = useMutation({

    mutationFn: () => api.runGptVerdict(projectId, stepCode, draft),

    onSuccess: (r) => {

      setLastRaw(r.last_raw);

      if (r.approved) {

        toast.success(r.advanced ? `GPT: одобрено → ${r.status}` : "GPT: одобрено");

      } else if (r.fix_applied) {

        toast.success(

          r.advanced

            ? `GPT: правки сохранены → ${r.status}`

            : r.fix_path

              ? `GPT: правки сохранены (${r.fix_path.split(/[/\\]/).pop()})`

              : "GPT: правки сохранены, проверка завершена",

        );

      } else {

        toast.error(r.fix_text || "GPT: не одобрено");

      }

      if (r.advanced) {

        qc.invalidateQueries({ queryKey: ["project", projectId] });

        qc.invalidateQueries({ queryKey: ["project-run", projectId] });

      }

    },

    onError: (e) => toast.error(errorMessageFromUnknown(e)),

  });



  if (ctx.isLoading) {

    return (

      <div className="flex items-center gap-2 text-sm text-muted-foreground">

        <Loader2 className="h-4 w-4 animate-spin" />

        Загрузка проверки…

      </div>

    );

  }



  if (ctx.isError) {

    return (

      <p className="text-sm text-destructive">

        {errorMessageFromUnknown(ctx.error)}

      </p>

    );

  }



  const attachments = ctx.data?.attachments ?? [];

  const templates =

    templatesQuery.data?.templates ?? ctx.data?.templates ?? ["default"];

  const canDelete = activeTemplate !== "default" && templates.includes(activeTemplate);



  return (

    <div className="flex flex-col gap-3">

      <div className="flex flex-wrap items-center gap-2">

        <span className="text-sm font-medium">Проверка GPT (формат «Вердикт»)</span>

        <Badge variant="muted">{activeTemplate}</Badge>

        {attachments.length > 0 && (

          <Badge variant="muted">{attachments.join(", ")}</Badge>

        )}

      </div>



      <div className="flex flex-col gap-2 rounded-lg border border-white/10 bg-white/[0.02] p-2.5">

        <label className="text-[10px] font-semibold uppercase tracking-wider text-muted-foreground">

          Шаблон проверки

        </label>

        <select

          className="h-9 rounded-md border border-input bg-background px-2 text-sm"

          value={activeTemplate}

          onChange={(e) => {

            const next = e.target.value;

            setActiveTemplate(next);

            setDirty(false);

            persistTemplateChoice(next);

          }}

        >

          {templates.map((name) => (

            <option key={name} value={name}>

              {name}

            </option>

          ))}

        </select>

        <div className="flex flex-wrap gap-2">

          <Button

            type="button"

            size="sm"

            className="gap-1.5"

            disabled={!draft.trim() || saveTemplate.isPending}

            onClick={saveCurrentTemplate}

          >

            {saveTemplate.isPending ? (

              <Loader2 className="h-3.5 w-3.5 animate-spin" />

            ) : (

              <Save className="h-3.5 w-3.5" />

            )}

            Сохранить «{activeTemplate}»

          </Button>

          <Input

            value={saveName}

            onChange={(e) => setSaveName(e.target.value)}

            placeholder="Имя нового шаблона"

            className="h-9 max-w-xs text-sm"

          />

          <Button

            type="button"

            size="sm"

            variant="outline"

            className="gap-1.5"

            disabled={!saveName.trim() || saveTemplate.isPending}

            onClick={saveAsNewTemplate}

          >

            {saveTemplate.isPending ? (

              <Loader2 className="h-3.5 w-3.5 animate-spin" />

            ) : (

              <Save className="h-3.5 w-3.5" />

            )}

            Сохранить как…

          </Button>

          <Button

            type="button"

            size="sm"

            variant="outline"

            className="gap-1.5 text-destructive hover:text-destructive"

            disabled={!canDelete || deleteTemplate.isPending}

            onClick={() => {

              if (

                !window.confirm(`Удалить шаблон «${activeTemplate}»? Это необратимо.`)

              ) {

                return;

              }

              deleteTemplate.mutate(activeTemplate);

            }}

          >

            {deleteTemplate.isPending ? (

              <Loader2 className="h-3.5 w-3.5 animate-spin" />

            ) : (

              <Trash2 className="h-3.5 w-3.5" />

            )}

            Удалить «{activeTemplate}»

          </Button>

        </div>

      </div>



      <Textarea

        value={draft}

        onChange={(e) => {

          setDraft(e.target.value);

          setDirty(true);

        }}

        rows={12}

        className="font-mono text-xs"

      />

      <Button

        size="sm"

        className="w-fit"

        disabled={run.isPending}

        onClick={() => run.mutate()}

      >

        {run.isPending ? (

          <Loader2 className="mr-2 h-4 w-4 animate-spin" />

        ) : (

          <Play className="mr-2 h-4 w-4" />

        )}

        Запустить проверку

      </Button>

      {lastRaw ? (

        <pre className="max-h-64 overflow-auto rounded-md border bg-muted/40 p-3 text-xs whitespace-pre-wrap">

          {lastRaw}

        </pre>

      ) : null}

    </div>

  );

}


