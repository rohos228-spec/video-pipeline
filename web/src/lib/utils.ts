import { type ClassValue, clsx } from "clsx";
import { twMerge } from "tailwind-merge";

export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs));
}

export function parseUtcDate(value: Date | string): Date {
  if (value instanceof Date) return value;
  const raw = String(value).trim();
  if (!raw) return new Date(NaN);
  // SQLite naive timestamps (YYYY-MM-DD HH:MM:SS or ISO without Z) are stored in UTC
  if (/^\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}:\d{2}(?:\.\d+)?$/.test(raw)) {
    return new Date(raw.replace(" ", "T") + "Z");
  }
  return new Date(raw);
}

export function formatRelativeTime(date: Date | string | null | undefined): string {
  if (!date) return "—";
  const d = parseUtcDate(date);
  if (Number.isNaN(d.getTime())) return "—";
  const diffMs = Date.now() - d.getTime();
  const sec = Math.floor(diffMs / 1000);
  if (sec < 10) return "только что";
  if (sec < 60) return `${Math.max(1, sec)} сек назад`;
  const min = Math.floor(sec / 60);
  if (min < 60) return `${min} мин назад`;
  const hr = Math.floor(min / 60);
  if (hr < 24) return `${hr} ч назад`;
  const days = Math.floor(hr / 24);
  if (days < 7) return `${days} д назад`;
  return d.toLocaleDateString("ru-RU", { day: "2-digit", month: "short" });
}

export function formatDuration(seconds: number | null | undefined): string {
  if (seconds == null) return "—";
  const m = Math.floor(seconds / 60);
  const s = Math.floor(seconds % 60);
  return `${m}:${s.toString().padStart(2, "0")}`;
}
