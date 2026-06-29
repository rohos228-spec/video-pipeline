import { formatApiError } from "./api";

export type ElevenLabsConnectRequest = {
  api_key?: string;
  proxy_url?: string;
  proxy_ip?: string;
  proxy_port?: number;
  proxy_user?: string;
  proxy_password?: string;
  proxy_scheme?: "socks5" | "http";
};

export type ElevenLabsConnectResult = {
  ok: boolean;
  voice_count?: number;
  proxy?: string | null;
  connection_mode?: string;
  key_source?: string;
  key_hint?: string;
  user_read_ok?: boolean;
  note?: string | null;
  subscription_tier?: string | null;
  subscription_status?: string | null;
  can_use_instant_voice_cloning?: boolean | null;
};

export type AccountDiagResult = {
  key_source?: string;
  key_hint?: string;
  subscription_tier?: string | null;
  subscription_status?: string | null;
  can_use_instant_voice_cloning?: boolean | null;
  can_use_professional_voice_cloning?: boolean | null;
  user_read_ok?: boolean;
  verdict?: string | null;
  website_ivc_test?: string;
  website_subscription?: string;
  website_api_keys?: string;
  api_key_preview?: string | null;
  note?: string | null;
  error?: string | null;
};

export type ProxyProfile = {
  id: string;
  label: string;
  url: string;
};

export type ElevenLabsStatus = {
  api_key_configured: boolean;
  api_key_hint?: string | null;
  env_key_configured?: boolean;
  proxy_configured?: boolean;
  proxy_url?: string | null;
  proxy_alt_url?: string | null;
  proxy_profiles?: ProxyProfile[];
};

function parseApiError(detail: string | object): string {
  if (typeof detail === "object" && detail && "detail" in detail) {
    const inner = (detail as { detail?: unknown }).detail;
    if (typeof inner === "object" && inner && inner !== null && "message" in inner) {
      return parseApiError(inner);
    }
    if (typeof inner === "string") return inner;
  }
  if (typeof detail === "object" && detail && "message" in detail) {
    const d = detail as { message?: string; error_kind?: string };
    const kind = d.error_kind;
    const prefix =
      kind === "auth"
        ? "Авторизация"
        : kind === "missing_key"
          ? "Нет ключа"
          : kind === "network"
            ? "Сеть/proxy"
            : kind === "sample"
              ? "Образец"
              : null;
    const msg = d.message || formatApiError(detail);
    return prefix ? `${prefix}: ${msg}` : msg;
  }
  return formatApiError(detail);
}

function cleanConnectBody(body: ElevenLabsConnectRequest): ElevenLabsConnectRequest {
  const out: ElevenLabsConnectRequest = {};
  const key = body.api_key?.trim();
  if (key) out.api_key = key;
  const ip = body.proxy_ip?.trim();
  if (ip) {
    out.proxy_ip = ip;
    if (body.proxy_port) out.proxy_port = body.proxy_port;
    out.proxy_scheme = body.proxy_scheme || "http";
    const user = body.proxy_user?.trim();
    const pass = body.proxy_password?.trim();
    if (user) out.proxy_user = user;
    if (pass) out.proxy_password = pass;
  }
  const proxyUrl = body.proxy_url?.trim();
  if (proxyUrl) out.proxy_url = proxyUrl;
  return out;
}

export function backendFetchHint(): string {
  if (typeof window === "undefined") return "";
  if (window.location.protocol === "file:") {
    return "UI открыт как файл (file://). Запусти GO.cmd и открой http://127.0.0.1:8765";
  }
  const host = window.location.hostname;
  const port = window.location.port;
  if (port && port !== "8765" && (host === "localhost" || host === "127.0.0.1")) {
    return `Страница на :${port}, а API на :8765. Запусти backend (GO.cmd) или открой http://127.0.0.1:8765`;
  }
  return "Backend Studio не отвечает на http://127.0.0.1:8765. Запусти GO.cmd и обнови страницу (Ctrl+F5).";
}

export type SavedVoice = {
  id: string;
  name: string;
  voice_id: string;
  sample_path?: string | null;
  sample_preview_url?: string | null;
  meta?: Record<string, unknown>;
  created_at?: string;
};

export type RemoteVoice = {
  voice_id: string;
  name: string;
  category?: string | null;
  preview_url?: string | null;
  description?: string | null;
  labels?: Record<string, string> | null;
  gender?: string | null;
  age?: string | null;
  accent?: string | null;
  language?: string | null;
  use_case?: string | null;
  descriptive?: string | null;
};

export type LibraryVoiceFilters = {
  gender: string;
  age: string;
  language: string;
  accent: string;
  sort: string;
};

export const defaultLibraryFilters = (): LibraryVoiceFilters => ({
  gender: "",
  age: "",
  language: "",
  accent: "",
  sort: "trending",
});

export const LIBRARY_GENDER_OPTIONS = [
  { value: "", label: "Пол: любой" },
  { value: "male", label: "Мужской" },
  { value: "female", label: "Женский" },
  { value: "neutral", label: "Нейтральный" },
] as const;

export const LIBRARY_AGE_OPTIONS = [
  { value: "", label: "Возраст: любой" },
  { value: "young", label: "Молодой" },
  { value: "middle_aged", label: "Средний" },
  { value: "old", label: "Пожилой" },
] as const;

export const LIBRARY_LANGUAGE_OPTIONS = [
  { value: "", label: "Язык: любой" },
  { value: "ru", label: "Русский" },
  { value: "en", label: "English" },
  { value: "de", label: "Deutsch" },
  { value: "fr", label: "Français" },
  { value: "es", label: "Español" },
  { value: "it", label: "Italiano" },
  { value: "pt", label: "Português" },
  { value: "pl", label: "Polski" },
  { value: "uk", label: "Українська" },
  { value: "ja", label: "日本語" },
  { value: "ko", label: "한국어" },
  { value: "zh", label: "中文" },
  { value: "ar", label: "العربية" },
  { value: "hi", label: "हिन्दी" },
  { value: "tr", label: "Türkçe" },
] as const;

export const LIBRARY_ACCENT_OPTIONS = [
  { value: "", label: "Акцент: любой" },
  { value: "american", label: "American" },
  { value: "british", label: "British" },
  { value: "australian", label: "Australian" },
  { value: "irish", label: "Irish" },
  { value: "scottish", label: "Scottish" },
  { value: "indian", label: "Indian" },
  { value: "russian", label: "Russian" },
  { value: "german", label: "German" },
  { value: "french", label: "French" },
  { value: "spanish", label: "Spanish" },
  { value: "italian", label: "Italian" },
  { value: "brazilian", label: "Brazilian" },
  { value: "mexican", label: "Mexican" },
  { value: "transatlantic", label: "Transatlantic" },
] as const;

export const LIBRARY_SORT_OPTIONS = [
  { value: "trending", label: "Сорт: в тренде" },
  { value: "usage_character_count_1y", label: "Сорт: популярность" },
  { value: "cloned_by_count", label: "Сорт: клонирования" },
  { value: "created_date", label: "Сорт: новые" },
] as const;

export type TtsResult = {
  ok: boolean;
  preview_url: string;
  filename: string;
  duration_s: number;
  voice_id: string;
};

async function json<T>(path: string, init?: RequestInit): Promise<T> {
  let res: Response;
  try {
    res = await fetch(path, {
      ...init,
      headers: {
        ...(init?.body instanceof FormData ? {} : { "Content-Type": "application/json" }),
        ...(init?.headers || {}),
      },
    });
  } catch (err) {
    const raw = err instanceof Error ? err.message : String(err);
    if (/failed to fetch|networkerror|load failed/i.test(raw)) {
      throw new Error(`${backendFetchHint()} (${raw})`);
    }
    throw err instanceof Error ? err : new Error(raw);
  }
  if (!res.ok) {
    let detail: string | object = await res.text();
    try {
      detail = JSON.parse(detail as string);
    } catch {
      /* text */
    }
    const msg = parseApiError(detail);
    if (res.status >= 500 && /internal server error/i.test(String(msg))) {
      throw new Error(
        `Backend 500 — перезапусти GO.cmd. Диагностика: CHECK-PROXY.cmd → data\\proxy-check-result.txt`,
      );
    }
    throw new Error(msg);
  }
  return res.json() as Promise<T>;
}

export const elevenLabsLab = {
  pingBackend: () => json<{ status: string }>("/api/health"),

  status: () => json<ElevenLabsStatus>("/api/elevenlabs/status"),

  connect: (body: ElevenLabsConnectRequest) =>
    json<ElevenLabsConnectResult>("/api/elevenlabs/connect", {
      method: "POST",
      body: JSON.stringify(cleanConnectBody(body)),
    }),

  checkEnv: () => json<ElevenLabsConnectResult>("/api/elevenlabs/check-env"),

  accountDiag: (opts?: { api_key?: string; proxy_url?: string }) => {
    const q = new URLSearchParams();
    if (opts?.api_key) q.set("api_key", opts.api_key);
    if (opts?.proxy_url) q.set("proxy_url", opts.proxy_url);
    const qs = q.toString();
    return json<AccountDiagResult>(`/api/elevenlabs/account-diag${qs ? `?${qs}` : ""}`);
  },

  savedVoices: () => json<{ voices: SavedVoice[] }>("/api/elevenlabs/voices"),

  saveVoice: (body: { name: string; voice_id: string; sample_path?: string; meta?: Record<string, unknown> }) =>
    json<SavedVoice>("/api/elevenlabs/voices", {
      method: "POST",
      body: JSON.stringify(body),
    }),

  deleteVoice: (id: string) =>
    json<{ ok: boolean }>(`/api/elevenlabs/voices/${id}`, { method: "DELETE" }),

  remoteVoices: (params?: {
    api_key?: string;
    proxy_url?: string;
    scope?: "account" | "library" | "all";
    search?: string;
    max_pages?: number;
    gender?: string;
    age?: string;
    accent?: string;
    language?: string;
    locale?: string;
    sort?: string;
    category?: string;
  }) => {
    const q = new URLSearchParams();
    if (params?.api_key) q.set("api_key", params.api_key);
    if (params?.proxy_url) q.set("proxy_url", params.proxy_url);
    if (params?.scope) q.set("scope", params.scope);
    if (params?.search) q.set("search", params.search);
    if (params?.max_pages) q.set("max_pages", String(params.max_pages));
    if (params?.gender) q.set("gender", params.gender);
    if (params?.age) q.set("age", params.age);
    if (params?.accent) q.set("accent", params.accent);
    if (params?.language) q.set("language", params.language);
    if (params?.locale) q.set("locale", params.locale);
    if (params?.sort) q.set("sort", params.sort);
    if (params?.category) q.set("category", params.category);
    const qs = q.toString();
    return json<{
      voices: RemoteVoice[];
      scope?: string;
      account_count?: number;
      library_count?: number;
      total_count?: number;
      api_total_count?: number;
      max_pages?: number;
      filters_active?: boolean;
    }>(`/api/elevenlabs/remote-voices${qs ? `?${qs}` : ""}`);
  },

  tts: (body: {
    text: string;
    voice_id: string;
    api_key?: string;
    proxy_url?: string;
    model_id?: string;
  }) =>
    json<TtsResult>("/api/elevenlabs/tts", {
      method: "POST",
      body: JSON.stringify(body),
    }),

  extractClip: (source: File, start_s: number, end_s: number) => {
    const fd = new FormData();
    fd.append("source", source);
    fd.append("start_s", String(start_s));
    fd.append("end_s", String(end_s));
    return json<{ preview_url: string; duration_s: number; clip_path: string }>(
      "/api/elevenlabs/extract-clip",
      { method: "POST", body: fd },
    );
  },

  clone: (voice_name: string, sample: File, opts?: { api_key?: string; proxy_url?: string }) => {
    const fd = new FormData();
    fd.append("voice_name", voice_name);
    fd.append("sample", sample);
    fd.append("save", "true");
    if (opts?.api_key) fd.append("api_key", opts.api_key);
    if (opts?.proxy_url) fd.append("proxy_url", opts.proxy_url);
    return json<{ clone: { voice_id: string }; saved?: SavedVoice }>("/api/elevenlabs/clone", {
      method: "POST",
      body: fd,
    });
  },

  cloneRedub: (fields: {
    voice_name: string;
    sample: File;
    source_audio: File;
    start_s: number;
    end_s: number;
    fragment_text: string;
    old_word: string;
    new_word: string;
    voice_id?: string;
    api_key?: string;
    proxy_url?: string;
  }) => {
    const fd = new FormData();
    Object.entries(fields).forEach(([k, v]) => {
      if (v != null && v !== "") fd.append(k, v instanceof File ? v : String(v));
    });
    fd.append("save_voice", "true");
    return json<{
      output_mp3: string;
      preview_url: string;
      spoken_text: string;
      voice_id: string;
    }>("/api/elevenlabs/clone-redub", { method: "POST", body: fd });
  },

  previewRedub: (fields: {
    voice_name: string;
    sample: File;
    fragment_text: string;
    old_word: string;
    new_word: string;
    voice_id?: string;
    api_key?: string;
    proxy_url?: string;
  }) => {
    const fd = new FormData();
    Object.entries(fields).forEach(([k, v]) => {
      if (v != null && v !== "") fd.append(k, v instanceof File ? v : String(v));
    });
    fd.append("save_voice", "true");
    return json<{
      run_id: string;
      voice_id: string;
      spoken_text: string;
      patch_filename: string;
      patch_preview_url: string;
      patch_duration_s: number;
    }>("/api/elevenlabs/redub-preview", { method: "POST", body: fd });
  },

  applyRedub: (fields: {
    source_audio: File;
    patch_filename: string;
    start_s: number;
    end_s: number;
    run_id?: string;
  }) => {
    const fd = new FormData();
    Object.entries(fields).forEach(([k, v]) => {
      if (v != null && v !== "") fd.append(k, v instanceof File ? v : String(v));
    });
    return json<{
      preview_url: string;
      output_filename: string;
      output_duration_s: number;
    }>("/api/elevenlabs/redub-apply", { method: "POST", body: fd });
  },
};

function encodeWav(buffer: AudioBuffer): ArrayBuffer {
  const numChannels = buffer.numberOfChannels;
  const sampleRate = buffer.sampleRate;
  const bitDepth = 16;
  const blockAlign = (numChannels * bitDepth) / 8;
  const samples = buffer.length;
  const dataSize = samples * blockAlign;
  const arrayBuffer = new ArrayBuffer(44 + dataSize);
  const view = new DataView(arrayBuffer);
  const writeStr = (offset: number, str: string) => {
    for (let i = 0; i < str.length; i++) view.setUint8(offset + i, str.charCodeAt(i));
  };
  writeStr(0, "RIFF");
  view.setUint32(4, 36 + dataSize, true);
  writeStr(8, "WAVE");
  writeStr(12, "fmt ");
  view.setUint32(16, 16, true);
  view.setUint16(20, 1, true);
  view.setUint16(22, numChannels, true);
  view.setUint32(24, sampleRate, true);
  view.setUint32(28, sampleRate * blockAlign, true);
  view.setUint16(32, blockAlign, true);
  view.setUint16(34, bitDepth, true);
  writeStr(36, "data");
  view.setUint32(40, dataSize, true);
  let offset = 44;
  for (let i = 0; i < samples; i++) {
    for (let ch = 0; ch < numChannels; ch++) {
      const sample = Math.max(-1, Math.min(1, buffer.getChannelData(ch)[i] ?? 0));
      view.setInt16(offset, sample < 0 ? sample * 0x8000 : sample * 0x7fff, true);
      offset += 2;
    }
  }
  return arrayBuffer;
}

export async function extractClipLocal(
  source: File,
  start_s: number,
  end_s: number,
): Promise<{ blob: Blob; duration_s: number }> {
  const ctx = new AudioContext();
  try {
    const buf = await source.arrayBuffer();
    const audio = await ctx.decodeAudioData(buf.slice(0));
    const sr = audio.sampleRate;
    const i0 = Math.max(0, Math.floor(start_s * sr));
    const i1 = Math.min(audio.length, Math.floor(end_s * sr));
    const len = Math.max(1, i1 - i0);
    const out = ctx.createBuffer(audio.numberOfChannels, len, sr);
    for (let ch = 0; ch < audio.numberOfChannels; ch++) {
      out.copyToChannel(audio.getChannelData(ch).subarray(i0, i1), ch);
    }
    const wav = encodeWav(out);
    return { blob: new Blob([wav], { type: "audio/wav" }), duration_s: len / sr };
  } finally {
    await ctx.close();
  }
}

export async function extractClipWithFallback(
  source: File,
  start_s: number,
  end_s: number,
): Promise<{ blob: Blob; duration_s: number; via: "server" | "local" }> {
  try {
    const r = await elevenLabsLab.extractClip(source, start_s, end_s);
    const resp = await fetch(r.preview_url);
    if (!resp.ok) throw new Error("preview fetch failed");
    const blob = await resp.blob();
    return { blob, duration_s: r.duration_s, via: "server" };
  } catch {
    const local = await extractClipLocal(source, start_s, end_s);
    return { ...local, via: "local" };
  }
}

export const LS_KEY = "vp-elevenlabs-lab-settings";

export type LabSettings = {
  apiKey: string;
    proxyScheme: "http" | "socks5";
  proxyIp: string;
  proxyPort: string;
  proxyUser: string;
  proxyPassword: string;
  proxyUrl: string;
};

export function loadLabSettings(): LabSettings {
  if (typeof window === "undefined") {
    return defaultLabSettings();
  }
  try {
    const raw = JSON.parse(localStorage.getItem(LS_KEY) || "{}");
    return { ...defaultLabSettings(), ...raw, proxyScheme: raw.proxyScheme === "socks5" ? "socks5" : "http" };
  } catch {
    return defaultLabSettings();
  }
}

export function saveLabSettings(s: LabSettings) {
  localStorage.setItem(LS_KEY, JSON.stringify(s));
}

export function defaultLabSettings(): LabSettings {
  return {
    apiKey: "",
    proxyScheme: "http",
    proxyIp: "",
    proxyPort: "8000",
    proxyUser: "",
    proxyPassword: "",
    proxyUrl: "",
  };
}

export function proxyUrlFromSettings(s: LabSettings): string | undefined {
  const full = s.proxyUrl.trim();
  if (full) return full;
  const ip = s.proxyIp.trim();
  if (!ip) return undefined;
  const port = s.proxyPort.trim() || (s.proxyScheme === "http" ? "8080" : "8000");
  const user = s.proxyUser.trim();
  const pass = s.proxyPassword.trim();
  const scheme = s.proxyScheme === "http" ? "http" : "socks5";
  if (user && pass) {
    return `${scheme}://${encodeURIComponent(user)}:${encodeURIComponent(pass)}@${ip}:${port}`;
  }
  return `${scheme}://${ip}:${port}`;
}
