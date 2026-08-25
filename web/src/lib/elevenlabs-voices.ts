/** Каталог голосов 11Labs для ноды «Озвучка». */

export type ElevenLabsVoice = {
  id: string;
  name: string;
  description: string;
};

export const ELEVENLABS_VOICES: ElevenLabsVoice[] = [
  {
    id: "pNInz6obpgDQGcFmaJgB",
    name: "Адам (Adam)",
    description: "глубокий, эпичный голос рассказчика (рекомендуется)",
  },
  {
    id: "JBFqnCBsd6RMkjVDRZzb",
    name: "Джордж (George)",
    description: "тёплый, харизматичный сторителлер",
  },
  {
    id: "nPczCjzI2devNBz1zQrb",
    name: "Брайан (Brian)",
    description: "бархатный, солидный диктор",
  },
  {
    id: "IKne3meq5aSn9XLyUdCD",
    name: "Чарли (Charlie)",
    description: "уверенный, энергичный голос",
  },
  {
    id: "TX3LPaxmHKxFdv7VOQHJ",
    name: "Лиам (Liam)",
    description: "молодой, современный голос",
  },
  {
    id: "Xb7hH8MSUJpSbSDYk0k2",
    name: "Алиса (Alice)",
    description: "чёткий, выразительный женский голос",
  },
  {
    id: "EXAVITQu4vr4xnSDxMaL",
    name: "Сара (Sarah)",
    description: "уверенный, зрелый женский голос",
  },
  {
    id: "hpp4J3VqNfWAUOO0d1Us",
    name: "Белла (Bella)",
    description: "тёплый, яркий женский голос",
  },
  {
    id: "pFZP5JQG7iQjIQuC4Bku",
    name: "Лили (Lily)",
    description: "бархатный, кинематографичный женский голос",
  },
];

export const DEFAULT_ELEVENLABS_VOICE_ID = "pNInz6obpgDQGcFmaJgB";

export function elevenLabsVoiceLabel(v: ElevenLabsVoice): string {
  return `${v.name} — ${v.description}`;
}

export function findElevenLabsVoice(id: string | null | undefined): ElevenLabsVoice | undefined {
  if (!id) return undefined;
  return ELEVENLABS_VOICES.find((v) => v.id === id);
}
