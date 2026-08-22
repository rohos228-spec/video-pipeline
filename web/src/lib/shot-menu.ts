export type ShotMenuTrackKey = string;

export interface ShotMenuTrackDef {
  key: ShotMenuTrackKey;
  label: string;
  pinned: boolean;
  /** своя строка: читается из shot.all[key] (любое поле кадра/attrs) */
  custom?: boolean;
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
  /** все поля кадра (колонки + attrs) — для своих строк */
  all?: Record<string, string>;
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

const STORAGE_KEY = "shot-menu-tracks-v3";

interface StoredTrack {
  key: string;
  label?: string;
  custom?: boolean;
}

export function loadShotMenuTracks(defaults: ShotMenuTrackKey[]): StoredTrack[] {
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    if (!raw) return defaults.map((k) => ({ key: k }));
    const arr = JSON.parse(raw) as StoredTrack[];
    if (!Array.isArray(arr) || !arr.some((t) => t.key === "vo")) {
      return defaults.map((k) => ({ key: k }));
    }
    return arr
      .filter((t) => typeof t?.key === "string" && t.key)
      .flatMap((t) =>
        t.key === "cam" ? [{ key: "size" }, { key: "move" }] : [t],
      );
  } catch {
    return defaults.map((k) => ({ key: k }));
  }
}

export function saveShotMenuTracks(tracks: StoredTrack[]): void {
  try {
    const next = tracks.some((t) => t.key === "vo")
      ? tracks
      : [{ key: "vo" }, ...tracks];
    localStorage.setItem(STORAGE_KEY, JSON.stringify(next));
  } catch {
    /* private mode */
  }
}
