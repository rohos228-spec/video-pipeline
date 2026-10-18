/**
 * Клиентская очистка HTML отчёта — второй слой после серверного bleach (WS2).
 * Теги, атрибуты и схемы URL читаются из `orchestratorSanitizeAllowlist.json`
 * (тот же файл, что WS2 подставит в bleach.clean).
 * DOMPurify — vendored `orchestratorDomPurify.ts` (пакет в package.json вне зоны WS4).
 *
 * Префикс src картинок bleach-протоколами не задаётся: план пускает только
 * `/api/orchestrator/reports/…`. Хук ниже оставляет это ограничение.
 */

import DOMPurify from "./orchestratorDomPurify";
import allowlist from "./orchestratorSanitizeAllowlist.json";

const ALLOWED_TAGS = allowlist.allowed_tags;
const ATTRS = allowlist.allowed_attributes as Record<string, readonly string[]>;
const ALLOWED_ATTR = [...new Set(Object.values(ATTRS).flat())];
const PROTOCOLS = new Set(allowlist.allowed_protocols.map((item) => item.toLowerCase()));
const REPORT_IMG_SRC_PREFIX = "/api/orchestrator/reports/";

type AttrHook = (node: Element, data: { attrName: string; attrValue: string; keepAttr: boolean }) => void;

type Purify = {
  sanitize?: (dirty: string, config?: Record<string, unknown>) => string;
  addHook?: (entry: string, hook: AttrHook) => void;
  isSupported?: boolean;
};

const purify = DOMPurify as Purify;
let hooked = false;

function attrsFor(tag: string): readonly string[] {
  return [...(ATTRS[tag] ?? []), ...(ATTRS["*"] ?? [])];
}

function schemeOf(value: string): string | null {
  const match = /^([a-z][a-z0-9+.-]*):/i.exec(value.trim());
  return match ? match[1].toLowerCase() : null;
}

function ensureHook(): void {
  if (hooked || !purify.addHook) return;
  hooked = true;
  purify.addHook("uponSanitizeAttribute", (node, data) => {
    const tag = node.tagName.toLowerCase();
    if (!attrsFor(tag).includes(data.attrName)) {
      data.keepAttr = false;
      return;
    }
    if (tag === "img" && data.attrName === "src") {
      if (!data.attrValue.startsWith(REPORT_IMG_SRC_PREFIX)) data.keepAttr = false;
      return;
    }
    if (data.attrName === "href" || data.attrName === "src") {
      const scheme = schemeOf(data.attrValue);
      if (!scheme || !PROTOCOLS.has(scheme)) data.keepAttr = false;
    }
  });
}

export function sanitizeReportHtml(dirty: string): string {
  if (typeof window === "undefined" || typeof purify.sanitize !== "function") return "";
  ensureHook();
  return purify.sanitize(dirty, {
    ALLOWED_TAGS,
    ALLOWED_ATTR,
    ALLOW_DATA_ATTR: false,
    ALLOW_UNKNOWN_PROTOCOLS: false,
  });
}

function escapeText(value: string): string {
  return value.replace(/[&<>"']/g, (char) => {
    if (char === "&") return "&amp;";
    if (char === "<") return "&lt;";
    if (char === ">") return "&gt;";
    if (char === '"') return "&quot;";
    return "&#39;";
  });
}

/** Тёмные токены INT ea6c5926: --background 2%, --foreground 94%. */
const INT_PREVIEW_BG = "hsl(0 0% 2%)";
const INT_PREVIEW_FG = "hsl(0 0% 94%)";

/**
 * Документ для sandbox-iframe. HTML секций проходит DOMPurify, заголовки — как текст.
 * Свой фон и цвет из отчёта не доходят (стили вырезаются), поэтому светлый фрагмент
 * без фона получает пару INT: светлый текст на тёмном фоне.
 */
export function reportPreviewDocument(sections: { heading: string; html: string }[]): string {
  const body = sections
    .map((section) => `<h2>${escapeText(section.heading)}</h2>${sanitizeReportHtml(section.html)}`)
    .join("");
  const surface = `<style>html,body{margin:0;background:${INT_PREVIEW_BG};color:${INT_PREVIEW_FG};}</style>`;
  return `<!DOCTYPE html><html><head><meta charset="utf-8">${surface}<meta http-equiv="Content-Security-Policy" content="default-src 'none'; img-src 'self'; style-src 'unsafe-inline'"></head><body>${body}</body></html>`;
}
