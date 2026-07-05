import type { BlockKind } from "./types";

export type OrchestratorFieldType = "slider" | "number" | "text";

export type OrchestratorField = {
  key: string;
  label: string;
  type: OrchestratorFieldType;
  min?: number;
  max?: number;
  step?: number;
  placeholder?: string;
  /** Ключи слотов, куда уходит значение */
  feedsSlots?: string[];
};

/** Параметры → оркестратор агентов (не финальный промт) */
export const ORCHESTRATOR_FIELDS_BY_KIND: Record<BlockKind, OrchestratorField[]> = {
  role: [
    {
      key: "AGENT_PERSONA_WEIGHT",
      label: "Жёсткость роли",
      type: "slider",
      min: 0,
      max: 100,
      step: 5,
    },
    {
      key: "AGENT_CONTEXT_NOTE",
      label: "Контекст агенту",
      type: "text",
      placeholder: "Доп. инструкция оркестратору…",
    },
  ],
  technical: [
    {
      key: "VIDEO_DURATION_SEC",
      label: "Длительность, сек",
      type: "slider",
      min: 15,
      max: 180,
      step: 5,
    },
    {
      key: "PROMPT_LEN_MIN",
      label: "Мин. длина",
      type: "number",
      min: 100,
      max: 4000,
    },
    {
      key: "PROMPT_LEN_MAX",
      label: "Макс. длина",
      type: "number",
      min: 500,
      max: 8000,
    },
  ],
  features: [
    {
      key: "STYLE_INTENSITY",
      label: "Интенсивность стиля",
      type: "slider",
      min: 0,
      max: 100,
      step: 1,
    },
    {
      key: "STYLE_LOCK_STRENGTH",
      label: "Запрет смешения",
      type: "slider",
      min: 0,
      max: 100,
      step: 5,
    },
    {
      key: "STYLE_OVERRIDE",
      label: "Уточнение",
      type: "text",
      placeholder: "Текстура, палитра…",
    },
  ],
  rules: [
    {
      key: "RULES_STRICTNESS",
      label: "Строгость правил",
      type: "slider",
      min: 0,
      max: 100,
      step: 5,
    },
  ],
  narrative: [
    {
      key: "VOICEOVER_MIN_CHARS",
      label: "Мин. знаков озвучки",
      type: "number",
      min: 200,
      max: 2000,
    },
    {
      key: "VOICEOVER_MAX_CHARS",
      label: "Макс. знаков",
      type: "number",
      min: 400,
      max: 3000,
    },
    {
      key: "NARRATIVE_HOOK_SEC",
      label: "Hook, сек",
      type: "slider",
      min: 1,
      max: 8,
      step: 1,
    },
  ],
  negative: [
    {
      key: "NEGATIVE_STRENGTH",
      label: "Сила негатива",
      type: "slider",
      min: 0,
      max: 100,
      step: 5,
    },
  ],
  output: [
    {
      key: "OUTPUT_FORMAT",
      label: "Формат ответа",
      type: "text",
      placeholder: "markdown / json / plain",
    },
    {
      key: "OUTPUT_SECTIONS",
      label: "Секции",
      type: "text",
      placeholder: "через запятую",
    },
  ],
};

export const DEFAULT_ORCHESTRATOR_VARS: Record<string, string | number> = {
  AGENT_PERSONA_WEIGHT: 70,
  VIDEO_DURATION_SEC: 60,
  PROMPT_LEN_MIN: 500,
  PROMPT_LEN_MAX: 4800,
  STYLE_INTENSITY: 65,
  STYLE_LOCK_STRENGTH: 80,
  RULES_STRICTNESS: 75,
  VOICEOVER_MIN_CHARS: 800,
  VOICEOVER_MAX_CHARS: 900,
  NARRATIVE_HOOK_SEC: 3,
  NEGATIVE_STRENGTH: 60,
  OUTPUT_FORMAT: "markdown",
};
