"use client";

import { useEffect } from "react";

/**
 * Простейшая обработка клавиш на window, активная только пока модалка
 * открыта. Без зависимостей от внешних либ.
 */
export function useHotkeysInDialog(active: boolean, handler: (e: KeyboardEvent) => void) {
  useEffect(() => {
    if (!active) return;
    const onKey = (e: KeyboardEvent) => handler(e);
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [active, handler]);
}
