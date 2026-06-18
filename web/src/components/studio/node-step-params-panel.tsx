"use client";

import { useEffect, useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Loader2, Save, ChevronDown, ChevronUp } from "lucide-react";
import { toast } from "sonner";
import { errorMessageFromUnknown } from "@/lib/error-message";
import { api } from "@/lib/api";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import {
  DEFAULT_ELEVENLABS_VOICE_ID,
  ELEVENLABS_VOICES,
  elevenLabsVoiceLabel,
  findElevenLabsVoice,
} from "@/lib/elevenlabs-voices";
import {
  charCountFromDuration,
  effectiveDurationSeconds,
  readNodeStepParams,
  withNodeStepParams,
  bgmLevelToDb,
  type AssembleStepParams,
  type AudioStepParams,
  type NodeStepParamsMeta,
  type PlanScriptStepParams,
  type SplitStepParams,
} from "@/lib/node-step-params";
import { cn } from "@/lib/utils";

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

function AudioFields({
  params,
  onSave,
  saving,
}: {
  params: NodeStepParamsMeta;
  onSave: (patch: AudioStepParams) => void;
  saving: boolean;
}) {
  const savedId = params.audio?.elevenlabs_voice_id ?? DEFAULT_ELEVENLABS_VOICE_ID;
  const [voiceDraft, setVoiceDraft] = useState(savedId);

  useEffect(() => {
    setVoiceDraft(savedId);
  }, [savedId]);

  const selected = findElevenLabsVoice(voiceDraft);

  return (
    <section className="flex flex-col gap-4 rounded-lg border border-white/10 bg-white/[0.02] p-4">
      <div>
        <h3 className="text-sm font-semibold text-foreground">11Labs — голос</h3>
        <p className="mt-1 text-xs text-muted-foreground">
          Бот откроет Text to Speech, выберет модель Eleven v3, вставит ID голоса в поиск
          голоса, выберет карточку и сгенерирует полный закадровый текст.
        </p>
      </div>
      <label className="flex flex-col gap-1.5">
        <span className="text-sm font-medium text-foreground">Голос</span>
        <select
          className="h-9 w-full max-w-md rounded-md border border-input bg-background px-2 text-sm"
          value={voiceDraft}
          onChange={(e) => setVoiceDraft(e.target.value)}
        >
          {ELEVENLABS_VOICES.map((v) => (
            <option key={v.id} value={v.id}>
              {elevenLabsVoiceLabel(v)}
            </option>
          ))}
        </select>
        {selected ? (
          <span className="font-mono text-[11px] text-muted-foreground">ID: {selected.id}</span>
        ) : null}
      </label>
      <Button
        size="sm"
        className="w-fit gap-1.5"
        disabled={saving}
        onClick={() =>
          onSave({
            elevenlabs_voice_id: voiceDraft || DEFAULT_ELEVENLABS_VOICE_ID,
          })
        }
      >
        {saving ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Save className="h-3.5 w-3.5" />}
        Сохранить
      </Button>
    </section>
  );
}

function StepperControl({
  label,
  description,
  value,
  min,
  max,
  step,
  valueLabel,
  disabled,
  onChange,
}: {
  label: string;
  description?: string;
  value: number;
  min: number;
  max: number;
  step: number;
  valueLabel: string;
  disabled?: boolean;
  onChange: (next: number) => void;
}) {
  const dec = () => onChange(Math.max(min, value - step));
  const inc = () => onChange(Math.min(max, value + step));

  return (
    <div className="flex flex-col gap-1.5">
      <span className="text-sm font-medium text-foreground">{label}</span>
      {description ? (
        <span className="text-xs text-muted-foreground">{description}</span>
      ) : null}
      <div className="flex items-center gap-2">
        <Button
          type="button"
          variant="outline"
          size="icon"
          className="h-8 w-8 shrink-0"
          disabled={disabled || value <= min}
          onClick={dec}
          aria-label="Уменьшить"
        >
          <ChevronDown className="h-4 w-4" />
        </Button>
        <span className="min-w-[7rem] text-center font-mono text-sm text-foreground">
          {valueLabel}
        </span>
        <Button
          type="button"
          variant="outline"
          size="icon"
          className="h-8 w-8 shrink-0"
          disabled={disabled || value >= max}
          onClick={inc}
          aria-label="Увеличить"
        >
          <ChevronUp className="h-4 w-4" />
        </Button>
      </div>
    </div>
  );
}

function AssembleFields({
  params,
  metaRecord,
  onSave,
  saving,
}: {
  params: NodeStepParamsMeta;
  metaRecord: Record<string, unknown>;
  onSave: (patch: AssembleStepParams) => void;
  saving: boolean;
}) {
  const subsOn = params.assemble?.subtitles_enabled !== false;
  const tailSaved = params.assemble?.post_voiceover_tail_seconds ?? 0;
  const bgmFromMeta =
    typeof metaRecord.bgm_level === "number" ? Math.round(metaRecord.bgm_level) : 35;
  const bgmSaved = params.assemble?.bgm_level ?? bgmFromMeta;
  const sendToMain = params.assemble?.send_to_main_pc !== false;

  return (
    <section className="flex flex-col gap-4 rounded-lg border border-white/10 bg-white/[0.02] p-4">
      <div>
        <h3 className="text-sm font-semibold text-foreground">Сборка FFmpeg</h3>
        <p className="mt-1 text-xs text-muted-foreground">
          Субтитры, хвост видео после озвучки и громкость фона из папки music/.
        </p>
      </div>
      <StepperControl
        label="Время видео после окончания озвучки"
        description="Секунды: последний кадр держится после конца голоса; музыка продолжается."
        value={tailSaved}
        min={0}
        max={120}
        step={1}
        valueLabel={`${tailSaved} с`}
        disabled={saving}
        onChange={(next) => onSave({ post_voiceover_tail_seconds: next })}
      />
      <StepperControl
        label="Громкость фоновой музыки"
        description="Файл bgm.mp3 в music/. 100% = 0 дБ на шкале слайдера."
        value={bgmSaved}
        min={0}
        max={100}
        step={5}
        valueLabel={`${bgmSaved} · ${bgmLevelToDb(bgmSaved)}`}
        disabled={saving}
        onChange={(next) => onSave({ bgm_level: next })}
      />
      <button
        type="button"
        disabled={saving}
        onClick={() => onSave({ send_to_main_pc: !sendToMain })}
        className={cn(
          "flex w-full items-start justify-between gap-2 rounded-lg border px-2.5 py-2 text-left transition-colors",
          sendToMain ? "border-primary/40 bg-primary/10" : "border-border/60 hover:bg-accent/40",
        )}
      >
        <span className="flex flex-col">
          <span className="text-sm font-medium text-foreground">Отправить на основной ПК</span>
          <span className="text-xs text-muted-foreground">
            {sendToMain
              ? "После музыки проект уйдёт на hub для ASR и FFmpeg"
              : "Монтаж локально на этой станции"}
          </span>
        </span>
        <span
          className={cn(
            "mt-0.5 h-5 w-9 shrink-0 rounded-full p-0.5 transition-colors",
            sendToMain ? "bg-primary" : "bg-muted",
          )}
        >
          <span
            className={cn(
              "block h-4 w-4 rounded-full bg-white shadow transition-transform",
              sendToMain && "translate-x-4",
            )}
          />
        </span>
      </button>
      <button
        type="button"
        disabled={saving}
        onClick={() => onSave({ subtitles_enabled: !subsOn })}
        className={cn(
          "flex w-full items-start justify-between gap-2 rounded-lg border px-2.5 py-2 text-left transition-colors",
          subsOn ? "border-primary/40 bg-primary/10" : "border-border/60 hover:bg-accent/40",
        )}
      >
        <span className="flex flex-col">
          <span className="text-sm font-medium text-foreground">Субтитры в ролике</span>
          <span className="text-xs text-muted-foreground">
            {subsOn ? "Включены — ASS вшивается при сборке" : "Выключены — без прожига текста"}
          </span>
        </span>
        <span
          className={cn(
            "mt-0.5 h-5 w-9 shrink-0 rounded-full p-0.5 transition-colors",
            subsOn ? "bg-primary" : "bg-muted",
          )}
        >
          <span
            className={cn(
              "block h-4 w-4 rounded-full bg-white shadow transition-transform",
              subsOn && "translate-x-4",
            )}
          />
        </span>
      </button>
      {saving ? (
        <span className="flex items-center gap-1 text-[10px] text-muted-foreground">
          <Loader2 className="h-3 w-3 animate-spin" />
          Сохранение…
        </span>
      ) : null}
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
  const step =
    nodeType === "plan" ||
    nodeType === "script" ||
    nodeType === "split" ||
    nodeType === "audio" ||
    nodeType === "assemble"
      ? nodeType
      : null;

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
    onError: (e) => toast.error(errorMessageFromUnknown(e)),
  });

  const metaRecord = (project.data?.meta || {}) as Record<string, unknown>;
  const params = useMemo(() => readNodeStepParams(metaRecord), [metaRecord]);

  if (!step || project.isLoading) return null;

  const persist = (
    patch: PlanScriptStepParams | SplitStepParams | AudioStepParams | AssembleStepParams,
  ) => {
    save.mutate({ meta: withNodeStepParams(metaRecord, step, patch) });
  };

  return (
    <div className="flex flex-col gap-4">
      {step !== "audio" && step !== "assemble" ? (
        <p className="text-xs text-muted-foreground">
          Эти параметры автоматически добавляются в конец сопроводительного текста для ChatGPT
          (вкладка «Промты GPT» → «Текстовый вариант»). Пустые поля в сообщении GPT отображаются
          как <span className="font-mono">____</span>.
        </p>
      ) : null}
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
      {step === "audio" ? (
        <AudioFields params={params} onSave={persist} saving={save.isPending} />
      ) : null}
      {step === "assemble" ? (
        <AssembleFields
          params={params}
          metaRecord={metaRecord}
          onSave={persist}
          saving={save.isPending}
        />
      ) : null}
    </div>
  );
}
