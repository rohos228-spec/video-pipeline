export type ShotMenuTrackKey =
  | "vo"
  | "action"
  | "cam"
  | "set"
  | "characters"
  | "stitch"
  | "scene"
  | "img_prompt"
  | "video_prompt"
  | "frame_no";

export interface ShotMenuTrackDef {
  key: ShotMenuTrackKey;
  label: string;
  pinned: boolean;
}

export interface ShotMenuShot {
  id: number | null;
  uuid: string | null;
  number: number | null;
  label: string;
  duration_sec: number;
  image_url?: string | null;
  voiceover_in_shot: string;
  fields: Record<string, string>;
}

export interface ShotMenuCell {
  index: number;
  parent_id: number | null;
  parent_uuid: string | null;
  title: string;
  voiceover: string;
  duration_sec: number;
  loc: string;
  items: string;
  hide: string;
  layer: string;
  shots: ShotMenuShot[];
}

export interface ShotMenuSummary {
  vo_cells: number;
  shots: number;
  duration_sec: number;
  duration_clock: string;
  vo_chars: number;
}

export interface ShotMenuDTO {
  tracks: ShotMenuTrackDef[];
  default_tracks: ShotMenuTrackKey[];
  cells: ShotMenuCell[];
  summary: ShotMenuSummary;
}

const STORAGE_KEY = "shot-menu-tracks-v1";

const KNOWN_TRACKS = new Set<ShotMenuTrackKey>([
  "vo",
  "action",
  "cam",
  "set",
  "characters",
  "stitch",
  "scene",
  "img_prompt",
  "video_prompt",
  "frame_no",
]);

export function loadShotMenuTracks(defaults: ShotMenuTrackKey[]): ShotMenuTrackKey[] {
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    if (!raw) return defaults;
    const arr = JSON.parse(raw) as string[];
    if (!Array.isArray(arr) || !arr.includes("vo")) return defaults;
    const valid = arr.filter((k): k is ShotMenuTrackKey =>
      KNOWN_TRACKS.has(k as ShotMenuTrackKey),
    );
    return valid.includes("vo") ? valid : defaults;
  } catch {
    return defaults;
  }
}

export function saveShotMenuTracks(keys: ShotMenuTrackKey[]): void {
  try {
    const next = keys.includes("vo") ? keys : (["vo", ...keys] as ShotMenuTrackKey[]);
    localStorage.setItem(STORAGE_KEY, JSON.stringify(next));
  } catch {
    /* private mode */
  }
}
