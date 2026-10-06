/** Пресеты voice_id для WaveSpeed Eleven V4. Имена с docs WaveSpeed; можно ввести и свой ID. */

export type WaveSpeedVoice = {
  id: string;
  description: string;
};

export const DEFAULT_WAVESPEED_VOICE_ID = "Alicia";

export const WAVESPEED_ELEVEN_V4_VOICES: WaveSpeedVoice[] = [
  { id: "Alicia", description: "полированный диктор, дефолт Eleven V4" },
  { id: "Adam", description: "яркий уверенный мужской" },
  { id: "Alice", description: "ясный женский, британский" },
  { id: "Aria", description: "спокойный женский" },
  { id: "Bella", description: "тёплый американский рассказ" },
  { id: "Bill", description: "дружелюбный рассказчик" },
  { id: "Brian", description: "средний мужской, реклама и нарратив" },
  { id: "Charlie", description: "молодой энергичный" },
  { id: "Daniel", description: "новостной, собранный" },
  { id: "George", description: "тёплый мужской" },
  { id: "Jessica", description: "молодой женский" },
  { id: "Liam", description: "молодой, для коротких роликов" },
  { id: "Lily", description: "бархатный британский женский" },
  { id: "River", description: "нейтральный разговорный" },
  { id: "Sarah", description: "уверенный зрелый женский" },
  { id: "Thomas", description: "тихий мужской, нарратив" },
  { id: "Baxter", description: "документальный мужской" },
  { id: "Caleb", description: "приземлённый мужской" },
  { id: "Callum", description: "низкий, с хрипотцой" },
  { id: "Charlotte", description: "выразительный женский" },
  { id: "Chris", description: "естественный разговорный" },
  { id: "Clyde", description: "характерный" },
  { id: "Darian", description: "баритон для историй" },
  { id: "Dave", description: "пресет WaveSpeed" },
  { id: "Dorothy", description: "пресет WaveSpeed" },
  { id: "Drew", description: "пресет WaveSpeed" },
  { id: "Eddie", description: "ясный тенор" },
  { id: "Elara", description: "чистый женский" },
  { id: "Eldrin", description: "британский баритон" },
  { id: "Elowen", description: "живой женский" },
  { id: "Emily", description: "пресет WaveSpeed" },
  { id: "Eric", description: "ровный мужской" },
  { id: "Ethan", description: "пресет WaveSpeed" },
  { id: "Fin", description: "пресет WaveSpeed" },
  { id: "Finley", description: "чёткий британский" },
  { id: "Florence", description: "лирический, для длинного текста" },
  { id: "Freya", description: "пресет WaveSpeed" },
  { id: "Gigi", description: "пресет WaveSpeed" },
  { id: "Giovanni", description: "пресет WaveSpeed" },
  { id: "Glinda", description: "пресет WaveSpeed" },
  { id: "Grace", description: "пресет WaveSpeed" },
  { id: "Harry", description: "энергичный мужской" },
  { id: "Jade", description: "быстрый женский" },
  { id: "James", description: "пресет WaveSpeed" },
  { id: "Jeremy", description: "пресет WaveSpeed" },
  { id: "Jessie", description: "пресет WaveSpeed" },
  { id: "Joseph", description: "пресет WaveSpeed" },
  { id: "Kaelen", description: "низкий кинематографичный" },
  { id: "Laura", description: "яркий женский" },
  { id: "Lawrence", description: "энергичный диктор" },
  { id: "Maisie", description: "тёплый женский" },
  { id: "Matilda", description: "собранный женский" },
  { id: "Michael", description: "пресет WaveSpeed" },
  { id: "Mimi", description: "пресет WaveSpeed" },
  { id: "Nicole", description: "пресет WaveSpeed" },
  { id: "Patrick", description: "пресет WaveSpeed" },
  { id: "Paul", description: "пресет WaveSpeed" },
  { id: "Roger", description: "спокойный разговорный" },
  { id: "Sawyer", description: "баритон, нуар" },
  { id: "Serena", description: "пресет WaveSpeed" },
  { id: "Talia", description: "мягкий женский" },
  { id: "Warren", description: "расслабленный баритон" },
  { id: "Will", description: "разговорный" },
  { id: "Wyatt", description: "размеренный документальный" },
];

const _IDS = new Set(WAVESPEED_ELEVEN_V4_VOICES.map((v) => v.id));

export function isWaveSpeedPreset(id: string | null | undefined): boolean {
  return !!id && _IDS.has(id);
}

export function waveSpeedVoiceLabel(v: WaveSpeedVoice): string {
  return v.description ? `${v.id} — ${v.description}` : v.id;
}
