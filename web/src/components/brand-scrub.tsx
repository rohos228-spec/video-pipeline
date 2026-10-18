"use client";
/** Глобальный фильтр: бренд стороннего провайдера никогда не показывается в UI. */
import { useEffect } from "react";

const RX = /(outsee(\.io)?|оутси|аутси)/gi;
const ATTRS = ["title", "placeholder", "aria-label", "alt"];

function fix(s: string): string {
  return s.replace(RX, "VIA");
}

function scrub(root: Node) {
  if (root.nodeType === 3) {
    const v = root.nodeValue || "";
    if (RX.test(v)) root.nodeValue = fix(v);
    RX.lastIndex = 0;
    return;
  }
  if (root.nodeType !== 1) return;
  const el = root as Element;
  const tag = el.tagName;
  if (tag === "SCRIPT" || tag === "STYLE") return;
  for (const a of ATTRS) {
    const v = el.getAttribute(a);
    if (v && /outsee|оутси|аутси/i.test(v)) el.setAttribute(a, fix(v));
  }
  if (tag === "INPUT" || tag === "TEXTAREA") return;
  const w = document.createTreeWalker(el, NodeFilter.SHOW_TEXT);
  let n: Node | null;
  while ((n = w.nextNode())) {
    const p = n.parentElement;
    if (p && (p.tagName === "SCRIPT" || p.tagName === "STYLE" || p.tagName === "TEXTAREA")) continue;
    const v = n.nodeValue || "";
    if (/outsee|оутси|аутси/i.test(v)) n.nodeValue = fix(v);
  }
  el.querySelectorAll("[title],[placeholder],[aria-label],[alt]").forEach((c) => {
    for (const a of ATTRS) {
      const v = c.getAttribute(a);
      if (v && /outsee|оутси|аутси/i.test(v)) c.setAttribute(a, fix(v));
    }
  });
}

export function BrandScrub() {
  useEffect(() => {
    scrub(document.body);
    if (/outsee/i.test(document.title)) document.title = fix(document.title);
    const mo = new MutationObserver((ms) => {
      for (const m of ms) {
        if (m.type === "characterData") scrub(m.target);
        else if (m.type === "attributes") scrub(m.target);
        else m.addedNodes.forEach((n) => scrub(n));
      }
    });
    mo.observe(document.body, { subtree: true, childList: true, characterData: true, attributes: true, attributeFilter: ATTRS });
    return () => mo.disconnect();
  }, []);
  return null;
}
