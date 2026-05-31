"use client";

import { useCallback, useEffect, useState } from "react";

const STORAGE_KEY = "vp-studio-sidebar-width";
const DEFAULT_WIDTH = 304;
const MIN_WIDTH = 240;
const MAX_WIDTH = 560;

function clampWidth(value: number) {
  return Math.min(MAX_WIDTH, Math.max(MIN_WIDTH, Math.round(value)));
}

export function useSidebarWidth() {
  const [width, setWidthState] = useState(DEFAULT_WIDTH);

  useEffect(() => {
    try {
      const raw = localStorage.getItem(STORAGE_KEY);
      if (!raw) return;
      const parsed = Number(raw);
      if (Number.isFinite(parsed)) setWidthState(clampWidth(parsed));
    } catch {
      // ignore
    }
  }, []);

  const setWidth = useCallback((next: number) => {
    const clamped = clampWidth(next);
    setWidthState(clamped);
    try {
      localStorage.setItem(STORAGE_KEY, String(clamped));
    } catch {
      // ignore
    }
  }, []);

  return { width, setWidth, minWidth: MIN_WIDTH, maxWidth: MAX_WIDTH };
}
