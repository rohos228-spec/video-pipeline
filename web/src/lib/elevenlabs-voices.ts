/** Каталог голосов 11Labs для ноды «Озвучка». */

export type ElevenLabsVoice = {
  id: string;
  name: string;
  description: string;
};

export const ELEVENLABS_VOICES: ElevenLabsVoice[] = [
  {
    id: "TUQNWEvVPBLzMBSVDPUA",
    name: "Алекс",
    description: "эпичный голос",
  },
  {
    id: "hLjwV7lYzk15SWLUmhEH",
    name: "Маруся",
    description: "милый голос, тёплый",
  },
  {
    id: "MWyJiWDobXN8FX3CJTdE",
    name: "Олег",
    description: "средний дикторский голос",
  },
  {
    id: "t6lBrEl93uCiLR1Lgm8v",
    name: "Алиса",
    description: "естественный голос",
  },
];

export const DEFAULT_ELEVENLABS_VOICE_ID = ELEVENLABS_VOICES[3].id;

export function elevenLabsVoiceLabel(v: ElevenLabsVoice): string {
  return `${v.name} — ${v.description}`;
}

export function findElevenLabsVoice(id: string | null | undefined): ElevenLabsVoice | undefined {
  if (!id) return undefined;
  return ELEVENLABS_VOICES.find((v) => v.id === id);
}
