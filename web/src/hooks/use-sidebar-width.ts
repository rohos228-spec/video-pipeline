"use client";

import { useCallback, useEffect, useState } from "react";

const STORAGE_KEY = "vp-studio-sidebar-width-v2";
const LEGACY_KEY = "vp-studio-sidebar-width";
const DEFAULT_WIDTH = 350; // +15% (was 304)
const MIN_WIDTH = 240;
const MAX_WIDTH = 644;

function clampWidth(value: number) {
  return Math.min(MAX_WIDTH, Math.max(MIN_WIDTH, Math.round(value)));
}

export function useSidebarWidth() {
  const [width, setWidthState] = useState(DEFAULT_WIDTH);

  useEffect(() => {
    try {
      let raw = localStorage.getItem(STORAGE_KEY);
      if (!raw) {
        const legacy = Number(localStorage.getItem(LEGACY_KEY));
        if (!Number.isFinite(legacy) || legacy <= 0) return;
        raw = String(Math.round(legacy * 1.15));
        localStorage.setItem(STORAGE_KEY, raw);
      }
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
