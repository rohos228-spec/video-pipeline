/** Тип блока — категория в сборке (mock kind или backend category id). */
export type BlockKind = string;

export type BlockKindMeta = {
  id: BlockKind;
  label: string;
  short: string;
  color: string;
  description: string;
  /** Уровень градации для группировки на пайплайне (1 = вход, 3 = стиль…) */
  tier?: number;
  tierLabel?: string;
};

export const BLOCK_KINDS: BlockKindMeta[] = [
  {
    id: "role",
    label: "Роль",
    short: "Роль",
    color: "hsl(263 55% 62%)",
    description: "Кто агент, контекст задачи",
  },
  {
    id: "technical",
    label: "Технический",
    short: "Тех.",
    color: "hsl(199 80% 55%)",
    description: "Формат, тайминги, объём, JSON, Excel",
  },
  {
    id: "features",
    label: "Особенности",
    short: "Особ.",
    color: "hsl(42 95% 58%)",
    description: "Стиль, визуал, текстуры, камера",
  },
  {
    id: "rules",
    label: "Правила",
    short: "Прав.",
    color: "hsl(0 72% 58%)",
    description: "Запреты, anti-doubles, заполнение таблицы",
  },
  {
    id: "narrative",
    label: "Драматургия",
    short: "Драм.",
    color: "hsl(142 55% 48%)",
    description: "Структура, тон, hook",
  },
  {
    id: "negative",
    label: "Негатив",
    short: "Neg",
    color: "hsl(240 5% 52%)",
    description: "Negative prompt, no text/logos",
  },
  {
    id: "output",
    label: "Формат вывода",
    short: "Вывод",
    color: "hsl(280 60% 60%)",
    description: "Структура ответа, секции, markdown",
  },
];

export type BlockVariant = {
  id: string;
  kind: BlockKind;
  label: string;
  tags: string[];
  /** Шаги пайплайна, где блок уместен */
  steps: string[];
  /** Текст блока (кусок промта) */
  body: string;
  /** Сколько других промтов используют этот же блок */
  sharedCount?: number;
  /** Гибкие критерии для сочетаемости */
  criteria?: Partial<Record<string, string | string[]>>;
  /** С чем хорошо сочетается (block ids) */
  pairsWell?: string[];
  /** Что должно уже быть в контексте */
  requires?: Partial<Record<string, string | string[]>>;
};

export type PromptSlot = {
  slotId: string;
  kind: BlockKind;
  required: boolean;
  /** id варианта по умолчанию */
  defaultBlockId: string;
};

export type PromptTemplate = {
  id: string;
  stepCode: string;
  label: string;
  category: string;
  description?: string;
  /** Legacy-имя файла из prompts/ */
  legacyFile?: string;
  slots: PromptSlot[];
  /** Сопроводительный текст GPT (отдельный слой) */
  hasAccompanying?: boolean;
};

export type PromptSelection = {
  templateId: string;
  /** slotId → blockId (несколько слотов одного kind допустимы) */
  slots: Record<string, string>;
  vars: Record<string, string | number>;
};

export type ComposeWarning = {
  level: "info" | "warn" | "error";
  message: string;
};

export type ComposeResult = {
  text: string;
  sections: { kind: BlockKind; label: string; body: string }[];
  warnings: ComposeWarning[];
  charCount: number;
};
