"use client";

import { useEffect, useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Loader2, Save } from "lucide-react";
import { toast } from "sonner";
import { api } from "@/lib/api";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import {
  charCountFromDuration,
  effectiveDurationSeconds,
  readNodeStepParams,
  withNodeStepParams,
  type NodeStepParamsMeta,
  type PlanScriptStepParams,
  type SplitStepParams,
} from "@/lib/node-step-params";

function NumField({
  label,
  description,
  value,
  onChange,
  placeholder = "____",
}: {
  label: string;
  description?: string;
  value: string;
  onChange: (v: string) => void;
  placeholder?: string;
}) {
  return (
    <label className="flex flex-col gap-1">
      <span className="text-sm font-medium text-foreground">{label}</span>
      {description ? (
        <span className="text-xs text-muted-foreground">{description}</span>
      ) : null}
      <Input
        type="number"
        min={1}
        step={1}
        className="max-w-[200px] font-mono"
        placeholder={placeholder}
        value={value}
        onChange={(e) => onChange(e.target.value)}
      />
    </label>
  );
}

function PlanScriptFields({
  header,
  step,
  params,
  inheritedFromPlan,
  onSave,
  saving,
}: {
  header: string;
  step: "plan" | "script";
  params: NodeStepParamsMeta;
  inheritedFromPlan: boolean;
  onSave: (patch: PlanScriptStepParams) => void;
  saving: boolean;
}) {
  const own = params[step]?.duration_seconds;
  const effective = effectiveDurationSeconds(params, step);
  const [durDraft, setDurDraft] = useState(
    own != null && own > 0 ? String(own) : "",
  );

  useEffect(() => {
    setDurDraft(own != null && own > 0 ? String(own) : "");
  }, [own, step]);

  const charCount = charCountFromDuration(effective);

  return (
    <section className="flex flex-col gap-4 rounded-lg border border-white/10 bg-white/[0.02] p-4">
      <div>
        <h3 className="text-sm font-semibold text-foreground">{header}</h3>
        {inheritedFromPlan ? (
          <p className="mt-1 text-xs text-amber-400/90">
            Длина не задана для закадрового текста — используются значения из «Сценарий» (
            {effective} сек).
          </p>
        ) : null}
      </div>
      <NumField
        label="Длина, секунд"
        description="Пустое поле в GPT-сообщении отображается как ____"
        value={durDraft}
        onChange={setDurDraft}
      />
      <div className="rounded-md border border-white/5 bg-black/20 px-3 py-2 text-sm">
        <p className="text-muted-foreground">Количество символов (длина × 14)</p>
        <p className="mt-1 font-mono text-foreground">
          = {charCount != null ? charCount : "____"}
        </p>
      </div>
      <Button
        size="sm"
        className="w-fit gap-1.5"
        disabled={saving}
        onClick={() => {
          const trimmed = durDraft.trim();
          onSave({
            duration_seconds: trimmed ? Math.max(1, Math.round(Number(trimmed))) : null,
          });
        }}
      >
        {saving ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Save className="h-3.5 w-3.5" />}
        Сохранить
      </Button>
    </section>
  );
}

function SplitFields({
  params,
  onSave,
  saving,
}: {
  params: NodeStepParamsMeta;
  onSave: (patch: SplitStepParams) => void;
  saving: boolean;
}) {
  const split = params.split || {};
  const [minDraft, setMinDraft] = useState(split.cell_min_chars?.toString() ?? "");
  const [maxDraft, setMaxDraft] = useState(split.cell_max_chars?.toString() ?? "");
  const [avgMinDraft, setAvgMinDraft] = useState(split.cell_avg_min?.toString() ?? "");
  const [avgMaxDraft, setAvgMaxDraft] = useState(split.cell_avg_max?.toString() ?? "");

  useEffect(() => {
    setMinDraft(split.cell_min_chars?.toString() ?? "");
    setMaxDraft(split.cell_max_chars?.toString() ?? "");
    setAvgMinDraft(split.cell_avg_min?.toString() ?? "");
    setAvgMaxDraft(split.cell_avg_max?.toString() ?? "");
  }, [split.cell_avg_max, split.cell_avg_min, split.cell_max_chars, split.cell_min_chars]);

  const parseOpt = (s: string) => {
    const t = s.trim();
    if (!t) return null;
    const n = Math.round(Number(t));
    return Number.isFinite(n) && n > 0 ? n : null;
  };

  return (
    <section className="flex flex-col gap-4 rounded-lg border border-white/10 bg-white/[0.02] p-4">
      <h3 className="text-sm font-semibold text-foreground">Разбивка</h3>
      <NumField
        label="Минимальное количество символов в ячейке"
        value={minDraft}
        onChange={setMinDraft}
      />
      <NumField
        label="Максимальное количество символов в ячейке"
        value={maxDraft}
        onChange={setMaxDraft}
      />
      <div className="grid gap-3 sm:grid-cols-2">
        <NumField label="Средние значения — от" value={avgMinDraft} onChange={setAvgMinDraft} />
        <NumField label="Средние значения — до" value={avgMaxDraft} onChange={setAvgMaxDraft} />
      </div>
      <Button
        size="sm"
        className="w-fit gap-1.5"
        disabled={saving}
        onClick={() =>
          onSave({
            cell_min_chars: parseOpt(minDraft),
            cell_max_chars: parseOpt(maxDraft),
            cell_avg_min: parseOpt(avgMinDraft),
            cell_avg_max: parseOpt(avgMaxDraft),
          })
        }
      >
        {saving ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Save className="h-3.5 w-3.5" />}
        Сохранить
      </Button>
    </section>
  );
}

export function NodeStepParamsPanel({
  projectId,
  nodeType,
}: {
  projectId: number;
  nodeType: string;
}) {
  const qc = useQueryClient();
  const step = nodeType === "plan" || nodeType === "script" || nodeType === "split" ? nodeType : null;

  const project = useQuery({
    queryKey: ["project", projectId],
    queryFn: () => api.getProject(projectId),
    enabled: projectId > 0 && step != null,
  });

  const save = useMutation({
    mutationFn: async ({
      meta,
    }: {
      meta: Record<string, unknown>;
    }) => api.patchProject(projectId, { meta }),
    onSuccess: () => {
      toast.success("Параметры сохранены");
      qc.invalidateQueries({ queryKey: ["project", projectId] });
      qc.invalidateQueries({ queryKey: ["gpt-text", projectId] });
    },
    onError: (e) => toast.error(String(e)),
  });

  const metaRecord = (project.data?.meta || {}) as Record<string, unknown>;
  const params = useMemo(() => readNodeStepParams(metaRecord), [metaRecord]);

  if (!step || project.isLoading) return null;

  const persist = (patch: PlanScriptStepParams | SplitStepParams) => {
    save.mutate({ meta: withNodeStepParams(metaRecord, step, patch) });
  };

  return (
    <div className="flex flex-col gap-4">
      <p className="text-xs text-muted-foreground">
        Эти параметры автоматически добавляются в конец сопроводительного текста для ChatGPT
        (вкладка «Промты GPT» → «Текстовый вариант»). Пустые поля в сообщении GPT отображаются
        как <span className="font-mono">____</span>.
      </p>
      {step === "plan" ? (
        <PlanScriptFields
          header="Сценарий"
          step="plan"
          params={params}
          inheritedFromPlan={false}
          onSave={persist}
          saving={save.isPending}
        />
      ) : null}
      {step === "script" ? (
        <PlanScriptFields
          header="Закадровый текст"
          step="script"
          params={params}
          inheritedFromPlan={
            !(params.script?.duration_seconds != null && params.script.duration_seconds > 0) &&
            effectiveDurationSeconds(params, "script") != null
          }
          onSave={persist}
          saving={save.isPending}
        />
      ) : null}
      {step === "split" ? (
        <SplitFields params={params} onSave={persist} saving={save.isPending} />
      ) : null}
    </div>
  );
}
