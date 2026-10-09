"use client";

import { useEffect, useState } from "react";

/**
 * Совпадает ли окно с CSS-условием (например, "(max-width: 767.98px)").
 * До монтирования возвращает false: сервер и первый кадр клиента рисуют одно и то же,
 * иначе React ругается на несовпадение разметки.
 */
export function useMediaQuery(query: string): boolean {
  const [matches, setMatches] = useState(false);
  useEffect(() => {
    if (typeof window === "undefined" || typeof window.matchMedia !== "function") return;
    const mq = window.matchMedia(query);
    const sync = () => setMatches(mq.matches);
    sync();
    mq.addEventListener("change", sync);
    return () => mq.removeEventListener("change", sync);
  }, [query]);
  return matches;
}

/** Телефон: ширина меньше 768px (граница `md` в Tailwind). */
export const NARROW_QUERY = "(max-width: 767.98px)";
